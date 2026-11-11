"""Add PostGIS driver tracking and idempotent wallet/payment ledger."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261112_0002"
down_revision = "20261111_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table(
        "driver_locations",
        sa.Column("driver_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("position", sa.Text(), nullable=False),
        sa.Column("accuracy_m", sa.Float(), nullable=True),
        sa.Column("heading_deg", sa.Float(), nullable=True),
        sa.Column("speed_mps", sa.Float(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("accuracy_m IS NULL OR accuracy_m >= 0", name="ck_driver_locations_accuracy"),
        sa.CheckConstraint("heading_deg IS NULL OR (heading_deg >= 0 AND heading_deg < 360)", name="ck_driver_locations_heading"),
        sa.CheckConstraint("speed_mps IS NULL OR speed_mps >= 0", name="ck_driver_locations_speed"),
    )
    op.execute("ALTER TABLE driver_locations ALTER COLUMN position TYPE geography(Point,4326) USING ST_SetSRID(ST_GeomFromText(position),4326)::geography")
    op.create_index("ix_driver_locations_position", "driver_locations", ["position"], postgresql_using="gist")
    op.create_index("ix_driver_locations_received", "driver_locations", ["received_at"])
    op.create_table(
        "trip_tracks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("driver_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Text(), nullable=False),
        sa.Column("accuracy_m", sa.Float(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("ALTER TABLE trip_tracks ALTER COLUMN position TYPE geography(Point,4326) USING ST_SetSRID(ST_GeomFromText(position),4326)::geography")
    op.create_index("ix_trip_tracks_order_recorded", "trip_tracks", ["order_id", "recorded_at"])
    op.create_index("ix_trip_tracks_position", "trip_tracks", ["position"], postgresql_using="gist")
    op.create_table(
        "wallet_accounts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("balance_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("balance_minor >= 0", name="ck_wallet_balance_nonnegative"),
    )
    op.create_table(
        "wallet_transactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("wallet_id", sa.BigInteger(), sa.ForeignKey("wallet_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("provider_reference", sa.String(128), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("amount_minor <> 0", name="ck_wallet_transaction_nonzero"),
    )
    op.create_index("ix_wallet_transactions_wallet_created", "wallet_transactions", ["wallet_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_wallet_transactions_wallet_created", table_name="wallet_transactions")
    op.drop_table("wallet_transactions")
    op.drop_table("wallet_accounts")
    op.drop_index("ix_trip_tracks_position", table_name="trip_tracks")
    op.drop_index("ix_trip_tracks_order_recorded", table_name="trip_tracks")
    op.drop_table("trip_tracks")
    op.drop_index("ix_driver_locations_received", table_name="driver_locations")
    op.drop_index("ix_driver_locations_position", table_name="driver_locations")
    op.drop_table("driver_locations")
    # PostGIS may be shared by other extensions/apps; do not DROP EXTENSION.
