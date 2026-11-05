"""Database engine and session factory."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from repolens.config import Settings


def database_url(settings: Settings) -> str:
    if settings.database_url is None:
        raise ValueError("REPOLENS_DATABASE_URL is not set; see .env.example.")
    return settings.database_url.get_secret_value()


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(database_url(settings), pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine)
