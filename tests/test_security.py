import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.core.security import create_access_token, hash_password, verify_password, verify_token


def test_password_hash_round_trip():
    encoded = hash_password("correct horse battery staple")
    assert encoded.startswith("pbkdf2_sha256$")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong password", encoded)


def test_password_hash_rejects_weak_password():
    with pytest.raises(ValueError):
        hash_password("short")


def test_password_hash_rejects_unknown_algorithm():
    assert not verify_password("anything", "plain$1$salt$value")


@pytest.mark.asyncio
async def test_verify_token_rejects_missing_credentials():
    with pytest.raises(HTTPException) as exc:
        await verify_token(None)
    assert exc.value.status_code == 401


def test_access_token_requires_strong_secret(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "weak")
    with pytest.raises(HTTPException) as exc:
        create_access_token("admin")
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_verify_token_accepts_valid_admin_token(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 48)
    token = create_access_token("admin")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    payload = await verify_token(credentials)
    assert payload["sub"] == "admin"
    assert payload["role"] == "admin"

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from app.core.security import verify_telegram_webapp_init_data


def _telegram_init_data(bot_token: str, user_id: int, auth_date: int) -> str:
    fields = {
        "auth_date": str(auth_date),
        "user": json.dumps({"id": user_id, "first_name": "Driver"}, separators=(",", ":")),
        "query_id": "test-query",
    }
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(bot_token.encode(), b"WebAppData", hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_telegram_init_data_signature():
    token = "test-bot-token"
    data = _telegram_init_data(token, 123456, int(time.time()))
    verified = verify_telegram_webapp_init_data(data, token)
    assert verified["user"]["id"] == 123456


def test_telegram_init_data_rejects_tampering():
    token = "test-bot-token"
    data = _telegram_init_data(token, 123456, int(time.time())).replace("123456", "654321")
    with pytest.raises(ValueError):
        verify_telegram_webapp_init_data(data, token)


def test_telegram_init_data_rejects_expired_payload():
    token = "test-bot-token"
    data = _telegram_init_data(token, 123456, int(time.time()) - 90_000)
    with pytest.raises(ValueError):
        verify_telegram_webapp_init_data(data, token)

from app.core.security import verify_driver_token


@pytest.mark.asyncio
async def test_driver_token_requires_driver_role(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "d" * 48)
    token = create_access_token("12345", role="driver")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    payload = await verify_driver_token(credentials)
    assert payload["sub"] == "12345"
    assert payload["role"] == "driver"


@pytest.mark.asyncio
async def test_driver_token_rejects_admin_role(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "d" * 48)
    token = create_access_token("admin", role="admin")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as exc:
        await verify_driver_token(credentials)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("subject", ["", "admin", "0", "-1", "１２３", "9" * 20])
async def test_driver_token_rejects_non_telegram_or_out_of_range_subject(monkeypatch, subject):
    import jwt
    import time
    monkeypatch.setenv("SECRET_KEY", "d" * 48)
    token = jwt.encode(
        {"sub": subject, "role": "driver", "exp": int(time.time()) + 300},
        "d" * 48,
        algorithm="HS256",
    )
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as exc:
        await verify_driver_token(credentials)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("telegram_id", [0, -1, 2**63, True])
def test_telegram_init_data_rejects_invalid_signed_bigint_user_ids(telegram_id):
    token = "test-bot-token"
    data = _telegram_init_data(token, telegram_id, int(time.time()))
    with pytest.raises(ValueError, match="user is invalid"):
        verify_telegram_webapp_init_data(data, token)


@pytest.mark.asyncio
async def test_admin_token_without_exp_is_rejected(monkeypatch):
    secret = "missing-exp-admin-secret-" + "x" * 32
    monkeypatch.setenv("SECRET_KEY", secret)
    token = jwt.encode({"sub": "admin", "role": "admin"}, secret, algorithm="HS256")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as error:
        await verify_token(credentials)
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_driver_token_without_exp_is_rejected(monkeypatch):
    secret = "missing-exp-driver-secret-" + "x" * 32
    monkeypatch.setenv("SECRET_KEY", secret)
    token = jwt.encode({"sub": "12345", "role": "driver"}, secret, algorithm="HS256")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as error:
        await verify_driver_token(credentials)
    assert error.value.status_code == 401
