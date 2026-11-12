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


class WalletBalanceLimit(WalletError):
    pass


class IdempotencyConflict(WalletError):
    pass


@dataclass(frozen=True)
class LedgerResult:
    transaction_id: int
    wallet_id: int
    balance_minor: int
    replayed: bool


@dataclass(frozen=True)
class LedgerBatchResult:
    batch_key: str
    transactions: tuple[LedgerResult, ...]
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
            or not -(2**63) <= amount_minor <= bigint_max
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
                    "SELECT id, currency, balance_minor FROM wallet_accounts WHERE user_id=$1 AND currency=$2 FOR UPDATE",
                    user_id,
                    currency.upper(),
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
                # PostgreSQL BIGINT is signed 64-bit. Reject a credit that
                # would overflow before issuing arithmetic in the UPDATE.
                if amount_minor > 0 and wallet["balance_minor"] > bigint_max - amount_minor:
                    raise WalletBalanceLimit("Wallet balance exceeds the supported maximum")
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


    @staticmethod
    async def post_balanced_batch(
        *,
        currency: str,
        legs: list[dict],
        idempotency_key: str,
        order_id: int | None = None,
    ) -> LedgerBatchResult:
        """Post an explicitly balanced set of wallet legs in one transaction.

        Each leg is {user_id, amount_minor, kind, metadata?}. Positive values
        credit and negative values debit. This primitive does not calculate
        fares, commissions, taxes, refunds, or payout shares; callers must
        supply an approved, zero-sum allocation in one currency.
        """
        bigint_max = 2**63 - 1
        if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise ValueError("currency must be a three-letter ASCII code")
        currency = currency.upper()
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key.strip()) > 110:
            raise ValueError("idempotency_key must contain 1 to 110 non-blank characters")
        idempotency_key = idempotency_key.strip()
        if not isinstance(legs, list) or not 2 <= len(legs) <= 32:
            raise ValueError("legs must contain between 2 and 32 entries")
        if order_id is not None and (
            not isinstance(order_id, int) or isinstance(order_id, bool)
            or not 0 < order_id <= bigint_max
        ):
            raise ValueError("order_id must be a positive signed 64-bit integer")

        normalized = []
        seen_users = set()
        total = 0
        for index, leg in enumerate(legs):
            if not isinstance(leg, dict):
                raise ValueError("each leg must be an object")
            user_id = leg.get("user_id")
            amount = leg.get("amount_minor")
            kind = leg.get("kind")
            metadata = leg.get("metadata", {})
            if not isinstance(user_id, int) or isinstance(user_id, bool) or not 0 < user_id <= bigint_max:
                raise ValueError("each leg user_id must be a positive signed 64-bit integer")
            if user_id in seen_users:
                raise ValueError("each wallet may appear only once in a batch")
            seen_users.add(user_id)
            if not isinstance(amount, int) or isinstance(amount, bool) or amount == 0 or not -(2**63) <= amount <= bigint_max:
                raise ValueError("each leg amount_minor must be a non-zero signed 64-bit integer")
            if not isinstance(kind, str) or not kind.strip() or len(kind.strip()) > 32:
                raise ValueError("each leg kind must contain 1 to 32 characters")
            if not isinstance(metadata, dict) or "_batch_key" in metadata or "_batch_index" in metadata:
                raise ValueError("leg metadata must be an object without reserved batch keys")
            try:
                json.dumps(metadata, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError("leg metadata must contain finite JSON values") from exc
            total += amount
            normalized.append({
                "user_id": user_id,
                "amount_minor": amount,
                "kind": kind.strip(),
                "metadata": metadata,
                "idempotency_key": f"{idempotency_key}:{index}",
            })
        if total != 0:
            raise ValueError("balanced batch legs must sum to zero")

        async with db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    idempotency_key,
                )
                user_ids = [leg["user_id"] for leg in normalized]
                wallets = await conn.fetch(
                    """SELECT id, user_id, currency, balance_minor
                       FROM wallet_accounts
                       WHERE user_id=ANY($1::bigint[]) AND currency=$2
                       ORDER BY id FOR UPDATE""",
                    user_ids, currency,
                )
                wallet_by_user = {row["user_id"]: row for row in wallets}
                if len(wallet_by_user) != len(normalized):
                    raise WalletError("One or more wallet accounts do not exist")

                keys = [leg["idempotency_key"] for leg in normalized]
                existing = await conn.fetch(
                    """SELECT t.id, t.wallet_id, t.idempotency_key, t.amount_minor, t.currency, t.kind,
                              t.order_id, t.provider_reference, t.metadata, w.user_id
                       FROM wallet_transactions t
                       JOIN wallet_accounts w ON w.id=t.wallet_id
                       WHERE t.idempotency_key=ANY($1::text[])
                       ORDER BY t.id""",
                    keys,
                )
                if existing:
                    if len(existing) != len(normalized):
                        raise IdempotencyConflict("Wallet batch is only partially present")
                    existing_by_key = {}
                    for row in existing:
                        # The per-leg key is deterministic and unique in the ledger.
                        existing_by_key[row["idempotency_key"]] = row
                    # Query aliases the key explicitly below for compatibility with asyncpg Records.
                    results = []
                    for index, leg in enumerate(normalized):
                        previous = existing_by_key.get(leg["idempotency_key"])
                        expected_metadata = {**leg["metadata"], "_batch_key": idempotency_key,
                                             "_batch_index": index}
                        if (
                            not previous
                            or previous["user_id"] != leg["user_id"]
                            or previous["amount_minor"] != leg["amount_minor"]
                            or previous["currency"] != currency
                            or previous["kind"] != leg["kind"]
                            or previous["order_id"] != order_id
                            or previous["provider_reference"] is not None
                            or _canonical_metadata(previous["metadata"]) != _canonical_metadata(expected_metadata)
                        ):
                            raise IdempotencyConflict("Idempotency key was already used for a different wallet batch")
                        wallet = wallet_by_user[leg["user_id"]]
                        results.append(LedgerResult(
                            previous["id"], wallet["id"], wallet["balance_minor"], True
                        ))
                    return LedgerBatchResult(idempotency_key, tuple(results), True)

                results = []
                for index, leg in enumerate(normalized):
                    wallet = wallet_by_user[leg["user_id"]]
                    amount = leg["amount_minor"]
                    balance = wallet["balance_minor"]
                    if amount > 0 and balance > bigint_max - amount:
                        raise WalletBalanceLimit("Wallet balance exceeds the supported maximum")
                    if amount < 0 and balance < -amount:
                        raise InsufficientFunds("Wallet balance would become negative")
                    updated = await conn.fetchrow(
                        """UPDATE wallet_accounts SET balance_minor=balance_minor+$1
                           WHERE id=$2 AND balance_minor+$1 >= 0 RETURNING balance_minor""",
                        amount, wallet["id"],
                    )
                    if not updated:
                        raise InsufficientFunds("Wallet balance would become negative")
                    metadata = {**leg["metadata"], "_batch_key": idempotency_key, "_batch_index": index}
                    row = await conn.fetchrow(
                        """INSERT INTO wallet_transactions
                           (wallet_id, order_id, kind, amount_minor, currency,
                            idempotency_key, provider_reference, metadata)
                           VALUES ($1,$2,$3,$4,$5,$6,NULL,$7::jsonb) RETURNING id""",
                        wallet["id"], order_id, leg["kind"], amount, currency,
                        leg["idempotency_key"], json.dumps(metadata),
                    )
                    wallet_by_user[leg["user_id"]] = {
                        **dict(wallet), "balance_minor": updated["balance_minor"]
                    }
                    results.append(LedgerResult(row["id"], wallet["id"], updated["balance_minor"], False))
                return LedgerBatchResult(idempotency_key, tuple(results), False)
