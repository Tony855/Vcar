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


async def reconcile_order_settlements() -> list[dict]:
    """Audit settlement snapshots against their atomically posted wallet batches.

    This is read-only. A PREPARED snapshot with a complete batch is reported as
    recoverable and is never silently finalized here; an operator can retry the
    idempotent posting operation after reviewing the exact allocation.
    """
    async with db.pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                s.id AS settlement_id,
                s.order_id,
                s.currency AS settlement_currency,
                s.policy_version,
                s.status,
                COUNT(t.id)::bigint AS ledger_count,
                COALESCE(SUM(t.amount_minor), 0)::text AS ledger_sum_minor,
                COUNT(DISTINCT t.currency)::bigint AS currency_count,
                COUNT(DISTINCT t.order_id)::bigint AS order_count,
                COUNT(DISTINCT t.wallet_id)::bigint AS wallet_count,
                COUNT(DISTINCT t.metadata->>'_batch_key')::bigint AS batch_key_count,
                ARRAY_AGG(t.metadata->>'_batch_index' ORDER BY t.id)
                    FILTER (WHERE t.id IS NOT NULL) AS batch_indexes,
                COUNT(*) FILTER (
                    WHERE t.id IS NOT NULL
                      AND t.currency <> s.currency
                )::bigint AS currency_mismatch_count,
                COUNT(*) FILTER (
                    WHERE t.id IS NOT NULL
                      AND t.order_id IS DISTINCT FROM s.order_id
                )::bigint AS order_mismatch_count,
                COUNT(*) FILTER (
                    WHERE t.id IS NOT NULL
                      AND t.metadata->>'_batch_key' IS DISTINCT FROM 'settlement:' || s.id::text
                )::bigint AS batch_key_mismatch_count,
                COUNT(*) FILTER (
                    WHERE t.id IS NOT NULL
                      AND t.metadata->>'_policy_version' IS DISTINCT FROM s.policy_version
                )::bigint AS policy_mismatch_count,
                COUNT(*) FILTER (
                    WHERE t.id IS NOT NULL
                      AND t.idempotency_key IS DISTINCT FROM (
                          'settlement:' || s.id::text || ':' || (t.metadata->>'_batch_index')
                      )
                )::bigint AS idempotency_key_mismatch_count
            FROM order_settlements AS s
            LEFT JOIN wallet_transactions AS t
              ON t.metadata->>'_settlement_id' = s.id::text
            GROUP BY s.id, s.order_id, s.currency, s.policy_version, s.status
            ORDER BY s.id
            """
        )

    # Also inspect ledger metadata that claims a settlement ID for which
    # no immutable snapshot exists. The original snapshot-driven LEFT JOIN
    # cannot see these orphan rows, so audit them independently.
    async with db.pool.acquire() as connection:
        orphan_rows = await connection.fetch(
            """
            SELECT
                t.metadata->>'_settlement_id' AS settlement_id,
                COUNT(*)::bigint AS ledger_count,
                COALESCE(SUM(t.amount_minor), 0)::text AS ledger_sum_minor,
                COUNT(DISTINCT t.currency)::bigint AS currency_count,
                COUNT(DISTINCT t.order_id)::bigint AS order_count,
                ARRAY_AGG(DISTINCT t.metadata->>'_batch_key') AS batch_keys
            FROM wallet_transactions AS t
            WHERE t.metadata ? '_settlement_id'
              AND NOT EXISTS (
                  SELECT 1
                  FROM order_settlements AS s
                  WHERE s.id::text = t.metadata->>'_settlement_id'
              )
            GROUP BY t.metadata->>'_settlement_id'
            ORDER BY t.metadata->>'_settlement_id'
            """
        )

    findings = []
    for row in rows:
        settlement_id = int(row["settlement_id"])
        status = row["status"]
        count = int(row["ledger_count"])
        ledger_sum = int(row["ledger_sum_minor"])
        indexes = row["batch_indexes"] or []
        index_values = []
        malformed_indexes = False
        for value in indexes:
            try:
                parsed = int(value)
                if str(parsed) != value or parsed < 0:
                    malformed_indexes = True
                index_values.append(parsed)
            except (TypeError, ValueError):
                malformed_indexes = True

        if count == 0:
            if status == "POSTED":
                finding = "POSTED_WITHOUT_LEDGER"
            elif status == "PREPARED":
                finding = "AWAITING_POSTING"
            else:
                finding = "FAILED_WITHOUT_LEDGER"
        else:
            complete_batch = (
                2 <= count <= 32
                and ledger_sum == 0
                and int(row["currency_count"]) == 1
                and int(row["order_count"]) == 1
                # Each batch leg must belong to a distinct wallet, matching
                # WalletLedger.post_balanced_batch and crash recovery checks.
                and int(row["wallet_count"]) == count
                and int(row["batch_key_count"]) == 1
                and sorted(index_values) == list(range(count))
                and not malformed_indexes
                and int(row["currency_mismatch_count"]) == 0
                and int(row["order_mismatch_count"]) == 0
                and int(row["batch_key_mismatch_count"]) == 0
                and int(row["policy_mismatch_count"]) == 0
                and int(row["idempotency_key_mismatch_count"]) == 0
            )
            if not complete_batch:
                finding = "INCONSISTENT_LEDGER_BATCH"
            elif status == "PREPARED":
                finding = "BATCH_COMMITTED_NEEDS_FINALIZATION"
            elif status == "POSTED":
                finding = "RECONCILED"
            else:
                finding = "FAILED_WITH_LEDGER_ENTRIES"

        if finding != "RECONCILED":
            findings.append({
                "settlement_id": settlement_id,
                "order_id": int(row["order_id"]),
                "status": status,
                "currency": row["settlement_currency"],
                "policy_version": row["policy_version"],
                "ledger_count": count,
                "ledger_sum_minor": ledger_sum,
                "finding": finding,
            })
    for row in orphan_rows:
        findings.append({
            "settlement_id": row["settlement_id"],
            "order_id": None,
            "status": "MISSING_SNAPSHOT",
            "currency": None,
            "policy_version": None,
            "ledger_count": int(row["ledger_count"]),
            "ledger_sum_minor": int(row["ledger_sum_minor"]),
            "finding": "ORPHAN_SETTLEMENT_LEDGER",
            "currency_count": int(row["currency_count"]),
            "order_count": int(row["order_count"]),
            "batch_keys": row["batch_keys"] or [],
        })
    return findings
