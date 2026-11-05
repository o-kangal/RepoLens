"""Alembic environment. Runs against the database in REPOLENS_DATABASE_URL."""

from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine

from repolens.config import Settings
from repolens.db.models import Base
from repolens.db.session import database_url

if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported.")

# A local developer convenience: real environment variables take precedence, and the application
# itself reads only the environment.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

url = context.config.attributes.get("url") or database_url(Settings())
engine = create_engine(url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
