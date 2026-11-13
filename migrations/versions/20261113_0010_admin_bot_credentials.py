"""Add administrator-managed encrypted Telegram Bot credentials."""
from alembic import op

revision = "20261113_0010"
down_revision = "20261113_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy plaintext token settings are deliberately discarded, never copied.
    op.execute("DELETE FROM settings WHERE key IN ('PASSENGER_BOT_TOKEN', 'DRIVER_BOT_TOKEN')")


def downgrade() -> None:
    # Credentials are encrypted with SECRET_KEY; downgrade never exports plaintext.
    pass
