from app.core.config import Settings
from app.services import bot_credentials


def test_bot_token_encryption_round_trip(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 64)
    monkeypatch.setattr(bot_credentials, "settings", Settings.from_env())
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    encrypted = bot_credentials.encrypt_token(token)
    assert encrypted != token
    assert bot_credentials.decrypt_token(encrypted) == token
