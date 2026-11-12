import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from repolens.api.deps import get_clone, get_embedder
from repolens.config import Settings
from repolens.db.models import Repo, RepoStatus
from repolens.ingest.clone import Checkout, CloneError, GitHubRepository
from repolens.ingest.pipeline import INTERRUPTED_MESSAGE
from repolens.main import create_app
from repolens.providers.embeddings import FakeEmbedder

URL = "https://github.com/acme/app"


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine)


@pytest.fixture
def app(engine: Engine, scratch_url: str) -> FastAPI:
    return create_app(Settings(database_url=SecretStr(scratch_url), voyage_api_key=None))


class RecordingClone:
    """Serves a local tree and records the repository status seen while the job runs."""

    def __init__(self, root: Path, sessions: sessionmaker[Session]) -> None:
        self.root = root
        self.sessions = sessions
        self.status_during_job: list[str] = []

    def __call__(
        self, repository: GitHubRepository, ref: str | None
    ) -> AbstractAsyncContextManager[Checkout]:
        return self._clone(repository, ref)

    @asynccontextmanager
    async def _clone(
        self, repository: GitHubRepository, ref: str | None
    ) -> AsyncIterator[Checkout]:
        with self.sessions() as session:
            self.status_during_job.extend(session.scalars(select(Repo.status)))
        yield Checkout(root=self.root, commit_sha="abc123")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text("# App\n\n## Install\n\nRun it.\n")
    (tmp_path / "main.py").write_text("print('hi')\n")
    return tmp_path


@pytest.fixture
def recording_clone(tree: Path, sessions: sessionmaker[Session]) -> RecordingClone:
    return RecordingClone(tree, sessions)


@pytest.fixture
def client(app: FastAPI, recording_clone: RecordingClone) -> Iterator[TestClient]:
    app.dependency_overrides[get_embedder] = FakeEmbedder
    app.dependency_overrides[get_clone] = lambda: recording_clone
    with TestClient(app) as test_client:
        yield test_client


def repo_count(sessions: sessionmaker[Session]) -> int:
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(Repo)) or 0


def test_indexing_moves_from_running_to_done(
    client: TestClient, recording_clone: RecordingClone
) -> None:
    response = client.post("/api/repos", json={"url": URL + ".git", "ref": "main"})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "running"
    assert recording_clone.status_during_job == ["running"]

    status = client.get(f"/api/repos/{body['repo_id']}")

    assert status.status_code == 200
    assert status.json() == {
        "id": body["repo_id"],
        "url": URL,
        "ref": "main",
        "commit_sha": "abc123",
        "status": "done",
        "error": None,
        "chunk_counts": {"naive": 2, "ast": 0, "markdown": 2},
    }


def broken_clone(_: GitHubRepository, __: str | None) -> AbstractAsyncContextManager[Checkout]:
    raise CloneError("git clone failed: repository not found")


def test_failed_indexing_reports_the_error(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[get_clone] = lambda: broken_clone

    repo_id = client.post("/api/repos", json={"url": URL}).json()["repo_id"]
    status = client.get(f"/api/repos/{repo_id}").json()

    assert status["status"] == "failed"
    assert status["error"] == "git clone failed: repository not found"
    assert status["commit_sha"] is None
    assert status["chunk_counts"] == {"naive": 0, "ast": 0, "markdown": 0}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"ref": "main"},
        {"url": 42},
        {"url": URL, "extra": True},
        {"url": "https://gitlab.com/acme/app"},
        {"url": "https://github.com/acme/app/tree/main"},
        {"url": URL, "ref": "--upload-pack=touch"},
        {"url": URL + "/" + "a" * 300},
        {"url": URL, "ref": "r" * 256},
    ],
)
def test_invalid_requests_are_rejected_without_a_repository(
    client: TestClient, sessions: sessionmaker[Session], body: dict[str, object]
) -> None:
    response = client.post("/api/repos", json=body)

    assert response.status_code == 422
    assert repo_count(sessions) == 0


def test_unsupported_url_explains_the_rule(client: TestClient) -> None:
    response = client.post("/api/repos", json={"url": "https://gitlab.com/acme/app"})

    assert response.json() == {"detail": "only https://github.com/<owner>/<repo> URLs are accepted"}


def test_unknown_repository_is_not_found(client: TestClient) -> None:
    response = client.get(f"/api/repos/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "repository not found"}


def test_malformed_repository_id_is_rejected(client: TestClient) -> None:
    assert client.get("/api/repos/not-a-uuid").status_code == 422


def test_indexing_needs_an_embedding_provider(
    app: FastAPI, sessions: sessionmaker[Session]
) -> None:
    with TestClient(app) as test_client:
        response = test_client.post("/api/repos", json={"url": URL})

    assert response.status_code == 503
    assert response.json() == {"detail": "no embedding provider is configured"}
    assert repo_count(sessions) == 0


def test_startup_fails_jobs_a_previous_process_left_running(
    app: FastAPI, sessions: sessionmaker[Session]
) -> None:
    with sessions.begin() as session:
        stale = Repo(url=URL, status=RepoStatus.RUNNING)
        session.add(stale)
        session.flush()
        stale_id = stale.id

    with TestClient(app) as test_client:
        status = test_client.get(f"/api/repos/{stale_id}").json()

    assert (status["status"], status["error"]) == ("failed", INTERRUPTED_MESSAGE)
