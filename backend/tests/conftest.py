"""Shared fixtures. Database tests run in their own scratch database."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from repolens.config import Settings
from repolens.db.session import database_url

BACKEND = Path(__file__).resolve().parents[1]


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
