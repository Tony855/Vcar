"""HTTP contract tests for wallet account and ledger endpoints."""
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import jwt
import pytest
from fastapi.testclient import TestClient

from app.db.core import db
from app.modules.payments import router as wallet_router
from app.web.server import app


class FakeConnection:
    def __init__(self, *, user_id=41, wallet=None, transactions=None):
        self.user_id = user_id
        self.wallet = wallet or {
            "id": 7,
            "currency": "USD",
            "balance_minor": 1234,
            "created_at": datetime(2026, 11, 12, tzinfo=timezone.utc),
        }
        self.transactions = transactions or []
        self.fetchval_calls = []
        self.execute_calls = []
        self.fetchrow_calls = []
        self.fetch_calls = []

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query, args))
        return self.user_id

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))
        return "INSERT 0 1"

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        return self.wallet

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        return self.transactions


class FakeAcquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return FakeAcquire(self.connection)


@pytest.fixture
def wallet_client(monkeypatch):
    secret = "wallet-api-test-secret-" + "x" * 40
    monkeypatch.setattr(wallet_router, "settings", SimpleNamespace(secret_key=secret))
    connection = FakeConnection()
    monkeypatch.setattr(db, "pool", FakePool(connection))
    client = TestClient(app)

    def token(role="passenger", subject="7003003"):
        return jwt.encode(
            {"sub": subject, "role": role, "exp": int(time.time()) + 300},
            secret,
            algorithm="HS256",
        )

    return client, connection, token


def test_wallet_open_requires_passenger_or_driver_role(wallet_client):
    client, connection, token = wallet_client
    response = client.post(
        "/api/v1/wallet/me",
        headers={"Authorization": f"Bearer {token('admin')}"},
        json={"currency": "USD"},
    )
    assert response.status_code == 403
    assert connection.fetchval_calls == []


