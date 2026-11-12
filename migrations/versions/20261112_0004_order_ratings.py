"""Add authenticated, idempotent two-sided order ratings."""
from alembic import op
import sqlalchemy as sa

revision = "20261112_0004"
down_revision = "20261112_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_ratings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "order_id", sa.BigInteger(),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rater_role", sa.String(16), nullable=False),
        sa.Column(
            "rater_user_id", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ratee_user_id", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("rater_role IN ('PASSENGER', 'DRIVER')", name="ck_order_ratings_role"),
        sa.CheckConstraint("score BETWEEN 1 AND 5", name="ck_order_ratings_score"),
        sa.CheckConstraint("rater_user_id <> ratee_user_id", name="ck_order_ratings_distinct_users"),
        sa.UniqueConstraint("order_id", "rater_role", name="uq_order_ratings_order_role"),
    )
    op.create_index("ix_order_ratings_ratee_created", "order_ratings", ["ratee_user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_order_ratings_ratee_created", table_name="order_ratings")
    op.drop_table("order_ratings")
