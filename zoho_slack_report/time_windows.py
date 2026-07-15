from datetime import datetime, timedelta

from .config import IST


def current_work_week(now=None):
    """Current work week: Mon 00:00 -> Fri 23:59:59.999 IST (no weekend)."""
    now = now or datetime.now(IST)
    start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(days=5) - timedelta(microseconds=1)
    return start, end


def trailing_24_hours(now=None):
    """Trailing 24-hour window: now - 24h -> now (IST)."""
    now = now or datetime.now(IST)
    return now - timedelta(hours=24), now


def trailing_7_days(now=None):
    """Trailing 7-day window: now - 7d -> now (IST)."""
    now = now or datetime.now(IST)
    return now - timedelta(days=7), now
