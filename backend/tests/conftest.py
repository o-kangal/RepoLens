"""Shared fixtures. Database tests run in their own scratch database."""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from repolens.config import Settings
from repolens.db.session import database_url

BACKEND = Path(__file__).resolve().parents[1]


def alembic_config(url: str) -> Config:
    config = Config(BACKEND / "alembic.ini")
    config.attributes["url"] = url
    return config


def _database_url() -> SecretStr | None:
    """``REPOLENS_DATABASE_URL`` from the environment, or else from ``.env``.

    Only this one value is read from ``.env``: the file may also hold real provider keys, and
    those must never reach a test.
    """
    name = "REPOLENS_DATABASE_URL"
    url = os.environ.get(name) or dotenv_values(BACKEND.parent / ".env").get(name)
    return SecretStr(url) if url else None


@pytest.fixture
def scratch_url() -> Iterator[str]:
    server = make_url(database_url(Settings(database_url=_database_url())))
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
