"""Repository endpoints: request indexing and follow its status."""

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from repolens.api.deps import get_clone, get_embedder, get_session_factory
from repolens.db.models import RepoStatus
from repolens.ingest.chunk import Strategy
from repolens.ingest.clone import InvalidRepositoryError
from repolens.ingest.pipeline import (
    Clone,
    register_repository,
    repository_status,
    run_index_job,
)
from repolens.providers.embeddings import Embedder

router = APIRouter(prefix="/api/repos", tags=["repos"])

SessionFactory = Annotated[sessionmaker[Session], Depends(get_session_factory)]


class IndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(max_length=300, examples=["https://github.com/fastapi/fastapi"])
    ref: str | None = Field(default=None, max_length=255, description="Branch or tag")


class IndexAccepted(BaseModel):
    repo_id: uuid.UUID
    status: RepoStatus


class RepositoryResponse(BaseModel):
    id: uuid.UUID
    url: str
    ref: str | None
    commit_sha: str | None
    status: RepoStatus
    error: str | None
    chunk_counts: dict[Strategy, int]


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def request_indexing(
    body: IndexRequest,
    background_tasks: BackgroundTasks,
    session_factory: SessionFactory,
    embedder: Annotated[Embedder, Depends(get_embedder)],
    clone: Annotated[Clone, Depends(get_clone)],
) -> IndexAccepted:
    try:
        repo_id = register_repository(session_factory, body.url, body.ref)
    except InvalidRepositoryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    background_tasks.add_task(
        run_index_job, repo_id, embedder=embedder, session_factory=session_factory, clone=clone
    )
    return IndexAccepted(repo_id=repo_id, status=RepoStatus.RUNNING)


@router.get("/{repo_id}")
def get_repository(repo_id: uuid.UUID, session_factory: SessionFactory) -> RepositoryResponse:
    found = repository_status(session_factory, repo_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repository not found")
    return RepositoryResponse(
        id=found.id,
        url=found.url,
        ref=found.ref,
        commit_sha=found.commit_sha,
        status=found.status,
        error=found.error,
        chunk_counts=found.chunk_counts,
    )
