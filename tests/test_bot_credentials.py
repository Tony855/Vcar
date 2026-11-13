from app.core.config import Settings
from app.services import bot_credentials


def test_bot_token_encryption_round_trip(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 64)
    monkeypatch.setenv("BOT_CREDENTIALS_ENCRYPTION_KEY", "")
    monkeypatch.setattr(bot_credentials, "settings", Settings.from_env())
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    encrypted = bot_credentials.encrypt_token(token)
    assert encrypted != token
    assert bot_credentials.decrypt_token(encrypted) == token


def test_dedicated_bot_key_survives_secret_key_rotation(monkeypatch):
    import base64
    import hashlib

    monkeypatch.setenv("SECRET_KEY", "x" * 64)
    legacy_key = base64.urlsafe_b64encode(hashlib.sha256(b"x" * 64).digest()).decode()
    monkeypatch.setenv("BOT_CREDENTIALS_ENCRYPTION_KEY", legacy_key)
    monkeypatch.setattr(bot_credentials, "settings", Settings.from_env())
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    encrypted = bot_credentials.encrypt_token(token)

    monkeypatch.setenv("SECRET_KEY", "y" * 64)
    monkeypatch.setattr(bot_credentials, "settings", Settings.from_env())
    assert bot_credentials.decrypt_token(encrypted) == token


def test_invalid_dedicated_bot_key_fails_closed(monkeypatch):
    import pytest

    monkeypatch.setenv("SECRET_KEY", "x" * 64)
    monkeypatch.setenv("BOT_CREDENTIALS_ENCRYPTION_KEY", "not-a-fernet-key")
    monkeypatch.setattr(bot_credentials, "settings", Settings.from_env())
    with pytest.raises(RuntimeError, match="BOT_CREDENTIALS_ENCRYPTION_KEY"):
        bot_credentials.encrypt_token("token")
