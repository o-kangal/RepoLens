import json
import uuid
from collections.abc import Sequence

import httpx2
import pytest
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from repolens.ingest.chunk import Strategy
from repolens.retrieval.rerank import (
    RERANK_TOP,
    VOYAGE_RERANK_URL,
    FakeReranker,
    RerankError,
    RerankResult,
    VoyageReranker,
    rerank_chunks,
)
from repolens.retrieval.search import RERANK_CANDIDATES, SearchMode, search
from repolens.retrieval.types import RetrievedChunk
from tests.retrieval.seed import QueryEmbedder, add_chunk, add_repo, vector

pytestmark = pytest.mark.anyio

QUERY = "where is the user loaded?"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def chunk(path: str, score: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        strategy=Strategy.NAIVE,
        path=path,
        start_line=1,
        end_line=3,
        language="python",
        symbol=None,
        kind="window",
        content=f"content of {path}",
        score=score,
    )


class ScriptedReranker(FakeReranker):
    """Returns fixed results, or raises, whatever the documents."""

    def __init__(self, results: list[RerankResult] | RerankError) -> None:
        super().__init__()
        self._results = results

    async def rerank(self, query: str, documents: Sequence[str], *, top: int) -> list[RerankResult]:
        self.calls.append((query, list(documents), top))
        if isinstance(self._results, RerankError):
            raise self._results
        return self._results


# Reordering


async def test_reranking_reorders_keeps_the_top_and_takes_the_rerank_score() -> None:
    candidates = [chunk("a.py", 0.9), chunk("b.py", 0.8), chunk("c.py", 0.7)]
    reranker = FakeReranker({"c.py": 0.95, "a.py": 0.4, "b.py": 0.1})

    ranked = await rerank_chunks(reranker, QUERY, candidates, top=2)

    assert [(hit.path, hit.score) for hit in ranked] == [("c.py", 0.95), ("a.py", 0.4)]
    assert ranked[0].chunk_id == candidates[2].chunk_id


async def test_the_reranker_sees_each_location_and_content_once_in_candidate_order() -> None:
    reranker = FakeReranker()

    await rerank_chunks(reranker, QUERY, [chunk("a.py"), chunk("b.py")])

    assert reranker.calls == [
        (QUERY, ["a.py:1-3\ncontent of a.py", "b.py:1-3\ncontent of b.py"], 2)
    ]


async def test_the_default_keeps_the_top_eight() -> None:
    candidates = [chunk(f"f{i}.py") for i in range(RERANK_CANDIDATES)]

    ranked = await rerank_chunks(FakeReranker(), QUERY, candidates)

    assert RERANK_TOP == 8
    assert len(ranked) == RERANK_TOP


async def test_no_candidates_means_no_rerank_call() -> None:
    reranker = FakeReranker()

    assert await rerank_chunks(reranker, QUERY, []) == []
    assert reranker.calls == []


async def test_results_are_ordered_by_score_whatever_order_they_arrive_in() -> None:
    reranker = ScriptedReranker([RerankResult(0, 0.1), RerankResult(1, 0.9)])

    ranked = await rerank_chunks(reranker, QUERY, [chunk("a.py"), chunk("b.py")])

    assert [hit.path for hit in ranked] == ["b.py", "a.py"]


@pytest.mark.parametrize(
    "results",
    [
        [RerankResult(0, 0.9)],
        [RerankResult(0, 0.9), RerankResult(0, 0.8)],
        [RerankResult(0, 0.9), RerankResult(2, 0.8)],
        [RerankResult(0, 0.9), RerankResult(-1, 0.8)],
    ],
    ids=["too-few", "duplicate", "past-the-end", "negative"],
)
async def test_results_that_do_not_name_distinct_candidates_are_errors(
    results: list[RerankResult],
) -> None:
    with pytest.raises(RerankError):
        await rerank_chunks(ScriptedReranker(results), QUERY, [chunk("a.py"), chunk("b.py")])


# Search with the rerank flag


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine)


@pytest.fixture
def repo(sessions: sessionmaker[Session]) -> uuid.UUID:
    repo_id = add_repo(sessions)
    # f0.py is nearest to the query, f24.py the farthest; 25 chunks, more than are reranked.
    for i in range(25):
        add_chunk(sessions, repo_id, f"f{i}.py", vector((0, 1.0), (1, i / 10)))
    return repo_id


