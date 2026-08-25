"""A build records API pacing and failure causes separately from response time."""

from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.history import COLLECTION_VERSION, HistoryArchive


DAY = "2026-07-31"


def image(image_id, created):
    return {"id": image_id, "postId": image_id, "username": "metric-test",
        "createdAt": created, "url": f"https://example.invalid/{image_id}.jpg",
        "type": "image", "nsfwLevel": "Soft", "browsingLevel": 2, "stats": {}}


with tempfile.TemporaryDirectory(prefix="civitai-collection-metrics-") as temporary:
    archive = HistoryArchive(Path(temporary) / "history")
    calls = 0

    def request(_params, minimum_interval=None, on_delay=None, cancel_event=None, on_timing=None,
                on_transfer=None):
        global calls
        calls += 1
        if on_timing:
            on_timing("pace", .5)
            on_timing("response", .25)
        if on_transfer:
            on_transfer(400, 1234)
        if on_delay:
            on_delay("rate_limited" if calls == 1 else "network_retry",
                     5 if calls == 1 else 2, 1, 8)
        return ({"items": [image(1, "2026-07-31T12:00:00Z"),
                            image(2, "2026-07-31T04:59:59Z")],
                 "metadata": {"nextCursor": "older"}}, 1234)

    archive._request = request
    key = f"{DAY}#morning"
    archive.start(DAY, "2026-07-31T05:00:00Z", "2026-07-31T17:00:00Z",
                  "America/Chicago", "morning")
    deadline = time.monotonic() + 5
    while archive.status(key)["state"] == "loading" and time.monotonic() < deadline:
        time.sleep(.01)
    metrics = archive.status(key)["metrics"]
    assert metrics["paceSeconds"] == 1.0, metrics
    assert metrics["responseSeconds"] == .5, metrics
    assert metrics["retrySeconds"] == 7.0 and metrics["retryCount"] == 2, metrics
    assert metrics["rateLimitCount"] == 1 and metrics["networkRetryCount"] == 1, metrics
    assert metrics["serviceRetryCount"] == 0, metrics
    assert metrics["seekPages"] == 1 and metrics["collectPages"] == 1, metrics
    assert metrics["seekBytes"] == 1234 and metrics["collectBytes"] == 1234, metrics
    assert metrics["wireBytes"] == 800 and metrics["decodedBytes"] == 2468, metrics

with tempfile.TemporaryDirectory(prefix="civitai-resumed-metrics-") as temporary:
    archive = HistoryArchive(Path(temporary) / "history")
    key = f"{DAY}#morning"
    with archive.connect() as db:
        db.execute("""INSERT INTO days(day,complete,scan_cursor,content_rating,elapsed_seconds,
                    collect_pages,pace_seconds,response_seconds,retry_seconds,retry_count,
                    rate_limit_count,wire_bytes,decoded_bytes,collection_version,updated_at)
                    VALUES(?,0,NULL,'Soft',100,3,10,20,30,2,2,1000,2000,?,?)""",
                   (key, COLLECTION_VERSION, "2026-07-31T00:00:00Z"))
        db.execute("""INSERT INTO block_feeds(block_key,browsing_mask,complete,scan_cursor,
                    pages,updated_at) VALUES(?,3,0,?,3,?)""",
                   (key, "saved-cursor", "2026-07-31T00:00:00Z"))

    def resumed_request(_params, minimum_interval=None, on_delay=None, cancel_event=None,
                        on_timing=None, on_transfer=None):
        if on_timing:
            on_timing("pace", .5)
            on_timing("response", .25)
        if on_transfer:
            on_transfer(400, 1234)
        return ({"items": [image(3, "2026-07-31T04:59:59Z")],
                 "metadata": {"nextCursor": "older"}}, 1234)

    archive._request = resumed_request
    archive.start(DAY, "2026-07-31T05:00:00Z", "2026-07-31T17:00:00Z",
                  "America/Chicago", "morning")
    deadline = time.monotonic() + 5
    while archive.status(key)["state"] == "loading" and time.monotonic() < deadline:
        time.sleep(.01)
    metrics = archive.status(key)["metrics"]
    assert metrics["collectPages"] == 4, metrics
    assert metrics["paceSeconds"] == 10.5 and metrics["responseSeconds"] == 20.25, metrics
    assert metrics["retrySeconds"] == 30 and metrics["retryCount"] == 2, metrics
    assert metrics["rateLimitCount"] == 2, metrics
    assert metrics["wireBytes"] == 1400 and metrics["decodedBytes"] == 3234, metrics
    assert metrics["elapsedSeconds"] >= 100, metrics

print({"pacingSeparated": True, "responseSeparated": True,
       "retryReasonsSeparated": True, "pagesAndBytesRecorded": True,
       "resumePreservesMetrics": True})
