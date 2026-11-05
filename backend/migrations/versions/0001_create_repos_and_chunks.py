"""Create repos and chunks.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects.postgresql import TSVECTOR

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "repos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("commit_sha", sa.Text(), nullable=False),
        sa.Column(
            "indexed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("url", "commit_sha", name="uq_repos_url_commit_sha"),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "repo_id", sa.Uuid(), sa.ForeignKey("repos.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("strategy", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("language", sa.Text()),
        sa.Column("symbol", sa.Text()),
        sa.Column("kind", sa.Text()),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("embedding", VECTOR(1024)),
        sa.Column(
            "tsv",
            TSVECTOR(),
            sa.Computed("to_tsvector('simple', content)", persisted=True),
            nullable=False,
        ),
        sa.CheckConstraint("strategy IN ('naive', 'ast', 'markdown')", name="ck_chunks_strategy"),
    )
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("ix_chunks_tsv_gin", "chunks", ["tsv"], postgresql_using="gin")
    op.create_index("ix_chunks_repo_id_strategy", "chunks", ["repo_id", "strategy"])


def downgrade() -> None:
    op.drop_table("chunks")
    op.drop_table("repos")
    op.execute("DROP EXTENSION IF EXISTS vector")
