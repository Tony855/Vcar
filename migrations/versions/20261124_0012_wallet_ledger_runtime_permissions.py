"""Restrict runtime role from mutating wallet ledger history."""

from alembic import op

revision = "20261124_0012"
down_revision = "20261124_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The app role may append and read ledger entries, but must not rewrite
    # or erase financial history. Keep the migration owner as table owner.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vcar_app') THEN
                REVOKE UPDATE, DELETE ON TABLE public.wallet_transactions FROM vcar_app;
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    # Restore the previous installer-granted privileges on rollback.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vcar_app') THEN
                GRANT UPDATE, DELETE ON TABLE public.wallet_transactions TO vcar_app;
            END IF;
        END
        $$;
    """)
