"""Split identifiers into words in the full-text column, so code matches by its parts.

The default parser keeps ``self.derive_key`` as ``self.derive`` and ``key``, and
``UserService.findById`` as one token, so neither ``derive_key`` nor ``findById`` could be
found. Treating every run of non-alphanumeric characters as a word break gives ``self``,
``derive``, ``key``, ``userservice`` and ``findbyid``; queries are normalised the same way.
Existing rows are recomputed in place.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE chunks ALTER COLUMN tsv SET EXPRESSION AS "
        "(to_tsvector('simple', regexp_replace(content, '[^[:alnum:]]+', ' ', 'g')))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE chunks ALTER COLUMN tsv SET EXPRESSION AS (to_tsvector('simple', content))"
    )
