"""Collect recent days before Civitai's feed can no longer reach them.

Civitai caps how deep its public image list can be paged, and that ceiling counts rows
rather than time, so it moves forward as new artwork is posted. A day not collected
within roughly two days of its end is out of reach permanently -- there is no parameter,
credential, or retry that recovers it. Everything else the app does to a day (tags,
reaction counts, artist indexes) can be done at any time afterwards.

That asymmetry is the whole reason this exists: the listing sweep is the only perishable
step, so it is the only one worth running unattended.

Note that capturing "just the image ids" is not cheaper than capturing everything. The
public API has no field selector, so a request returns up to 200 whole listing rows
whichever fields are wanted, and the cost is measured in requests. The listing already
carries the id, url, creator, timestamp, dimensions, reaction counts, base model, and
visual hash, so the archive stores all of it -- discarding the rest would save nothing
and throw away data already paid for.
"""

from __future__ import annotations

from datetime import date, datetime, time as day_time, timedelta
import json
import threading
import traceback

from .history import LOCAL_ZONE, active_backend, oldest_buildable_day
from .recovery import CaptureRecovery
from .site import RATING_RANK


# A capture only ever collects days that have already ended, so a day is never captured
# while artwork is still being posted to it.
CAPTURE_SEGMENTS = ("morning", "evening")
# How many recent days to consider. The feed reaches back about two, so looking four back
# covers the reachable range with margin without ever attempting the plainly impossible.
CAPTURE_LOOKBACK_DAYS = 4
# Unattended work that leaves no trace cannot be diagnosed after the fact. Every run
# appends one line here, including runs that did nothing, so "it never fired" and "it
# fired and found nothing to do" are distinguishable days later.
CAPTURE_LOG_BYTES = 1024 * 1024


def day_bounds(value: date) -> tuple[datetime, datetime]:
    start = datetime.combine(value, day_time.min, LOCAL_ZONE)
    return start, start + timedelta(days=1)


def segment_bounds(value: date, segment: str) -> tuple[datetime, datetime]:
    start, end = day_bounds(value)
    midday = start + timedelta(hours=12)
    if segment == "morning":
        return start, midday
    if segment == "evening":
        return midday, end
    raise ValueError("Automatic capture requires a morning or evening segment")


