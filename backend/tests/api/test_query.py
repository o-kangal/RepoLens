import json
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from repolens.api.deps import get_embedder, get_llms
from repolens.config import Settings
from repolens.db.models import RepoStatus
from repolens.generation.prompt import NOT_FOUND_ANSWER, SYSTEM_PROMPT
from repolens.main import create_app
from repolens.providers.embeddings import EmbeddingProviderError
from repolens.providers.llm import (
    LLM,
    ChatMessage,
    LLMError,
    LLMEvent,
    ScriptedLLM,
    StopReason,
)
from tests.retrieval.seed import QueryEmbedder, add_chunk, add_repo, vector

QUESTION = "where is the user loaded?"
type Event = tuple[str, dict[str, object]]


class FailingEmbedder(QueryEmbedder):
    async def embed_query(self, text: str) -> list[float]:
        raise EmbeddingProviderError("Voyage returned HTTP 503: busy", status=503)


class BrokenLLM(ScriptedLLM):
    async def stream(
        self, *, system: str, messages: Sequence[ChatMessage], max_tokens: int
    ) -> AsyncIterator[LLMEvent]:
        raise RuntimeError("secret internal detail")
        yield  # pragma: no cover - makes this an async generator


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine)


@pytest.fixture
def repo(sessions: sessionmaker[Session]) -> uuid.UUID:
    repo_id = add_repo(sessions)
    add_chunk(sessions, repo_id, "src/users.py", vector((0, 1.0)))
    add_chunk(sessions, repo_id, "src/auth.py", vector((0, 1.0), (1, 1.0)))
    return repo_id


@pytest.fixture
def default_llm() -> ScriptedLLM:
    return ScriptedLLM(chunks=("Users load in users.py [S2]", " and ghosts [S9]."), model="main")


@pytest.fixture
def other_llm() -> ScriptedLLM:
    return ScriptedLLM(chunks=("Other model [S1].",), model="other")


@pytest.fixture
def app(engine: Engine, scratch_url: str) -> FastAPI:
    return create_app(Settings(database_url=SecretStr(scratch_url), voyage_api_key=None))


def configure(
    app: FastAPI, *, embedder: QueryEmbedder | None = None, llms: dict[str, LLM] | None = None
) -> None:
    query_embedder = embedder or QueryEmbedder({QUESTION: vector((0, 1.0))})
    app.dependency_overrides[get_embedder] = lambda: query_embedder
    if llms is not None:
        app.dependency_overrides[get_llms] = lambda: llms


@pytest.fixture
def client(app: FastAPI, default_llm: ScriptedLLM, other_llm: ScriptedLLM) -> Iterator[TestClient]:
    configure(app, llms={"main": default_llm, "other": other_llm})
    with TestClient(app) as test_client:
        yield test_client


def events(client: TestClient, **body: object) -> list[Event]:
    response = client.post("/api/query", json={"question": QUESTION} | body)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    parsed: list[Event] = []
    for block in response.text.split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if not line.startswith(":")
        )
        if "event" in fields:
            parsed.append((fields["event"], json.loads(fields["data"])))
    return parsed


def test_streams_sources_tokens_then_done_with_validated_citations(
    client: TestClient, repo: uuid.UUID, default_llm: ScriptedLLM
) -> None:
    stream = events(client, repo_id=str(repo))

    assert [name for name, _ in stream] == ["sources", "token", "token", "done"]
    assert stream[0][1] == {
        "sources": [
            {"label": "S1", "path": "src/auth.py", "start_line": 1, "end_line": 3},
            {"label": "S2", "path": "src/users.py", "start_line": 1, "end_line": 3},
        ]
    }
    assert [data["text"] for name, data in stream if name == "token"] == [
        "Users load in users.py [S2]",
        " and ghosts [S9].",
    ]
    assert stream[-1][1] == {
        "answer": "Users load in users.py [S2] and ghosts.",
        "citations": [{"label": "S2", "path": "src/users.py", "start_line": 1, "end_line": 3}],
        "invalid_labels": ["S9"],
        "stop_reason": "end",
        "model": "main",
        "usage": [{"model": "main", "input_tokens": 0, "output_tokens": 8}],
    }
    [(system, messages, max_tokens)] = default_llm.calls
    assert system == SYSTEM_PROMPT
    assert "content of src/users.py" in messages[0].content
    assert messages[0].content.endswith(f"Question: {QUESTION}")
    assert max_tokens > 0


