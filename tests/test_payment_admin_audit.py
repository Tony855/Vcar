import pytest

from app.modules.payments import router


@pytest.mark.asyncio
async def test_admin_adjustment_records_authenticated_admin_subject(monkeypatch):
    captured = {}
    monkeypatch.setattr(router, "_claims", lambda authorization, roles: {"sub": "admin-42", "role": "admin"})

    async def fake_post(**kwargs):
        captured.update(kwargs)
        return type("Result", (), {
            "transaction_id": 7,
            "wallet_id": 9,
            "balance_minor": 1234,
            "replayed": False,
        })()

    monkeypatch.setattr(router.WalletLedger, "post", fake_post)
    request = router.AdminAdjustment(
        user_id=12,
        currency="USD",
        amount_minor=500,
        reason="Manual correction approved",
        idempotency_key="admin-adjustment-0007",
    )
    result = await router.admin_wallet_adjustment(request, "Bearer test")

    assert result["transaction_id"] == 7
    assert captured["metadata"]["admin_subject"] == "admin-42"
    assert captured["metadata"]["reason"] == "Manual correction approved"



def test_admin_adjustment_rejects_zero_amount():
    with pytest.raises(ValueError):
        router.AdminAdjustment(
            user_id=12,
            currency="USD",
            amount_minor=0,
            reason="Manual correction approved",
            idempotency_key="admin-adjustment-0008",
        )


@pytest.mark.parametrize("amount", [True, False, 1.5, "500"])
def test_admin_adjustment_rejects_non_integer_minor_units(amount):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        router.AdminAdjustment(
            user_id=12,
            currency="USD",
            amount_minor=amount,
            reason="Manual correction approved",
            idempotency_key="admin-adjustment-invalid",
        )
