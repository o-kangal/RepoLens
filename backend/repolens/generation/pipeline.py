"""Answer a question about a repository: retrieve, build the context, generate, cite.

The pipeline yields typed events, so the API can stream them and tests can read them directly.
"""

import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session, sessionmaker

from repolens.generation.citations import Citation, parse_citations
from repolens.generation.context import Source, build_context
from repolens.generation.prompt import NOT_FOUND_ANSWER, SYSTEM_PROMPT, build_messages
from repolens.ingest.chunk import Strategy
from repolens.providers.embeddings import Embedder, EmbeddingError
from repolens.providers.llm import LLM, Completion, LLMError, StopReason, TextDelta, TokenUsage
from repolens.retrieval.types import (
    EmbeddingModelMismatchError,
    RepositoryNotFoundError,
    RepositoryNotReadyError,
)
from repolens.retrieval.vector import vector_search

RETRIEVE_K = 20
ANSWER_MAX_TOKENS = 16_000
"""Generous, because thinking tokens count against it on models that always think."""

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    REPOSITORY_NOT_FOUND = "repository_not_found"
    REPOSITORY_NOT_READY = "repository_not_ready"
    EMBEDDING_MODEL_MISMATCH = "embedding_model_mismatch"
    EMBEDDING_FAILED = "embedding_failed"
    LLM_FAILED = "llm_failed"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class SourcesEvent:
    sources: list[Source]


@dataclass(frozen=True, slots=True)
class TokenEvent:
    text: str


@dataclass(frozen=True, slots=True)
class DoneEvent:
    answer: str
    """The full answer with invalid citation labels removed."""
    citations: list[Citation]
    invalid_labels: list[str]
    stop_reason: StopReason
    model: str
    usage: tuple[TokenUsage, ...]


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    code: ErrorCode
    message: str


type QueryEvent = SourcesEvent | TokenEvent | DoneEvent | ErrorEvent

# Errors whose own message is safe to show; anything else is reported generically and logged.
_KNOWN_ERRORS: tuple[tuple[type[Exception], ErrorCode], ...] = (
    (RepositoryNotFoundError, ErrorCode.REPOSITORY_NOT_FOUND),
    (RepositoryNotReadyError, ErrorCode.REPOSITORY_NOT_READY),
    (EmbeddingModelMismatchError, ErrorCode.EMBEDDING_MODEL_MISMATCH),
    (EmbeddingError, ErrorCode.EMBEDDING_FAILED),
    (LLMError, ErrorCode.LLM_FAILED),
)


async def answer_question(
    question: str,
    *,
    repo_id: uuid.UUID,
    strategy: Strategy,
    session_factory: sessionmaker[Session],
    embedder: Embedder,
    llm: LLM,
) -> AsyncIterator[QueryEvent]:
    """Yield the sources, the answer's text deltas, then one ``DoneEvent``, or an ``ErrorEvent``.

    With no sources retrieved, the answer is the not-found reply and the LLM is not called.
    """
    try:
        chunks = await vector_search(
            session_factory,
            embedder,
            repo_id=repo_id,
            strategy=strategy,
            query=question,
            k=RETRIEVE_K,
        )
        sources = build_context(chunks)
        yield SourcesEvent(sources)
        if not sources:
            yield DoneEvent(NOT_FOUND_ANSWER, [], [], StopReason.END, llm.model, ())
            return

        parts: list[str] = []
        completion: Completion | None = None
        async for event in llm.stream(
            system=SYSTEM_PROMPT,
            messages=build_messages(question, sources),
            max_tokens=ANSWER_MAX_TOKENS,
        ):
            if isinstance(event, TextDelta):
                parts.append(event.text)
                yield TokenEvent(event.text)
            else:
                completion = event
        if completion is None:
            raise LLMError("the LLM stream ended without a completion")

        parsed = parse_citations("".join(parts), sources)
        yield DoneEvent(
            answer=parsed.answer,
            citations=parsed.citations,
            invalid_labels=parsed.invalid_labels,
            stop_reason=completion.stop_reason,
            model=llm.model,
            usage=completion.usage,
        )
    except Exception as exc:
        yield _error_event(exc)


def _error_event(exc: Exception) -> ErrorEvent:
    for error_type, code in _KNOWN_ERRORS:
        if isinstance(exc, error_type):
            return ErrorEvent(code, str(exc))
    logger.exception("answering a question failed")
    return ErrorEvent(ErrorCode.INTERNAL, "answering failed because of an internal error")
