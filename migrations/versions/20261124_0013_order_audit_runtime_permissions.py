"""Protect order audit history from runtime deletion."""

from alembic import op

revision = "20261124_0013"
down_revision = "20261124_0012"
branch_labels = None
depends_on = None


_TABLES = ("orders", "order_events", "order_event_outbox", "order_settlements")


def upgrade() -> None:
    # The application has no hard-delete workflow for orders. Prevent deleting
    # an order from cascading away its status history, delivery outbox, or
    # settlement snapshot. Event/outbox/settlement rows are retained for audit.
    for table in _TABLES:
        op.execute(f"""
            DO $protect_order_audit$
            BEGIN
                IF to_regclass('public.{table}') IS NOT NULL
                   AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vcar_app') THEN
                    EXECUTE 'REVOKE DELETE ON TABLE public.{table} FROM vcar_app';
                END IF;
            END
            $protect_order_audit$;
        """)


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"""
            DO $restore_order_audit$
            BEGIN
                IF to_regclass('public.{table}') IS NOT NULL
                   AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vcar_app') THEN
                    EXECUTE 'GRANT DELETE ON TABLE public.{table} TO vcar_app';
                END IF;
            END
            $restore_order_audit$;
        """)
