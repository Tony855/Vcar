import asyncio
import os
import uuid

import asyncpg
import pytest

from app.db.core import db
from app.modules.payments.ledger import WalletLedger, InsufficientFunds, IdempotencyConflict


@pytest.fixture
async def database_pool(monkeypatch):
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is not configured")
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    monkeypatch.setattr(db, "pool", pool)
    yield pool
    await pool.close()


@pytest.mark.asyncio
async def test_wallet_ledger_is_atomic_and_idempotent(database_pool):
    telegram_id = int(uuid.uuid4().int % 8_000_000_000) + 1
    async with database_pool.acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO users (telegram_id, first_name) VALUES ($1, 'wallet-test') RETURNING id",
            telegram_id,
        )
        wallet_id = await conn.fetchval(
            "INSERT INTO wallet_accounts (user_id, currency, balance_minor) VALUES ($1, 'USD', 0) RETURNING id",
            user_id,
        )

    key = f"wallet-test-{uuid.uuid4()}"
    try:
        results = await asyncio.gather(*[
            WalletLedger.post(
                user_id=user_id, currency="USD", amount_minor=1250,
                kind="TOPUP", idempotency_key=key,
            )
            for _ in range(2)
        ])
        assert results[0].transaction_id == results[1].transaction_id
        assert sorted([results[0].replayed, results[1].replayed]) == [False, True]
        async with database_pool.acquire() as conn:
            balance = await conn.fetchval("SELECT balance_minor FROM wallet_accounts WHERE id=$1", wallet_id)
            count = await conn.fetchval("SELECT COUNT(*) FROM wallet_transactions WHERE idempotency_key=$1", key)
        assert balance == 1250
        assert count == 1

        with pytest.raises(IdempotencyConflict):
            await WalletLedger.post(
                user_id=user_id, currency="USD", amount_minor=1000,
                kind="TOPUP", idempotency_key=key,
            )
        with pytest.raises(InsufficientFunds):
            await WalletLedger.post(
                user_id=user_id, currency="USD", amount_minor=-2000,
                kind="WITHDRAWAL", idempotency_key=f"{key}-debit",
            )
    finally:
        async with database_pool.acquire() as conn:
            await conn.execute("DELETE FROM wallet_transactions WHERE wallet_id=$1", wallet_id)
            await conn.execute("DELETE FROM wallet_accounts WHERE id=$1", wallet_id)
            await conn.execute("DELETE FROM users WHERE id=$1", user_id)


@pytest.mark.asyncio
async def test_postgis_driver_location_schema(database_pool):
    async with database_pool.acquire() as conn:
        version = await conn.fetchval("SELECT PostGIS_Version()")
        assert version
        columns = await conn.fetch(
            """
            SELECT column_name, udt_name
            FROM information_schema.columns
            WHERE table_name='driver_locations'
            """
        )
    names = {row["column_name"] for row in columns}
    assert {"position", "accuracy_m", "heading_deg", "speed_mps", "recorded_at", "received_at"} <= names



@pytest.mark.asyncio
async def test_idempotency_key_cannot_be_replayed_into_another_wallet(database_pool):
    async with database_pool.acquire() as conn:
        user_ids = []
        wallet_ids = []
        for _ in range(2):
            telegram_id = int(uuid.uuid4().int % 8_000_000_000) + 1
            user_id = await conn.fetchval(
                "INSERT INTO users (telegram_id, first_name) VALUES ($1, 'wallet-cross-user') RETURNING id",
                telegram_id,
            )
            wallet_id = await conn.fetchval(
                "INSERT INTO wallet_accounts (user_id, currency, balance_minor) VALUES ($1, 'USD', 0) RETURNING id",
                user_id,
            )
            user_ids.append(user_id)
            wallet_ids.append(wallet_id)

    key = f"wallet-cross-user-{uuid.uuid4()}"
    try:
        await WalletLedger.post(
            user_id=user_ids[0], currency="USD", amount_minor=500,
            kind="TOPUP", idempotency_key=key,
        )
        with pytest.raises(IdempotencyConflict):
            await WalletLedger.post(
                user_id=user_ids[1], currency="USD", amount_minor=500,
                kind="TOPUP", idempotency_key=key,
            )
        async with database_pool.acquire() as conn:
            balances = await conn.fetch(
                "SELECT balance_minor FROM wallet_accounts WHERE id = ANY($1) ORDER BY id",
                wallet_ids,
            )
        assert [row["balance_minor"] for row in balances] == [500, 0]
    finally:
        async with database_pool.acquire() as conn:
            await conn.execute("DELETE FROM wallet_transactions WHERE wallet_id = ANY($1)", wallet_ids)
            await conn.execute("DELETE FROM wallet_accounts WHERE id = ANY($1)", wallet_ids)
            await conn.execute("DELETE FROM users WHERE id = ANY($1)", user_ids)

