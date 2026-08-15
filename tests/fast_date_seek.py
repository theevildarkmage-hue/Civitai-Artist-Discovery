"""The date locator jumps by offset without skipping the target boundary."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from discovery.history import HistoryArchive, PAGE_SIZE


with tempfile.TemporaryDirectory(prefix="civitai-fast-seek-") as temporary:
    archive = HistoryArchive(Path(temporary) / "history")
    value = "2026-07-31"
    target_end = datetime(2026, 8, 1, 5, tzinfo=timezone.utc)
    live_anchor = target_end + timedelta(minutes=1000)
    offsets = []
    archive.jobs[value] = {"state": "loading", "phase": "locating", "pages": 0}

    def request(params, minimum_interval=1.0, on_delay=None, cancel_event=None, on_timing=None,
                on_transfer=None):
        offset = int(params["cursor"].split("|", 1)[0]); offsets.append(offset)
        page = offset // PAGE_SIZE
        newest = live_anchor - timedelta(minutes=page * 10)
        oldest = newest - timedelta(minutes=10)
        return {"items": [{"createdAt": newest.isoformat()}, {"createdAt": oldest.isoformat()}]}, 100

    archive._request = request
    cursor, pages, transferred = archive._seek_cursor(
        value, target_end, threading.Event(), archive.content_rating, lambda *_: None)
    selected = int(cursor.split("|", 1)[0])

    def oldest_at(offset):
        return live_anchor - timedelta(minutes=(offset // PAGE_SIZE) * 10 + 10)

    assert oldest_at(selected) < target_end
    assert selected == PAGE_SIZE or oldest_at(selected - PAGE_SIZE) >= target_end
    assert pages == len(offsets) < 20
    assert transferred == pages * 100
    assert max(offsets) >= selected

print({"linearPagesAvoided": selected // PAGE_SIZE, "seekRequests": pages, "boundaryPreserved": True})
