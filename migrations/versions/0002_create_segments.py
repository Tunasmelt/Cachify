"""Create segments table."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "segments",
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column("raw_hash", sa.Text(), nullable=False),
        sa.Column("canon_hash", sa.Text(), nullable=False),
        sa.Column("byte_len", sa.Integer(), nullable=False),
        sa.Column("has_breakpoint", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("request_id", "idx"),
    )


def downgrade() -> None:
    op.drop_table("segments")
