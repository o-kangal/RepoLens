"""Second-stage reranking: a cross-encoder scores each candidate against the query.

A failed rerank raises ``RerankError`` instead of falling back to the first-stage order, so an
experiment that asks for reranking never measures unreranked results by accident.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol

import httpx2
from pydantic import BaseModel, SecretStr, ValidationError

from repolens.retrieval.types import RetrievedChunk

VOYAGE_RERANK_URL = "https://api.voyageai.com/v1/rerank"
RERANK_TOP = 8
"""Chunks kept after reranking."""
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class RerankError(Exception):
    """A rerank request failed or returned something unusable."""


@dataclass(frozen=True, slots=True)
class RerankResult:
    index: int
    """Position of the document in the request."""
    score: float
    """Higher is more relevant."""


class Reranker(Protocol):
    @property
    def model(self) -> str: ...

    async def rerank(self, query: str, documents: Sequence[str], *, top: int) -> list[RerankResult]:
        """The ``top`` most relevant documents, most relevant first."""
        ...


def rerank_document(chunk: RetrievedChunk) -> str:
    """The text a reranker scores: the chunk's location, then its content.

    The path often names what the code does, so the reranker sees it as well.
    """
    return f"{chunk.path}:{chunk.start_line}-{chunk.end_line}\n{chunk.content}"


async def rerank_chunks(
    reranker: Reranker, query: str, chunks: Sequence[RetrievedChunk], *, top: int = RERANK_TOP
) -> list[RetrievedChunk]:
    """Reorder ``chunks`` by the reranker's scores and keep the ``top`` ones.

    Each kept chunk's score becomes its rerank score. Raises ``RerankError`` when the reranker
    fails or returns indices that do not name distinct candidates.
    """
    if not chunks:
        return []
    keep = min(top, len(chunks))
    results = await reranker.rerank(query, [rerank_document(chunk) for chunk in chunks], top=keep)
    indices = [result.index for result in results]
    if len(indices) != keep or len(set(indices)) != keep:
        raise RerankError(f"reranker returned {len(indices)} results for {keep} requested")
    if not all(0 <= index < len(chunks) for index in indices):
        raise RerankError("reranker returned an index outside the candidates")
    ordered = sorted(results, key=lambda result: result.score, reverse=True)
    return [replace(chunks[result.index], score=result.score) for result in ordered]


class _VoyageRerankItem(BaseModel):
    index: int
    relevance_score: float


class _VoyageRerankResponse(BaseModel):
    data: list[_VoyageRerankItem]


class VoyageReranker:
    """Voyage AI rerankers over its REST API.

    Inputs longer than the model's context are truncated by Voyage rather than rejected. 429 and
    5xx responses and transport errors are retried with exponential backoff.
    """

    def __init__(
        self,
        *,
        http: httpx2.AsyncClient,
        api_key: SecretStr,
        model: str,
        max_attempts: int = 3,
        retry_delay: float = 1.0,
        timeout: float = 30.0,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._model = model
        self._max_attempts = max_attempts
        self._retry_delay = retry_delay
        self._timeout = timeout

    @property
    def model(self) -> str:
        return self._model

    async def rerank(self, query: str, documents: Sequence[str], *, top: int) -> list[RerankResult]:
        response = await self._post(
            {
                "query": query,
                "documents": list(documents),
                "model": self._model,
                "top_k": top,
                "truncation": True,
            }
        )
        try:
            data = _VoyageRerankResponse.model_validate_json(response.content).data
        except ValidationError as exc:
            raise RerankError("Voyage returned a malformed rerank response") from exc
        return [RerankResult(index=item.index, score=item.relevance_score) for item in data]

    async def _post(self, payload: dict[str, object]) -> httpx2.Response:
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        error = RerankError("Voyage rerank request was not attempted")
        for attempt in range(self._max_attempts):
            if attempt:
                await asyncio.sleep(self._retry_delay * 2 ** (attempt - 1))
            try:
                response = await self._http.post(
                    VOYAGE_RERANK_URL, json=payload, headers=headers, timeout=self._timeout
                )
            except httpx2.TransportError as exc:
                error = RerankError(f"Voyage rerank request failed: {type(exc).__name__}")
                continue
            if response.status_code == 200:
                return response
            error = RerankError(
                f"Voyage rerank returned HTTP {response.status_code}: {_error_detail(response)}"
            )
            if response.status_code not in _RETRYABLE_STATUSES:
                break
        raise error


def _error_detail(response: httpx2.Response) -> str:
    try:
        body: object = response.json()
    except ValueError:
        return "no error detail"
    if isinstance(body, dict) and isinstance(detail := body.get("detail"), str):
        return detail
    return "no error detail"


class FakeReranker:
    """Offline reranker for tests: scores each document by its chunk's path, unknown paths 0."""

    def __init__(self, scores: dict[str, float] | None = None, *, model: str = "fake") -> None:
        self._scores = scores or {}
        self._model = model
        self.calls: list[tuple[str, list[str], int]] = []

    @property
    def model(self) -> str:
        return self._model

    async def rerank(self, query: str, documents: Sequence[str], *, top: int) -> list[RerankResult]:
        self.calls.append((query, list(documents), top))
        results = [
            RerankResult(index=index, score=self._score(document))
            for index, document in enumerate(documents)
        ]
        return sorted(results, key=lambda result: result.score, reverse=True)[:top]

    def _score(self, document: str) -> float:
        return self._scores.get(document.split(":", 1)[0], 0.0)
