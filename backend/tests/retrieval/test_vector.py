import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

from repolens.db.models import RepoStatus
from repolens.ingest.chunk import Strategy
from repolens.retrieval.types import (
    EmbeddingModelMismatchError,
    RepositoryNotFoundError,
    RepositoryNotReadyError,
)
from repolens.retrieval.vector import vector_search
from tests.retrieval.seed import QueryEmbedder, add_chunk, add_repo, vector

pytestmark = pytest.mark.anyio

QUERY = "where is the user loaded?"
EMBEDDER = QueryEmbedder({QUERY: vector((0, 1.0))})


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine)


async def search(
    sessions: sessionmaker[Session],
    repo_id: uuid.UUID,
    *,
    strategy: Strategy = Strategy.NAIVE,
    k: int = 10,
    embedder: QueryEmbedder = EMBEDDER,
) -> list[tuple[str, float]]:
    chunks = await vector_search(
        sessions, embedder, repo_id=repo_id, strategy=strategy, query=QUERY, k=k
    )
    return [(chunk.path, round(chunk.score, 3)) for chunk in chunks]


async def test_ranks_by_cosine_similarity(sessions: sessionmaker[Session]) -> None:
    repo = add_repo(sessions)
    add_chunk(sessions, repo, "orthogonal.py", vector((1, 1.0)))
    add_chunk(sessions, repo, "exact.py", vector((0, 1.0)))
    add_chunk(sessions, repo, "close.py", vector((0, 2.0), (1, 1.0)))
    add_chunk(sessions, repo, "opposite.py", vector((0, -1.0)))

    assert await search(sessions, repo) == [
        ("exact.py", 1.0),
        ("close.py", 0.894),
        ("orthogonal.py", 0.0),
        ("opposite.py", -1.0),
    ]


async def test_returns_at_most_k(sessions: sessionmaker[Session]) -> None:
    repo = add_repo(sessions)
    for index in range(5):
        add_chunk(sessions, repo, f"f{index}.py", vector((0, 1.0), (1, float(index))))

    assert [path for path, _ in await search(sessions, repo, k=2)] == ["f0.py", "f1.py"]


async def test_never_crosses_repositories_or_strategies(sessions: sessionmaker[Session]) -> None:
    repo = add_repo(sessions)
    other = add_repo(sessions, url="https://github.com/acme/other")
    add_chunk(sessions, repo, "mine.py", vector((1, 1.0)))
    add_chunk(sessions, repo, "README.md", vector((0, 1.0)), strategy="markdown")
    add_chunk(sessions, other, "theirs.py", vector((0, 1.0)))

    assert await search(sessions, repo) == [("mine.py", 0.0)]
    assert await search(sessions, repo, strategy=Strategy.MARKDOWN) == [("README.md", 1.0)]
    assert await search(sessions, repo, strategy=Strategy.AST) == []


@pytest.fixture
def index_only_sessions(scratch_url: str, engine: Engine) -> Iterator[sessionmaker[Session]]:
    """Sessions whose planner must order by the HNSW index, as it can on a large table."""
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_chunks_repo_id_strategy"))
    forced = create_engine(scratch_url)

    @event.listens_for(forced, "connect")
    def disable_seqscan(connection: DBAPIConnection, _: ConnectionPoolEntry) -> None:
        cursor = connection.cursor()
        cursor.execute("SET enable_seqscan = off")
        cursor.close()

    yield sessionmaker(forced)
    forced.dispose()


async def test_filtered_index_scan_still_returns_k_results(
    index_only_sessions: sessionmaker[Session],
) -> None:
    sessions = index_only_sessions
    repo = add_repo(sessions)
    crowd = add_repo(sessions, url="https://github.com/acme/crowd")
    # Another repository holds many chunks nearer to the query than any of ours, so the first
    # ef_search candidates of the index scan all belong to it.
    for index in range(300):
        add_chunk(sessions, crowd, f"crowd{index}.py", vector((0, 1.0), (2, index / 1000)))
    for index in range(5):
        add_chunk(sessions, repo, f"mine{index}.py", vector((0, 1.0), (1, 1.0 + index)))

    with sessions() as session:
        plan = "\n".join(
            session.scalars(
                text(
                    "EXPLAIN SELECT id FROM chunks WHERE repo_id = :repo "
                    "ORDER BY embedding <=> CAST(:query AS vector) LIMIT 5"
                ),
                {"repo": repo, "query": str(vector((0, 1.0)))},
            )
        )
    assert "ix_chunks_embedding_hnsw" in plan

    paths = [path for path, _ in await search(sessions, repo, k=5)]

    assert paths == [f"mine{index}.py" for index in range(5)]


async def test_unknown_repository_is_an_error(sessions: sessionmaker[Session]) -> None:
    with pytest.raises(RepositoryNotFoundError):
        await search(sessions, uuid.uuid4())


@pytest.mark.parametrize("status", [RepoStatus.RUNNING, RepoStatus.FAILED])
async def test_unfinished_repository_is_an_error(
    sessions: sessionmaker[Session], status: RepoStatus
) -> None:
    with pytest.raises(RepositoryNotReadyError):
        await search(sessions, add_repo(sessions, status=status))


async def test_query_is_embedded_with_the_index_model(sessions: sessionmaker[Session]) -> None:
    repo = add_repo(sessions)
    add_chunk(sessions, repo, "a.py", vector((0, 1.0)), model="voyage-code-3")
    embedder = QueryEmbedder({QUERY: vector((0, 1.0))}, model="voyage-code-4")

    with pytest.raises(EmbeddingModelMismatchError, match=r"voyage-code-3.*voyage-code-4"):
        await search(sessions, repo, embedder=embedder)

    assert embedder.calls == []
