import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from repolens.api.deps import get_embedder
from repolens.config import Settings
from repolens.db.models import RepoStatus
from repolens.main import create_app
from tests.retrieval.seed import QueryEmbedder, add_chunk, add_repo, vector

QUERY = "where is the user loaded?"


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine)


@pytest.fixture
def app(engine: Engine, scratch_url: str) -> FastAPI:
    return create_app(Settings(database_url=SecretStr(scratch_url), voyage_api_key=None))


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    app.dependency_overrides[get_embedder] = lambda: QueryEmbedder({QUERY: vector((0, 1.0))})
    with TestClient(app) as test_client:
        yield test_client


def test_returns_ranked_hits_of_the_requested_strategy(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    repo = add_repo(sessions)
    add_chunk(sessions, repo, "close.py", vector((0, 2.0), (1, 1.0)))
    add_chunk(sessions, repo, "exact.py", vector((0, 1.0)))
    add_chunk(sessions, repo, "README.md", vector((0, 1.0)), strategy="markdown")

    response = client.post("/api/search", json={"repo_id": str(repo), "query": QUERY, "k": 5})

    assert response.status_code == 200
    results = response.json()["results"]
    assert [(hit["path"], hit["strategy"]) for hit in results] == [
        ("exact.py", "naive"),
        ("close.py", "naive"),
    ]
    assert results[0] == {
        "path": "exact.py",
        "start_line": 1,
        "end_line": 3,
        "strategy": "naive",
        "language": "python",
        "symbol": None,
        "kind": "window",
        "content": "content of exact.py",
        "score": pytest.approx(1.0),
    }


def test_strategy_selects_the_index(client: TestClient, sessions: sessionmaker[Session]) -> None:
    repo = add_repo(sessions)
    add_chunk(sessions, repo, "main.py", vector((0, 1.0)))
    add_chunk(sessions, repo, "README.md", vector((0, 1.0)), strategy="markdown")

    response = client.post(
        "/api/search", json={"repo_id": str(repo), "query": QUERY, "strategy": "markdown"}
    )

    assert [hit["path"] for hit in response.json()["results"]] == ["README.md"]


def test_hybrid_mode_adds_full_text_matches(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    repo = add_repo(sessions)
    add_chunk(sessions, repo, "near.py", vector((0, 1.0)))
    add_chunk(sessions, repo, "users.py", vector((1, 1.0)), content="def load_user(): ...\n")
    body = {"repo_id": str(repo), "query": QUERY, "k": 1}

    vector_hits = client.post("/api/search", json=body).json()["results"]
    hybrid_hits = client.post("/api/search", json=body | {"mode": "hybrid"}).json()["results"]

    assert [hit["path"] for hit in vector_hits] == ["near.py"]
    # users.py is second by vector but first by full text ("user"), so RRF puts it first.
    assert [hit["path"] for hit in hybrid_hits] == ["users.py"]
    assert hybrid_hits[0]["score"] == pytest.approx(1 / 62 + 1 / 61)


@pytest.mark.parametrize(
    "overrides",
    [
        {"query": ""},
        {"query": "x" * 2001},
        {"k": 0},
        {"k": 51},
        {"strategy": "semantic"},
        {"mode": "keyword"},
        {"repo_id": "not-a-uuid"},
        {"unexpected": True},
    ],
)
def test_invalid_requests_are_rejected(client: TestClient, overrides: dict[str, object]) -> None:
    body: dict[str, object] = {"repo_id": str(uuid.uuid4()), "query": QUERY} | overrides

    assert client.post("/api/search", json=body).status_code == 422


def test_unknown_repository_is_not_found(client: TestClient) -> None:
    response = client.post("/api/search", json={"repo_id": str(uuid.uuid4()), "query": QUERY})

    assert response.status_code == 404
    assert response.json() == {"detail": "repository not found"}


def test_unfinished_repository_is_a_conflict(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    repo = add_repo(sessions, status=RepoStatus.RUNNING)

    response = client.post("/api/search", json={"repo_id": str(repo), "query": QUERY})

    assert response.status_code == 409
    assert "running" in response.json()["detail"]


def test_index_of_another_model_is_a_conflict(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    repo = add_repo(sessions)
    add_chunk(sessions, repo, "a.py", vector((0, 1.0)), model="voyage-code-3")

    response = client.post("/api/search", json={"repo_id": str(repo), "query": QUERY})

    assert response.status_code == 409
    assert "voyage-code-3" in response.json()["detail"]


def test_search_needs_an_embedding_provider(app: FastAPI) -> None:
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/search", json={"repo_id": str(uuid.uuid4()), "query": QUERY}
        )

    assert response.status_code == 503
