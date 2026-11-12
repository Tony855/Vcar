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


def _canonical_metadata(value) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    return json.dumps(value or {}, sort_keys=True, separators=(",", ":"))


def _request_matches(
    previous,
    *,
    user_id: int,
    amount_minor: int,
    currency: str,
    kind: str,
    order_id: int | None,
    provider_reference: str | None,
    metadata: dict | None,
) -> bool:
    return (
        previous["user_id"] == user_id
        and previous["amount_minor"] == amount_minor
        and previous["currency"] == currency.upper()
        and previous["kind"] == kind
        and previous["order_id"] == order_id
        and previous["provider_reference"] == provider_reference
        and _canonical_metadata(previous["metadata"]) == _canonical_metadata(metadata)
    )


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
        bigint_max = 2**63 - 1
        if (
            not isinstance(amount_minor, int)
            or isinstance(amount_minor, bool)
            or amount_minor == 0
            or not -bigint_max <= amount_minor <= bigint_max
        ):
            raise ValueError("amount_minor must be a non-zero signed 64-bit integer")
        if (
            not isinstance(user_id, int)
            or isinstance(user_id, bool)
            or not 0 < user_id <= bigint_max
        ):
            raise ValueError("user_id must be a positive 64-bit integer")
        if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise ValueError("currency must be a three-letter ASCII code")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 128:
            raise ValueError("a valid idempotency_key is required")
        if not isinstance(kind, str) or not kind.strip() or len(kind) > 32:
            raise ValueError("a valid transaction kind is required")
        if order_id is not None and (
            not isinstance(order_id, int)
            or isinstance(order_id, bool)
            or not 0 < order_id <= bigint_max
        ):
            raise ValueError("order_id must be a positive 64-bit integer")
        if provider_reference is not None and (
            not isinstance(provider_reference, str)
            or not provider_reference.strip()
            or len(provider_reference) > 128
        ):
            raise ValueError("provider_reference must be a non-blank string of at most 128 characters")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        try:
            json.dumps(metadata or {}, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must contain only finite JSON values") from exc

        async with db.pool.acquire() as conn:
            async with conn.transaction():
                # Serialize reuse of an idempotency key even when callers target different wallets.
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", idempotency_key)
                previous = await conn.fetchrow(
                    "SELECT t.id, t.wallet_id, t.amount_minor, t.currency, t.kind, t.order_id, t.provider_reference, t.metadata, w.user_id FROM wallet_transactions t JOIN wallet_accounts w ON w.id=t.wallet_id WHERE t.idempotency_key=$1",
                    idempotency_key,
                )
                if previous:
                    if not _request_matches(
                        previous,
                        user_id=user_id,
                        amount_minor=amount_minor,
                        currency=currency,
                        kind=kind,
                        order_id=order_id,
                        provider_reference=provider_reference,
                        metadata=metadata,
                    ):
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
                    "SELECT t.id, t.wallet_id, t.amount_minor, t.currency, t.kind, t.order_id, t.provider_reference, t.metadata, w.user_id FROM wallet_transactions t JOIN wallet_accounts w ON w.id=t.wallet_id WHERE t.idempotency_key=$1",
                    idempotency_key,
                )
                if previous:
                    if (
                        previous["wallet_id"] != wallet["id"]
                        or not _request_matches(
                            previous,
                            user_id=user_id,
                            amount_minor=amount_minor,
                            currency=currency,
                            kind=kind,
                            order_id=order_id,
                            provider_reference=provider_reference,
                            metadata=metadata,
                        )
                    ):
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