class AutoCapture:
    """Runs the listing sweep for recent days on a fixed interval."""

    def __init__(self, archive, settings, now=None):
        self.archive = archive
        self.settings = settings
        self._now = now or (lambda: datetime.now(LOCAL_ZONE))
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.last_run: str | None = None
        self.last_result: dict | None = None
        self.recovery = CaptureRecovery(archive) if hasattr(archive, "connect") else None

    # -- planning ---------------------------------------------------------------

    def pending_blocks(self) -> list[tuple[str, str]]:
        """Which recent blocks are unarchived and still reachable, oldest first.

        Oldest first matters: the oldest reachable block is the one about to fall out of
        the window, so it is the one that cannot wait for the next interval.
        """
        rating = self.settings.load()["captureCoverage"]
        now = self._now()
        if self.recovery:
            self.recovery.audit(now)
        retries = self.recovery.entries() if self.recovery else {}
        try:
            window = self.archive.history_window(rating) if active_backend() != "search" else {}
        except Exception as error:
            if self.recovery:
                keys = {key for key, row in retries.items() if row["next_retry_at"] <= now.timestamp()}
                yesterday = (now.date() - timedelta(days=1)).isoformat()
                for segment in CAPTURE_SEGMENTS:
                    key = self.archive.archive_key(yesterday, segment)
                    status = self.archive.status(key)
                    if not status["complete"] and status["state"] != "loading":
                        keys.add(key)
                for key in keys:
                    self.recovery.failed(key, str(error), now)
            raise
        floor = window.get("floor")
        oldest = None
        if floor:
            oldest = oldest_buildable_day(datetime.fromisoformat(floor))
        today = now.date()
        candidates = set(retries)
        for back in range(CAPTURE_LOOKBACK_DAYS, 0, -1):
            value = today - timedelta(days=back)
            for segment in CAPTURE_SEGMENTS:
                candidates.add(self.archive.archive_key(value.isoformat(), segment))
        pending: list[tuple[str, str]] = []
        for key in sorted(candidates):
            value, segment = key.split("#", 1)
            retry = retries.get(key)
            if retry and retry["next_retry_at"] > now.timestamp():
                continue
            if oldest is not None and date.fromisoformat(value) < oldest:
                if retry:
                    self.recovery.failed(key, "Civitai's public feed cannot currently reach this date. "
                                         "Saved progress is retained.", now, unreachable=True)
                continue
            status = self.archive.status(key)
            if status["state"] == "loading" or (status["complete"] and not retry):
                continue
            pending.append((value, segment))
        return pending

    # -- running ----------------------------------------------------------------

    def _log(self, record: dict) -> None:
        """Append one run to the rotating capture journal."""
        try:
            path = self.archive.root.parent / "capture-log.jsonl"
            line = json.dumps(record, ensure_ascii=False) + chr(10)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size + len(line) > CAPTURE_LOG_BYTES:
                path.replace(path.with_suffix(".jsonl.1"))
            with path.open("a", encoding="utf-8") as output:
                output.write(line)
        except OSError:
            pass   # Logging must never be the reason a capture fails.

    def run_once(self) -> dict:
        """Capture every pending block, in order. Returns what it did."""
        started = self._now()
        captured, failed, skipped, details = [], [], [], []
        for value, segment in self.pending_blocks():
            if self.stopping.is_set():
                break
            start, end = segment_bounds(date.fromisoformat(value), segment)
            key = self.archive.archive_key(value, segment)
            try:
                retry = self.recovery.entries().get(key) if self.recovery else None
                rating = self.settings.load()["captureCoverage"]
                if retry:
                    with self.archive.connect() as db:
                        stored = db.execute("SELECT content_rating FROM days WHERE day=?", (key,)).fetchone()
                    if stored and stored[0] in RATING_RANK:
                        rating = max((rating, stored[0]), key=RATING_RANK.get)
                if retry and retry["restart_required"]:
                    self.archive.restart_capture(key)
                    self.recovery.restarted(key)
                self.archive.start(value, start.isoformat(), end.isoformat(),
                                   str(LOCAL_ZONE), segment, rating)
            except ValueError as error:
                # Out of reach or otherwise refused before any work started.
                skipped.append(key)
                details.append({"block": key, "outcome": "skipped", "reason": str(error)[:300]})
                if self.recovery:
                    self.recovery.failed(key, str(error), self._now())
                continue
            # A block is collected start to finish before the next one begins, so the
            # archive never has two feeds competing for the same paced request lane.
            while not self.stopping.wait(2):
                status = self.archive.status(key)
                if status["state"] not in {"loading"}:
                    break
            status = self.archive.status(key)
            done = bool(status["complete"])
            if self.recovery:
                if done:
                    self.recovery.completed(key)
                else:
                    self.recovery.failed(key, status.get("error") or "Collection was interrupted.",
                                         self._now(), restart=status.get("errorKind") == "suspicious_capture")
            (captured if done else failed).append(key)
            details.append({"block": key, "outcome": "captured" if done else "failed",
                            "images": status.get("itemCount"),
                            "requests": status.get("pages"),
                            "elapsedSeconds": status.get("elapsedSeconds"),
                            # The reason a block did not finish is the whole point of
                            # keeping this, so carry it rather than just the block name.
                            "error": status.get("error"),
                            "errorKind": status.get("errorKind")})
        finished = self._now()
        result = {"captured": captured, "failed": failed, "skipped": skipped,
                  "at": finished.isoformat(),
                  "startedAt": started.isoformat(),
                  "durationSeconds": round((finished - started).total_seconds(), 1),
                  "blocks": details}
        with self.lock:
            self.last_run, self.last_result = result["at"], result
        self._log(result)
        return result

    def seconds_until_next(self, settings: dict | None = None) -> float:
        """Seconds to this install's own slot in the next interval.

        Every copy of the app wakes on the same interval, so without a spread they would
        all reach Civitai at the same moment -- and a synchronised burst is worse for a
        service than the same requests scattered. The slot is derived from a stable
        per-install number, so it does not drift between restarts but differs between
        installs, and the interval is covered uniformly.

        Deliberately not aimed at "quiet hours": measured over 626,407 archived images,
        Civitai's posting volume runs 3.35%-4.83% per hour against a 4.17% flat line, so
        the quietest six-hour block carries 22.2% of a day rather than 25%. Steering every
        install into that window would concentrate them about four times over for an ~11%
        quieter baseline, which is a straight loss.
        """
        settings = settings or self.settings.load()
        now = self._now()
        return (self.next_run_at(settings, now) - now).total_seconds()

    def next_run_at(self, settings: dict | None = None, now: datetime | None = None):
        """The datetime of this install's next slot.

        Both the countdown and the displayed clock time come from here, so they cannot
        disagree -- computing them from two separate readings of the clock left the shown
        time a fraction of a second short, which rendered a chosen 3:30 as 3:29.
        """
        settings = settings or self.settings.load()
        now = now or self._now()
        period = settings["autoCaptureHours"] * 3600
        chosen = settings.get("autoCaptureMinute")
        if chosen is not None:
            # An explicit local time, honoured exactly. Picking one gives up the spread,
            # which is why it is not the default.
            target = now.replace(hour=chosen // 60, minute=chosen % 60,
                                 second=0, microsecond=0)
            while target <= now:
                target += timedelta(seconds=period)
            return self._with_recovery_time(target, settings, now)
        slot = settings["captureSeed"] % period
        elapsed = (now.timestamp() - slot) % period
        return self._with_recovery_time(now + timedelta(seconds=period - elapsed), settings, now)

    def _with_recovery_time(self, scheduled: datetime, settings: dict, now: datetime) -> datetime:
        retry = self.recovery.next_retry_at() if self.recovery and settings["autoCapture"] else None
        if retry is not None:
            return min(scheduled, datetime.fromtimestamp(max(now.timestamp() + 1, retry), now.tzinfo))
        return scheduled

    def _worker(self) -> None:
        while not self.stopping.is_set():
            settings = self.settings.load()
            if settings["autoCapture"]:
                try:
                    self.run_once()
                except Exception:  # noqa: BLE001
                    # A capture failure must never take the app down with it; the next
                    # interval simply tries again.
                    try:
                        with (self.archive.root.parent / "error.log").open(
                                "a", encoding="utf-8") as output:
                            output.write(f"\n[{self._now().isoformat()}] Auto capture\n"
                                         f"{traceback.format_exc()}")
                    except OSError:
                        pass
            # Re-read settings each cycle so a change takes effect without a restart,
            # and wake early when asked to stop or to run now.
            self.wake.wait(max(1.0, self.seconds_until_next()))
            self.wake.clear()

    def start(self) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.stopping.clear()
            self.thread = threading.Thread(target=self._worker, daemon=True,
                                           name="auto-capture")
            self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        self.wake.set()

    def trigger(self) -> None:
        """Ask the worker to wake and capture now rather than at the next interval."""
        self.wake.set()

    def status(self) -> dict:
        settings = self.settings.load()
        now = self._now()
        next_run = self.next_run_at(settings, now)
        with self.lock:
            running = bool(self.thread and self.thread.is_alive())
            return {"enabled": settings["autoCapture"],
                    "intervalHours": settings["autoCaptureHours"],
                    "coverage": settings["captureCoverage"],
                    "running": running, "lastRun": self.last_run,
                    "lastResult": self.last_result,
                    "recovery": list(self.recovery.entries().values()) if self.recovery else [],
                    "atMinute": settings.get("autoCaptureMinute"),
                    "nextRunAt": next_run.isoformat(),
                    "nextRunInSeconds": round((next_run - now).total_seconds())}
