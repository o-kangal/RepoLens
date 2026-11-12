"""Track indexing status on repos, so a repository exists from the moment indexing starts.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rows that exist already were fully indexed by the previous pipeline.
    op.add_column("repos", sa.Column("status", sa.Text(), nullable=False, server_default="done"))
    op.alter_column("repos", "status", server_default=None)
    op.add_column("repos", sa.Column("ref", sa.Text()))
    op.add_column("repos", sa.Column("error", sa.Text()))
    op.alter_column("repos", "commit_sha", nullable=True)
    op.create_check_constraint(
        "ck_repos_status", "repos", "status IN ('running', 'done', 'failed')"
    )
    op.create_check_constraint(
        "ck_repos_done_has_commit", "repos", "status <> 'done' OR commit_sha IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DELETE FROM repos WHERE commit_sha IS NULL")
    op.drop_constraint("ck_repos_done_has_commit", "repos", type_="check")
    op.drop_constraint("ck_repos_status", "repos", type_="check")
    op.alter_column("repos", "commit_sha", nullable=False)
    op.drop_column("repos", "error")
    op.drop_column("repos", "ref")
    op.drop_column("repos", "status")