@pytest.mark.asyncio
async def test_concurrent_cross_wallet_idempotency_key_cannot_double_credit(database_pool):
    async with database_pool.acquire() as conn:
        user_ids = []
        wallet_ids = []
        for _ in range(2):
            telegram_id = int(uuid.uuid4().int % 8_000_000_000) + 1
            user_id = await conn.fetchval(
                "INSERT INTO users (telegram_id, first_name) VALUES ($1, 'wallet-race') RETURNING id",
                telegram_id,
            )
            wallet_id = await conn.fetchval(
                "INSERT INTO wallet_accounts (user_id, currency, balance_minor) VALUES ($1, 'USD', 0) RETURNING id",
                user_id,
            )
            user_ids.append(user_id)
            wallet_ids.append(wallet_id)

    key = f"wallet-cross-wallet-race-{uuid.uuid4()}"
    try:
        outcomes = await asyncio.gather(
            *[
                WalletLedger.post(
                    user_id=user_id, currency="USD", amount_minor=700,
                    kind="TOPUP", idempotency_key=key,
                )
                for user_id in user_ids
            ],
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in outcomes) == 1
        assert sum(isinstance(result, IdempotencyConflict) for result in outcomes) == 1
        async with database_pool.acquire() as conn:
            balances = await conn.fetch(
                "SELECT balance_minor FROM wallet_accounts WHERE id = ANY($1) ORDER BY id",
                wallet_ids,
            )
            transaction_count = await conn.fetchval(
                "SELECT COUNT(*) FROM wallet_transactions WHERE idempotency_key=$1",
                key,
            )
        assert sorted(row["balance_minor"] for row in balances) == [0, 700]
        assert transaction_count == 1
    finally:
        async with database_pool.acquire() as conn:
            await conn.execute("DELETE FROM wallet_transactions WHERE wallet_id = ANY($1)", wallet_ids)
            await conn.execute("DELETE FROM wallet_accounts WHERE id = ANY($1)", wallet_ids)
            await conn.execute("DELETE FROM users WHERE id = ANY($1)", user_ids)


@pytest.mark.asyncio
async def test_idempotency_replay_requires_same_provider_reference_and_audit_metadata(database_pool):
    telegram_id = int(uuid.uuid4().int % 8_000_000_000) + 1
    async with database_pool.acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO users (telegram_id, first_name) VALUES ($1, 'wallet-idempotency') RETURNING id",
            telegram_id,
        )
        wallet_id = await conn.fetchval(
            "INSERT INTO wallet_accounts (user_id, currency, balance_minor) VALUES ($1, 'USD', 0) RETURNING id",
            user_id,
        )

    key = f"wallet-provider-{uuid.uuid4()}"
    try:
        first = await WalletLedger.post(
            user_id=user_id,
            currency="USD",
            amount_minor=1500,
            kind="TOPUP",
            idempotency_key=key,
            provider_reference="provider-payment-001",
            metadata={"provider": "sandbox", "merchant": "merchant-a"},
        )
        replay = await WalletLedger.post(
            user_id=user_id,
            currency="USD",
            amount_minor=1500,
            kind="TOPUP",
            idempotency_key=key,
            provider_reference="provider-payment-001",
            metadata={"merchant": "merchant-a", "provider": "sandbox"},
        )
        assert replay.replayed is True
        assert replay.transaction_id == first.transaction_id

        with pytest.raises(IdempotencyConflict):
            await WalletLedger.post(
                user_id=user_id,
                currency="USD",
                amount_minor=1500,
                kind="TOPUP",
                idempotency_key=key,
                provider_reference="provider-payment-002",
                metadata={"provider": "sandbox", "merchant": "merchant-a"},
            )
        with pytest.raises(IdempotencyConflict):
            await WalletLedger.post(
                user_id=user_id,
                currency="USD",
                amount_minor=1500,
                kind="TOPUP",
                idempotency_key=key,
                provider_reference="provider-payment-001",
                metadata={"provider": "sandbox", "merchant": "merchant-b"},
            )

        async with database_pool.acquire() as conn:
            balance = await conn.fetchval(
                "SELECT balance_minor FROM wallet_accounts WHERE id=$1", wallet_id
            )
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM wallet_transactions WHERE idempotency_key=$1", key
            )
        assert balance == 1500
        assert count == 1
    finally:
        async with database_pool.acquire() as conn:
            await conn.execute("DELETE FROM wallet_transactions WHERE wallet_id=$1", wallet_id)
            await conn.execute("DELETE FROM wallet_accounts WHERE id=$1", wallet_id)
            await conn.execute("DELETE FROM users WHERE id=$1", user_id)


