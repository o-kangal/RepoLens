import tempfile
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from repolens.db.models import Chunk as ChunkRow
from repolens.db.models import Repo, RepoStatus
from repolens.ingest.chunk import Strategy
from repolens.ingest.clone import (
    Checkout,
    GitHubRepository,
    InvalidRepositoryError,
    shallow_clone,
)
from repolens.ingest.pipeline import (
    INTERNAL_ERROR_MESSAGE,
    INTERRUPTED_MESSAGE,
    Clone,
    IngestResult,
    fail_interrupted_jobs,
    index_repository,
    register_repository,
    repository_status,
)
from repolens.ingest.walker import FileLimitExceededError
from repolens.providers.embeddings import Embedder, EmbeddingProviderError, FakeEmbedder
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


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Route temporary directories here so tests can check that clones are removed."""
    directory = tmp_path / "scratch"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    return directory


def local_clone(
    root: Path, commit_sha: str
) -> tuple[Clone, list[tuple[GitHubRepository, str | None]]]:
    """A clone that serves a local directory and records what was requested."""
    requested: list[tuple[GitHubRepository, str | None]] = []

    @asynccontextmanager
    async def clone(repository: GitHubRepository, ref: str | None) -> AsyncIterator[Checkout]:
        requested.append((repository, ref))
        yield Checkout(root=root, commit_sha=commit_sha)

    return clone, requested


def git_clone_of(source: str) -> Clone:
    def clone(_: GitHubRepository, ref: str | None) -> AbstractAsyncContextManager[Checkout]:
        return shallow_clone(source, ref=ref)

    return clone


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for rel_path, content in files.items():
        (root / rel_path).parent.mkdir(parents=True, exist_ok=True)
        (root / rel_path).write_text(content)
    return root


async def ingest(
    sessions: sessionmaker[Session],
    clone: Clone,
    embedder: Embedder | None = None,
    *,
    url: str = URL,
    ref: str | None = None,
    max_files: int = 100,
) -> IngestResult:
    repo_id = register_repository(sessions, url, ref)
    return await index_repository(
        repo_id,
        embedder=embedder or FakeEmbedder(),
        session_factory=sessions,
        clone=clone,
        max_files=max_files,
    )


def count(sessions: sessionmaker[Session], model: type[Repo] | type[ChunkRow]) -> int:
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


class FailingEmbedder(FakeEmbedder):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise self._error


def test_registration_records_a_running_repository(sessions: sessionmaker[Session]) -> None:
    repo_id = register_repository(sessions, "https://github.com/acme/app.git/", "v1.2")

    with sessions() as session:
        repo = session.get_one(Repo, repo_id)
        assert (repo.url, repo.ref, repo.commit_sha) == (URL, "v1.2", None)
        assert (repo.status, repo.error) == (RepoStatus.RUNNING, None)


@pytest.mark.parametrize(
    ("url", "ref"),
    [
        ("https://gitlab.com/acme/app", None),
        ("file:///etc", None),
        ("acme/app", None),
        (URL, "--upload-pack=touch"),
        (URL, "main..dev"),
    ],
)
def test_rejected_requests_store_nothing(
    sessions: sessionmaker[Session], url: str, ref: str | None
) -> None:
    with pytest.raises(InvalidRepositoryError):
        register_repository(sessions, url, ref)

    assert count(sessions, Repo) == 0


async def test_indexing_stores_every_strategy_and_marks_done(
    tmp_path: Path, sessions: sessionmaker[Session]
) -> None:
    clone, requested = local_clone(write_tree(tmp_path, FILES), "abc123")

    result = await ingest(sessions, clone, ref="main")

    assert requested == [(GitHubRepository("acme", "app"), "main")]
    assert (result.url, result.commit_sha) == (URL, "abc123")
    assert result.chunk_counts == {Strategy.NAIVE: 2, Strategy.AST: 3, Strategy.MARKDOWN: 2}
    with sessions() as session:
        repo = session.get_one(Repo, result.repo_id)
        assert (repo.status, repo.commit_sha, repo.error) == (RepoStatus.DONE, "abc123", None)
        rows = session.scalars(
            select(ChunkRow).order_by(ChunkRow.strategy, ChunkRow.path, ChunkRow.start_line)
        )
        stored = [
            (row.strategy, row.path, row.start_line, row.end_line, row.kind, row.symbol)
            for row in rows
        ]
        # AST chunks sit beside the naive ones; for documentation they are the heading sections.
        assert stored == [
            ("ast", "README.md", 1, 2, "section", None),
            ("ast", "README.md", 3, 5, "section", None),
            ("ast", "src/main.py", 1, 2, "function", "main"),
            ("markdown", "README.md", 1, 2, "section", None),
            ("markdown", "README.md", 3, 5, "section", None),
            ("naive", "README.md", 1, 5, "window", None),
            ("naive", "src/main.py", 1, 2, "window", None),
        ]
        assert session.scalar(select(func.count()).where(ChunkRow.embedding.is_(None))) == 0
        assert set(session.scalars(select(ChunkRow.embedding_model))) == {"fake"}


async def test_status_reports_chunk_counts_per_strategy(
    tmp_path: Path, sessions: sessionmaker[Session]
) -> None:
    clone, _ = local_clone(write_tree(tmp_path, FILES), "abc123")
    result = await ingest(sessions, clone)

    found = repository_status(sessions, result.repo_id)

    assert found is not None
    assert (found.status, found.commit_sha) == (RepoStatus.DONE, "abc123")
    assert found.chunk_counts == {Strategy.NAIVE: 2, Strategy.AST: 3, Strategy.MARKDOWN: 2}
    assert repository_status(sessions, uuid.uuid4()) is None


async def test_identical_contents_are_embedded_once(
    tmp_path: Path, sessions: sessionmaker[Session]
) -> None:
    clone, _ = local_clone(write_tree(tmp_path, {"a.py": "same = 1\n", "b.py": "same = 1\n"}), "c1")
    embedder = FakeEmbedder()

    result = await ingest(sessions, clone, embedder)

    # The naive windows match; AST chunks differ because their header names the path.
    assert embedder.calls == [
        [
            "same = 1\n",
            "# path: a.py\n# symbol: module-level code\nsame = 1\n",
            "# path: b.py\n# symbol: module-level code\nsame = 1\n",
        ]
    ]
    assert (result.embedded, result.reused) == (3, 0)


async def test_stored_hashes_are_not_re_embedded(
    tmp_path: Path, sessions: sessionmaker[Session]
) -> None:
    root = write_tree(tmp_path, FILES)
    await ingest(sessions, local_clone(root, "abc123")[0])

    (root / "src" / "main.py").write_text("def main():\n    return 2\n")
    embedder = FakeEmbedder()
    result = await ingest(sessions, local_clone(root, "def456")[0], embedder)

    assert embedder.calls == [
        [
            "def main():\n    return 2\n",
            "# path: src/main.py\n# symbol: main (function)\ndef main():\n    return 2\n",
        ]
    ]
    # The README's AST sections share their hashes with its Markdown sections.
    assert (result.embedded, result.reused) == (2, 3)
    assert count(sessions, Repo) == 2


async def test_embeddings_of_another_model_are_not_reused(
    tmp_path: Path, sessions: sessionmaker[Session]
) -> None:
    clone, _ = local_clone(write_tree(tmp_path, FILES), "abc123")
    await ingest(sessions, clone, FakeEmbedder(model="old"))

    result = await ingest(sessions, clone, FakeEmbedder(model="new"))

    assert (result.embedded, result.reused) == (5, 0)
    with sessions() as session:
        assert set(session.scalars(select(ChunkRow.embedding_model))) == {"new"}


async def test_reindexing_a_commit_replaces_the_earlier_index(
    tmp_path: Path, sessions: sessionmaker[Session]
) -> None:
    clone, _ = local_clone(write_tree(tmp_path, FILES), "abc123")
    first = await ingest(sessions, clone)

    again = await ingest(sessions, clone)

    assert (again.embedded, again.reused) == (0, 5)
    assert repository_status(sessions, first.repo_id) is None
    assert count(sessions, Repo) == 1
    assert count(sessions, ChunkRow) == 7


async def test_walk_failure_marks_failed_removes_the_clone_and_stores_no_chunks(
    tmp_path: Path, sessions: sessionmaker[Session], scratch: Path
) -> None:
    source = make_repo(tmp_path / "origin", FILES)
    repo_id = register_repository(sessions, URL, None)

    with pytest.raises(FileLimitExceededError):
        await index_repository(
            repo_id,
            embedder=FakeEmbedder(),
            session_factory=sessions,
            clone=git_clone_of(source),
            max_files=1,
        )

    assert list(scratch.iterdir()) == []
    found = repository_status(sessions, repo_id)
    assert found is not None
    assert found.status is RepoStatus.FAILED
    assert found.error == "repository has more than 1 indexable files"
    assert count(sessions, ChunkRow) == 0


async def test_provider_failure_is_reported_and_stores_no_chunks(
    tmp_path: Path, sessions: sessionmaker[Session], scratch: Path
) -> None:
    source = make_repo(tmp_path / "origin", FILES)
    error = EmbeddingProviderError("Voyage returned HTTP 503: busy", status=503)

    with pytest.raises(EmbeddingProviderError):
        await ingest(sessions, git_clone_of(source), FailingEmbedder(error))

    assert list(scratch.iterdir()) == []
    with sessions() as session:
        repo = session.scalars(select(Repo)).one()
        assert (repo.status, repo.error) == (RepoStatus.FAILED, "Voyage returned HTTP 503: busy")
    assert count(sessions, ChunkRow) == 0


async def test_unexpected_errors_are_reported_generically(
    tmp_path: Path, sessions: sessionmaker[Session]
) -> None:
    clone, _ = local_clone(write_tree(tmp_path, FILES), "abc123")
    error = RuntimeError("password=hunter2 leaked in an internal message")

    with pytest.raises(RuntimeError):
        await ingest(sessions, clone, FailingEmbedder(error))

    with sessions() as session:
        assert session.scalars(select(Repo.error)).one() == INTERNAL_ERROR_MESSAGE


async def test_embedder_dimension_must_match_the_column(
    tmp_path: Path, sessions: sessionmaker[Session]
) -> None:
    clone, requested = local_clone(tmp_path, "abc123")

    with pytest.raises(ValueError, match="dimension 8"):
        await ingest(sessions, clone, FakeEmbedder(dimension=8))

    assert requested == []
    with sessions() as session:
        assert session.scalars(select(Repo.status)).one() == RepoStatus.FAILED


def test_interrupted_jobs_are_marked_failed(sessions: sessionmaker[Session]) -> None:
    running = register_repository(sessions, URL, None)

    assert fail_interrupted_jobs(sessions) == 1
    assert fail_interrupted_jobs(sessions) == 0
    found = repository_status(sessions, running)
    assert found is not None
    assert (found.status, found.error) == (RepoStatus.FAILED, INTERRUPTED_MESSAGE)
