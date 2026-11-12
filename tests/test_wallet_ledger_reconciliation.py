import pytest

from app.modules.payments import reconciliation


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, query):
        assert "SUM(t.amount_minor)" in query
        assert "::text" in query
        assert "FILTER (WHERE t.currency <> w.currency)" in query
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
        self.rows = rows

    def acquire(self):
        return Acquire(FakeConnection(self.rows))


@pytest.mark.asyncio
async def test_reconciliation_reports_exact_ledger_balance_drift(monkeypatch):
    monkeypatch.setattr(reconciliation.db, "pool", Pool([{
        "wallet_id": 7,
        "wallet_currency": "USD",
        "balance_minor": "9223372036854775000",
        "ledger_balance_minor": "9223372036854774000",
        "transaction_count": 4,
        "currency_mismatch_count": 0,
    }]))

    result = await reconciliation.reconcile_wallet_ledger()

    assert result == [{
        "wallet_id": 7,
        "currency": "USD",
        "balance_minor": 9223372036854775000,
        "ledger_balance_minor": 9223372036854774000,
        "transaction_count": 4,
        "currency_mismatch_count": 0,
        "difference_minor": 1000,
    }]


@pytest.mark.asyncio
async def test_reconciliation_reports_currency_mismatch_even_when_totals_match(monkeypatch):
    monkeypatch.setattr(reconciliation.db, "pool", Pool([{
        "wallet_id": 8,
        "wallet_currency": "KHR",
        "balance_minor": "5000",
        "ledger_balance_minor": "5000",
        "transaction_count": 2,
        "currency_mismatch_count": 1,
    }]))

    result = await reconciliation.reconcile_wallet_ledger()

    assert len(result) == 1
    assert result[0]["difference_minor"] == 0
    assert result[0]["currency_mismatch_count"] == 1


@pytest.mark.asyncio
async def test_reconciliation_returns_empty_for_balanced_wallets(monkeypatch):
    monkeypatch.setattr(reconciliation.db, "pool", Pool([{
        "wallet_id": 9,
        "wallet_currency": "USD",
        "balance_minor": "1250",
        "ledger_balance_minor": "1250",
        "transaction_count": 3,
        "currency_mismatch_count": 0,
    }]))

    assert await reconciliation.reconcile_wallet_ledger() == []


@pytest.mark.asyncio
async def test_reconciliation_handles_wallet_without_transactions(monkeypatch):
    monkeypatch.setattr(reconciliation.db, "pool", Pool([{
        "wallet_id": 10,
        "wallet_currency": "USD",
        "balance_minor": "0",
        "ledger_balance_minor": "0",
        "transaction_count": 0,
        "currency_mismatch_count": 0,
    }]))

    assert await reconciliation.reconcile_wallet_ledger() == []
