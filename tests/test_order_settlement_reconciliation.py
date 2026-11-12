import pytest

from app.modules.payments import reconciliation


class SettlementAuditConnection:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    async def fetch(self, query):
        self.queries.append(query)
        assert "order_settlements" in query
        assert "wallet_transactions" in query
        assert "IS DISTINCT FROM" in query
        return self.rows


class Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class Pool:
    def __init__(self, rows):
        self.connection = SettlementAuditConnection(rows)

    def acquire(self):
        return Acquire(self.connection)


def audit_row(**overrides):
    row = {
        "settlement_id": 91,
        "order_id": 12,
        "settlement_currency": "USD",
        "policy_version": "kh-v1",
        "status": "POSTED",
        "ledger_count": 2,
        "ledger_sum_minor": "0",
        "currency_count": 1,
        "order_count": 1,
        "batch_key_count": 1,
        "batch_indexes": ["0", "1"],
        "currency_mismatch_count": 0,
        "order_mismatch_count": 0,
        "batch_key_mismatch_count": 0,
        "policy_mismatch_count": 0,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_settlement_audit_accepts_complete_posted_batch(monkeypatch):
    pool = Pool([audit_row()])
    monkeypatch.setattr(reconciliation.db, "pool", pool)

    assert await reconciliation.reconcile_order_settlements() == []
    assert len(pool.connection.queries) == 1


@pytest.mark.asyncio
async def test_settlement_audit_flags_prepared_snapshot_with_committed_batch(monkeypatch):
    monkeypatch.setattr(
        reconciliation.db, "pool", Pool([audit_row(status="PREPARED")])
    )

    findings = await reconciliation.reconcile_order_settlements()

    assert findings == [{
        "settlement_id": 91,
        "order_id": 12,
        "status": "PREPARED",
        "currency": "USD",
        "policy_version": "kh-v1",
        "ledger_count": 2,
        "ledger_sum_minor": 0,
        "finding": "BATCH_COMMITTED_NEEDS_FINALIZATION",
    }]


@pytest.mark.asyncio
async def test_settlement_audit_flags_posted_snapshot_without_ledger(monkeypatch):
    monkeypatch.setattr(
        reconciliation.db, "pool", Pool([audit_row(ledger_count=0, batch_indexes=None)])
    )

    findings = await reconciliation.reconcile_order_settlements()

    assert findings[0]["finding"] == "POSTED_WITHOUT_LEDGER"


@pytest.mark.asyncio
async def test_settlement_audit_flags_partial_or_unbalanced_batch(monkeypatch):
    monkeypatch.setattr(
        reconciliation.db,
        "pool",
        Pool([audit_row(ledger_count=1, batch_indexes=["0"], ledger_sum_minor="-900")]),
    )

    findings = await reconciliation.reconcile_order_settlements()

    assert findings[0]["finding"] == "INCONSISTENT_LEDGER_BATCH"


@pytest.mark.asyncio
async def test_settlement_audit_flags_missing_batch_metadata(monkeypatch):
    monkeypatch.setattr(
        reconciliation.db,
        "pool",
        Pool([audit_row(batch_key_mismatch_count=1, policy_mismatch_count=1)]),
    )

    findings = await reconciliation.reconcile_order_settlements()

    assert findings[0]["finding"] == "INCONSISTENT_LEDGER_BATCH"


@pytest.mark.asyncio
async def test_settlement_audit_does_not_treat_failed_snapshot_with_ledger_as_healthy(monkeypatch):
    monkeypatch.setattr(
        reconciliation.db, "pool", Pool([audit_row(status="FAILED")])
    )

    findings = await reconciliation.reconcile_order_settlements()

    assert findings[0]["finding"] == "FAILED_WITH_LEDGER_ENTRIES"
