"""One follower-count validity and freshness policy for gallery metadata and sweeps."""
from datetime import datetime, timezone
import math
import time

FOLLOWER_CACHE_SECONDS = 24 * 60 * 60

def follower_count(value):
    if type(value) not in (int,float) or value < 0:
        return None
    if type(value) is float and (not math.isfinite(value) or not value.is_integer()):return None
    return int(value) if value <= 2**63-1 else None

def fetched_time(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if stamp.tzinfo is None: stamp=stamp.replace(tzinfo=timezone.utc)
        return stamp.timestamp()
    except (TypeError,ValueError,OverflowError):
        return None

def follower_fresh(stamp):
    fetched=fetched_time(stamp)
    return fetched is not None and 0 <= time.time()-fetched < FOLLOWER_CACHE_SECONDS
