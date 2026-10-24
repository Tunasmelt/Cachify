"""Add divergence columns to requests."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("requests") as batch:
        batch.add_column(sa.Column("divergence_index", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("divergence_kind", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("requests") as batch:
        batch.drop_column("divergence_kind")
        batch.drop_column("divergence_index")
