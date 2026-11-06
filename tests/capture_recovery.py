"""False successes are audited and interrupted captures resume across app restarts."""
import test_safety  # Refuse live application endpoints and isolate all test data.

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["CIVITAI_HISTORY_BACKEND"] = "search"

from discovery.capture import AutoCapture
from discovery.history import COLLECTION_VERSION, HistoryArchive, RetryBudgetExhausted
from discovery.recovery import SuspiciousCaptureError
from discovery.settings import AppSettings


DAY = "2026-10-04"
KEY = DAY + "#morning"
START = datetime(2026, 10, 4, 5, tzinfo=timezone.utc)


def hit(image_id, start=START):
    return {"id": image_id, "postId": image_id,
            "createdAt": (start + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "url": f"test-{image_id}", "nsfwLevel": 1, "type": "image",
            "user": {"username": "test"}, "stats": {}}


with tempfile.TemporaryDirectory(prefix="cad3-capture-recovery-", ignore_cleanup_errors=True) as temporary:
    root = Path(temporary)
    archive = HistoryArchive(root / "history")
    settings = AppSettings(root / "settings.json")
    settings.update(auto_capture_value=True, capture_coverage_value="Soft")
    from discovery.search import normalize_hit

    # Three comparable normal mornings, then a ten-record false success.
    for back, count in ((3, 1200), (2, 1200), (1, 1200), (0, 10)):
        start = START - timedelta(days=back)
        day = start.date().isoformat()
        key = day + "#morning"
        ids = archive._upsert_normalized([normalize_hit(hit(10_000 * back + i + 1, start))
                                         for i in range(count)], forced_date=day)
        with archive.connect() as db:
            db.execute("""INSERT INTO days(day,complete,content_rating,collection_version,
                start_utc,end_utc,updated_at) VALUES(?,1,'Soft',?,?,?,?)""",
                (key, COLLECTION_VERSION, start.isoformat(),
                 (start + timedelta(hours=12)).isoformat(), start.isoformat()))
            db.executemany("INSERT INTO block_images VALUES(?,?)", [(key, i) for i in ids])
            db.executemany("INSERT INTO block_feeds(block_key,browsing_mask,complete) VALUES(?,?,1)",
                           [(key, 1), (key, 2)])

    now = START + timedelta(days=10)
    capture = AutoCapture(archive, settings, now=lambda: now)
    with archive.connect() as db:
        original_count = db.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    capture.recovery.audit(now)
    assert list(capture.recovery.entries()) == [KEY], capture.recovery.entries()
    assert (DAY, "morning") in capture.pending_blocks(), "old retry fell outside the lookback"
    assert capture.seconds_until_next() <= 1.1, "due recovery waited for the 12-hour schedule"
    with archive.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM images").fetchone()[0] == original_count
        assert db.execute("SELECT complete FROM days WHERE day=?", (KEY,)).fetchone()[0] == 1
    try:
        archive.verify_capture_volume(KEY)
        raise AssertionError("a false success passed the volume check")
    except SuspiciousCaptureError:
        pass

    # Visibility filters must not change the raw volume check.
    archive.set_content_filter((2,))
    assert archive.status(KEY)["itemCount"] == 0
    capture.recovery.audit(now)
    assert list(capture.recovery.entries()) == [KEY]
    archive.set_content_filter((1, 2))

    # Limited public-feed reach retains the old gap without hammering the service.
    os.environ["CIVITAI_HISTORY_BACKEND"] = "v1"
    archive.history_window = lambda rating: {"floor": (now - timedelta(days=1)).isoformat()}
    assert (DAY, "morning") not in capture.pending_blocks()
    queued = capture.recovery.entries()[KEY]
    assert queued["next_retry_at"] == now.timestamp() + 6 * 3600
    os.environ["CIVITAI_HISTORY_BACKEND"] = "search"
    now += timedelta(hours=7)

    rows = [hit(i + 1) for i in range(1201)]
    requests = []
    fail = True

    def search(body, **kwargs):
        global fail
        mask = int(body["filter"][-1].split()[-1])
        expected = len(rows) if mask == 1 else 0
        offset, limit = body["offset"], body["limit"]
        if limit:
            requests.append((mask, offset))
            if offset == 1000 and fail:
                fail = False
                raise RetryBudgetExhausted("service_retry", 16, "503")
        return {"estimatedTotalHits": expected,
                "hits": rows[offset:offset + limit] if mask == 1 else []}, 100

    archive._search_request = search
    # Limit execution to the historical gap; planning of normal days is tested separately.
    capture.pending_blocks = lambda: [(DAY, "morning")]
    failed = capture.run_once()
    assert failed["failed"] == [KEY], failed
    queued = capture.recovery.entries()[KEY]
    assert not queued["restart_required"], "a network retry would discard its checkpoint"
    assert archive.status(KEY)["itemCount"] == 1000

    # Reopen both objects as a new app process would. The retry and offset survive.
    reopened = HistoryArchive(root / "history")
    reopened._search_request = search
    recovery = AutoCapture(reopened, settings, now=lambda: now)
    assert recovery.recovery.entries()[KEY] == queued
    assert (DAY, "morning") not in recovery.pending_blocks(), "retry ignored its backoff"
    assert 0 < recovery.seconds_until_next() <= 3600
    now = datetime.fromtimestamp(queued["next_retry_at"], timezone.utc) + timedelta(seconds=1)
    assert (DAY, "morning") in recovery.pending_blocks()
    recovery.pending_blocks = lambda: [(DAY, "morning")]
    result = recovery.run_once()
    assert result["captured"] == [KEY], result
    assert KEY not in recovery.recovery.entries()
    assert reopened.status(KEY)["itemCount"] == 1201
    assert requests.count((1, 0)) == 1, requests
    assert requests.count((1, 1000)) == 2, requests

    # A successful HTTP response with a tiny but internally consistent count must
    # still fail before either the half-day or its merged gallery is published.
    tiny_day = "2026-10-05"
    tiny_start = START + timedelta(days=1)
    tiny_key = tiny_day + "#morning"
    tiny_rows = [hit(50_000 + i, tiny_start) for i in range(10)]
    def tiny_search(body, **kwargs):
        count = 10 if body["filter"][-1].endswith("= 1") else 0
        return {"estimatedTotalHits": count,
                "hits": tiny_rows[:body["limit"]] if count else []}, 100
    reopened._search_request = tiny_search
    reopened.start(tiny_day, tiny_start.isoformat(), (tiny_start + timedelta(hours=12)).isoformat(),
                   "America/Chicago", "morning", "Soft")
    deadline = time.monotonic() + 10
    while reopened.status(tiny_key)["state"] == "loading" and time.monotonic() < deadline:
        time.sleep(.01)
    state = reopened.status(tiny_key)
    assert state["errorKind"] == "suspicious_capture" and not state["complete"], state
    assert not reopened.status(tiny_day)["complete"]

    # A failure while probing reach must back off too, rather than waking every second.
    os.environ["CIVITAI_HISTORY_BACKEND"] = "v1"
    def unavailable(rating):
        raise RuntimeError("Civitai is unavailable")
    reopened.history_window = unavailable
    planner = AutoCapture(reopened, settings, now=lambda: now)
    try:
        planner.pending_blocks()
        raise AssertionError("outage was swallowed")
    except RuntimeError:
        pass
    assert all(row["next_retry_at"] > now.timestamp() for row in planner.recovery.entries().values())
    assert 1 < planner.seconds_until_next() <= 1800

print({"falseSuccessFound": True, "rawCountsUsed": True, "savedMetadataPreserved": True,
       "oldGapsRetained": True, "retrySurvivesRestart": True,
       "backoffHonored": True, "resumedAtCheckpoint": True})
