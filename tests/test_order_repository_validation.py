import pytest

from app.db.core import db
from app.db.repositories.order_repo import OrderRepository


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("order_id", "driver_id"),
    [
        (0, 1), (-1, 1), (True, 1), (2**63, 1),
        (1, 0), (1, -1), (1, True), (1, 2**63),
        ("1", 1), (1, "1"),
    ],
)
async def test_accept_rejects_invalid_bigint_identifiers_before_database(
    monkeypatch, order_id, driver_id
):
    monkeypatch.setattr(db, "pool", None)
    assert await OrderRepository.accept(order_id, driver_id) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("order_id", [0, -1, True, 2**63, "1"])
async def test_update_status_rejects_invalid_order_id_before_database(monkeypatch, order_id):
    monkeypatch.setattr(db, "pool", None)
    assert await OrderRepository.update_status(
        order_id, "ARRIVED", "ACCEPTED", actor_user_id=2, actor_role="DRIVER"
    ) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("actor_id", [0, -1, True, 2**63, "2", 2.0])
async def test_update_status_rejects_invalid_human_actor_before_database(monkeypatch, actor_id):
    monkeypatch.setattr(db, "pool", None)
    assert await OrderRepository.update_status(
        1, "ARRIVED", "ACCEPTED", actor_user_id=actor_id, actor_role="DRIVER"
    ) is False


@pytest.mark.asyncio
async def test_update_status_system_actor_must_not_supply_user_identity(monkeypatch):
    monkeypatch.setattr(db, "pool", None)
    assert await OrderRepository.update_status(
        1, "ARRIVED", "ACCEPTED", actor_user_id=2, actor_role="SYSTEM"
    ) is False
