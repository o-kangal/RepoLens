"""Alembic environment. Runs against the database in REPOLENS_DATABASE_URL."""

import os
from pathlib import Path

from alembic import context
from dotenv import dotenv_values
from pydantic import SecretStr
from sqlalchemy import create_engine

from repolens.config import Settings
from repolens.db.models import Base
from repolens.db.session import database_url

if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported.")


def _cli_database_url() -> str:
    """The URL for the Alembic CLI: the environment first, then ``.env`` as a local convenience.

    Only the database URL is read from ``.env``, and nothing is added to the environment, so
    other values in that file (provider keys) never leak into the process that runs migrations,
    such as the test suite.
    """
    name = "REPOLENS_DATABASE_URL"
    env_file = Path(__file__).resolve().parents[2] / ".env"
    value = os.environ.get(name) or dotenv_values(env_file).get(name)
    return database_url(Settings(database_url=SecretStr(value) if value else None))


url = context.config.attributes.get("url") or _cli_database_url()
engine = create_engine(url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
