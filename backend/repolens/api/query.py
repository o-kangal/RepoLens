"""Question answering over SSE, and the models a question may use."""

import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from repolens.api.deps import get_embedder, get_llms, get_session_factory
from repolens.generation.pipeline import (
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    QueryEvent,
    SourcesEvent,
    TokenEvent,
    answer_question,
)
from repolens.ingest.chunk import Strategy
from repolens.providers.embeddings import Embedder
from repolens.providers.llm import LLM, StopReason

router = APIRouter(prefix="/api", tags=["query"])

LLMs = Annotated[dict[str, LLM], Depends(get_llms)]


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_id: uuid.UUID
    question: str = Field(min_length=1, max_length=2000)
    strategy: Strategy = Strategy.NAIVE
    rerank: Literal[False] = Field(
        default=False, description="Reranking is not available yet; only false is accepted."
    )
    model: str | None = Field(
        default=None, description="One of GET /api/models; default if omitted"
    )


class ModelsResponse(BaseModel):
    models: list[str]
    default: str | None


class SourceData(BaseModel):
    label: str
    path: str
    start_line: int
    end_line: int


class SourcesData(BaseModel):
    sources: list[SourceData]


class TokenData(BaseModel):
    text: str


class UsageData(BaseModel):
    model: str
    input_tokens: int
    output_tokens: int


class DoneData(BaseModel):
    answer: str
    citations: list[SourceData]
    invalid_labels: list[str]
    stop_reason: StopReason
    model: str
    usage: list[UsageData]


class ErrorData(BaseModel):
    code: ErrorCode
    message: str


@router.get("/models")
def list_models(llms: LLMs) -> ModelsResponse:
    models = list(llms)
    return ModelsResponse(models=models, default=models[0] if models else None)


def choose_llm(body: QueryRequest, llms: LLMs) -> LLM:
    """Resolve the requested model before the stream opens, so a bad one is a plain HTTP error."""
    if not llms:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "no LLM provider is configured")
    if body.model is None:
        return next(iter(llms.values()))
    llm = llms.get(body.model)
    if llm is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"model {body.model!r} is not allowed; choose one of {', '.join(llms)}",
        )
    return llm


@router.post("/query", response_class=EventSourceResponse)
async def query(
    body: QueryRequest,
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[LLM, Depends(choose_llm)],
) -> AsyncIterator[ServerSentEvent]:
    """Stream ``sources``, then ``token`` events, then one ``done`` or ``error`` event.

    Request problems are HTTP errors; failures once the stream is open are ``error`` events.
    """
    async for event in answer_question(
        body.question,
        repo_id=body.repo_id,
        strategy=body.strategy,
        session_factory=session_factory,
        embedder=embedder,
        llm=llm,
    ):
        yield _to_sse(event)


def _to_sse(event: QueryEvent) -> ServerSentEvent:
    match event:
        case SourcesEvent(sources):
            data: BaseModel = SourcesData(
                sources=[
                    SourceData(
                        label=source.label,
                        path=source.path,
                        start_line=source.start_line,
                        end_line=source.end_line,
                    )
                    for source in sources
                ]
            )
            return ServerSentEvent(event="sources", data=data)
        case TokenEvent(text):
            return ServerSentEvent(event="token", data=TokenData(text=text))
        case DoneEvent():
            data = DoneData(
                answer=event.answer,
                citations=[
                    SourceData(
                        label=citation.label,
                        path=citation.path,
                        start_line=citation.start_line,
                        end_line=citation.end_line,
                    )
                    for citation in event.citations
                ],
                invalid_labels=event.invalid_labels,
                stop_reason=event.stop_reason,
                model=event.model,
                usage=[
                    UsageData(
                        model=usage.model,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                    )
                    for usage in event.usage
                ],
            )
            return ServerSentEvent(event="done", data=data)
        case ErrorEvent(code, message):
            return ServerSentEvent(event="error", data=ErrorData(code=code, message=message))
