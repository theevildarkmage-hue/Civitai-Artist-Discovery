"""Unattended capture takes the reachable, unbuilt days -- oldest first -- and no others."""

from datetime import date, datetime, timedelta
import json
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


with tempfile.TemporaryDirectory(prefix="civitai-capture-spread-") as temporary:
    # Installs share an interval, so without a spread they would all reach Civitai at the
    # same instant. A synchronised burst is worse for a service than scattered requests.
    counts, seeds = [0] * 12, set()
    for index in range(600):
        each = AppSettings(Path(temporary) / f"s{index}.json")
        each.update(auto_capture_value=True, auto_capture_hours_value=12)
        seeds.add(each.load()["captureSeed"])
        hours = AutoCapture(None, each, now=lambda: NOW).seconds_until_next() / 3600
        assert 0 <= hours <= 12, hours
        counts[min(11, int(hours))] += 1
    assert len(seeds) > 590, f"seeds must differ between installs: {len(seeds)}"
    # Every hour of the interval gets used; nothing clumps into one slot.
    assert all(counts), counts
    assert max(counts) < 3 * min(counts), counts

    # The slot is stable across restarts, or the schedule would drift on every launch.
    fixed = AppSettings(Path(temporary) / "stable.json")
    fixed.update(auto_capture_value=True, auto_capture_hours_value=12)
    first = AutoCapture(None, fixed, now=lambda: NOW).seconds_until_next()
    again = AutoCapture(None, fixed, now=lambda: NOW).seconds_until_next()
    assert first == again, (first, again)
    # A longer interval spreads over a longer span.
    fixed.update(auto_capture_hours_value=24)
    assert AutoCapture(None, fixed, now=lambda: NOW).seconds_until_next() <= 24 * 3600


with tempfile.TemporaryDirectory(prefix="civitai-capture-log-") as temporary:
    # Unattended runs have to leave evidence, including runs that did nothing: days later
    # "it never fired" and "it fired and found nothing" must be tellable apart.
    root = Path(temporary)
    settings = AppSettings(root / "settings.json")
    settings.update(auto_capture_value=True)

    class LoggingArchive(FakeArchive):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.root = root / "history"

        def status(self, key):
            state = super().status(key)
            return {**state, "itemCount": 4321, "pages": 77, "elapsedSeconds": 12.5,
                    "error": None if state["complete"] else "stopped early",
                    "errorKind": None if state["complete"] else "collection_failed"}

    archive = LoggingArchive(floor_day=date(2026, 8, 29), complete=set())
    capture = AutoCapture(archive, settings, now=lambda: NOW)
    capture.run_once()

    log = root / "capture-log.jsonl"
    assert log.exists(), "a run must leave a record"
    first = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[0])
    assert first["captured"] and first["startedAt"] and first["durationSeconds"] >= 0, first
    assert first["blocks"] and first["blocks"][0]["images"] == 4321, first["blocks"][0]
    assert first["blocks"][0]["requests"] == 77, first["blocks"][0]

    # A run with nothing to do still records, or a silent log is ambiguous.
    capture.run_once()
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 2, entries
    assert entries[1]["captured"] == [] and entries[1]["blocks"] == [], entries[1]

    # A refusal keeps the reason, which is the only thing worth having afterwards.
    refused = AutoCapture(Refusing(date(2026, 8, 29), set()), settings, now=lambda: NOW)
    refused.archive.root = root / "history"
    refused.run_once()
    last = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert last["skipped"] and "out of reach" in last["blocks"][0]["reason"], last

print({"lookbackDays": CAPTURE_LOOKBACK_DAYS, "oldestFirst": True,
       "unreachableSkipped": True, "todayNeverCaptured": True, "idempotent": True,
       "installsSpreadAcrossInterval": True, "slotStableAcrossRestarts": True,
       "everyRunLogged": True, "noopRunsLogged": True, "failureReasonsKept": True})
