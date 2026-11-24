"""Make order status event history append-only for the runtime role."""

from alembic import op

revision = "20261124_0014"
down_revision = "20261124_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Event rows are created in the same transaction as order status changes.
    # Runtime code only appends and reads them; delivery state belongs to outbox.
    op.execute("""
        DO $order_event_history$
        BEGIN
            IF to_regclass('public.order_events') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vcar_app') THEN
                REVOKE UPDATE, DELETE ON TABLE public.order_events FROM vcar_app;
            END IF;
        END
        $order_event_history$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $restore_order_event_history$
        BEGIN
            IF to_regclass('public.order_events') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vcar_app') THEN
                GRANT UPDATE, DELETE ON TABLE public.order_events TO vcar_app;
            END IF;
        END
        $restore_order_event_history$;
    """)
