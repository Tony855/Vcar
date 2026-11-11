from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.security import create_access_token, verify_telegram_webapp_init_data
from app.db.core import db

router = APIRouter(prefix="/driver", tags=["driver-auth"])


class TelegramSessionRequest(BaseModel):
    init_data: str = Field(min_length=1, max_length=8192)


@router.post("/session")
async def create_driver_session(request: TelegramSessionRequest):
    if not settings.driver_bot_token:
        raise HTTPException(status_code=503, detail="Driver Telegram bot is not configured")
    try:
        verified = verify_telegram_webapp_init_data(request.init_data, settings.driver_bot_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid Telegram WebApp authentication") from exc

    telegram_id = verified["user"]["id"]
    async with db.pool.acquire() as connection:
        driver = await connection.fetchrow(
            "SELECT driver_status FROM users WHERE telegram_id=$1",
            telegram_id,
        )
    if not driver or driver["driver_status"] != "APPROVED":
        raise HTTPException(status_code=403, detail="Approved driver account required")

    token = create_access_token(subject=str(telegram_id), role="driver", expires_minutes=20)
    return {"token": token, "tg_id": telegram_id, "expires_in": 1200}
