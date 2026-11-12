"""Record which model produced each chunk embedding.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("embedding_model", sa.Text()))
    op.create_check_constraint(
        "ck_chunks_embedding_model",
        "chunks",
        "(embedding IS NULL) = (embedding_model IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_chunks_embedding_model", "chunks", type_="check")
    op.drop_column("chunks", "embedding_model")
