"""Plant shifts, decided by the clock and not by the profile of the user:
Turno 1 07:00-15:00, Turno 2 15:00-23:00, Turno 3 23:00-07:00 (server local time)."""
from datetime import datetime, timedelta, timezone

BOUNDARIES = (7, 15, 23)


def _local(value):
    """Naive UTC (how the database stores time) -> aware local time. Without a value, now."""
    if value is None:
        return datetime.now(timezone.utc).astimezone()
    return value.replace(tzinfo=timezone.utc).astimezone()


def shift_for(value=None):
    hour = _local(value).hour
    if 7 <= hour < 15:
        return "Turno 1"
    if 15 <= hour < 23:
        return "Turno 2"
    return "Turno 3"


def shift_end(value=None):
    """Naive UTC instant at which the shift containing `value` ends."""
    local = _local(value)
    candidates = [local.replace(hour=hour, minute=0, second=0, microsecond=0) for hour in BOUNDARIES]
    candidates.append(candidates[0] + timedelta(days=1))
    end = next(moment for moment in candidates if moment > local)
    return end.astimezone(timezone.utc).replace(tzinfo=None)