async def run(
    sessions: sessionmaker[Session],
    repo_id: uuid.UUID,
    *,
    mode: SearchMode = SearchMode.VECTOR,
    k: int = 20,
    reranker: FakeReranker | None = None,
) -> list[str]:
    chunks = await search(
        sessions,
        QueryEmbedder({QUERY: vector((0, 1.0))}),
        repo_id=repo_id,
        strategy=Strategy.NAIVE,
        query=QUERY,
        mode=mode,
        k=k,
        reranker=reranker,
    )
    return [hit.path for hit in chunks]


async def test_without_the_flag_search_does_not_rerank(
    sessions: sessionmaker[Session], repo: uuid.UUID
) -> None:
    assert await run(sessions, repo, k=3) == ["f0.py", "f1.py", "f2.py"]


@pytest.mark.parametrize("mode", list(SearchMode))
async def test_the_flag_reranks_the_top_twenty_into_the_top_eight(
    sessions: sessionmaker[Session], repo: uuid.UUID, mode: SearchMode
) -> None:
    # f19.py is the last candidate and f20.py is not one, so it cannot be promoted.
    reranker = FakeReranker({"f19.py": 0.9, "f20.py": 1.0, "f3.py": 0.5})

    paths = await run(sessions, repo, mode=mode, reranker=reranker)

    [(_, documents, top)] = reranker.calls
    assert len(documents) == RERANK_CANDIDATES
    assert top == RERANK_TOP
    assert len(paths) == RERANK_TOP
    assert paths[:2] == ["f19.py", "f3.py"]
    assert "f20.py" not in paths


async def test_a_smaller_k_keeps_fewer_reranked_chunks(
    sessions: sessionmaker[Session], repo: uuid.UUID
) -> None:
    reranker = FakeReranker({"f7.py": 1.0})

    assert await run(sessions, repo, k=1, reranker=reranker) == ["f7.py"]


async def test_a_failed_rerank_fails_the_search(
    sessions: sessionmaker[Session], repo: uuid.UUID
) -> None:
    with pytest.raises(RerankError, match="busy"):
        await run(sessions, repo, reranker=ScriptedReranker(RerankError("busy")))


# Voyage adapter


class Recorder:
    def __init__(self, *responses: httpx2.Response | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def ok(*items: tuple[int, float]) -> httpx2.Response:
    data = [{"index": index, "relevance_score": score} for index, score in items]
    return httpx2.Response(
        200, json={"object": "list", "data": data, "model": "rerank-3", "usage": {}}
    )


def voyage(recorder: Recorder) -> VoyageReranker:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(recorder))
    return VoyageReranker(http=http, api_key=SecretStr("test-key"), model="rerank-3", retry_delay=0)


async def test_voyage_request_and_response_shape() -> None:
    recorder = Recorder(ok((1, 0.9), (0, 0.2)))

    results = await voyage(recorder).rerank(QUERY, ["first", "second"], top=2)

    assert results == [RerankResult(1, 0.9), RerankResult(0, 0.2)]
    [request] = recorder.requests
    assert str(request.url) == VOYAGE_RERANK_URL
    assert request.headers["Authorization"] == "Bearer test-key"
    assert json.loads(request.content) == {
        "query": QUERY,
        "documents": ["first", "second"],
        "model": "rerank-3",
        "top_k": 2,
        "truncation": True,
    }


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_rate_limits_and_server_errors_are_retried(status: int) -> None:
    recorder = Recorder(httpx2.Response(status), ok((0, 0.5)))

    assert await voyage(recorder).rerank(QUERY, ["only"], top=1) == [RerankResult(0, 0.5)]
    assert len(recorder.requests) == 2


async def test_client_errors_are_not_retried() -> None:
    recorder = Recorder(httpx2.Response(400, json={"detail": "query too long"}))

    with pytest.raises(RerankError, match="HTTP 400: query too long"):
        await voyage(recorder).rerank(QUERY, ["only"], top=1)
    assert len(recorder.requests) == 1


async def test_persistent_failure_raises_after_the_last_attempt() -> None:
    recorder = Recorder(*[httpx2.ConnectError("refused")] * 3)

    with pytest.raises(RerankError, match="ConnectError"):
        await voyage(recorder).rerank(QUERY, ["only"], top=1)
    assert len(recorder.requests) == 3


async def test_a_malformed_response_is_an_error() -> None:
    recorder = Recorder(httpx2.Response(200, json={"data": [{"index": 0}]}))

    with pytest.raises(RerankError, match="malformed"):
        await voyage(recorder).rerank(QUERY, ["only"], top=1)
