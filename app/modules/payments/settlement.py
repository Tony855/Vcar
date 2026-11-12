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
