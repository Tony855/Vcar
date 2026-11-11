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
