"""Fence outbox acknowledgements to the worker that owns the current lease."""
from alembic import op
import sqlalchemy as sa

revision = "20261112_0006"
down_revision = "20261112_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "order_event_outbox",
        sa.Column("lock_token", sa.Uuid(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("order_event_outbox", "lock_token")
