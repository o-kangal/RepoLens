"""Migration and schema tests. Each test runs in its own scratch database."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, delete, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DataError, IntegrityError, StatementError
from sqlalchemy.orm import Session

from repolens.config import Settings
from repolens.db.models import EMBEDDING_DIMENSION, Chunk, Repo
from repolens.db.session import database_url

BACKEND = Path(__file__).resolve().parents[2]


def alembic_config(url: str) -> Config:
    config = Config(BACKEND / "alembic.ini")
    config.attributes["url"] = url
    return config


@pytest.fixture
def scratch_url() -> Iterator[str]:
    load_dotenv(BACKEND.parent / ".env")
    server = make_url(database_url(Settings()))
    name = f"repolens_test_{uuid.uuid4().hex}"
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield server.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def engine(scratch_url: str) -> Iterator[Engine]:
    command.upgrade(alembic_config(scratch_url), "head")
    migrated = create_engine(scratch_url)
    yield migrated
    migrated.dispose()


def make_chunk(repo: Repo, **overrides: object) -> Chunk:
    values: dict[str, object] = {
        "repo_id": repo.id,
        "strategy": "naive",
        "path": "src/user.ts",
        "start_line": 1,
        "end_line": 2,
        "content": "findById returns a user",
        "token_count": 5,
        "content_hash": "hash",
    }
    return Chunk(**(values | overrides))


def add_repo(session: Session) -> Repo:
    repo = Repo(id=uuid.uuid4(), url="https://github.com/acme/app", commit_sha="abc")
    session.add(repo)
    session.flush()
    return repo


def test_upgrade_and_downgrade_round_trip(scratch_url: str) -> None:
    config = alembic_config(scratch_url)
    probe = create_engine(scratch_url)

    command.upgrade(config, "head")
    assert {"repos", "chunks"} <= set(inspect(probe).get_table_names())

    command.downgrade(config, "base")
    assert not {"repos", "chunks"} & set(inspect(probe).get_table_names())
    with probe.connect() as connection:
        installed = connection.scalar(
            text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
        )
    assert installed == 0

    command.upgrade(config, "head")
    assert {"repos", "chunks"} <= set(inspect(probe).get_table_names())
    probe.dispose()


def test_models_match_the_migrations(engine: Engine, scratch_url: str) -> None:
    command.check(alembic_config(scratch_url))


def test_indexes_match_the_design(engine: Engine) -> None:
    with engine.connect() as connection:
        definitions = dict(
            connection.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'chunks'")
            ).all()
        )

    assert "USING hnsw (embedding vector_cosine_ops)" in definitions["ix_chunks_embedding_hnsw"]
    assert "USING gin (tsv)" in definitions["ix_chunks_tsv_gin"]
    assert "(repo_id, strategy)" in definitions["ix_chunks_repo_id_strategy"]


@pytest.mark.parametrize("strategy", ["naive", "ast", "markdown"])
def test_known_strategies_are_accepted(engine: Engine, strategy: str) -> None:
    with Session(engine) as session:
        session.add(make_chunk(add_repo(session), strategy=strategy))
        session.flush()


def test_unknown_strategy_is_rejected(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(make_chunk(add_repo(session), strategy="semantic"))
        with pytest.raises(IntegrityError, match="ck_chunks_strategy"):
            session.flush()


def test_repo_url_and_commit_are_unique(engine: Engine) -> None:
    with Session(engine) as session:
        add_repo(session)
        with pytest.raises(IntegrityError, match="uq_repos_url_commit_sha"):
            add_repo(session)


def test_deleting_a_repo_deletes_its_chunks(engine: Engine) -> None:
    with Session(engine) as session:
        repo = add_repo(session)
        session.add(make_chunk(repo))
        session.flush()

        session.execute(delete(Repo).where(Repo.id == repo.id))

        assert session.scalar(select(Chunk.id)) is None


def test_full_text_column_is_generated_from_content(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(make_chunk(add_repo(session)))
        session.flush()

        match = session.scalar(
            select(Chunk.id).where(Chunk.tsv.op("@@")(text("to_tsquery('simple', 'findbyid')")))
        )

        assert match is not None


def test_embedding_accepts_the_configured_dimension(engine: Engine) -> None:
    with Session(engine) as session:
        chunk = make_chunk(add_repo(session), embedding=[0.1] * EMBEDDING_DIMENSION)
        session.add(chunk)
        session.flush()


def test_embedding_rejects_another_dimension(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(make_chunk(add_repo(session), embedding=[0.1] * 3))
        with pytest.raises((DataError, StatementError), match="1024"):
            session.flush()
