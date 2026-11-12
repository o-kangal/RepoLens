"""Types shared by the retrieval stages."""

import uuid
from dataclasses import dataclass

from repolens.ingest.chunk import Strategy


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    strategy: Strategy
    path: str
    start_line: int
    end_line: int
    language: str | None
    symbol: str | None
    kind: str | None
    content: str
    score: float
    """Higher is more relevant. For vector search, the cosine similarity."""


class RetrievalError(Exception):
    """A search that cannot run as asked."""


class RepositoryNotFoundError(RetrievalError):
    pass


class RepositoryNotReadyError(RetrievalError):
    """The repository has no finished index to search."""


class EmbeddingModelMismatchError(RetrievalError):
    """The index was built with another embedding model than the one configured."""
