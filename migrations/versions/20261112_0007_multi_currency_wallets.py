"""Allow each user to hold independent USD and KHR wallet accounts."""
from alembic import op

revision = "20261112_0007"
down_revision = "20261112_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The previous unique(user_id) constraint guarantees no duplicate pairs.
    op.drop_constraint("wallet_accounts_user_id_key", "wallet_accounts", type_="unique")
    op.create_unique_constraint(
        "uq_wallet_accounts_user_currency",
        "wallet_accounts",
        ["user_id", "currency"],
    )


def downgrade() -> None:
    # This intentionally fails if users have created more than one currency wallet.
    op.create_unique_constraint(
        "wallet_accounts_user_id_key", "wallet_accounts", ["user_id"]
    )
    op.drop_constraint("uq_wallet_accounts_user_currency", "wallet_accounts", type_="unique")
