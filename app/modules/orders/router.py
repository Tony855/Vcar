"""Authenticated driver order lifecycle endpoints."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.security import verify_driver_token
from app.db.core import db
from app.db.repositories.order_repo import OrderRepository

router = APIRouter(prefix="/driver/orders", tags=["driver-orders"])


class DriverOrderStatusUpdate(BaseModel):
    status: Literal["ARRIVED", "IN_PROGRESS", "COMPLETED"]


async def _approved_driver(telegram_id: int) -> int:
    async with db.pool.acquire() as connection:
        driver = await connection.fetchrow(
            "SELECT id, driver_status FROM users WHERE telegram_id=$1",
            telegram_id,
        )
    if not driver or driver["driver_status"] != "APPROVED":
        raise HTTPException(status_code=403, detail="Approved driver account required")
    return int(driver["id"])


@router.post("/{order_id}/accept")
async def accept_driver_order(order_id: int, claims: dict = Depends(verify_driver_token)):
    driver_id = await _approved_driver(int(claims["sub"]))
    accepted = await OrderRepository.accept(order_id, driver_id)
    if not accepted:
        raise HTTPException(status_code=409, detail="Order is no longer available or driver is busy")
    return {"order_id": order_id, "status": "ACCEPTED"}


@router.patch("/{order_id}/status")
async def update_driver_order_status(
    order_id: int,
    request: DriverOrderStatusUpdate,
    claims: dict = Depends(verify_driver_token),
):
    driver_id = await _approved_driver(int(claims["sub"]))
    order = await OrderRepository.get(order_id)
    if not order or order.get("driver_id") != driver_id:
        raise HTTPException(status_code=404, detail="Order not found")

    current_status = order.get("status")
    allowed = {
        "ACCEPTED": "ARRIVED",
        "ARRIVED": "IN_PROGRESS",
        "IN_PROGRESS": "COMPLETED",
    }
    if allowed.get(current_status) != request.status:
        raise HTTPException(status_code=409, detail="Invalid or stale order status transition")

    updated = await OrderRepository.update_status(
        order_id,
        request.status,
        current_status,
        actor_user_id=driver_id,
        actor_role="DRIVER",
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Order status changed; refresh and retry")
    return {"order_id": order_id, "status": request.status}