@pytest.mark.asyncio
async def test_wallet_ledger_rolls_back_balance_when_transaction_insert_fails(database_pool):
    """A failed ledger insert must roll back the preceding balance update."""
    telegram_id = int(uuid.uuid4().int % 8_000_000_000) + 1
    async with database_pool.acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO users (telegram_id, first_name) VALUES ($1, 'wallet-rollback') RETURNING id",
            telegram_id,
        )
        wallet_id = await conn.fetchval(
            "INSERT INTO wallet_accounts (user_id, currency, balance_minor) VALUES ($1, 'USD', 1000) RETURNING id",
            user_id,
        )

    suffix = uuid.uuid4().hex[:12]
    function_name = f"vcar_fail_wallet_insert_{suffix}"
    trigger_name = f"vcar_fail_wallet_insert_trigger_{suffix}"
    key = f"wallet-rollback-{uuid.uuid4()}"
    async with database_pool.acquire() as conn:
        await conn.execute(
            f"""
            CREATE FUNCTION {function_name}() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'injected wallet ledger insert failure';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        await conn.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON wallet_transactions "
            f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
        )
    try:
        with pytest.raises(Exception, match="injected wallet ledger insert failure"):
            await WalletLedger.post(
                user_id=user_id,
                currency="USD",
                amount_minor=-300,
                kind="WITHDRAWAL",
                idempotency_key=key,
            )
        async with database_pool.acquire() as conn:
            balance = await conn.fetchval(
                "SELECT balance_minor FROM wallet_accounts WHERE id=$1", wallet_id
            )
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM wallet_transactions WHERE idempotency_key=$1", key
            )
        assert balance == 1000
        assert count == 0
    finally:
        async with database_pool.acquire() as conn:
            await conn.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON wallet_transactions")
            await conn.execute(f"DROP FUNCTION IF EXISTS {function_name}()")
            await conn.execute("DELETE FROM wallet_transactions WHERE wallet_id=$1", wallet_id)
            await conn.execute("DELETE FROM wallet_accounts WHERE id=$1", wallet_id)
            await conn.execute("DELETE FROM users WHERE id=$1", user_id)


@pytest.mark.asyncio
async def test_concurrent_wallet_debits_cannot_overdraw_balance(database_pool):
    """Row locking and the conditional update allow only affordable concurrent debits."""
    telegram_id = int(uuid.uuid4().int % 8_000_000_000) + 1
    async with database_pool.acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO users (telegram_id, first_name) VALUES ($1, 'wallet-debit-race') RETURNING id",
            telegram_id,
        )
        wallet_id = await conn.fetchval(
            "INSERT INTO wallet_accounts (user_id, currency, balance_minor) VALUES ($1, 'USD', 1000) RETURNING id",
            user_id,
        )

    keys = [f"wallet-debit-race-{uuid.uuid4()}" for _ in range(2)]
    try:
        outcomes = await asyncio.gather(
            *[
                WalletLedger.post(
                    user_id=user_id,
                    currency="USD",
                    amount_minor=-700,
                    kind="WITHDRAWAL",
                    idempotency_key=key,
                )
                for key in keys
            ],
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in outcomes) == 1
        assert sum(isinstance(result, InsufficientFunds) for result in outcomes) == 1
        async with database_pool.acquire() as conn:
            balance = await conn.fetchval(
                "SELECT balance_minor FROM wallet_accounts WHERE id=$1", wallet_id
            )
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM wallet_transactions WHERE wallet_id=$1", wallet_id
            )
        assert balance == 300
        assert count == 1
    finally:
        async with database_pool.acquire() as conn:
            await conn.execute("DELETE FROM wallet_transactions WHERE wallet_id=$1", wallet_id)
            await conn.execute("DELETE FROM wallet_accounts WHERE id=$1", wallet_id)
            await conn.execute("DELETE FROM users WHERE id=$1", user_id)
