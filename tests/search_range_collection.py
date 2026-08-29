"""Exact-range search collects 1,000-item pages and resumes without re-reading them.

The search collector is opt-in and off by default (Civitai ToS 11.4 -- see
``discovery.history.active_backend``), so this test enables it explicitly.
"""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["CIVITAI_HISTORY_BACKEND"] = "search"

from discovery.history import HistoryArchive, RetryBudgetExhausted


DAY = "2026-07-31"
START = datetime(2026, 7, 31, 5, tzinfo=timezone.utc)
END = datetime(2026, 7, 31, 17, tzinfo=timezone.utc)


def hit(image_id: int, level: int) -> dict:
    created = START + timedelta(hours=1, microseconds=image_id % 900_000)
    return {
        "id": image_id, "postId": image_id, "createdAt": created.isoformat().replace("+00:00", "Z"),
        "url": f"00000000-0000-0000-0000-{image_id:012d}", "name": f"{image_id}.png",
        "hash": f"hash-{image_id}", "width": 1024, "height": 1024, "type": "image",
        "nsfwLevel": level, "baseModel": "Test", "modelVersionId": 42,
        "user": {"username": f"Artist{image_id % 7}"},
        "stats": {"likeCountAllTime": 2, "heartCountAllTime": 1,
                  "laughCountAllTime": 0, "cryCountAllTime": 0},
    }


def wait(archive: HistoryArchive, key: str) -> dict:
    deadline = time.monotonic() + 15
    while archive.status(key)["state"] == "loading" and time.monotonic() < deadline:
        time.sleep(.01)
    return archive.status(key)


with tempfile.TemporaryDirectory(prefix="civitai-search-range-") as temporary:
    archive = HistoryArchive(Path(temporary) / "history")
    rows = {1: [hit(10_000 + index, 1) for index in range(1001)], 2: [hit(20_000, 2)]}
    offsets: list[tuple[int, int]] = []
    fail_second_page = True

    def search_request(body, on_delay=None, cancel_event=None, on_timing=None, on_transfer=None):
        global fail_second_page
        level = int(str(body["filter"][-1]).rsplit(" ", 1)[-1])
        limit, offset = int(body["limit"]), int(body["offset"])
        if limit == 0:
            payload = {"estimatedTotalHits": len(rows[level]), "hits": []}
        else:
            offsets.append((level, offset))
            if level == 1 and offset == 1000 and fail_second_page:
                fail_second_page = False
                raise RetryBudgetExhausted("service_retry", 16, "simulated 503")
            payload = {"estimatedTotalHits": len(rows[level]),
                       "hits": rows[level][offset:offset + limit]}
        if on_timing:
            on_timing("response", .01)
        if on_transfer:
            on_transfer(100, 200)
        return payload, 200

    archive._search_request = search_request
    key = f"{DAY}#morning"
    archive.start(DAY, START.isoformat(), END.isoformat(), "America/Chicago", "morning", "Soft")
    failed = wait(archive, key)
    assert failed["state"] == "error" and failed["itemCount"] == 1000, failed
    with archive.connect() as db:
        cursor = db.execute("SELECT scan_cursor FROM block_feeds WHERE block_key=? AND browsing_mask=1",
                            (key,)).fetchone()[0]
    assert "|1000|1001" in cursor, cursor

    archive.start(DAY, START.isoformat(), END.isoformat(), "America/Chicago", "morning", "Soft")
    completed = wait(archive, key)
    assert completed["complete"] and completed["itemCount"] == 1002, completed
    assert completed["collectionBackend"] == "search", completed
    assert offsets.count((1, 0)) == 1 and offsets.count((1, 1000)) == 2, offsets
    assert completed["metrics"]["seekPages"] == 0, completed
    with archive.connect() as db:
        sample = db.execute("SELECT url,browsing_level,model_version_ids,stats FROM images WHERE id=10000").fetchone()
    assert sample["url"].startswith("https://image.civitai.com/")
    assert sample["browsing_level"] == 1 and sample["model_version_ids"] == "[42]"
    assert '"reactionCount": 3' in sample["stats"], sample["stats"]

print({"pageSize": 1000, "exactDateRange": True, "resumedAtOffset": 1000,
       "uniqueCountVerified": True, "ratingStreams": [1, 2]})
