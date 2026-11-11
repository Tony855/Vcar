from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.modules.auth.ws_tickets import issue_ws_ticket

router = APIRouter(tags=["websocket-auth"])


class WebSocketTicketRequest(BaseModel):
    scope: str = Field(min_length=1, max_length=128)


@router.post("/ws-ticket")
async def create_websocket_ticket(
    request: WebSocketTicketRequest,
    authorization: str | None = Header(default=None),
):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    try:
        ticket = await issue_ws_ticket(authorization[7:], request.scope)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="WebSocket ticket service unavailable") from exc
    return {"ticket": ticket, "expires_in": 30, "single_use": True}
