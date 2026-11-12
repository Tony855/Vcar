"""Persist driver online intent and monotonic presence generations."""

from alembic import op
import sqlalchemy as sa

revision = "20261113_0009"
down_revision = "20261112_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("driver_is_online", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "users",
        sa.Column("driver_presence_generation", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_users_driver_presence_generation_nonnegative",
        "users",
        "driver_presence_generation >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_users_driver_presence_generation_nonnegative",
        "users",
        type_="check",
    )
    op.drop_column("users", "driver_presence_generation")
    op.drop_column("users", "driver_is_online")
