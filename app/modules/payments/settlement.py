"""Idempotent settlement preparation for completed orders.

This module records a reviewed policy breakdown; it does not move money. Posting
wallet entries is deliberately separate until the merchant's payment source,
commission, rounding, refund and payout policies are configured and approved.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.db.core import db
from app.modules.payments.ledger import LedgerBatchResult, WalletError, WalletLedger


class SettlementError(Exception):
    """Base error for settlement preparation."""


class SettlementConflict(SettlementError):
    """An order or idempotency key already has a different settlement snapshot."""


class OrderNotSettleable(SettlementError):
    """Only completed orders can have a settlement snapshot prepared."""


@dataclass(frozen=True)
class SettlementResult:
    settlement_id: int
    order_id: int
    currency: str
    policy_version: str
    components: dict[str, int]
    status: str
    replayed: bool


def _validate_components(components: dict[str, int]) -> dict[str, int]:
    if not isinstance(components, dict) or not components or len(components) > 32:
        raise ValueError("components must be a non-empty object with at most 32 entries")
    normalized = {}
    for name, amount in components.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", name):
            raise ValueError("component names must be lowercase identifiers")
        if not isinstance(amount, int) or isinstance(amount, bool) or not 0 <= amount <= 2**63 - 1:
            raise ValueError("component amounts must be non-negative signed 64-bit integers")
        normalized[name] = amount
    return dict(sorted(normalized.items()))


class OrderSettlement:
    @staticmethod
    async def prepare(
        *, order_id: int, currency: str, policy_version: str,
        idempotency_key: str, components: dict[str, int],
    ) -> SettlementResult:
        if not isinstance(order_id, int) or isinstance(order_id, bool) or not 0 < order_id <= 2**63 - 1:
            raise ValueError("order_id must be a positive signed 64-bit integer")
        if not isinstance(currency, str) or currency not in {"USD", "KHR"}:
            raise ValueError("currency must be USD or KHR")
        if not isinstance(policy_version, str) or not policy_version.strip() or len(policy_version.strip()) > 64:
            raise ValueError("policy_version must contain 1 to 64 characters")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key.strip()) > 128:
            raise ValueError("idempotency_key must contain 1 to 128 characters")
        policy_version = policy_version.strip()
        idempotency_key = idempotency_key.strip()
        normalized = _validate_components(components)
        canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        async with db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    idempotency_key,
                )
                order = await conn.fetchrow(
                    "SELECT id, status FROM orders WHERE id=$1 FOR UPDATE", order_id
                )
                if not order or order["status"] != "COMPLETED":
                    raise OrderNotSettleable("order must exist and be COMPLETED")
                previous = await conn.fetchrow(
                    """SELECT id, order_id, currency, policy_version, idempotency_key,
                              components, status
                       FROM order_settlements
                       WHERE order_id=$1 OR idempotency_key=$2""",
                    order_id, idempotency_key,
                )
                if previous:
                    old_components = previous["components"]
                    if isinstance(old_components, str):
                        old_components = json.loads(old_components)
                    old_canonical = json.dumps(old_components, sort_keys=True, separators=(",", ":"))
                    if (
                        previous["order_id"] != order_id
                        or previous["currency"] != currency
                        or previous["policy_version"] != policy_version
                        or previous["idempotency_key"] != idempotency_key
                        or old_canonical != canonical
                    ):
                        raise SettlementConflict("order already has a different settlement snapshot")
                    return SettlementResult(
                        previous["id"], order_id, currency, policy_version,
                        normalized, previous["status"], True,
                    )
                row = await conn.fetchrow(
                    """INSERT INTO order_settlements
                       (order_id, currency, policy_version, idempotency_key, components)
                       VALUES ($1, $2, $3, $4, $5::jsonb)
                       RETURNING id, status""",
                    order_id, currency, policy_version, idempotency_key, canonical,
                )
                return SettlementResult(
                    row["id"], order_id, currency, policy_version,
                    normalized, row["status"], False,
                )


@dataclass(frozen=True)
class SettlementPostingResult:
    settlement_id: int
    order_id: int
    status: str
    ledger_batch: LedgerBatchResult
    replayed: bool


async def post_explicit_allocation(
    *,
    settlement_id: int,
    legs: list[dict],
) -> SettlementPostingResult:
    """Post a caller-approved, zero-sum wallet allocation for a prepared settlement.

    This is a recoverable two-transaction workflow: the wallet batch has its own
    atomic transaction and a deterministic idempotency key; only after that
    batch commits is the immutable settlement marked POSTED. If the process
    stops between those commits, retrying replays the exact wallet batch and
    completes the status transition without moving money twice.

    The caller must supply every wallet leg explicitly. This function never
    derives fare splits, driver earnings, commissions, taxes, refunds or payouts
    from the snapshot components.
    """
    if (
        not isinstance(settlement_id, int)
        or isinstance(settlement_id, bool)
        or not 0 < settlement_id <= 2**63 - 1
    ):
        raise ValueError("settlement_id must be a positive signed 64-bit integer")
    if not isinstance(legs, list):
        raise ValueError("legs must be an explicit list of wallet allocations")

    async with db.pool.acquire() as conn:
        settlement = await conn.fetchrow(
            """SELECT id, order_id, currency, policy_version, status
               FROM order_settlements WHERE id=$1""",
            settlement_id,
        )
    if not settlement:
        raise SettlementError("settlement snapshot does not exist")
    if settlement["status"] not in {"PREPARED", "POSTED"}:
        raise SettlementError("only PREPARED or already POSTED settlements can be posted")

    # Bind every ledger leg to this exact reviewed snapshot. A retry with
    # altered amounts, recipients, kinds or metadata conflicts in the ledger.
    bound_legs = []
    for leg in legs:
        if not isinstance(leg, dict):
            raise ValueError("each allocation leg must be an object")
        metadata = leg.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("allocation leg metadata must be an object")
        if "_settlement_id" in metadata or "_policy_version" in metadata:
            raise ValueError("allocation metadata uses reserved settlement keys")
        bound_legs.append({
            **leg,
            "metadata": {
                **metadata,
                "_settlement_id": settlement_id,
                "_policy_version": settlement["policy_version"],
            },
        })

    batch = await WalletLedger.post_balanced_batch(
        currency=settlement["currency"],
        legs=bound_legs,
        idempotency_key=f"settlement:{settlement_id}",
        order_id=settlement["order_id"],
    )

    async with db.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """UPDATE order_settlements SET status='POSTED'
                   WHERE id=$1 AND status='PREPARED'""",
                settlement_id,
            )
            final_status = await conn.fetchval(
                "SELECT status FROM order_settlements WHERE id=$1",
                settlement_id,
            )
    if final_status != "POSTED":
        # The ledger batch remains safely replayable under its deterministic
        # key; do not report a successful settlement state transition.
        raise SettlementError("wallet batch committed but settlement status is not POSTED")

    return SettlementPostingResult(
        settlement_id=settlement_id,
        order_id=settlement["order_id"],
        status=final_status,
        ledger_batch=batch,
        replayed=batch.replayed,
    )
