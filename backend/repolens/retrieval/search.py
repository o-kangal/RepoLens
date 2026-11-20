"""Retrieval entry point: vector search alone, or hybrid search fused with RRF, either one
optionally reranked.

The experiments compare these, so mode and reranker are parameters rather than settings.
"""

import uuid
from enum import StrEnum

from sqlalchemy.orm import Session, sessionmaker

from repolens.ingest.chunk import Strategy
from repolens.providers.embeddings import Embedder
from repolens.retrieval.fulltext import fulltext_search
from repolens.retrieval.fusion import reciprocal_rank_fusion
from repolens.retrieval.rerank import RERANK_TOP, Reranker, rerank_chunks
from repolens.retrieval.types import RetrievedChunk
from repolens.retrieval.vector import vector_search

HYBRID_CANDIDATES = 30
"""Chunks taken from each of vector and full-text search before fusion."""
HYBRID_TOP = 20
"""Chunks hybrid search keeps after fusion."""
RERANK_CANDIDATES = HYBRID_TOP
"""Chunks either mode hands the reranker, so both rerank the same number of candidates."""


class SearchMode(StrEnum):
    VECTOR = "vector"
    HYBRID = "hybrid"


async def search(
    session_factory: sessionmaker[Session],
    embedder: Embedder,
    *,
    repo_id: uuid.UUID,
    strategy: Strategy,
    query: str,
    mode: SearchMode,
    k: int,
    reranker: Reranker | None = None,
) -> list[RetrievedChunk]:
    """The top ``k`` chunks; hybrid search returns at most ``HYBRID_TOP``.

    With a ``reranker``, the mode's top ``RERANK_CANDIDATES`` chunks are reranked and at most
    ``RERANK_TOP`` are kept; a failed rerank raises ``RerankError``. Raises the
    ``RetrievalError`` subclasses of ``vector_search`` for a missing, unfinished or mismatched
    index, in either mode.
    """
    if reranker is None:
        return await _first_stage(
            session_factory,
            embedder,
            repo_id=repo_id,
            strategy=strategy,
            query=query,
            mode=mode,
            k=k,
        )
    candidates = await _first_stage(
        session_factory,
        embedder,
        repo_id=repo_id,
        strategy=strategy,
        query=query,
        mode=mode,
        k=RERANK_CANDIDATES,
    )
    return await rerank_chunks(reranker, query, candidates, top=min(k, RERANK_TOP))


async def _first_stage(
    session_factory: sessionmaker[Session],
    embedder: Embedder,
    *,
    repo_id: uuid.UUID,
    strategy: Strategy,
    query: str,
    mode: SearchMode,
    k: int,
) -> list[RetrievedChunk]:
    if mode is SearchMode.VECTOR:
        return await vector_search(
            session_factory, embedder, repo_id=repo_id, strategy=strategy, query=query, k=k
        )
    semantic = await vector_search(
        session_factory,
        embedder,
        repo_id=repo_id,
        strategy=strategy,
        query=query,
        k=HYBRID_CANDIDATES,
    )
    lexical = await fulltext_search(
        session_factory, repo_id=repo_id, strategy=strategy, query=query, k=HYBRID_CANDIDATES
    )
    return reciprocal_rank_fusion([semantic, lexical], top=min(k, HYBRID_TOP))
