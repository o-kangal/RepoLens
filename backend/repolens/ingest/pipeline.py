"""Ingestion: clone, walk, chunk with each strategy, embed, store under ``(url, commit_sha)``."""

import asyncio
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from repolens.db.models import EMBEDDING_DIMENSION, Repo
from repolens.db.models import Chunk as ChunkRow
from repolens.ingest.chunk import Chunk, Strategy
from repolens.ingest.clone import Checkout, GitHubRepository, clone_github, parse_github_url
from repolens.ingest.markdown import chunk_markdown
from repolens.ingest.naive import chunk_naive
from repolens.ingest.walker import MAX_FILES, Language, SourceFile, walk_repository
from repolens.providers.embeddings import Embedder

type Clone = Callable[[GitHubRepository, str | None], AbstractAsyncContextManager[Checkout]]


@dataclass(frozen=True, slots=True)
class IngestResult:
    repo_id: uuid.UUID
    url: str
    commit_sha: str
    chunk_counts: dict[Strategy, int]
    embedded: int
    """Distinct chunk contents sent to the embedder."""
    reused: int
    """Distinct chunk contents whose stored embedding was reused."""


async def ingest_repository(
    url: str,
    *,
    ref: str | None = None,
    embedder: Embedder,
    session_factory: sessionmaker[Session],
    clone: Clone = clone_github,
    max_files: int = MAX_FILES,
) -> IngestResult:
    """Index a GitHub repository. On any failure nothing is stored and the clone is removed."""
    if embedder.dimension != EMBEDDING_DIMENSION:
        raise ValueError(
            f"embedder dimension {embedder.dimension} does not match the "
            f"{EMBEDDING_DIMENSION}-dimension embedding column"
        )
    repository = parse_github_url(url)
    async with clone(repository, ref) as checkout:
        files = await asyncio.to_thread(walk_repository, checkout.root, max_files=max_files)
        commit_sha = checkout.commit_sha

    chunks = chunk_files(files)
    stored = await asyncio.to_thread(
        _stored_embeddings,
        session_factory,
        embedder.model,
        {chunk.content_hash for chunk in chunks},
    )
    missing: dict[str, str] = {}
    for chunk in chunks:
        if chunk.content_hash not in stored:
            missing.setdefault(chunk.content_hash, chunk.content)
    vectors = await embedder.embed_documents(list(missing.values()))
    embeddings = stored | dict(zip(missing, vectors, strict=True))

    repo_id = await asyncio.to_thread(
        _store, session_factory, repository.url, commit_sha, chunks, embeddings, embedder.model
    )
    return IngestResult(
        repo_id=repo_id,
        url=repository.url,
        commit_sha=commit_sha,
        chunk_counts=dict(Counter(chunk.strategy for chunk in chunks)),
        embedded=len(missing),
        reused=len(stored),
    )


def chunk_files(files: Iterable[SourceFile]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for source in files:
        chunks.extend(chunk_naive(source))
        if source.language is Language.MARKDOWN:
            chunks.extend(chunk_markdown(source))
    return chunks


def _stored_embeddings(
    session_factory: sessionmaker[Session], model: str, hashes: set[str]
) -> dict[str, list[float]]:
    """Embeddings already stored for these hashes, from the same model only."""
    if not hashes:
        return {}
    with session_factory() as session:
        rows = session.execute(
            select(ChunkRow.content_hash, ChunkRow.embedding)
            .where(ChunkRow.content_hash.in_(hashes), ChunkRow.embedding_model == model)
            .ext(distinct_on(ChunkRow.content_hash))
        )
        return {content_hash: list(vector) for content_hash, vector in rows if vector is not None}


def _store(
    session_factory: sessionmaker[Session],
    url: str,
    commit_sha: str,
    chunks: list[Chunk],
    embeddings: dict[str, list[float]],
    model: str,
) -> uuid.UUID:
    """Replace the repository's chunks at this commit in one transaction."""
    with session_factory.begin() as session:
        repo_id = session.scalars(
            pg_insert(Repo)
            .values(id=uuid.uuid4(), url=url, commit_sha=commit_sha)
            .on_conflict_do_update(
                constraint="uq_repos_url_commit_sha", set_={"indexed_at": func.now()}
            )
            .returning(Repo.id)
        ).one()
        session.execute(delete(ChunkRow).where(ChunkRow.repo_id == repo_id))
        if chunks:
            session.execute(
                insert(ChunkRow),
                [
                    {
                        "id": uuid.uuid4(),
                        "repo_id": repo_id,
                        "strategy": chunk.strategy.value,
                        "path": chunk.path,
                        "language": chunk.language.value,
                        "symbol": chunk.symbol,
                        "kind": chunk.kind.value,
                        "start_line": chunk.start_line,
                        "end_line": chunk.end_line,
                        "content": chunk.content,
                        "token_count": chunk.token_count,
                        "content_hash": chunk.content_hash,
                        "embedding": embeddings[chunk.content_hash],
                        "embedding_model": model,
                    }
                    for chunk in chunks
                ],
            )
    return repo_id
