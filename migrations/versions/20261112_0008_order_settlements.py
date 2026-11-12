"""Persist an immutable, idempotent settlement breakdown for completed orders."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261112_0008"
down_revision = "20261112_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_settlements",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("components", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PREPARED"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("order_id", name="uq_order_settlements_order"),
        sa.CheckConstraint("currency IN ('USD', 'KHR')", name="ck_order_settlements_currency"),
        sa.CheckConstraint("status IN ('PREPARED', 'POSTED', 'FAILED')", name="ck_order_settlements_status"),
        sa.CheckConstraint("length(policy_version) BETWEEN 1 AND 64", name="ck_order_settlements_policy_version"),
        sa.CheckConstraint("jsonb_typeof(components) = 'object' AND components <> '{}'::jsonb", name="ck_order_settlements_components_object"),
    )
    op.create_index("ix_order_settlements_status_created", "order_settlements", ["status", "created_at"])
    op.execute("""
        CREATE FUNCTION protect_order_settlement_snapshot() RETURNS trigger AS '
        BEGIN
            IF NEW.order_id IS DISTINCT FROM OLD.order_id
               OR NEW.currency IS DISTINCT FROM OLD.currency
               OR NEW.policy_version IS DISTINCT FROM OLD.policy_version
               OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
               OR NEW.components IS DISTINCT FROM OLD.components
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION ''order settlement snapshot is immutable'';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status
               AND NOT (OLD.status = ''PREPARED'' AND NEW.status IN (''POSTED'', ''FAILED'')) THEN
                RAISE EXCEPTION ''invalid order settlement status transition'';
            END IF;
            RETURN NEW;
        END;
        ' LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_order_settlement_snapshot_immutable
        BEFORE UPDATE ON order_settlements
        FOR EACH ROW EXECUTE FUNCTION protect_order_settlement_snapshot()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_order_settlement_snapshot_immutable ON order_settlements")
    op.execute("DROP FUNCTION IF EXISTS protect_order_settlement_snapshot()")
    op.drop_index("ix_order_settlements_status_created", table_name="order_settlements")
    op.drop_table("order_settlements")
