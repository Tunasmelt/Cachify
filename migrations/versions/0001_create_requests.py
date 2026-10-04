"""Create requests table."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "requests",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("tenant_hash", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("upstream", sa.Text(), nullable=False),
        sa.Column("wire_format", sa.Text(), nullable=False),
        sa.Column("cache_mode", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("stream", sa.Integer(), nullable=False),
        sa.Column("upstream_status", sa.Integer(), nullable=True),
        sa.Column("complete", sa.Integer(), nullable=True),
        sa.Column("truncated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("log_mode", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_requests_tenant_hash_created_at",
        "requests",
        ["tenant_hash", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_requests_tenant_hash_created_at", table_name="requests")
    op.drop_table("requests")