def test_wallet_open_creates_or_returns_account_for_authenticated_user(wallet_client):
    client, connection, token = wallet_client
    response = client.post(
        "/api/v1/wallet/me",
        headers={"Authorization": f"Bearer {token()}"},
        json={"currency": "USD"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == 7
    assert response.json()["currency"] == "USD"
    assert response.json()["balance_minor"] == 1234
    assert connection.fetchval_calls[0][1] == (7003003,)
    assert connection.execute_calls[0][1] == (41, "USD")


def test_wallet_open_rejects_currency_change_for_existing_account(wallet_client):
    client, connection, token = wallet_client
    connection.wallet["currency"] = "KHR"
    response = client.post(
        "/api/v1/wallet/me",
        headers={"Authorization": f"Bearer {token()}"},
        json={"currency": "USD"},
    )
    assert response.status_code == 409


def test_wallet_transactions_are_scoped_to_authenticated_user_and_pagination(wallet_client):
    client, connection, token = wallet_client
    connection.transactions = [{
        "id": 19,
        "order_id": 8,
        "kind": "TOPUP",
        "amount_minor": 2500,
        "currency": "USD",
        "provider_reference": "sandbox-19",
        "created_at": datetime(2026, 11, 12, tzinfo=timezone.utc),
    }]
    response = client.get(
        "/api/v1/wallet/me/transactions?limit=2&before_id=20",
        headers={"Authorization": f"Bearer {token()}"},
    )
    assert response.status_code == 200
    assert response.json()[0]["id"] == 19
    assert connection.fetch_calls[0][1] == (7003003, 20, 2)


def test_admin_wallet_adjustment_records_admin_identity_through_http(wallet_client, monkeypatch):
    client, _, token = wallet_client
    captured = {}

    async def fake_post(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            transaction_id=27,
            wallet_id=7,
            balance_minor=1734,
            replayed=False,
        )

    monkeypatch.setattr(wallet_router.WalletLedger, "post", fake_post)
    response = client.post(
        "/api/v1/wallet/admin/adjust",
        headers={"Authorization": f"Bearer {token('admin', 'admin-9')}"},
        json={
            "user_id": 41,
            "currency": "USD",
            "amount_minor": 500,
            "reason": "Approved support correction",
            "idempotency_key": "admin-adjustment-http-0027",
        },
    )
    assert response.status_code == 200
    assert response.json()["transaction_id"] == 27
    assert captured["metadata"]["admin_subject"] == "admin-9"
    assert captured["metadata"]["reason"] == "Approved support correction"


@pytest.mark.parametrize("user_id", [True, False, "41", 0, -1])
def test_admin_wallet_adjustment_rejects_non_positive_or_coerced_user_ids(wallet_client, user_id):
    client, _, token = wallet_client
    response = client.post(
        "/api/v1/wallet/admin/adjust",
        headers={"Authorization": f"Bearer {token('admin')}"},
        json={
            "user_id": user_id,
            "currency": "USD",
            "amount_minor": 500,
            "reason": "Approved support correction",
            "idempotency_key": "admin-adjustment-http-invalid",
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize(("field", "value"), [
    ("reason", "        "),
    ("idempotency_key", "            "),
])
def test_admin_wallet_adjustment_rejects_blank_audit_fields(wallet_client, field, value):
    client, _, token = wallet_client
    payload = {
        "user_id": 41,
        "currency": "USD",
        "amount_minor": 500,
        "reason": "Approved support correction",
        "idempotency_key": "admin-adjustment-http-valid",
    }
    payload[field] = value
    response = client.post(
        "/api/v1/wallet/admin/adjust",
        headers={"Authorization": f"Bearer {token('admin')}"},
        json=payload,
    )
    assert response.status_code == 422


def test_admin_wallet_adjustment_normalizes_audit_fields(wallet_client, monkeypatch):
    client, _, token = wallet_client
    captured = {}

    async def fake_post(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(transaction_id=28, wallet_id=7, balance_minor=1500, replayed=False)

    monkeypatch.setattr(wallet_router.WalletLedger, "post", fake_post)
    response = client.post(
        "/api/v1/wallet/admin/adjust",
        headers={"Authorization": f"Bearer {token('admin')}"},
        json={
            "user_id": 41,
            "currency": "USD",
            "amount_minor": 500,
            "reason": "  Approved support correction  ",
            "idempotency_key": "  admin-adjustment-http-0028  ",
        },
    )
    assert response.status_code == 200
    assert captured["metadata"]["reason"] == "Approved support correction"
    assert captured["idempotency_key"] == "admin-adjustment-http-0028"


@pytest.mark.parametrize("user_id", [2**63, -(2**63)])
def test_admin_wallet_adjustment_rejects_user_ids_outside_bigint(wallet_client, user_id):
    client, _, token = wallet_client
    response = client.post(
        "/api/v1/wallet/admin/adjust",
        headers={"Authorization": f"Bearer {token('admin')}"},
        json={
            "user_id": user_id,
            "currency": "USD",
            "amount_minor": 500,
            "reason": "Approved support correction",
            "idempotency_key": "admin-adjustment-bigint-user",
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize("amount", [2**63, -(2**63) - 1])
def test_admin_wallet_adjustment_rejects_amounts_outside_bigint(wallet_client, amount):
    client, _, token = wallet_client
    response = client.post(
        "/api/v1/wallet/admin/adjust",
        headers={"Authorization": f"Bearer {token('admin')}"},
        json={
            "user_id": 41,
            "currency": "USD",
            "amount_minor": amount,
            "reason": "Approved support correction",
            "idempotency_key": "admin-adjustment-bigint-amount",
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize("subject", ["not-a-telegram-id", "0", "-1", "9" * 20])
def test_wallet_user_endpoints_reject_invalid_token_subjects(wallet_client, subject):
    client, connection, token = wallet_client
    response = client.post(
        "/api/v1/wallet/me",
        headers={"Authorization": f"Bearer {token('passenger', subject)}"},
        json={"currency": "USD"},
    )
    assert response.status_code == 403
    assert connection.fetchval_calls == []
