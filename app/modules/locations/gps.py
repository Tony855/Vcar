"""Pure GPS sample ordering rules shared by ingestion and tests."""
from datetime import datetime


def is_newer_sample(candidate: datetime, latest: datetime | None) -> bool:
    """Accept only strictly newer, timezone-aware GPS samples."""
    if candidate.tzinfo is None or candidate.utcoffset() is None:
        return False
    if latest is None:
        return True
    if latest.tzinfo is None or latest.utcoffset() is None:
        return False
    return candidate > latest


def build_location_snapshot(row: dict, telegram_id: int) -> dict:
    """Serialize the durable latest GPS row for WebSocket reconnect recovery."""
    if row is None:
        return None
    recorded_at = row["recorded_at"]
    return {
        "telegram_id": telegram_id,
        "latitude": float(row["latitude"]),
        "longitude": float(row["longitude"]),
        "accuracy_m": row["accuracy_m"],
        "heading_deg": row["heading_deg"],
        "speed_mps": row["speed_mps"],
        "recorded_at": recorded_at.isoformat(),
        "snapshot": True,
    }
