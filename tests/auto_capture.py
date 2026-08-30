"""Unattended capture takes the reachable, unbuilt days -- oldest first -- and no others."""

from datetime import date, datetime, timedelta
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.capture import AutoCapture, CAPTURE_LOOKBACK_DAYS
from discovery.history import LOCAL_ZONE, HistoryArchive
from discovery.settings import AUTO_CAPTURE_INTERVALS, AppSettings

TODAY = date(2026, 8, 30)
NOW = datetime(2026, 8, 30, 9, tzinfo=LOCAL_ZONE)


class FakeArchive:
    """Stands in for HistoryArchive so the plan can be checked without collecting."""

    def __init__(self, floor_day: date, complete: set[str]):
        self.floor_day, self.complete, self.started = floor_day, complete, []
        self.root = Path(".")

    @staticmethod
    def archive_key(value, segment="all"):
        return HistoryArchive.archive_key(value, segment)

    def history_window(self, rating):
        floor = datetime.combine(self.floor_day, datetime.min.time(), LOCAL_ZONE)
        return {"floor": floor.isoformat(), "oldestBuildableDay": self.floor_day.isoformat()}

    def status(self, key):
        return {"complete": key in self.complete,
                "state": "complete" if key in self.complete else "not_started"}

    def start(self, value, start_utc, end_utc, timezone_name, segment, rating):
        key = self.archive_key(value, segment)
        self.started.append(key)
        self.complete.add(key)   # completes immediately for the test
        return self.status(key)


with tempfile.TemporaryDirectory(prefix="civitai-autocapture-") as temporary:
    settings = AppSettings(Path(temporary) / "settings.json")
    settings.update(auto_capture_value=True, auto_capture_hours_value=12)

    # Reachable back to the 27th; the 26th and older are gone for good.
    archive = FakeArchive(floor_day=date(2026, 8, 27), complete=set())
    capture = AutoCapture(archive, settings, now=lambda: NOW)

    pending = capture.pending_blocks()
    # Only the 27th, 28th and 29th are both ended and reachable. The 26th is out of the
    # lookback's reachable part and must not be attempted at all.
    assert [p[0] for p in pending] == ["2026-08-27"] * 2 + ["2026-08-28"] * 2 + ["2026-08-29"] * 2, pending
    assert all(seg in ("morning", "evening") for _, seg in pending), pending
    # Oldest first: the block nearest to falling out of the window cannot wait.
    assert pending[0][0] < pending[-1][0], pending

    result = capture.run_once()
    assert len(result["captured"]) == 6 and not result["failed"], result
    assert archive.started[0].startswith("2026-08-27"), archive.started
    assert not any(k.startswith("2026-08-26") for k in archive.started), archive.started
    # Today is still in progress and must never be captured.
    assert not any(k.startswith("2026-08-30") for k in archive.started), archive.started

    # A second pass finds nothing left to do rather than re-collecting.
    assert capture.pending_blocks() == [], capture.pending_blocks()

    # Already-complete blocks are skipped even when reachable.
    archive2 = FakeArchive(date(2026, 8, 27), complete={"2026-08-28#morning"})
    capture2 = AutoCapture(archive2, settings, now=lambda: NOW)
    assert ("2026-08-28", "morning") not in capture2.pending_blocks()

    # A block Civitai refuses before starting is recorded as skipped, not failed.
    class Refusing(FakeArchive):
        def start(self, *a, **k):
            raise ValueError("out of reach")
    refused = AutoCapture(Refusing(date(2026, 8, 27), set()), settings, now=lambda: NOW)
    outcome = refused.run_once()
    assert len(outcome["skipped"]) == 6 and not outcome["captured"], outcome

    # Disabled means the worker does no capturing at all.
    settings.update(auto_capture_value=False)
    assert settings.load()["autoCapture"] is False
    state = AutoCapture(FakeArchive(date(2026, 8, 27), set()), settings, now=lambda: NOW).status()
    assert state["enabled"] is False and state["intervalHours"] in AUTO_CAPTURE_INTERVALS, state

print({"lookbackDays": CAPTURE_LOOKBACK_DAYS, "oldestFirst": True,
       "unreachableSkipped": True, "todayNeverCaptured": True, "idempotent": True})
