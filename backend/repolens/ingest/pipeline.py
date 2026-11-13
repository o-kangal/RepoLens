"""Ingestion: clone, walk, chunk with each strategy, embed, store under ``(url, commit_sha)``.

A repository row exists from the moment indexing is requested, with status ``running``. It ends as
``done`` with its chunks, or as ``failed`` with an error message and no chunks.
"""

import asyncio
import logging
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session, sessionmaker

from repolens.db.models import EMBEDDING_DIMENSION, Repo, RepoStatus
from repolens.db.models import Chunk as ChunkRow
from repolens.ingest.ast_chunker import chunk_ast
from repolens.ingest.chunk import Chunk, Strategy
from repolens.ingest.clone import (
    Checkout,
    CloneError,
    GitHubRepository,
    InvalidRepositoryError,
    clone_github,
    parse_github_url,
    validate_ref,
)
from repolens.ingest.markdown import chunk_markdown
from repolens.ingest.naive import chunk_naive
from repolens.ingest.walker import (
    MAX_FILES,
    FileLimitExceededError,
    Language,
    SourceFile,
    walk_repository,
)
from repolens.providers.embeddings import Embedder, EmbeddingError

type Clone = Callable[[GitHubRepository, str | None], AbstractAsyncContextManager[Checkout]]

INTERRUPTED_MESSAGE = "indexing was interrupted by a restart"
INTERNAL_ERROR_MESSAGE = "indexing failed because of an internal error"
# Failures whose message is safe and useful to show; anything else is reported generically.
_REPORTED_ERRORS = (InvalidRepositoryError, CloneError, FileLimitExceededError, EmbeddingError)

logger = logging.getLogger(__name__)


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


def register_repository(
    session_factory: sessionmaker[Session], url: str, ref: str | None
) -> uuid.UUID:
    """Validate the request and record the repository as running.

    Raises ``InvalidRepositoryError`` for a URL or ref that is not accepted, before anything is
    stored.
    """
    repository = parse_github_url(url)
    if ref is not None:
        validate_ref(ref)
    with session_factory.begin() as session:
        repo = Repo(url=repository.url, ref=ref, status=RepoStatus.RUNNING)
        session.add(repo)
        session.flush()
        return repo.id


async def run_index_job(
    repo_id: uuid.UUID,
    *,
    embedder: Embedder,
    session_factory: sessionmaker[Session],
    clone: Clone = clone_github,
) -> None:
    """Background entry point: index the repository and log, rather than raise, a failure."""
    try:
        await index_repository(
            repo_id, embedder=embedder, session_factory=session_factory, clone=clone
        )
    except Exception:
        logger.exception("indexing repository %s failed", repo_id)


async def index_repository(
    repo_id: uuid.UUID,
    *,
    embedder: Embedder,
    session_factory: sessionmaker[Session],
    clone: Clone = clone_github,
    max_files: int = MAX_FILES,
) -> IngestResult:
    """Index a registered repository and mark it done; on failure mark it failed and re-raise.

    The temporary clone is always removed, and a failure stores no chunks.
    """
    try:
        return await _index(
            repo_id,
            embedder=embedder,
            session_factory=session_factory,
            clone=clone,
            max_files=max_files,
        )
    except Exception as exc:
        message = str(exc) if isinstance(exc, _REPORTED_ERRORS) else INTERNAL_ERROR_MESSAGE
        await asyncio.to_thread(_mark_failed, session_factory, repo_id, message)
        raise


def fail_interrupted_jobs(session_factory: sessionmaker[Session]) -> int:
    """Mark repositories left running by a previous process as failed; return how many."""
    with session_factory.begin() as session:
        failed = session.scalars(
            update(Repo)
            .where(Repo.status == RepoStatus.RUNNING)
            .values(status=RepoStatus.FAILED, error=INTERRUPTED_MESSAGE)
            .returning(Repo.id)
        )
        return len(failed.all())


