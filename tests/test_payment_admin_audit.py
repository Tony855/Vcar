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
