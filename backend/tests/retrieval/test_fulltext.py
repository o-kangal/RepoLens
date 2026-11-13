import uuid

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from repolens.db.models import RepoStatus
from repolens.ingest.chunk import Strategy
from repolens.retrieval.fulltext import fulltext_search
from repolens.retrieval.search import HYBRID_TOP, SearchMode, search
from repolens.retrieval.types import RepositoryNotFoundError, RepositoryNotReadyError
from repolens.retrieval.vector import vector_search
from tests.retrieval.seed import QueryEmbedder, add_chunk, add_repo, vector

pytestmark = pytest.mark.anyio

ANYWHERE = vector((5, 1.0))
"""An embedding unrelated to every query below."""


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine)


@pytest.fixture
def code_repo(sessions: sessionmaker[Session]) -> uuid.UUID:
    repo_id = add_repo(sessions)
    for path, content in {
        "signer.py": "def sign(self, value):\n    key = self.derive_key()\n",
        "order.py": "key = derive()\n",
        "users.ts": "const user = await UserService.findById(id);\n",
        "notes.md": "How does it go? Nobody knows how.\n",
    }.items():
        add_chunk(sessions, repo_id, path, ANYWHERE, content=content)
    return repo_id


async def fulltext(
    sessions: sessionmaker[Session],
    repo_id: uuid.UUID,
    query: str,
    *,
    strategy: Strategy = Strategy.NAIVE,
    k: int = 30,
) -> list[str]:
    chunks = await fulltext_search(sessions, repo_id=repo_id, strategy=strategy, query=query, k=k)
    return [chunk.path for chunk in chunks]


async def test_snake_case_identifiers_match_inside_attribute_access(
    sessions: sessionmaker[Session], code_repo: uuid.UUID
) -> None:
    # "key = derive()" holds both words, but not as the phrase derive_key.
    assert await fulltext(sessions, code_repo, "derive_key") == ["signer.py"]


async def test_camel_case_identifiers_match_inside_a_dotted_name(
    sessions: sessionmaker[Session], code_repo: uuid.UUID
) -> None:
    assert await fulltext(sessions, code_repo, "findById") == ["users.ts"]
    assert await fulltext(sessions, code_repo, "UserService.findById") == ["users.ts"]


async def test_a_question_matches_its_words_and_ignores_stop_words(
    sessions: sessionmaker[Session], code_repo: uuid.UUID
) -> None:
    # "How", "does" and "the" are stop words; notes.md holds them twice, yet does not match.
    assert await fulltext(sessions, code_repo, "How does the derive_key work?") == ["signer.py"]
    assert await fulltext(sessions, code_repo, "how does the") == []
    assert await fulltext(sessions, code_repo, "?! ...") == []
    assert await fulltext(sessions, code_repo, "   ") == []


async def test_ranks_by_ts_rank(sessions: sessionmaker[Session]) -> None:
    repo_id = add_repo(sessions)
    add_chunk(sessions, repo_id, "once.py", ANYWHERE, content="token = 1\n")
    add_chunk(sessions, repo_id, "twice.py", ANYWHERE, content="token = token + 1\n")
    add_chunk(sessions, repo_id, "both.py", ANYWHERE, content="token = refresh(token, token)\n")

    chunks = await fulltext_search(
        sessions, repo_id=repo_id, strategy=Strategy.NAIVE, query="refresh token", k=10
    )

    assert [chunk.path for chunk in chunks] == ["both.py", "twice.py", "once.py"]
    assert [chunk.score for chunk in chunks] == sorted(
        (chunk.score for chunk in chunks), reverse=True
    )
    assert all(chunk.score > 0 for chunk in chunks)


async def test_filters_by_repository_and_strategy_and_limits_to_k(
    sessions: sessionmaker[Session], code_repo: uuid.UUID
) -> None:
    other = add_repo(sessions, url="https://github.com/acme/other")
    add_chunk(sessions, other, "elsewhere.py", ANYWHERE, content="self.derive_key()\n")
    add_chunk(
        sessions, code_repo, "ast.py", ANYWHERE, content="self.derive_key()\n", strategy="ast"
    )

    assert await fulltext(sessions, code_repo, "derive_key") == ["signer.py"]
    assert await fulltext(sessions, code_repo, "derive_key", strategy=Strategy.AST) == ["ast.py"]
    assert len(await fulltext(sessions, code_repo, "key derive", k=1)) == 1


# Hybrid mode: vector and full-text search fused with RRF.

QUERY = "derive_key"


async def run(
    sessions: sessionmaker[Session], repo_id: uuid.UUID, mode: SearchMode, *, k: int = 20
) -> list[tuple[str, float]]:
    chunks = await search(
        sessions,
        QueryEmbedder({QUERY: vector((0, 1.0))}),
        repo_id=repo_id,
        strategy=Strategy.NAIVE,
        query=QUERY,
        mode=mode,
        k=k,
    )
    return [(chunk.path, chunk.score) for chunk in chunks]


async def test_hybrid_fuses_both_rankings_with_rrf(sessions: sessionmaker[Session]) -> None:
    repo_id = add_repo(sessions)
    # Vector ranking: near.py (1), close.py (2), lexical.py (3). Full text: lexical.py only.
    add_chunk(sessions, repo_id, "near.py", vector((0, 1.0)), content="pass\n")
    add_chunk(sessions, repo_id, "close.py", vector((0, 1.0), (1, 0.5)), content="pass\n")
    add_chunk(sessions, repo_id, "lexical.py", vector((1, 1.0)), content="self.derive_key()\n")

    assert await run(sessions, repo_id, SearchMode.HYBRID) == [
        ("lexical.py", pytest.approx(1 / 63 + 1 / 61)),
        ("near.py", pytest.approx(1 / 61)),
        ("close.py", pytest.approx(1 / 62)),
    ]
    vector_only = await run(sessions, repo_id, SearchMode.VECTOR)
    assert [path for path, _ in vector_only] == ["near.py", "close.py", "lexical.py"]


async def test_vector_mode_is_plain_vector_search(
    sessions: sessionmaker[Session], code_repo: uuid.UUID
) -> None:
    embedder = QueryEmbedder({QUERY: vector((0, 1.0))})
    expected = await vector_search(
        sessions, embedder, repo_id=code_repo, strategy=Strategy.NAIVE, query=QUERY, k=3
    )

    assert await run(sessions, code_repo, SearchMode.VECTOR, k=3) == [
        (chunk.path, chunk.score) for chunk in expected
    ]


async def test_hybrid_keeps_at_most_the_top_twenty(sessions: sessionmaker[Session]) -> None:
    repo_id = add_repo(sessions)
    for index in range(25):
        add_chunk(sessions, repo_id, f"f{index}.py", ANYWHERE, content="self.derive_key()\n")

    assert len(await run(sessions, repo_id, SearchMode.HYBRID, k=50)) == HYBRID_TOP
    assert len(await run(sessions, repo_id, SearchMode.HYBRID, k=5)) == 5


async def test_hybrid_checks_the_index_like_vector_search(
    sessions: sessionmaker[Session],
) -> None:
    running = add_repo(sessions, status=RepoStatus.RUNNING)

    with pytest.raises(RepositoryNotReadyError):
        await run(sessions, running, SearchMode.HYBRID)
    with pytest.raises(RepositoryNotFoundError):
        await run(sessions, uuid.uuid4(), SearchMode.HYBRID)
