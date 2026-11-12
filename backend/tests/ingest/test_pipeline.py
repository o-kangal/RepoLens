import tempfile
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from repolens.db.models import Chunk as ChunkRow
from repolens.db.models import Repo
from repolens.ingest.chunk import Strategy
from repolens.ingest.clone import (
    Checkout,
    GitHubRepository,
    InvalidRepositoryError,
    shallow_clone,
)
from repolens.ingest.pipeline import Clone, ingest_repository
from repolens.ingest.walker import FileLimitExceededError
from repolens.providers.embeddings import EmbeddingProviderError, FakeEmbedder
from tests.ingest.git_fixtures import make_repo

pytestmark = pytest.mark.anyio

URL = "https://github.com/acme/app"
FILES = {
    "README.md": "# App\n\n## Install\n\nRun it.\n",
    "src/main.py": "def main():\n    return 1\n",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine)


def local_clone(root: Path, commit_sha: str) -> tuple[Clone, list[GitHubRepository]]:
    """A clone that serves a local directory and records which repositories were requested."""
    requested: list[GitHubRepository] = []

    @asynccontextmanager
    async def clone(repository: GitHubRepository, ref: str | None) -> AsyncIterator[Checkout]:
        requested.append(repository)
        yield Checkout(root=root, commit_sha=commit_sha)

    return clone, requested


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for rel_path, content in files.items():
        (root / rel_path).parent.mkdir(parents=True, exist_ok=True)
        (root / rel_path).write_text(content)
    return root


def git_clone_of(source: str) -> Clone:
    def clone(_: GitHubRepository, ref: str | None) -> AbstractAsyncContextManager[Checkout]:
        return shallow_clone(source, ref=ref)

    return clone


class FailingEmbedder(FakeEmbedder):
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingProviderError("Voyage returned HTTP 503", status=503)


async def test_stores_every_strategy_with_embeddings(
    tmp_path: Path, sessions: sessionmaker[Session]
) -> None:
    clone, requested = local_clone(write_tree(tmp_path, FILES), "abc123")
    embedder = FakeEmbedder()

    result = await ingest_repository(
        "https://github.com/acme/app.git", embedder=embedder, session_factory=sessions, clone=clone
    )

    assert requested == [GitHubRepository("acme", "app")]
    assert (result.url, result.commit_sha) == (URL, "abc123")
    assert result.chunk_counts == {Strategy.NAIVE: 2, Strategy.MARKDOWN: 2}
    with sessions() as session:
        repo = session.scalars(select(Repo)).one()
        assert (repo.id, repo.url, repo.commit_sha) == (result.repo_id, URL, "abc123")
        rows = session.scalars(select(ChunkRow).order_by(ChunkRow.strategy, ChunkRow.start_line))
        stored = [(row.strategy, row.path, row.start_line, row.end_line) for row in rows]
        assert stored == [
            ("markdown", "README.md", 1, 2),
            ("markdown", "README.md", 3, 5),
            ("naive", "README.md", 1, 5),
            ("naive", "src/main.py", 1, 2),
        ]
        assert session.scalar(select(func.count()).where(ChunkRow.embedding.is_(None))) == 0
        assert set(session.scalars(select(ChunkRow.embedding_model))) == {"fake"}


async def test_identical_contents_are_embedded_once(
    tmp_path: Path, sessions: sessionmaker[Session]
) -> None:
    files = {"a.py": "same = 1\n", "b.py": "same = 1\n"}
    clone, _ = local_clone(write_tree(tmp_path, files), "abc123")
    embedder = FakeEmbedder()

    result = await ingest_repository(URL, embedder=embedder, session_factory=sessions, clone=clone)

    assert embedder.calls == [["same = 1\n"]]
    assert (result.embedded, result.reused) == (1, 0)


async def test_stored_hashes_are_not_re_embedded(
    tmp_path: Path, sessions: sessionmaker[Session]
) -> None:
    root = write_tree(tmp_path, FILES)
    first, _ = local_clone(root, "abc123")
    await ingest_repository(URL, embedder=FakeEmbedder(), session_factory=sessions, clone=first)

    (root / "src" / "main.py").write_text("def main():\n    return 2\n")
    second, _ = local_clone(root, "def456")
    embedder = FakeEmbedder()
    result = await ingest_repository(URL, embedder=embedder, session_factory=sessions, clone=second)

    assert embedder.calls == [["def main():\n    return 2\n"]]
    assert (result.embedded, result.reused) == (1, 3)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Repo)) == 2


async def test_embeddings_of_another_model_are_not_reused(
    tmp_path: Path, sessions: sessionmaker[Session]
) -> None:
    clone, _ = local_clone(write_tree(tmp_path, FILES), "abc123")
    await ingest_repository(
        URL, embedder=FakeEmbedder(model="old"), session_factory=sessions, clone=clone
    )

    embedder = FakeEmbedder(model="new")
    result = await ingest_repository(URL, embedder=embedder, session_factory=sessions, clone=clone)

    assert (result.embedded, result.reused) == (4, 0)
    with sessions() as session:
        assert set(session.scalars(select(ChunkRow.embedding_model))) == {"new"}


async def test_reindexing_a_commit_replaces_its_chunks(
    tmp_path: Path, sessions: sessionmaker[Session]
) -> None:
    clone, _ = local_clone(write_tree(tmp_path, FILES), "abc123")
    first = await ingest_repository(
        URL, embedder=FakeEmbedder(), session_factory=sessions, clone=clone
    )
    again = await ingest_repository(
        URL, embedder=FakeEmbedder(), session_factory=sessions, clone=clone
    )

    assert again.repo_id == first.repo_id
    assert (again.embedded, again.reused) == (0, 4)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ChunkRow)) == 4


@pytest.mark.parametrize("url", ["https://gitlab.com/acme/app", "file:///etc", "acme/app"])
async def test_rejected_urls_are_never_cloned(
    tmp_path: Path, sessions: sessionmaker[Session], url: str
) -> None:
    clone, requested = local_clone(tmp_path, "abc123")

    with pytest.raises(InvalidRepositoryError):
        await ingest_repository(url, embedder=FakeEmbedder(), session_factory=sessions, clone=clone)

    assert requested == []


async def test_embedder_dimension_must_match_the_column(
    tmp_path: Path, sessions: sessionmaker[Session]
) -> None:
    clone, requested = local_clone(tmp_path, "abc123")

    with pytest.raises(ValueError, match="dimension 8"):
        await ingest_repository(
            URL, embedder=FakeEmbedder(dimension=8), session_factory=sessions, clone=clone
        )

    assert requested == []


async def test_failure_while_walking_removes_the_clone_and_stores_nothing(
    tmp_path: Path, sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    source = make_repo(tmp_path / "origin", FILES)

    with pytest.raises(FileLimitExceededError):
        await ingest_repository(
            URL,
            embedder=FakeEmbedder(),
            session_factory=sessions,
            clone=git_clone_of(source),
            max_files=1,
        )

    assert list(scratch.iterdir()) == []
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Repo)) == 0


async def test_provider_failure_stores_nothing(
    tmp_path: Path, sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    source = make_repo(tmp_path / "origin", FILES)

    with pytest.raises(EmbeddingProviderError):
        await ingest_repository(
            URL, embedder=FailingEmbedder(), session_factory=sessions, clone=git_clone_of(source)
        )

    assert list(scratch.iterdir()) == []
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Repo)) == 0
        assert session.scalar(select(func.count()).select_from(ChunkRow)) == 0
