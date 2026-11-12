"""Embedding providers. This module is the only code that calls an embedding API."""

import asyncio
import hashlib
import math
from collections.abc import Sequence
from typing import Literal, Protocol

import httpx2
from pydantic import BaseModel, SecretStr, ValidationError

from repolens.db.models import EMBEDDING_DIMENSION
from repolens.ingest.tokens import count_tokens

VOYAGE_EMBEDDINGS_URL = "https://api.voyageai.com/v1/embeddings"
VOYAGE_MAX_BATCH_TEXTS = 1000
# Voyage documents a 120K-token request limit for voyage-code-3 and none for voyage-code-4. Our
# token count only approximates Voyage's tokenizer, so batches stay well below that.
VOYAGE_MAX_BATCH_TOKENS = 60_000
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

type InputType = Literal["document", "query"]


class EmbeddingError(Exception):
    """An embedding request failed or returned something unusable."""


class EmbeddingProviderError(EmbeddingError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Embedder(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts for storage, in input order."""
        ...

    async def embed_query(self, text: str) -> list[float]: ...


def batch_texts(texts: Sequence[str], *, max_texts: int, max_tokens: int) -> list[list[str]]:
    """Group texts in order so each batch stays within both limits.

    A text larger than ``max_tokens`` on its own forms a batch by itself.
    """
    batches: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0
    for text in texts:
        tokens = count_tokens(text)
        if current and (len(current) == max_texts or current_tokens + tokens > max_tokens):
            batches.append(current)
            current, current_tokens = [], 0
        current.append(text)
        current_tokens += tokens
    if current:
        batches.append(current)
    return batches


class _VoyageEmbedding(BaseModel):
    index: int
    embedding: list[float]


class _VoyageResponse(BaseModel):
    data: list[_VoyageEmbedding]


class VoyageEmbedder:
    """Voyage AI embeddings over its REST API.

    Inputs longer than the model's context are truncated by Voyage rather than rejected, so one
    oversized chunk cannot fail a whole ingestion. 429 and 5xx responses and transport errors are
    retried with exponential backoff.
    """

    def __init__(
        self,
        *,
        http: httpx2.AsyncClient,
        api_key: SecretStr,
        model: str,
        dimension: int = EMBEDDING_DIMENSION,
        max_batch_texts: int = VOYAGE_MAX_BATCH_TEXTS,
        max_batch_tokens: int = VOYAGE_MAX_BATCH_TOKENS,
        max_attempts: int = 3,
        retry_delay: float = 1.0,
        timeout: float = 60.0,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._model = model
        self._dimension = dimension
        self._max_batch_texts = max_batch_texts
        self._max_batch_tokens = max_batch_tokens
        self._max_attempts = max_attempts
        self._retry_delay = retry_delay
        self._timeout = timeout

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        batches = batch_texts(
            texts, max_texts=self._max_batch_texts, max_tokens=self._max_batch_tokens
        )
        for batch in batches:
            vectors.extend(await self._embed(batch, "document"))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        [vector] = await self._embed([text], "query")
        return vector

    async def _embed(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        response = await self._post(
            {
                "input": texts,
                "model": self._model,
                "input_type": input_type,
                "output_dimension": self._dimension,
                "output_dtype": "float",
                "truncation": True,
            }
        )
        try:
            data = _VoyageResponse.model_validate_json(response.content).data
        except ValidationError as exc:
            raise EmbeddingError("Voyage returned a malformed embeddings response") from exc

        if sorted(item.index for item in data) != list(range(len(texts))):
            raise EmbeddingError(f"Voyage returned {len(data)} embeddings for {len(texts)} inputs")
        vectors = [item.embedding for item in sorted(data, key=lambda item: item.index)]
        for vector in vectors:
            if len(vector) != self._dimension:
                raise EmbeddingError(
                    f"{self._model} returned dimension {len(vector)}, expected {self._dimension}"
                )
        return vectors

    async def _post(self, payload: dict[str, object]) -> httpx2.Response:
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        error = EmbeddingProviderError("Voyage request was not attempted")
        for attempt in range(self._max_attempts):
            if attempt:
                await asyncio.sleep(self._retry_delay * 2 ** (attempt - 1))
            try:
                response = await self._http.post(
                    VOYAGE_EMBEDDINGS_URL, json=payload, headers=headers, timeout=self._timeout
                )
            except httpx2.TransportError as exc:
                error = EmbeddingProviderError(f"Voyage request failed: {type(exc).__name__}")
                continue
            if response.status_code == 200:
                return response
            error = EmbeddingProviderError(
                f"Voyage returned HTTP {response.status_code}: {_error_detail(response)}",
                status=response.status_code,
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


class FakeEmbedder:
    """Deterministic, offline embedder for tests: equal texts get equal unit vectors."""

    def __init__(self, *, dimension: int = EMBEDDING_DIMENSION, model: str = "fake") -> None:
        self._dimension = dimension
        self._model = model
        self.calls: list[list[str]] = []

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.calls.append([text])
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.shake_256(text.encode("utf-8")).digest(self._dimension)
        values = [byte - 127.5 for byte in digest]
        norm = math.sqrt(sum(value * value for value in values))
        return [value / norm for value in values]
