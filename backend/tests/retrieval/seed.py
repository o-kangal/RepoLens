"""Seed repositories and chunks with hand-made vectors, and embed queries to known vectors."""

import math
import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session, sessionmaker

from repolens.db.models import EMBEDDING_DIMENSION, Chunk, Repo, RepoStatus
from repolens.providers.embeddings import FakeEmbedder

MODEL = "fake"


def vector(*weights: tuple[int, float]) -> list[float]:
    """A unit vector with the given (axis, weight) components."""
    values = [0.0] * EMBEDDING_DIMENSION
    for axis, weight in weights:
        values[axis] = weight
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values]


def add_repo(
    sessions: sessionmaker[Session],
    *,
    status: RepoStatus = RepoStatus.DONE,
    url: str = "https://github.com/acme/app",
) -> uuid.UUID:
    with sessions.begin() as session:
        repo = Repo(
            url=url,
            status=status,
            commit_sha=uuid.uuid4().hex if status is RepoStatus.DONE else None,
        )
        session.add(repo)
        session.flush()
        return repo.id


def add_chunk(
    sessions: sessionmaker[Session],
    repo_id: uuid.UUID,
    path: str,
    embedding: list[float],
    *,
    strategy: str = "naive",
    model: str = MODEL,
    content: str | None = None,
) -> None:
    with sessions.begin() as session:
        session.add(
            Chunk(
                repo_id=repo_id,
                strategy=strategy,
                path=path,
                language="python",
                kind="window",
                start_line=1,
                end_line=3,
                content=content if content is not None else f"content of {path}",
                token_count=4,
                content_hash=uuid.uuid4().hex,
                embedding=embedding,
                embedding_model=model,
            )
        )


class QueryEmbedder(FakeEmbedder):
    """Embeds each known query to a chosen vector."""

    def __init__(self, queries: dict[str, list[float]], *, model: str = MODEL) -> None:
        super().__init__(model=model)
        self._queries = queries

    async def embed_query(self, text: str) -> list[float]:
        self.calls.append([text])
        return self._queries[text]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError("search must not embed documents")
