import json
from collections.abc import Callable, Iterator

import httpx2
import pytest
from pydantic import SecretStr

from repolens.db.models import EMBEDDING_DIMENSION
from repolens.ingest.tokens import count_tokens
from repolens.providers.embeddings import (
    VOYAGE_EMBEDDINGS_URL,
    Embedder,
    EmbeddingError,
    EmbeddingProviderError,
    FakeEmbedder,
    VoyageEmbedder,
    batch_texts,
)

pytestmark = pytest.mark.anyio

DIMENSION = 4
type Handler = Callable[[httpx2.Request], httpx2.Response]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def vector_for(index: int) -> list[float]:
    return [float(index)] * DIMENSION


def echo(request: httpx2.Request) -> httpx2.Response:
    """Answer like Voyage: one vector per input, listed in reverse to exercise reordering."""
    inputs = json.loads(request.content)["input"]
    data = [
        {"object": "embedding", "index": i, "embedding": vector_for(i)} for i in range(len(inputs))
    ]
    return httpx2.Response(200, json={"object": "list", "data": data[::-1], "usage": {}})


class Recorder:
    def __init__(self, *responses: Handler | httpx2.Response | Exception) -> None:
        self._responses: Iterator[Handler | httpx2.Response | Exception] = iter(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        response = next(self._responses, echo)
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, httpx2.Response) else response(request)

    def payloads(self) -> list[dict[str, object]]:
        return [json.loads(request.content) for request in self.requests]


def voyage(recorder: Recorder, *, max_batch_texts: int = 1000) -> VoyageEmbedder:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(recorder))
    return VoyageEmbedder(
        http=http,
        api_key=SecretStr("test-key"),
        model="voyage-code-4",
        dimension=DIMENSION,
        max_batch_texts=max_batch_texts,
        retry_delay=0,
    )


def test_batches_respect_text_and_token_limits() -> None:
    texts = [f"text number {index}" for index in range(7)]
    tokens = count_tokens(texts[0])

    assert batch_texts(texts, max_texts=3, max_tokens=10_000) == [texts[0:3], texts[3:6], texts[6:]]
    assert batch_texts(texts, max_texts=100, max_tokens=tokens * 2) == [
        texts[0:2],
        texts[2:4],
        texts[4:6],
        texts[6:],
    ]
    assert batch_texts([], max_texts=3, max_tokens=10) == []


def test_text_over_the_token_limit_gets_its_own_batch() -> None:
    big = "word " * 200

    assert batch_texts(["a", big, "b"], max_texts=10, max_tokens=50) == [["a"], [big], ["b"]]


async def test_voyage_request_shape() -> None:
    recorder = Recorder()

    vector = await voyage(recorder).embed_query("where is auth?")

    [request] = recorder.requests
    assert str(request.url) == VOYAGE_EMBEDDINGS_URL
    assert request.method == "POST"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert recorder.payloads() == [
        {
            "input": ["where is auth?"],
            "model": "voyage-code-4",
            "input_type": "query",
            "output_dimension": DIMENSION,
            "output_dtype": "float",
            "truncation": True,
        }
    ]
    assert vector == vector_for(0)


async def test_documents_are_embedded_in_batches_and_keep_input_order() -> None:
    recorder = Recorder()
    texts = [f"chunk {index}" for index in range(5)]

    vectors = await voyage(recorder, max_batch_texts=2).embed_documents(texts)

    assert [payload["input"] for payload in recorder.payloads()] == [
        texts[0:2],
        texts[2:4],
        texts[4:],
    ]
    assert {payload["input_type"] for payload in recorder.payloads()} == {"document"}
    assert vectors == [vector_for(0), vector_for(1), vector_for(0), vector_for(1), vector_for(0)]


async def test_no_texts_means_no_request() -> None:
    recorder = Recorder()

    assert await voyage(recorder).embed_documents([]) == []
    assert recorder.requests == []


async def test_dimension_mismatch_is_an_error() -> None:
    wrong = {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}

    with pytest.raises(EmbeddingError, match="dimension 2, expected 4"):
        await voyage(Recorder(httpx2.Response(200, json=wrong))).embed_query("q")


@pytest.mark.parametrize(
    "body",
    [
        {"data": []},
        {"data": [{"index": 1, "embedding": [0.0] * DIMENSION}]},
        {"unexpected": True},
    ],
)
async def test_missing_or_malformed_embeddings_are_errors(body: dict[str, object]) -> None:
    with pytest.raises(EmbeddingError):
        await voyage(Recorder(httpx2.Response(200, json=body))).embed_query("q")


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_client_errors_are_not_retried(status: int) -> None:
    recorder = Recorder(httpx2.Response(status, json={"detail": "Invalid authentication."}))

    with pytest.raises(
        EmbeddingProviderError, match=f"HTTP {status}: Invalid authentication"
    ) as info:
        await voyage(recorder).embed_query("q")

    assert info.value.status == status
    assert len(recorder.requests) == 1
    assert "test-key" not in str(info.value)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_rate_limits_and_server_errors_are_retried(status: int) -> None:
    recorder = Recorder(httpx2.Response(status), httpx2.Response(status))

    assert await voyage(recorder).embed_query("q") == vector_for(0)
    assert len(recorder.requests) == 3


async def test_persistent_failure_raises_after_the_last_attempt() -> None:
    recorder = Recorder(*(httpx2.Response(503, text="busy") for _ in range(3)))

    with pytest.raises(EmbeddingProviderError, match="HTTP 503: no error detail") as info:
        await voyage(recorder).embed_query("q")

    assert info.value.status == 503
    assert len(recorder.requests) == 3


async def test_transport_errors_are_retried_then_raised() -> None:
    recorder = Recorder(*(httpx2.ConnectError("refused") for _ in range(3)))

    with pytest.raises(EmbeddingProviderError, match="ConnectError") as info:
        await voyage(recorder).embed_query("q")

    assert info.value.status is None
    assert len(recorder.requests) == 3


async def test_fake_is_deterministic_normalised_and_offline() -> None:
    fake: Embedder = FakeEmbedder()

    first, second, again = await fake.embed_documents(["alpha", "beta", "alpha"])

    assert fake.dimension == EMBEDDING_DIMENSION == len(first)
    assert first == again != second
    assert sum(value * value for value in first) == pytest.approx(1.0)
    assert await fake.embed_query("alpha") == first
