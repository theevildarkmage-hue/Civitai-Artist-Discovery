"""Shared Retry-After parsing; callers retain ownership of their retry budgets."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math


def retry_after_seconds(value, now=None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(str(value))
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return max(
                0.0, (target - (now or datetime.now(timezone.utc))).total_seconds()
            )
        except (TypeError, ValueError, OverflowError):
            return None
