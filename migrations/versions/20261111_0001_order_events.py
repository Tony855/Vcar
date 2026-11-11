"""Create append-only order status event history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261111_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_role", sa.String(length=32), nullable=False, server_default="SYSTEM"),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_order_events_order_created", "order_events", ["order_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_order_events_order_created", table_name="order_events")
    op.drop_table("order_events")
