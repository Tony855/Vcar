"""Read-only reconciliation between wallet cached balances and immutable ledger rows."""
from __future__ import annotations

from app.db.core import db


async def reconcile_wallet_ledger() -> list[dict]:
    """Return wallets whose cached balance differs from the signed ledger sum.

    SUM(bigint) is NUMERIC in PostgreSQL. Cast it to text and parse as int so
    reconciliation never rounds large balances through a floating-point codec.
    This routine deliberately does not repair or mutate financial records.
    """
    async with db.pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                w.id AS wallet_id,
                w.currency AS wallet_currency,
                w.balance_minor::text AS balance_minor,
                COALESCE(SUM(t.amount_minor), 0)::text AS ledger_balance_minor,
                COUNT(t.id)::bigint AS transaction_count,
                COUNT(t.id) FILTER (WHERE t.currency <> w.currency)::bigint
                    AS currency_mismatch_count
            FROM wallet_accounts AS w
            LEFT JOIN wallet_transactions AS t ON t.wallet_id = w.id
            GROUP BY w.id, w.currency, w.balance_minor
            ORDER BY w.id
            """
        )
    discrepancies = []
    for row in rows:
        balance = int(row["balance_minor"])
        ledger_balance = int(row["ledger_balance_minor"])
        mismatch_count = int(row["currency_mismatch_count"])
        if balance != ledger_balance or mismatch_count:
            discrepancies.append({
                "wallet_id": int(row["wallet_id"]),
                "currency": row["wallet_currency"],
                "balance_minor": balance,
                "ledger_balance_minor": ledger_balance,
                "transaction_count": int(row["transaction_count"]),
                "currency_mismatch_count": mismatch_count,
                "difference_minor": balance - ledger_balance,
            })
    return discrepancies
