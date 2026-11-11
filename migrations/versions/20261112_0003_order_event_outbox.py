"""Add durable delivery queue for order status events."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261112_0003"
down_revision = "20261112_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_event_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_id", sa.BigInteger(),
            sa.ForeignKey("order_events.id", ondelete="CASCADE"),
            nullable=False, unique=True,
        ),
        sa.Column(
            "order_id", sa.BigInteger(),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("attempts >= 0", name="ck_order_event_outbox_attempts"),
    )
    op.create_index(
        "ix_order_event_outbox_pending",
        "order_event_outbox",
        ["available_at", "id"],
        postgresql_where=sa.text("delivered_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_order_event_outbox_pending", table_name="order_event_outbox")
    op.drop_table("order_event_outbox")
