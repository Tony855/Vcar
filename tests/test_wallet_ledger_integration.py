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
