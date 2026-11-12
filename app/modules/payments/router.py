"""Authenticated wallet views and audited administrator ledger adjustments.

No external payment provider is implied here. ABA/Bakong settlement must only be
credited after a provider-specific adapter verifies its official callback.
"""
from __future__ import annotations

import jwt
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.db.core import db
from app.modules.payments.ledger import WalletLedger, WalletError, InsufficientFunds, IdempotencyConflict, WalletBalanceLimit

router = APIRouter(prefix="/wallet", tags=["wallet"])


def _claims(authorization: str | None, allowed_roles: set[str]) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    try:
        claims = jwt.decode(authorization[7:], settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc
    role = claims.get("role")
    subject = claims.get("sub")
    if role not in allowed_roles or not subject:
        raise HTTPException(status_code=403, detail="Wallet permission denied")
    if role in {"driver", "passenger"}:
        if (
            not isinstance(subject, str)
            or not subject.isascii()
            or not subject.isdecimal()
            or not 0 < int(subject) <= 2**63 - 1
        ):
            raise HTTPException(status_code=403, detail="Wallet account identity is invalid")
    return claims


class WalletOpenRequest(BaseModel):
    currency: str = Field(pattern="^(USD|KHR)$")


class AdminAdjustment(BaseModel):
    user_id: int = Field(strict=True, gt=0, le=2**63 - 1)
    currency: str = Field(pattern="^(USD|KHR)$")
    amount_minor: int = Field(strict=True, ge=-(2**63), le=2**63 - 1)
    reason: str = Field(min_length=8, max_length=300)
    idempotency_key: str = Field(min_length=12, max_length=128)

    @field_validator("amount_minor")
    @classmethod
    def amount_must_not_be_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("amount_minor must not be zero")
        return value

    @field_validator("reason", "idempotency_key", mode="before")
    @classmethod
    def reject_blank_audit_fields(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("audit fields must not be blank")
        return value.strip()


@router.post("/me", status_code=200)
async def open_or_get_wallet(
    request: WalletOpenRequest,
    authorization: str | None = Header(default=None),
):
    claims = _claims(authorization, {"driver", "passenger"})
    async with db.pool.acquire() as conn:
        user_id = await conn.fetchval("SELECT id FROM users WHERE telegram_id=$1", int(claims["sub"]))
        if user_id is None:
            raise HTTPException(status_code=404, detail="Account not found")
        await conn.execute(
            """
            INSERT INTO wallet_accounts (user_id, currency)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id, request.currency,
        )
        wallet = await conn.fetchrow(
            "SELECT id, currency, balance_minor, created_at FROM wallet_accounts WHERE user_id=$1",
            user_id,
        )
    if wallet["currency"] != request.currency:
        raise HTTPException(status_code=409, detail="Wallet already exists in another currency")
    return dict(wallet)


@router.get("/me/transactions")
async def my_wallet_transactions(
    authorization: str | None = Header(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    before_id: int | None = Query(default=None, gt=0),
):
    claims = _claims(authorization, {"driver", "passenger"})
    async with db.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT t.id, t.order_id, t.kind, t.amount_minor, t.currency,
                   t.provider_reference, t.created_at
            FROM wallet_transactions t
            JOIN wallet_accounts w ON w.id=t.wallet_id
            JOIN users u ON u.id=w.user_id
            WHERE u.telegram_id=$1 AND ($2::bigint IS NULL OR t.id<$2)
            ORDER BY t.id DESC LIMIT $3
            """,
            int(claims["sub"]), before_id, limit,
        )
    return [dict(row) for row in rows]


@router.post("/admin/adjust")
async def admin_wallet_adjustment(
    request: AdminAdjustment,
    authorization: str | None = Header(default=None),
):
    claims = _claims(authorization, {"admin"})
    try:
        result = await WalletLedger.post(
            user_id=request.user_id,
            currency=request.currency,
            amount_minor=request.amount_minor,
            kind="ADMIN_ADJUSTMENT",
            idempotency_key=request.idempotency_key,
            metadata={
                "reason": request.reason,
                "source": "admin",
                "admin_subject": str(claims["sub"]),
            },
        )
    except InsufficientFunds as exc:
        raise HTTPException(status_code=409, detail="Wallet balance cannot become negative") from exc
    except WalletBalanceLimit as exc:
        raise HTTPException(status_code=409, detail="Wallet balance exceeds the supported maximum") from exc
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail="Idempotency key conflict") from exc
    except WalletError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "transaction_id": result.transaction_id,
        "wallet_id": result.wallet_id,
        "balance_minor": result.balance_minor,
        "replayed": result.replayed,
    }
