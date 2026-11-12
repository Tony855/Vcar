"""Persist bounded comments on authenticated passenger ratings."""
from alembic import op
import sqlalchemy as sa

revision = "20261112_0005"
down_revision = "20261112_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("order_ratings", sa.Column("comment", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_order_ratings_comment_length",
        "order_ratings",
        "comment IS NULL OR char_length(comment) <= 1000",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_order_ratings_comment_length", "order_ratings", type_="check"
    )
    op.drop_column("order_ratings", "comment")
