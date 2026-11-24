"""Add passenger booking idempotency keys."""

from alembic import op
import sqlalchemy as sa

revision = "20261124_0011"
down_revision = "20261113_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    op.add_column("orders", sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
    op.create_check_constraint(
        "ck_orders_idempotency_fields_paired",
        "orders",
        "(idempotency_key IS NULL AND request_fingerprint IS NULL) OR "
        "(idempotency_key IS NOT NULL AND request_fingerprint IS NOT NULL)",
    )
    op.create_index(
        "uq_orders_customer_idempotency_key",
        "orders",
        ["customer_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_orders_customer_idempotency_key", table_name="orders")
    op.drop_constraint("ck_orders_idempotency_fields_paired", "orders", type_="check")
    op.drop_column("orders", "request_fingerprint")
    op.drop_column("orders", "idempotency_key")
