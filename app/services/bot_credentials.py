"""Encrypted Telegram Bot credentials shared by the admin API and polling worker."""
from __future__ import annotations
import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken
from app.core.config import settings

def _fernet() -> Fernet:
    secret = settings.secret_key.encode()
    if len(secret) < 32 or settings.secret_key.startswith("REPLACE_"):
        raise RuntimeError("SECRET_KEY must be initialized before storing Bot credentials")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))

def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()

def decrypt_token(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError) as exc:
        raise RuntimeError("Stored Telegram Bot credential cannot be decrypted") from exc

async def load_bot_tokens(pool) -> tuple[str, str]:
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT key, value FROM settings WHERE key IN "
            "('bot_token_passenger_enc', 'bot_token_driver_enc')"
        )
    values = {row["key"]: row["value"] for row in rows}
    passenger = decrypt_token(values["bot_token_passenger_enc"]) if values.get("bot_token_passenger_enc") else ""
    driver = decrypt_token(values["bot_token_driver_enc"]) if values.get("bot_token_driver_enc") else ""
    return passenger, driver
