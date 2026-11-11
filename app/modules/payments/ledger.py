"""Atomic, idempotent wallet ledger operations.

Payment provider callbacks must be verified by the provider adapter before calling
these methods. Amounts are signed integer minor units; floating-point money is never used.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.db.core import db


class WalletError(Exception):
    pass


class InsufficientFunds(WalletError):
    pass


class IdempotencyConflict(WalletError):
    pass


@dataclass(frozen=True)
class LedgerResult:
    transaction_id: int
    wallet_id: int
    balance_minor: int
    replayed: bool


class WalletLedger:
    @staticmethod
    async def post(
        *,
        user_id: int,
        currency: str,
        amount_minor: int,
        kind: str,
        idempotency_key: str,
        order_id: int | None = None,
        provider_reference: str | None = None,
        metadata: dict | None = None,
    ) -> LedgerResult:
        if not isinstance(amount_minor, int) or isinstance(amount_minor, bool) or amount_minor == 0:
            raise ValueError("amount_minor must be a non-zero integer")
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("currency must be a three-letter code")
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("a valid idempotency_key is required")
        if not kind or len(kind) > 32:
            raise ValueError("a valid transaction kind is required")

        async with db.pool.acquire() as conn:
            async with conn.transaction():
                previous = await conn.fetchrow(
                    "SELECT id, wallet_id, amount_minor, currency FROM wallet_transactions WHERE idempotency_key=$1",
                    idempotency_key,
                )
                if previous:
                    if (previous["amount_minor"] != amount_minor or
                        previous["currency"] != currency.upper()):
                        raise IdempotencyConflict("Idempotency key was already used for a different payment")
                    balance = await conn.fetchval(
                        "SELECT balance_minor FROM wallet_accounts WHERE id=$1",
                        previous["wallet_id"],
                    )
                    return LedgerResult(previous["id"], previous["wallet_id"], balance, True)

                wallet = await conn.fetchrow(
                    "SELECT id, currency, balance_minor FROM wallet_accounts WHERE user_id=$1 FOR UPDATE",
                    user_id,
                )
                if not wallet:
                    raise WalletError("Wallet account does not exist")
                previous = await conn.fetchrow(
                    "SELECT id, wallet_id, amount_minor, currency FROM wallet_transactions WHERE idempotency_key=$1",
                    idempotency_key,
                )
                if previous:
                    if (previous["wallet_id"] != wallet["id"] or previous["amount_minor"] != amount_minor or previous["currency"] != currency.upper()):
                        raise IdempotencyConflict("Idempotency key was already used for a different payment")
                    balance = await conn.fetchval("SELECT balance_minor FROM wallet_accounts WHERE id=$1", wallet["id"])
                    return LedgerResult(previous["id"], wallet["id"], balance, True)
                if wallet["currency"] != currency.upper():
                    raise WalletError("Wallet currency mismatch")
                updated = await conn.fetchrow(
                    """
                    UPDATE wallet_accounts
                    SET balance_minor=balance_minor + $1
                    WHERE id=$2 AND balance_minor + $1 >= 0
                    RETURNING balance_minor
                    """,
                    amount_minor, wallet["id"],
                )
                if not updated:
                    raise InsufficientFunds("Wallet balance would become negative")
                row = await conn.fetchrow(
                    """
                    INSERT INTO wallet_transactions
                        (wallet_id, order_id, kind, amount_minor, currency,
                         idempotency_key, provider_reference, metadata)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
                    RETURNING id
                    """,
                    wallet["id"], order_id, kind, amount_minor, currency.upper(),
                    idempotency_key, provider_reference, json.dumps(metadata or {}),
                )
                return LedgerResult(row["id"], wallet["id"], updated["balance_minor"], False)