def test_a_query_can_choose_an_allowed_model(
    client: TestClient, repo: uuid.UUID, default_llm: ScriptedLLM, other_llm: ScriptedLLM
) -> None:
    stream = events(client, repo_id=str(repo), model="other")

    assert stream[-1][1]["model"] == "other"
    assert len(other_llm.calls) == 1
    assert default_llm.calls == []


def test_a_model_outside_the_allowed_list_is_rejected(
    client: TestClient, repo: uuid.UUID, default_llm: ScriptedLLM
) -> None:
    response = client.post(
        "/api/query", json={"repo_id": str(repo), "question": QUESTION, "model": "gpt-free"}
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "model 'gpt-free' is not allowed; choose one of main, other"
    }
    assert default_llm.calls == []


def test_models_lists_the_allowed_models_and_the_default(client: TestClient) -> None:
    assert client.get("/api/models").json() == {"models": ["main", "other"], "default": "main"}


def test_no_sources_means_not_found_without_calling_the_llm(
    client: TestClient, repo: uuid.UUID, default_llm: ScriptedLLM
) -> None:
    stream = events(client, repo_id=str(repo), strategy="markdown")

    assert stream == [
        ("sources", {"sources": []}),
        (
            "done",
            {
                "answer": NOT_FOUND_ANSWER,
                "citations": [],
                "invalid_labels": [],
                "stop_reason": "end",
                "model": "main",
                "usage": [],
            },
        ),
    ]
    assert default_llm.calls == []


def test_unknown_repository_is_an_error_event(client: TestClient) -> None:
    stream = events(client, repo_id=str(uuid.uuid4()))

    assert [name for name, _ in stream] == ["error"]
    assert stream[0][1]["code"] == "repository_not_found"


def test_unfinished_repository_is_an_error_event(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    stream = events(client, repo_id=str(add_repo(sessions, status=RepoStatus.RUNNING)))

    assert stream[0][1]["code"] == "repository_not_ready"


def test_llm_failure_mid_answer_ends_with_an_error_event(app: FastAPI, repo: uuid.UUID) -> None:
    failing = ScriptedLLM(chunks=("partial",), error=LLMError("Anthropic returned HTTP 529"))
    configure(app, llms={"main": failing})

    with TestClient(app) as test_client:
        stream = events(test_client, repo_id=str(repo))

    assert [name for name, _ in stream] == ["sources", "token", "error"]
    assert stream[-1][1] == {"code": "llm_failed", "message": "Anthropic returned HTTP 529"}


def test_embedding_failure_is_an_error_event(app: FastAPI, repo: uuid.UUID) -> None:
    configure(app, embedder=FailingEmbedder({}), llms={"main": ScriptedLLM()})

    with TestClient(app) as test_client:
        stream = events(test_client, repo_id=str(repo))

    assert stream == [
        ("error", {"code": "embedding_failed", "message": "Voyage returned HTTP 503: busy"})
    ]


def test_unexpected_failure_hides_its_details(app: FastAPI, repo: uuid.UUID) -> None:
    configure(app, llms={"main": BrokenLLM()})

    with TestClient(app) as test_client:
        stream = events(test_client, repo_id=str(repo))

    assert stream[-1] == (
        "error",
        {"code": "internal", "message": "answering failed because of an internal error"},
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"question": ""},
        {"question": "x" * 2001},
        {"strategy": "semantic"},
        {"rerank": True},
        {"repo_id": "not-a-uuid"},
        {"unexpected": 1},
    ],
)
def test_invalid_requests_are_rejected(
    client: TestClient, repo: uuid.UUID, overrides: dict[str, object]
) -> None:
    body: dict[str, object] = {"repo_id": str(repo), "question": QUESTION} | overrides

    assert client.post("/api/query", json=body).status_code == 422


def test_querying_needs_an_llm_provider(app: FastAPI, repo: uuid.UUID) -> None:
    configure(app)

    with TestClient(app) as test_client:
        response = test_client.post("/api/query", json={"repo_id": str(repo), "question": "q"})
        models = test_client.get("/api/models").json()

    assert response.status_code == 503
    assert response.json() == {"detail": "no LLM provider is configured"}
    assert models == {"models": [], "default": None}


def test_stop_reason_is_reported(app: FastAPI, repo: uuid.UUID) -> None:
    configure(app, llms={"main": ScriptedLLM(chunks=("cut",), stop_reason=StopReason.MAX_TOKENS)})

    with TestClient(app) as test_client:
        stream = events(test_client, repo_id=str(repo))

    assert stream[-1][1]["stop_reason"] == "max_tokens"
