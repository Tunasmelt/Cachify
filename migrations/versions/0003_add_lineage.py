"""Add lineage columns to requests."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("requests") as batch:
        batch.add_column(sa.Column("predecessor_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("lineage_source", sa.Text(), nullable=True))
    op.create_index(
        "ix_requests_tenant_hash_session_id",
        "requests",
        ["tenant_hash", "session_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_requests_tenant_hash_session_id", table_name="requests")
    with op.batch_alter_table("requests") as batch:
        batch.drop_column("lineage_source")
        batch.drop_column("predecessor_id")
