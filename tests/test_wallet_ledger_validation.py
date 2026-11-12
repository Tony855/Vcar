import pytest

from app.modules.payments import ledger


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [0, 1.25, True, False])
async def test_wallet_ledger_rejects_non_integer_or_zero_amount_before_database(monkeypatch, amount):
    monkeypatch.setattr(ledger.db, "pool", None)
    with pytest.raises(ValueError, match="amount_minor"):
        await ledger.WalletLedger.post(
            user_id=1,
            currency="USD",
            amount_minor=amount,
            kind="TOPUP",
            idempotency_key="validation-key-001",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("currency", ["US", "USDD", "1SD", "U$D", ""])
async def test_wallet_ledger_rejects_invalid_currency_before_database(monkeypatch, currency):
    monkeypatch.setattr(ledger.db, "pool", None)
    with pytest.raises(ValueError, match="currency"):
        await ledger.WalletLedger.post(
            user_id=1,
            currency=currency,
            amount_minor=100,
            kind="TOPUP",
            idempotency_key="validation-key-002",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "x" * 129])
async def test_wallet_ledger_rejects_invalid_idempotency_key_before_database(monkeypatch, key):
    monkeypatch.setattr(ledger.db, "pool", None)
    with pytest.raises(ValueError, match="idempotency_key"):
        await ledger.WalletLedger.post(
            user_id=1,
            currency="USD",
            amount_minor=100,
            kind="TOPUP",
            idempotency_key=key,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["", "X" * 33])
async def test_wallet_ledger_rejects_invalid_transaction_kind_before_database(monkeypatch, kind):
    monkeypatch.setattr(ledger.db, "pool", None)
    with pytest.raises(ValueError, match="transaction kind"):
        await ledger.WalletLedger.post(
            user_id=1,
            currency="USD",
            amount_minor=100,
            kind=kind,
            idempotency_key="validation-key-003",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", [0, -1, True, False, "1", 1.0])
async def test_wallet_ledger_rejects_invalid_user_id_before_database(monkeypatch, user_id):
    monkeypatch.setattr(ledger.db, "pool", None)
    with pytest.raises(ValueError, match="user_id"):
        await ledger.WalletLedger.post(
            user_id=user_id,
            currency="USD",
            amount_minor=100,
            kind="TOPUP",
            idempotency_key="validation-user-001",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("currency", ["ÅBC", "ＡＢＣ", "U D"])
async def test_wallet_ledger_rejects_non_ascii_or_malformed_currency(monkeypatch, currency):
    monkeypatch.setattr(ledger.db, "pool", None)
    with pytest.raises(ValueError, match="currency"):
        await ledger.WalletLedger.post(
            user_id=1,
            currency=currency,
            amount_minor=100,
            kind="TOPUP",
            idempotency_key="validation-currency-003",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["   ", "\t\n"])
async def test_wallet_ledger_rejects_blank_idempotency_key_before_database(monkeypatch, key):
    monkeypatch.setattr(ledger.db, "pool", None)
    with pytest.raises(ValueError, match="idempotency_key"):
        await ledger.WalletLedger.post(
            user_id=1,
            currency="USD",
            amount_minor=100,
            kind="TOPUP",
            idempotency_key=key,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["   ", "\t\n"])
async def test_wallet_ledger_rejects_blank_kind_before_database(monkeypatch, kind):
    monkeypatch.setattr(ledger.db, "pool", None)
    with pytest.raises(ValueError, match="transaction kind"):
        await ledger.WalletLedger.post(
            user_id=1,
            currency="USD",
            amount_minor=100,
            kind=kind,
            idempotency_key="validation-kind-004",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [2**63])
async def test_wallet_ledger_rejects_amount_outside_signed_bigint(monkeypatch, amount):
    monkeypatch.setattr(ledger.db, "pool", None)
    with pytest.raises(ValueError, match="signed 64-bit"):
        await ledger.WalletLedger.post(
            user_id=1, currency="USD", amount_minor=amount,
            kind="TOPUP", idempotency_key="validation-bigint-amount",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value,error", [
    ("order_id", 0, "order_id"),
    ("order_id", True, "order_id"),
    ("order_id", 2**63, "order_id"),
    ("provider_reference", "", "provider_reference"),
    ("provider_reference", "x" * 129, "provider_reference"),
    ("provider_reference", 123, "provider_reference"),
    ("metadata", [], "metadata"),
    ("metadata", {"bad": object()}, "metadata"),
    ("metadata", {"bad": float("nan")}, "metadata"),
])
async def test_wallet_ledger_rejects_invalid_optional_fields_before_database(monkeypatch, field, value, error):
    monkeypatch.setattr(ledger.db, "pool", None)
    kwargs = {
        "user_id": 1,
        "currency": "USD",
        "amount_minor": 100,
        "kind": "TOPUP",
        "idempotency_key": "validation-optional-fields",
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match=error):
        await ledger.WalletLedger.post(**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", [2**63, -(2**63)])
async def test_wallet_ledger_rejects_user_id_outside_positive_bigint(monkeypatch, user_id):
    monkeypatch.setattr(ledger.db, "pool", None)
    with pytest.raises(ValueError, match="user_id"):
        await ledger.WalletLedger.post(
            user_id=user_id, currency="USD", amount_minor=100,
            kind="TOPUP", idempotency_key="validation-user-bigint",
        )


@pytest.mark.asyncio
async def test_wallet_ledger_accepts_minimum_signed_bigint_amount_before_database(monkeypatch):
    class DatabaseReached(RuntimeError):
        pass

    class Pool:
        def acquire(self):
            raise DatabaseReached("validation passed; database access reached")

    monkeypatch.setattr(ledger.db, "pool", Pool())
    with pytest.raises(DatabaseReached, match="validation passed"):
        await ledger.WalletLedger.post(
            user_id=1,
            currency="USD",
            amount_minor=-(2**63),
            kind="ADJUSTMENT",
            idempotency_key="validation-min-signed-bigint",
        )
