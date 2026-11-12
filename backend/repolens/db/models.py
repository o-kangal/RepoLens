"""SQLAlchemy models. Schema changes go through Alembic migrations."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIMENSION = 1024


class Base(DeclarativeBase):
    pass


class Repo(Base):
    __tablename__ = "repos"
    __table_args__ = (UniqueConstraint("url", "commit_sha", name="uq_repos_url_commit_sha"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(Text)
    commit_sha: Mapped[str] = mapped_column(Text)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint("strategy IN ('naive', 'ast', 'markdown')", name="ck_chunks_strategy"),
        CheckConstraint(
            "(embedding IS NULL) = (embedding_model IS NULL)", name="ck_chunks_embedding_model"
        ),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv_gin", "tsv", postgresql_using="gin"),
        Index("ix_chunks_repo_id_strategy", "repo_id", "strategy"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    repo_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("repos.id", ondelete="CASCADE"))
    strategy: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    symbol: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str | None] = mapped_column(Text)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(EMBEDDING_DIMENSION))
    embedding_model: Mapped[str | None] = mapped_column(Text)
    """The model that produced ``embedding``; vectors of different models are never mixed."""
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', content)", persisted=True)
    )
