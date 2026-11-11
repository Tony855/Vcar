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
