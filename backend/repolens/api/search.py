"""Retrieval-only search, used by the evals and the MCP server."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from repolens.api.deps import get_embedder, get_session_factory
from repolens.ingest.chunk import Strategy
from repolens.providers.embeddings import Embedder
from repolens.retrieval.types import (
    EmbeddingModelMismatchError,
    RepositoryNotFoundError,
    RepositoryNotReadyError,
)
from repolens.retrieval.vector import vector_search

router = APIRouter(prefix="/api", tags=["search"])


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_id: uuid.UUID
    query: str = Field(min_length=1, max_length=2000)
    strategy: Strategy = Strategy.NAIVE
    k: int = Field(default=8, ge=1, le=50)


class SearchHit(BaseModel):
    path: str
    start_line: int
    end_line: int
    strategy: Strategy
    language: str | None
    symbol: str | None
    kind: str | None
    content: str
    score: float


class SearchResponse(BaseModel):
    results: list[SearchHit]


@router.post("/search")
async def search(
    body: SearchRequest,
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
) -> SearchResponse:
    try:
        chunks = await vector_search(
            session_factory,
            embedder,
            repo_id=body.repo_id,
            strategy=body.strategy,
            query=body.query,
            k=body.k,
        )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repository not found") from exc
    except (RepositoryNotReadyError, EmbeddingModelMismatchError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return SearchResponse(
        results=[
            SearchHit(
                path=chunk.path,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                strategy=chunk.strategy,
                language=chunk.language,
                symbol=chunk.symbol,
                kind=chunk.kind,
                content=chunk.content,
                score=chunk.score,
            )
            for chunk in chunks
        ]
    )
