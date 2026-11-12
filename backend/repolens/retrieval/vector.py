"""Vector search: the top k chunks of one repository and strategy by cosine distance."""

import asyncio
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from repolens.db.models import Chunk, Repo, RepoStatus
from repolens.ingest.chunk import Strategy
from repolens.providers.embeddings import Embedder
from repolens.retrieval.types import (
    EmbeddingModelMismatchError,
    RepositoryNotFoundError,
    RepositoryNotReadyError,
    RetrievedChunk,
)

# pgvector's default number of HNSW candidates per scan.
_MIN_EF_SEARCH = 40


async def vector_search(
    session_factory: sessionmaker[Session],
    embedder: Embedder,
    *,
    repo_id: uuid.UUID,
    strategy: Strategy,
    query: str,
    k: int,
) -> list[RetrievedChunk]:
    """Embed ``query`` with the model that built the index, then rank that index's chunks."""
    await asyncio.to_thread(_check_index, session_factory, repo_id, strategy, embedder.model)
    vector = await embedder.embed_query(query)
    return await asyncio.to_thread(_nearest, session_factory, repo_id, strategy, vector, k)


def _check_index(
    session_factory: sessionmaker[Session], repo_id: uuid.UUID, strategy: Strategy, model: str
) -> None:
    with session_factory() as session:
        status = session.scalar(select(Repo.status).where(Repo.id == repo_id))
        if status is None:
            raise RepositoryNotFoundError(f"repository {repo_id} not found")
        if status != RepoStatus.DONE:
            raise RepositoryNotReadyError(f"repository {repo_id} is {status}, not indexed")
        models = set(
            session.scalars(
                select(Chunk.embedding_model)
                .where(Chunk.repo_id == repo_id, Chunk.strategy == strategy)
                .distinct()
            )
        )
    other_models = models - {model}
    if other_models:
        raise EmbeddingModelMismatchError(
            f"the index was built with {', '.join(sorted(filter(None, other_models)))}, "
            f"but the configured embedding model is {model}"
        )


def _nearest(
    session_factory: sessionmaker[Session],
    repo_id: uuid.UUID,
    strategy: Strategy,
    vector: list[float],
    k: int,
) -> list[RetrievedChunk]:
    distance = Chunk.embedding.cosine_distance(vector)
    with session_factory.begin() as session:
        # A filtered HNSW scan otherwise stops after ef_search candidates and can return fewer
        # than k rows; iterative scanning keeps going until k rows match, in exact order.
        session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
        session.execute(text(f"SET LOCAL hnsw.ef_search = {max(k, _MIN_EF_SEARCH):d}"))
        rows = session.execute(
            select(Chunk, distance)
            .where(Chunk.repo_id == repo_id, Chunk.strategy == strategy)
            .order_by(distance, Chunk.id)
            .limit(k)
        )
        return [
            RetrievedChunk(
                chunk_id=chunk.id,
                strategy=Strategy(chunk.strategy),
                path=chunk.path,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                language=chunk.language,
                symbol=chunk.symbol,
                kind=chunk.kind,
                content=chunk.content,
                score=1.0 - float(chunk_distance),
            )
            for chunk, chunk_distance in rows
        ]
