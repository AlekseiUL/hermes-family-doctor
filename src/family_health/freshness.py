from __future__ import annotations

from datetime import datetime, timezone


def verification_status(last_verified: str | None) -> str:
    if not last_verified:
        return "unknown"
    try:
        verified = datetime.fromisoformat(str(last_verified).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "invalid"
    return "verified_date_recorded" if isinstance(verified, datetime) else "invalid"


def assess_freshness(last_verified: str | None, max_age_days: int, now: datetime | None = None) -> str:
    status = verification_status(last_verified)
    if status != "verified_date_recorded":
        return "unknown"
    current = now or datetime.now(timezone.utc)
    verified = datetime.fromisoformat(str(last_verified).replace("Z", "+00:00"))
    if verified.tzinfo is None:
        verified = verified.replace(tzinfo=timezone.utc)
    age_days = (current - verified).total_seconds() / 86400
    return "stale" if age_days > max_age_days else "fresh"