async def _index(
    repo_id: uuid.UUID,
    *,
    embedder: Embedder,
    session_factory: sessionmaker[Session],
    clone: Clone,
    max_files: int,
) -> IngestResult:
    if embedder.dimension != EMBEDDING_DIMENSION:
        raise ValueError(
            f"embedder dimension {embedder.dimension} does not match the "
            f"{EMBEDDING_DIMENSION}-dimension embedding column"
        )
    url, ref = await asyncio.to_thread(_load_request, session_factory, repo_id)
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

    await asyncio.to_thread(
        _store, session_factory, repo_id, url, commit_sha, chunks, embeddings, embedder.model
    )
    return IngestResult(
        repo_id=repo_id,
        url=url,
        commit_sha=commit_sha,
        chunk_counts=dict(Counter(chunk.strategy for chunk in chunks)),
        embedded=len(missing),
        reused=len(stored),
    )


def chunk_files(files: Iterable[SourceFile]) -> list[Chunk]:
    """Chunk every file with every strategy.

    The AST strategy covers the same files as the naive one, so the two differ only in how they
    chunk: code by syntax tree, documentation by its Markdown heading sections.
    """
    chunks: list[Chunk] = []
    for source in files:
        chunks.extend(chunk_naive(source))
        if source.language is Language.MARKDOWN:
            sections = chunk_markdown(source)
            chunks.extend(sections)
            chunks.extend(replace(section, strategy=Strategy.AST) for section in sections)
        else:
            chunks.extend(chunk_ast(source))
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


def _load_request(
    session_factory: sessionmaker[Session], repo_id: uuid.UUID
) -> tuple[str, str | None]:
    with session_factory() as session:
        row = session.execute(select(Repo.url, Repo.ref).where(Repo.id == repo_id)).one()
        return row.url, row.ref


def _mark_failed(session_factory: sessionmaker[Session], repo_id: uuid.UUID, message: str) -> None:
    with session_factory.begin() as session:
        session.execute(
            update(Repo).where(Repo.id == repo_id).values(status=RepoStatus.FAILED, error=message)
        )


def _store(
    session_factory: sessionmaker[Session],
    repo_id: uuid.UUID,
    url: str,
    commit_sha: str,
    chunks: list[Chunk],
    embeddings: dict[str, list[float]],
    model: str,
) -> None:
    """Mark the repository done with its chunks, in one transaction.

    An earlier index of the same ``(url, commit_sha)`` is replaced: its row and chunks are deleted.
    """
    with session_factory.begin() as session:
        session.execute(
            delete(Repo).where(Repo.url == url, Repo.commit_sha == commit_sha, Repo.id != repo_id)
        )
        session.execute(
            update(Repo)
            .where(Repo.id == repo_id)
            .values(
                commit_sha=commit_sha, status=RepoStatus.DONE, error=None, indexed_at=func.now()
            )
        )
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


@dataclass(frozen=True, slots=True)
class RepositoryStatus:
    id: uuid.UUID
    url: str
    ref: str | None
    commit_sha: str | None
    status: RepoStatus
    error: str | None
    chunk_counts: dict[Strategy, int]
    """Stored chunks per strategy, zero for strategies without chunks."""


def repository_status(
    session_factory: sessionmaker[Session], repo_id: uuid.UUID
) -> RepositoryStatus | None:
    with session_factory() as session:
        repo = session.get(Repo, repo_id)
        if repo is None:
            return None
        counts = session.execute(
            select(ChunkRow.strategy, func.count())
            .where(ChunkRow.repo_id == repo_id)
            .group_by(ChunkRow.strategy)
        )
        stored = {Strategy(strategy): count for strategy, count in counts}
        return RepositoryStatus(
            id=repo.id,
            url=repo.url,
            ref=repo.ref,
            commit_sha=repo.commit_sha,
            status=RepoStatus(repo.status),
            error=repo.error,
            chunk_counts={strategy: stored.get(strategy, 0) for strategy in Strategy},
        )
