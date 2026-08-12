"""Small, checkpointed SQLite calendar history for Civitai discovery."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time as day_time, timedelta, timezone, tzinfo
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from .civitai import API_URL, USER_AGENT, normalize, utcnow
from .site import DEFAULT_CONTENT_RATING, RATING_RANK, content_rating, image_url


PAGE_SIZE = 200
RATE_LIMIT_RETRIES = 8
class AdaptivePacer:
    """Conservative request pacing that responds to live service conditions."""

    def __init__(self, initial: float = 1.0, minimum: float = 0.75, maximum: float = 8.0):
        self.interval = initial
        self.minimum = minimum
        self.maximum = maximum
        self.clean_streak = 0

    def success(self, latency: float) -> None:
        if latency >= 3.0:
            # A serialized slow response already spaces the next request. Do not
            # add another latency penalty unless Civitai returns an actual error.
            self.clean_streak = 0
            return
        self.clean_streak += 1
        if self.clean_streak >= 10:
            self.interval = max(self.minimum, self.interval - 0.1)
            self.clean_streak = 0

    def failure(self, reason: str) -> None:
        floor = 5.0 if reason == "rate_limited" else 2.0
        multiplier = 2.0 if reason == "rate_limited" else 1.5
        self.interval = min(self.maximum, max(floor, self.interval * multiplier))
        self.clean_streak = 0


def conservative_eta_range(seconds: float | None) -> tuple[int, int] | None:
    """Turn a noisy point estimate into a stable, human-friendly range."""
    if seconds is None or not math.isfinite(seconds) or seconds < 10:
        return None
    step = 15 if seconds < 120 else 60 if seconds < 900 else 300
    low = max(step, math.floor(seconds * 0.75 / step) * step)
    high = max(low + step, math.ceil(seconds * 1.5 / step) * step)
    return low, high


class CentralTime(tzinfo):
    def _transition(self, year: int, month: int, occurrence: int) -> datetime:
        first = datetime(year, month, 1)
        first_sunday = 1 + (6 - first.weekday()) % 7
        return datetime(year, month, first_sunday + 7 * (occurrence - 1), 2)

    def dst(self, value: datetime | None) -> timedelta:
        if value is None:
            return timedelta(0)
        naive = value.replace(tzinfo=None)
        return timedelta(hours=1) if self._transition(value.year, 3, 2) <= naive < self._transition(value.year, 11, 1) else timedelta(0)

    def utcoffset(self, value: datetime | None) -> timedelta:
        return timedelta(hours=-6) + self.dst(value)

    def tzname(self, value: datetime | None) -> str:
        return "CDT" if self.dst(value) else "CST"

    def fromutc(self, value: datetime) -> datetime:
        naive_utc = value.replace(tzinfo=None)
        start_local = self._transition(value.year, 3, 2)
        end_local = self._transition(value.year, 11, 1)
        start_utc = start_local + timedelta(hours=6)
        end_utc = end_local + timedelta(hours=5)
        offset = timedelta(hours=-5 if start_utc <= naive_utc < end_utc else -6)
        return (naive_utc + offset).replace(tzinfo=self)


LOCAL_ZONE = CentralTime()


def previous_local_day() -> str:
    return (datetime.now().date() - timedelta(days=1)).isoformat()


def parse_day(value: str) -> date:
    return date.fromisoformat(value)


def parse_bounds(value: str, start_utc: str, end_utc: str) -> tuple[datetime, datetime]:
    parse_day(value)
    start = parse_timestamp(start_utc); end = parse_timestamp(end_utc)
    duration = (end - start).total_seconds()
    valid_duration = 11 * 3600 <= duration <= 13 * 3600 or 23 * 3600 <= duration <= 25 * 3600
    if start.tzinfo is None or end.tzinfo is None or not valid_duration:
        raise ValueError("Invalid local-day UTC boundaries")
    if end > datetime.now(timezone.utc) + timedelta(hours=1):
        raise ValueError("History is available only for completed days")
    return start, end


def bounds(value: date) -> tuple[datetime, datetime]:
    start = datetime.combine(value, day_time.min, LOCAL_ZONE)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _model_clause(models: list[str] | None) -> tuple[str, tuple]:
    """SQL fragment restricting to chosen generation models. Empty means no restriction."""
    names = [name for name in (models or []) if name]
    if not names:
        return "", ()
    holes = ",".join("?" for _ in names)
    return (f" AND COALESCE(NULLIF(i.base_model,''),'Unknown') IN ({holes})", tuple(names))


def _rating_levels(rating: str) -> tuple[str, ...]:
    return ("None", "Soft") if rating == "Soft" else (("None", "Soft", "Mature") if rating == "Mature" else ("None", "Soft", "Mature", "X"))


def _rating_clause(rating: str) -> tuple[str, tuple[str, ...]]:
    levels = _rating_levels(rating)
    return f" AND COALESCE(i.nsfw_level,'None') IN ({','.join('?' for _ in levels)})", levels


def preview_url(url: str, width: int = 768) -> str:
    parts = url.rsplit("/", 2)
    if len(parts) == 3 and (parts[-2] == "original=true" or parts[-2].startswith("width=")):
        parts[-2] = f"width={width}"
        return "/".join(parts)
    return url


class HistoryArchive:
    @staticmethod
    def archive_key(value: str, segment: str = "all") -> str:
        parse_day(value)
        if segment not in {"all", "morning", "evening"}:
            raise ValueError("Invalid day segment")
        return value if segment == "all" else f"{value}#{segment}"

    def __init__(self, root: Path, selected_content_rating: str = DEFAULT_CONTENT_RATING):
        self.root = root
        self.db_path = root / "history.sqlite3"
        self.jobs: dict[str, dict] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.lock = threading.RLock()
        self.api_lock = threading.Lock()
        self.last_api_request = 0.0
        self.api_pacer = AdaptivePacer()
        self.content_rating = content_rating(selected_content_rating)
        # Optional hook the host sets to prepare a finished block for personalised views.
        self.on_block_complete = None
        self._initialize()
        self._migrate_json_once()
        self._publish_completed_days()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                CREATE TABLE IF NOT EXISTS images (
                    id INTEGER PRIMARY KEY, local_date TEXT NOT NULL, post_id INTEGER,
                    username TEXT NOT NULL, username_key TEXT NOT NULL, created_at TEXT NOT NULL,
                    url TEXT NOT NULL, width INTEGER, height INTEGER, type TEXT,
                    nsfw_level TEXT, browsing_level INTEGER, base_model TEXT,
                    model_version_ids TEXT NOT NULL DEFAULT '[]', stats TEXT NOT NULL DEFAULT '{}',
                    prompt TEXT NOT NULL DEFAULT '', negative_prompt TEXT NOT NULL DEFAULT '',
                    resources TEXT NOT NULL DEFAULT '[]', details_loaded INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS images_day_creator ON images(local_date, username_key);
                CREATE INDEX IF NOT EXISTS images_day_created ON images(local_date, created_at DESC);
                CREATE TABLE IF NOT EXISTS days (
                    day TEXT PRIMARY KEY, complete INTEGER NOT NULL DEFAULT 0,
                    scan_cursor TEXT, older_cursor TEXT, pages INTEGER NOT NULL DEFAULT 0,
                    metadata_bytes INTEGER NOT NULL DEFAULT 0, updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS day_artists (
                    day TEXT NOT NULL, username_key TEXT NOT NULL, username TEXT NOT NULL,
                    image_count INTEGER NOT NULL, representative_id INTEGER NOT NULL,
                    newest_at TEXT NOT NULL, rank_order INTEGER NOT NULL,
                    PRIMARY KEY(day, username_key)
                );
                CREATE INDEX IF NOT EXISTS day_artists_rank ON day_artists(day, rank_order);
                CREATE TABLE IF NOT EXISTS block_images (
                    block_key TEXT NOT NULL, image_id INTEGER NOT NULL,
                    PRIMARY KEY(block_key,image_id), FOREIGN KEY(image_id) REFERENCES images(id)
                );
                CREATE INDEX IF NOT EXISTS block_images_image ON block_images(image_id);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(days)")}
            for name in ("timezone", "start_utc", "end_utc", "top_cursor", "content_rating"):
                if name not in columns: db.execute(f"ALTER TABLE days ADD COLUMN {name} TEXT")
            numeric_metrics = {
                "seek_pages": "INTEGER NOT NULL DEFAULT 0", "seek_bytes": "INTEGER NOT NULL DEFAULT 0",
                "collect_pages": "INTEGER NOT NULL DEFAULT 0", "collect_bytes": "INTEGER NOT NULL DEFAULT 0",
                "api_seconds": "REAL NOT NULL DEFAULT 0", "retry_seconds": "REAL NOT NULL DEFAULT 0",
                "retry_count": "INTEGER NOT NULL DEFAULT 0", "elapsed_seconds": "REAL NOT NULL DEFAULT 0",
                "seek_seconds": "REAL NOT NULL DEFAULT 0", "organize_seconds": "REAL NOT NULL DEFAULT 0",
            }
            columns = {row[1] for row in db.execute("PRAGMA table_info(days)")}
            for name, declaration in numeric_metrics.items():
                if name not in columns: db.execute(f"ALTER TABLE days ADD COLUMN {name} {declaration}")
            db.execute("INSERT OR IGNORE INTO block_images(block_key,image_id) SELECT local_date,id FROM images")

    def _migrate_json_once(self) -> None:
        marker = self.root / ".json_migrated"
        if marker.exists():
            return
        days_dir = self.root / "days"
        manifest_path = self.root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"days": {}}
        for path in sorted(days_dir.glob("*.json")) if days_dir.exists() else []:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._upsert_normalized(data.get("items", []))
            info = manifest.get("days", {}).get(path.stem, {})
            with self.connect() as db:
                db.execute("""INSERT INTO days(day,complete,scan_cursor,older_cursor,pages,metadata_bytes,updated_at)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET complete=excluded.complete,
                    scan_cursor=excluded.scan_cursor,older_cursor=excluded.older_cursor,pages=excluded.pages,
                    metadata_bytes=excluded.metadata_bytes,updated_at=excluded.updated_at""",
                    (path.stem, int(bool(data.get("complete") or info.get("complete"))), info.get("scanCursor"),
                     info.get("olderCursor"), int(info.get("pages", 0)), int(info.get("metadataBytes", 0)), data.get("updatedAt") or utcnow()))
            if data.get("complete") or info.get("complete"):
                self.build_artist_index(path.stem)
        marker.write_text(utcnow(), encoding="utf-8")

    def _upsert_normalized(self, items: list[dict], forced_date: str | None = None) -> list[int]:
        rows = []
        for item in items:
            if not item.get("id") or not item.get("createdAt") or not item.get("url"):
                continue
            local_date = forced_date or parse_timestamp(item["createdAt"]).astimezone(LOCAL_ZONE).date().isoformat()
            username = str(item.get("username") or "Unknown")
            rows.append((int(item["id"]), local_date, item.get("postId"), username, username.casefold(), item["createdAt"],
                item["url"], item.get("width"), item.get("height"), item.get("type"), item.get("nsfwLevel"),
                item.get("browsingLevel"), item.get("baseModel"), json.dumps(item.get("modelVersionIds") or []),
                json.dumps(item.get("stats") or {}), item.get("prompt") or "", item.get("negativePrompt") or "",
                json.dumps(item.get("resources") or []), int(bool(item.get("prompt") or item.get("resources")))))
        with self.connect() as db:
            # Name every column: archives written by older versions carry columns this
            # schema no longer declares, and a positional insert breaks against them.
            db.executemany("""INSERT INTO images(
                    id, local_date, post_id, username, username_key, created_at,
                    url, width, height, type, nsfw_level, browsing_level, base_model,
                    model_version_ids, stats, prompt, negative_prompt, resources, details_loaded)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET local_date=excluded.local_date,post_id=excluded.post_id,
                username=excluded.username,username_key=excluded.username_key,created_at=excluded.created_at,
                url=excluded.url,width=excluded.width,height=excluded.height,type=excluded.type,
                nsfw_level=excluded.nsfw_level,browsing_level=excluded.browsing_level,
                base_model=excluded.base_model,model_version_ids=excluded.model_version_ids,
                stats=excluded.stats,prompt=CASE WHEN excluded.prompt!='' THEN excluded.prompt ELSE images.prompt END,
                negative_prompt=CASE WHEN excluded.negative_prompt!='' THEN excluded.negative_prompt ELSE images.negative_prompt END,
                resources=CASE WHEN excluded.resources!='[]' THEN excluded.resources ELSE images.resources END,
                details_loaded=MAX(images.details_loaded,excluded.details_loaded)""", rows)
            db.executemany("INSERT OR IGNORE INTO block_images(block_key,image_id) VALUES(?,?)",
                [(row[1], row[0]) for row in rows])
        return [row[0] for row in rows]

    def _row_item(self, row: sqlite3.Row, details: bool = False) -> dict:
        item = {"id": row["id"], "postId": row["post_id"], "username": row["username"], "createdAt": row["created_at"],
            "url": row["url"], "thumbnailUrl": preview_url(row["url"]), "civitaiUrl": image_url(row["id"]),
            "width": row["width"], "height": row["height"], "type": row["type"], "nsfwLevel": row["nsfw_level"],
            "browsingLevel": row["browsing_level"], "baseModel": row["base_model"] or "Unknown",
            "modelVersionIds": json.loads(row["model_version_ids"]), "stats": json.loads(row["stats"])}
        if details:
            item.update({"prompt": row["prompt"], "negativePrompt": row["negative_prompt"], "resources": json.loads(row["resources"]), "detailsLoaded": bool(row["details_loaded"]), "detailImageUrl": preview_url(row["url"], 1280)})
        return item

    def status(self, value: str) -> dict:
        with self.connect() as db:
            day = db.execute("SELECT * FROM days WHERE day=?", (value,)).fetchone()
            rating_clause, rating_params = _rating_clause(self.content_rating)
            count = db.execute("SELECT COUNT(*) FROM block_images b JOIN images i ON i.id=b.image_id "
                f"WHERE b.block_key=?{rating_clause}", (value, *rating_params)).fetchone()[0]
            creators = db.execute("SELECT COUNT(DISTINCT i.username_key) FROM block_images b JOIN images i ON i.id=b.image_id "
                f"WHERE b.block_key=?{rating_clause}", (value, *rating_params)).fetchone()[0]
        with self.lock:
            job = dict(self.jobs.get(value, {}))
        coverage = (day["content_rating"] if day and day["content_rating"] else DEFAULT_CONTENT_RATING)
        needs_upgrade = bool(day and day["complete"] and RATING_RANK[coverage] < RATING_RANK[self.content_rating])
        archive_complete = bool(day and day["complete"])
        complete = archive_complete and not needs_upgrade and job.get("state") not in {"loading", "error"}
        started = job.get("startedMonotonic")
        # Freeze completed durations at the persisted database value. Keeping the live
        # monotonic clock running after completion made yesterday's 32-minute block look
        # eighteen hours long when status was read the following morning.
        elapsed = (round(max(0.0, time.monotonic() - started))
                   if job.get("state") == "loading" and isinstance(started, (int, float)) else None)
        deadline = job.get("retryUntilMonotonic")
        retry_in = (max(0, round(deadline - time.monotonic()))
                    if job.get("delayReason") and isinstance(deadline, (int, float)) else None)
        elapsed_metric = elapsed if elapsed is not None else (round(day["elapsed_seconds"], 1) if day else 0)
        metrics = {"seekPages": int(day["seek_pages"] or 0) if day else 0,
            "seekBytes": int(day["seek_bytes"] or 0) if day else 0,
            "collectPages": int(day["collect_pages"] or 0) if day else 0,
            "collectBytes": int(day["collect_bytes"] or 0) if day else 0,
            "apiSeconds": round(float(day["api_seconds"] or 0), 2) if day else 0,
            "retrySeconds": round(float(day["retry_seconds"] or 0), 2) if day else 0,
            "retryCount": int(day["retry_count"] or 0) if day else 0,
            "elapsedSeconds": elapsed_metric,
            "seekSeconds": round(float(day["seek_seconds"] or 0), 2) if day else 0,
            "organizeSeconds": round(float(day["organize_seconds"] or 0), 2) if day else 0}
        metrics["collectSeconds"] = round(max(0, metrics["elapsedSeconds"] - metrics["seekSeconds"] - metrics["organizeSeconds"]), 2)
        return {"date": value, "state": job.get("state") or ("complete" if complete else "not_started"),
            "progress": job.get("progress", 100 if complete else 0), "pages": job.get("pages", 0),
            "phase": job.get("phase", "complete" if complete else "waiting"), "itemCount": count,
            "creatorCount": creators, "elapsedSeconds": elapsed,
            "etaSeconds": job.get("etaSeconds"), "etaLowSeconds": job.get("etaLowSeconds"),
            "etaHighSeconds": job.get("etaHighSeconds"), "delayReason": job.get("delayReason"),
            "retryInSeconds": retry_in, "retryAttempt": job.get("retryAttempt") or 0,
            "retryAttempts": job.get("retryAttempts") or RATE_LIMIT_RETRIES,
            "listingsChecked": int(job.get("pages", 0)) * PAGE_SIZE,
            "searchReachedAt": job.get("searchReachedAt"),
            "error": job.get("error"), "complete": complete, "archiveComplete": archive_complete,
            "rebuilding": bool(job.get("rebuilding")), "contentRating": self.content_rating,
            "metrics": metrics,
            "archiveContentRating": coverage if day else None, "needsUpgrade": needs_upgrade}

    def set_content_rating(self, value: str) -> None:
        """Change the listing ceiling; completed indexes are rebuilt to hide higher rows."""
        rating = content_rating(value)
        with self.lock:
            if any(job.get("state") == "loading" for job in self.jobs.values()):
                raise ValueError("Stop the current history build before changing content ratings")
            self.content_rating = rating
        with self.connect() as db:
            days = [row[0] for row in db.execute("SELECT day FROM days WHERE complete=1")]
        for day in days:
            self.build_artist_index(day)

    def start(self, value: str, start_utc: str, end_utc: str, timezone_name: str, segment: str = "all") -> dict:
        key = self.archive_key(value, segment)
        start, end = parse_bounds(value, start_utc, end_utc)
        current = self.status(key)
        if current["complete"] or current["state"] == "loading":
            return current
        with self.lock:
            self.jobs[key] = {"state": "loading", "phase": "locating", "progress": 0, "pages": 0,
                "startedMonotonic": time.monotonic(), "etaLowSeconds": None, "etaHighSeconds": None,
                "delayReason": None}
            self.cancel_events[key] = threading.Event()
        with self.connect() as db:
            db.execute("""INSERT INTO days(day,complete,timezone,start_utc,end_utc,content_rating,updated_at) VALUES(?,0,?,?,?,?,?)
                ON CONFLICT(day) DO UPDATE SET complete=0,timezone=excluded.timezone,start_utc=excluded.start_utc,
                end_utc=excluded.end_utc,content_rating=excluded.content_rating,seek_pages=0,seek_bytes=0,
                collect_pages=0,collect_bytes=0,api_seconds=0,retry_seconds=0,retry_count=0,
                elapsed_seconds=0,seek_seconds=0,organize_seconds=0,updated_at=excluded.updated_at""",
                (key, timezone_name[:100], start.isoformat(), end.isoformat(), self.content_rating, utcnow()))
        threading.Thread(target=self._collect, args=(key, value, start, end), daemon=True, name=f"history-{key}").start()
        return self.status(key)

    def rebuild(self, value: str, start_utc: str, end_utc: str, timezone_name: str, segment: str = "all") -> dict:
        key = self.archive_key(value, segment)
        start, end = parse_bounds(value, start_utc, end_utc)
        current = self.status(key)
        if not current["archiveComplete"]:
            raise ValueError("Build this day before rebuilding it")
        if current["state"] == "loading":
            return current
        with self.lock:
            self.jobs[key] = {"state": "loading", "phase": "locating", "progress": 0, "pages": 0,
                "startedMonotonic": time.monotonic(), "etaLowSeconds": None, "etaHighSeconds": None,
                "delayReason": None, "rebuilding": True}
            self.cancel_events[key] = threading.Event()
        with self.connect() as db:
            db.execute("UPDATE days SET timezone=?,start_utc=?,end_utc=?,seek_pages=0,seek_bytes=0,"
                "collect_pages=0,collect_bytes=0,api_seconds=0,retry_seconds=0,retry_count=0,elapsed_seconds=0,"
                "seek_seconds=0,organize_seconds=0,updated_at=? WHERE day=?",
                (timezone_name[:100], start.isoformat(), end.isoformat(), utcnow(), key))
        threading.Thread(target=self._collect, args=(key, value, start, end, True), daemon=True, name=f"rebuild-{key}").start()
        return self.status(key)

    def cancel(self, value: str | None = None) -> None:
        with self.lock:
            targets = [value] if value else list(self.cancel_events)
            for target in targets:
                event = self.cancel_events.get(target)
                if event:
                    event.set()
                if target in self.jobs and self.jobs[target].get("state") == "loading":
                    self.jobs[target].update({"state": "cancelled", "phase": "cancelled", "etaSeconds": None})

    def checkpoint(self) -> None:
        """Merge committed WAL pages into the main database for clean backups."""
        with self.connect() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _cursor_for(self, target: date) -> str | None:
        with self.connect() as db:
            own = db.execute("SELECT scan_cursor FROM days WHERE day=? AND complete=0", (target.isoformat(),)).fetchone()
            if own and own[0]:
                return own[0]
            newer = db.execute("SELECT older_cursor FROM days WHERE day>? AND complete=1 AND older_cursor IS NOT NULL ORDER BY day LIMIT 1", (target.isoformat(),)).fetchone()
            return newer[0] if newer else None

    def _cursor_for_key(self, key: str) -> str | None:
        with self.connect() as db:
            own = db.execute("SELECT scan_cursor FROM days WHERE day=? AND complete=0", (key,)).fetchone()
            return own[0] if own and own[0] else None

    def _rebuild_cursor_for(self, target: date) -> str | None:
        with self.connect() as db:
            own = db.execute("SELECT top_cursor FROM days WHERE day=?", (target.isoformat(),)).fetchone()
            if own and own[0]:
                return own[0]
            newer = db.execute("SELECT older_cursor FROM days WHERE day>? AND complete=1 AND older_cursor IS NOT NULL ORDER BY day LIMIT 1", (target.isoformat(),)).fetchone()
            return newer[0] if newer else None

    def _rebuild_cursor_for_key(self, key: str) -> str | None:
        with self.connect() as db:
            own = db.execute("SELECT top_cursor FROM days WHERE day=?", (key,)).fetchone()
            return own[0] if own and own[0] else None

    def _wait_api_lane(self, minimum_interval: float | None = None) -> None:
        interval = max(self.api_pacer.interval, minimum_interval or 0.0)
        remaining = interval - (time.monotonic() - self.last_api_request)
        if remaining > 0:
            time.sleep(remaining)

    def _request(self, params: dict, minimum_interval: float | None = None,
                 on_delay: Callable[[str, float, int, int], None] | None = None) -> tuple[dict, int]:
        """Fetch one page, retrying transient failures.

        ``on_delay`` receives the attempt number so callers can tell a single
        hiccup apart from a service that has genuinely stopped answering.
        """
        request = urllib.request.Request(f"{API_URL}?{urllib.parse.urlencode(params)}",
            headers={"Accept": "application/json", "Accept-Encoding": "gzip", "User-Agent": USER_AGENT})
        with self.api_lock:
            for attempt in range(RATE_LIMIT_RETRIES):
                try:
                    self._wait_api_lane(minimum_interval)
                    self.last_api_request = time.monotonic()
                    started = time.monotonic()
                    with urllib.request.urlopen(request, timeout=60) as response:
                        raw = response.read()
                        if response.headers.get("Content-Encoding") == "gzip":
                            raw = gzip.decompress(raw)
                    self.api_pacer.success(time.monotonic() - started)
                    return json.loads(raw), len(raw)
                except urllib.error.HTTPError as error:
                    if error.code != 429 and error.code < 500:
                        raise
                    self.api_pacer.failure("rate_limited" if error.code == 429 else "service_retry")
                    retry = error.headers.get("Retry-After")
                    if error.code == 429:
                        try: wait = max(60.0, float(retry)) if retry else min(600.0, 60.0 * (2**attempt))
                        except ValueError: wait = min(600.0, 60.0 * (2**attempt))
                    else: wait = min(60.0, 2.0**attempt)
                    if on_delay:
                        on_delay("rate_limited" if error.code == 429 else "service_retry",
                                 wait, attempt + 1, RATE_LIMIT_RETRIES)
                    time.sleep(wait + random.uniform(0, 2))
                except (TimeoutError, urllib.error.URLError):
                    self.api_pacer.failure("network_retry")
                    if attempt == RATE_LIMIT_RETRIES - 1: raise
                    wait = min(60, 2**attempt)
                    if on_delay:
                        on_delay("network_retry", wait, attempt + 1, RATE_LIMIT_RETRIES)
                    time.sleep(wait + random.uniform(0, 1))
        raise RuntimeError("Civitai request exhausted its retry budget")

    def _seek_cursor(self, value: str, end: datetime, cancel_event: threading.Event,
                     report_delay: Callable[[str, float, int, int], None]) -> tuple[str | None, int, int]:
        """Jump close to a day boundary using Civitai's offset cursor.

        The REST cursor is shaped as ``offset|timestamp``. We only probe offsets
        aligned to full pages, first exponentially and then with binary search,
        and return the earliest page whose oldest row crosses the requested end.
        Sequential collection still re-reads that crossing page, so no boundary
        records depend on the probe response itself.
        """
        timestamp_ms = int(end.timestamp() * 1000)
        pages = transferred = 0

        def probe(offset: int) -> tuple[datetime | None, datetime | None]:
            nonlocal pages, transferred
            if cancel_event.is_set():
                return None, None
            params = {"limit": PAGE_SIZE, "sort": "Newest", "period": "AllTime",
                "nsfw": self.content_rating, "withMeta": "false",
                "cursor": f"{offset}|{timestamp_ms}"}
            request_started = time.monotonic()
            payload, size = self._request(params, on_delay=report_delay)
            request_seconds = time.monotonic() - request_started
            pages += 1; transferred += size
            with self.connect() as db:
                db.execute("UPDATE days SET seek_pages=?,seek_bytes=?,api_seconds=api_seconds+? WHERE day=?",
                    (pages, transferred, request_seconds, value))
            timestamps = [parse_timestamp(row["createdAt"]) for row in payload.get("items", []) if row.get("createdAt")]
            oldest = min(timestamps) if timestamps else None
            newest = max(timestamps) if timestamps else None
            with self.lock:
                job = self.jobs.get(value)
                if job is not None:
                    job.update({"pages": pages, "searchReachedAt": oldest.isoformat() if oldest else None,
                        "delayReason": None, "retryInSeconds": None,
                        "retryUntilMonotonic": None, "retryAttempt": 0})
            return oldest, newest

        lower, upper, oldest = 0, PAGE_SIZE, None
        while not cancel_event.is_set():
            oldest, _ = probe(upper)
            if oldest is None or oldest < end:
                break
            lower, upper = upper, upper * 2
        if cancel_event.is_set() or oldest is None:
            return None, pages, transferred
        while upper - lower > PAGE_SIZE and not cancel_event.is_set():
            span_pages = (upper - lower) // PAGE_SIZE
            middle = lower + max(1, span_pages // 2) * PAGE_SIZE
            oldest, _ = probe(middle)
            if oldest is None:
                break
            if oldest < end:
                upper = middle
            else:
                lower = middle
        return f"{upper}|{timestamp_ms}", pages, transferred

    def _collect(self, key: str, value: str, start: datetime, end: datetime, rebuilding: bool = False) -> None:
        target = parse_day(value); cursor = self._rebuild_cursor_for_key(key) if rebuilding else self._cursor_for_key(key)
        top_cursor = cursor
        cancel_event = self.cancel_events[key]
        pages = transferred = 0
        scan_started = time.monotonic()
        newest_seen = None
        reached_target_day = False
        eta_samples: list[float] = []
        displayed_eta: tuple[int, int] | None = None
        last_eta_update = 0.0
        def report_delay(reason: str, wait: float, attempt: int, attempts: int) -> None:
            # Record when the wait ends so the status can count down live, and how
            # many attempts have failed in a row so the UI can stay calm at first.
            with self.lock:
                self.jobs[key].update({"delayReason": reason, "retryInSeconds": round(wait),
                    "retryUntilMonotonic": time.monotonic() + wait,
                    "retryAttempt": attempt, "retryAttempts": attempts,
                    "etaLowSeconds": None, "etaHighSeconds": None})
            with self.connect() as db:
                db.execute("UPDATE days SET retry_seconds=retry_seconds+?,retry_count=retry_count+1 WHERE day=?",
                    (wait, key))
        try:
            if cursor is None:
                seek_started = time.monotonic()
                cursor, seek_pages, seek_bytes = self._seek_cursor(key, end, cancel_event, report_delay)
                with self.connect() as db:
                    db.execute("UPDATE days SET seek_seconds=? WHERE day=?", (time.monotonic() - seek_started, key))
                pages += seek_pages; transferred += seek_bytes
                top_cursor = cursor
                if cancel_event.is_set():
                    return
            while True:
                if cancel_event.is_set():
                    return
                params = {"limit": PAGE_SIZE, "sort": "Newest", "period": "AllTime", "nsfw": self.content_rating, "withMeta": "false"}
                if cursor: params["cursor"] = cursor
                request_started = time.monotonic()
                payload, size = self._request(params, on_delay=report_delay)
                request_seconds = time.monotonic() - request_started
                if cancel_event.is_set():
                    return
                rows = payload.get("items", []); next_cursor = (payload.get("metadata") or {}).get("nextCursor")
                normalized = [normalize(row) for row in rows if row.get("type") == "image" and row.get("url") and row.get("createdAt")
                    and start <= parse_timestamp(row["createdAt"]) < end]
                image_ids = self._upsert_normalized(normalized, forced_date=value)
                with self.connect() as db:
                    db.executemany("INSERT OR IGNORE INTO block_images(block_key,image_id) VALUES(?,?)", [(key, image_id) for image_id in image_ids])
                pages += 1; transferred += size
                with self.connect() as db:
                    db.execute("""INSERT INTO days(day,complete,scan_cursor,pages,metadata_bytes,collect_pages,collect_bytes,api_seconds,elapsed_seconds,updated_at) VALUES(?,0,?,?,?,?,?,?,?,?)
                        ON CONFLICT(day) DO UPDATE SET scan_cursor=excluded.scan_cursor,pages=days.pages+1,
                        metadata_bytes=days.metadata_bytes+excluded.metadata_bytes,collect_pages=days.collect_pages+1,
                        collect_bytes=days.collect_bytes+excluded.collect_bytes,api_seconds=days.api_seconds+excluded.api_seconds,
                        elapsed_seconds=excluded.elapsed_seconds,updated_at=excluded.updated_at""",
                        (key, next_cursor, 1, size, 1, size, request_seconds, time.monotonic() - scan_started, utcnow()))
                timestamps = [parse_timestamp(row["createdAt"]) for row in rows if row.get("createdAt")]
                oldest = min(timestamps) if timestamps else start
                newest = max(timestamps) if timestamps else oldest
                newest_seen = max(newest_seen, newest) if newest_seen else newest
                covered = max(0.0, min(1.0, (end - oldest).total_seconds() / (end - start).total_seconds()))
                if not reached_target_day and oldest < end:
                    top_cursor = cursor
                reached_target_day = reached_target_day or oldest < end
                phase = "collecting" if reached_target_day else "locating"
                scanned_seconds = max(0.0, (newest_seen - oldest).total_seconds())
                remaining_seconds = max(0.0, ((oldest - end) if phase == "locating" else (oldest - start)).total_seconds())
                elapsed = max(0.001, time.monotonic() - scan_started)
                eta = round(elapsed * remaining_seconds / scanned_seconds) if pages >= 2 and scanned_seconds > 0 else None
                if eta is not None:
                    eta_samples.append(float(eta))
                    eta_samples = eta_samples[-5:]
                now = time.monotonic()
                if elapsed >= 10 and pages >= 5 and eta_samples and (displayed_eta is None or now - last_eta_update >= 10):
                    displayed_eta = conservative_eta_range(sorted(eta_samples)[len(eta_samples) // 2])
                    last_eta_update = now
                with self.lock: self.jobs[key].update({"pages": pages, "phase": phase,
                    "progress": round(covered * 100, 1), "etaSeconds": eta,
                    "searchReachedAt": oldest.isoformat(),
                    "etaLowSeconds": displayed_eta[0] if displayed_eta else None,
                    "etaHighSeconds": displayed_eta[1] if displayed_eta else None,
                    "delayReason": None, "retryInSeconds": None,
                        "retryUntilMonotonic": None, "retryAttempt": 0})
                if oldest < start or not next_cursor:
                    with self.connect() as db:
                        # Re-fetch the crossing page for the next older day so
                        # records on both sides of a timezone boundary are kept.
                        db.execute("UPDATE days SET complete=1,scan_cursor=NULL,older_cursor=?,top_cursor=?,content_rating=?,elapsed_seconds=?,updated_at=? WHERE day=?", (cursor, top_cursor, self.content_rating, time.monotonic() - scan_started, utcnow(), key))
                    with self.lock: self.jobs[key].update({"phase": "organizing", "progress": 100,
                        "etaSeconds": None, "etaLowSeconds": None, "etaHighSeconds": None})
                    organize_started = time.monotonic()
                    self.build_artist_index(key)
                    merged = self.merge_completed_halves(key)
                    if self.on_block_complete:
                        try: self.on_block_complete(key, merged)
                        except Exception: pass  # Preparation is optional; the day is built.
                    with self.connect() as db:
                        db.execute("UPDATE days SET organize_seconds=?,elapsed_seconds=? WHERE day=?",
                            (time.monotonic() - organize_started, time.monotonic() - scan_started, key))
                    with self.lock: self.jobs[key].update({"state": "complete", "phase": "complete", "progress": 100, "etaSeconds": 0})
                    return
                cursor = next_cursor
        except Exception as error:
            with self.connect() as db:
                db.execute("UPDATE days SET elapsed_seconds=?,updated_at=? WHERE day=?",
                    (time.monotonic() - scan_started, utcnow(), key))
            try:
                with (self.root.parent / "error.log").open("a", encoding="utf-8") as output:
                    output.write(f"\n[{utcnow()}] Daily history collection: {type(error).__name__}\n{traceback.format_exc()}")
            except OSError:
                pass
            with self.lock: self.jobs[key].update({"state": "error", "error": "Civitai history could not be loaded. Details were saved to the local error log."})

    def _publish_completed_days(self) -> None:
        """Backfill all-day archives for days whose halves were completed earlier."""
        with self.connect() as db:
            pending = [row["day"] for row in db.execute("""
                SELECT SUBSTR(day, 1, INSTR(day, '#') - 1) AS day
                FROM days WHERE day LIKE '%#morning' AND complete=1
                  AND SUBSTR(day, 1, INSTR(day, '#') - 1) IN
                      (SELECT SUBSTR(day, 1, INSTR(day, '#') - 1) FROM days
                       WHERE day LIKE '%#evening' AND complete=1)
                  AND SUBSTR(day, 1, INSTR(day, '#') - 1) NOT IN
                      (SELECT day FROM days WHERE complete=1)
            """)]
        for day in pending:
            self.merge_completed_halves(f"{day}#evening")

    def merge_completed_halves(self, key: str) -> str | None:
        """Publish the all-day archive once both halves of a day are complete.

        The images already exist: collecting either half stores its rows and maps them to
        the day's own block key as well, so the union is present long before this runs.
        What is missing is the bookkeeping — a `days` row and a creator index — and the
        gallery pages from the creator index, so without them an all-day view has nothing
        to show even though every image is there. Building it is local and needs no
        network. Only a day with *both* halves complete qualifies; publishing a half day as
        "all day" would misrepresent it.
        """
        day = key.split("#", 1)[0]
        if day == key:
            return None
        with self.connect() as db:
            half_rows = list(db.execute(
                "SELECT day, complete, content_rating FROM days WHERE day IN (?,?)",
                (f"{day}#morning", f"{day}#evening")))
            halves = {row["day"]: row["complete"] for row in half_rows}
            if len(halves) < 2 or not all(halves.values()):
                return None
            db.execute("""INSERT OR IGNORE INTO block_images(block_key,image_id)
                          SELECT ?, image_id FROM block_images WHERE block_key IN (?,?)""",
                       (day, f"{day}#morning", f"{day}#evening"))
            coverage = min((row["content_rating"] or DEFAULT_CONTENT_RATING for row in half_rows), key=lambda x: RATING_RANK[x])
            totals = db.execute("""SELECT COALESCE(SUM(seek_pages),0),COALESCE(SUM(seek_bytes),0),
                COALESCE(SUM(collect_pages),0),COALESCE(SUM(collect_bytes),0),COALESCE(SUM(api_seconds),0),
                COALESCE(SUM(retry_seconds),0),COALESCE(SUM(retry_count),0),COALESCE(SUM(elapsed_seconds),0)
                ,COALESCE(SUM(seek_seconds),0),COALESCE(SUM(organize_seconds),0)
                FROM days WHERE day IN (?,?)""", (f"{day}#morning", f"{day}#evening")).fetchone()
            db.execute("""INSERT INTO days(day,complete,content_rating,seek_pages,seek_bytes,collect_pages,
                          collect_bytes,api_seconds,retry_seconds,retry_count,elapsed_seconds,seek_seconds,
                          organize_seconds,updated_at)
                          VALUES(?,1,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET complete=1,
                          content_rating=excluded.content_rating,seek_pages=excluded.seek_pages,seek_bytes=excluded.seek_bytes,
                          collect_pages=excluded.collect_pages,collect_bytes=excluded.collect_bytes,
                          api_seconds=excluded.api_seconds,retry_seconds=excluded.retry_seconds,
                          retry_count=excluded.retry_count,elapsed_seconds=excluded.elapsed_seconds,
                          seek_seconds=excluded.seek_seconds,organize_seconds=excluded.organize_seconds,updated_at=excluded.updated_at""",
                       (day, coverage, *totals, utcnow()))
        self.build_artist_index(day)
        return day

    def build_artist_index(self, value: str) -> None:
        with self.connect() as db:
            levels = _rating_levels(self.content_rating)
            holes = ",".join("?" for _ in levels)
            rows = db.execute(f"SELECT i.id,i.username,i.username_key,i.created_at,i.stats FROM block_images b JOIN images i ON i.id=b.image_id WHERE b.block_key=? AND COALESCE(i.nsfw_level,'None') IN ({holes}) ORDER BY i.created_at DESC", (value, *levels)).fetchall()
            groups: dict[str, list[sqlite3.Row]] = {}
            for row in rows: groups.setdefault(row["username_key"], []).append(row)
            ranked = []
            for key, items in groups.items():
                digest = hashlib.sha256(f"{value}:{key}".encode()).digest()
                representative = items[int.from_bytes(digest[:4], "big") % len(items)]
                newest = items[0]["created_at"]
                reactions = max((int(json.loads(item["stats"]).get("reactionCount", 0)) for item in items), default=0)
                variety = int.from_bytes(digest[4:12], "big") / (2**64 - 1)
                score = reactions ** .35 + variety
                ranked.append((score, key, items[0]["username"], len(items), representative["id"], newest))
            ranked.sort(key=lambda row: row[0], reverse=True)
            db.execute("DELETE FROM day_artists WHERE day=?", (value,))
            db.executemany("""INSERT INTO day_artists(day, username_key, username,
                    image_count, representative_id, newest_at, rank_order)
                VALUES(?,?,?,?,?,?,?)""",
                [(value, key, username, count, rep, newest, order) for order, (_, key, username, count, rep, newest) in enumerate(ranked)])

    def day_summary(self, value: str) -> dict:
        with self.connect() as db:
            day = db.execute("SELECT complete,updated_at FROM days WHERE day=?", (value,)).fetchone()
            rating_clause, rating_params = _rating_clause(self.content_rating)
            images = db.execute("SELECT COUNT(*) FROM block_images b JOIN images i ON i.id=b.image_id "
                f"WHERE b.block_key=?{rating_clause}", (value, *rating_params)).fetchone()[0]
            artists = db.execute("SELECT COUNT(*) FROM day_artists WHERE day=?", (value,)).fetchone()[0]
        return {"date": value, "complete": bool(day and day["complete"]), "imageCount": images, "artistCount": artists, "updatedAt": day["updated_at"] if day else None}

    def day_artist_keys(self, value: str) -> list[dict]:
        """Every creator in the day, in the archive's own order, without image hydration.

        Cheap enough to sort in full for a view change: a large day is a few thousand rows.
        """
        with self.connect() as db:
            return [{"key": row["username_key"], "username": row["username"],
                     "imageCount": row["image_count"], "rank": row["rank_order"],
                     "representativeId": row["representative_id"]}
                    for row in db.execute(
                        "SELECT username_key, username, image_count, rank_order, representative_id "
                        "FROM day_artists WHERE day=? ORDER BY rank_order", (value,))]

    def creators_with_visible_images(self, value: str, excluded_images) -> set[str]:
        """Creator keys left with at least one image once hidden artwork is removed.

        A creator whose every image is hidden has no card, so they must also leave the
        count. Otherwise the gallery advertises a total that cannot be scrolled to.
        """
        hidden = set(excluded_images or ())
        if not hidden:
            with self.connect() as db:
                return {row["username_key"] for row in db.execute(
                    "SELECT DISTINCT username_key FROM day_artists WHERE day=?", (value,))}
        with self.connect() as db:
            rating_clause, rating_params = _rating_clause(self.content_rating)
            rows = db.execute("SELECT i.username_key AS key, i.id AS id FROM block_images b "
                              f"JOIN images i ON i.id=b.image_id WHERE b.block_key=?{rating_clause}", (value, *rating_params))
            return {row["key"] for row in rows if row["id"] not in hidden}

    def effective_representative_ids(self, value: str, excluded_images) -> dict[str, int]:
        """The image id each creator's card actually shows, once hidden covers are swapped.

        Ranking and "why this ranked here" both have to talk about the same image.
        Without this, a creator's *hidden* cover can carry the taste score while a
        *different* image is what renders — a strongly-matched card can appear to have
        no match at all, because the explanation was computed against the wrong image.
        Creators left with no visible image at all are simply absent, matching
        ``artists_page``'s own fallback.
        """
        hidden = set(excluded_images or ())
        with self.connect() as db:
            rows = db.execute("SELECT username_key, representative_id FROM day_artists WHERE day=?",
                              (value,)).fetchall()
            if not hidden:
                return {row["username_key"]: row["representative_id"] for row in rows}
            affected = {row["username_key"] for row in rows if row["representative_id"] in hidden}
            result = {row["username_key"]: row["representative_id"] for row in rows
                     if row["username_key"] not in affected}
            if affected:
                holes = ",".join("?" for _ in affected)
                rating_clause, rating_params = _rating_clause(self.content_rating)
                for row in db.execute(
                        f"SELECT i.username_key AS key, i.id AS id FROM block_images b "
                        f"JOIN images i ON i.id=b.image_id WHERE b.block_key=? AND i.username_key IN ({holes}) "
                        f"{rating_clause} ORDER BY i.created_at DESC,i.id DESC", (value, *affected, *rating_params)):
                    if row["key"] not in result and row["id"] not in hidden:
                        result[row["key"]] = row["id"]
            return result

    def artists_page(self, value: str, offset: int, limit: int, pinned_username: str | None = None,
                     order: list[str] | None = None, representatives: dict[str, int] | None = None,
                     excluded_images=None) -> list[dict]:
        with self.connect() as db:
            pinned_key = (pinned_username or "").casefold()
            if order is not None:
                window = order[offset:offset + limit]
                if not window:
                    return []
                placeholders = ",".join("?" for _ in window)
                rows = {row["username_key"]: row for row in db.execute(
                    f"SELECT * FROM day_artists WHERE day=? AND username_key IN ({placeholders})",
                    (value, *window))}
                summaries = [rows[key] for key in window if key in rows]
            else:
                summaries = db.execute("SELECT * FROM day_artists WHERE day=? ORDER BY CASE WHEN username_key=? THEN -1 ELSE rank_order END,rank_order LIMIT ? OFFSET ?",
                    (value, pinned_key, limit, offset)).fetchall()
            hidden = set(excluded_images or ())
            result = []
            for summary in summaries:
                # When a filter is on, the card must open on an image that matches it.
                chosen = (representatives or {}).get(summary["username_key"]) or summary["representative_id"]
                image = db.execute("SELECT * FROM images WHERE id=?", (chosen,)).fetchone()
                if image is not None and image["id"] in hidden:
                    # The usual cover is one the account hides, so open on their newest
                    # image that is not hidden instead of dropping the creator entirely.
                    rating_clause, rating_params = _rating_clause(self.content_rating)
                    image = next((row for row in db.execute(
                        "SELECT i.* FROM block_images b JOIN images i ON i.id=b.image_id "
                        f"WHERE b.block_key=? AND i.username_key=?{rating_clause} "
                        "ORDER BY i.created_at DESC,i.id DESC",
                        (value, summary["username_key"], *rating_params)) if row["id"] not in hidden), None)
                    # Every image this creator posted is hidden, so there is no card to show.
                    if image is None:
                        continue
                rating_clause, rating_params = _rating_clause(self.content_rating)
                representative_index = db.execute("SELECT COUNT(*) FROM images i WHERE local_date=? AND username_key=? "
                    "AND id IN (SELECT image_id FROM block_images WHERE block_key=?) "
                    f"AND (created_at>? OR (created_at=? AND id>?)){rating_clause}",
                    (image["local_date"], summary["username_key"], value, image["created_at"], image["created_at"], image["id"], *rating_params)).fetchone()[0]
                result.append({"username": summary["username"], "imageCount": summary["image_count"],
                    "representativeIndex": representative_index, "representative": self._row_item(image), "images": [self._row_item(image)]})
            return result

    def artist_images(self, value: str, username: str, models: list[str] | None = None,
                      excluded_images=None) -> list[dict]:
        """One creator's images for the block, newest first.

        Hidden images are removed in Python rather than in SQL: the exclusion set spans
        the whole archive and can run to thousands of ids, which is far past what is
        sensible to bind as query parameters, while a single creator's day is tiny.
        """
        clause, params = _model_clause(models)
        rating_clause, rating_params = _rating_clause(self.content_rating)
        hidden = set(excluded_images or ())
        with self.connect() as db:
            rows = db.execute("SELECT i.* FROM block_images b JOIN images i ON i.id=b.image_id "
                              f"WHERE b.block_key=? AND i.username_key=?{clause}{rating_clause} "
                              "ORDER BY i.created_at DESC,i.id DESC",
                              (value, username.casefold(), *params, *rating_params)).fetchall()
            return [self._row_item(row) for row in rows if row["id"] not in hidden]

    def day_models(self, value: str) -> list[dict]:
        """Which generation models produced this day's artwork, commonest first."""
        with self.connect() as db:
            rating_clause, rating_params = _rating_clause(self.content_rating)
            return [{"model": row["base_model"] or "Unknown", "images": row["n"]}
                    for row in db.execute(
                        "SELECT COALESCE(NULLIF(i.base_model,''),'Unknown') AS base_model, "
                        "COUNT(*) AS n FROM block_images b JOIN images i ON i.id=b.image_id "
                        f"WHERE b.block_key=?{rating_clause} GROUP BY base_model ORDER BY n DESC", (value, *rating_params))]

    def creators_using_models(self, value: str, models: list[str]) -> dict[str, int]:
        """Creator keys with at least one image from the chosen models, and a matching image."""
        clause, params = _model_clause(models)
        rating_clause, rating_params = _rating_clause(self.content_rating)
        if not clause:
            return {}
        with self.connect() as db:
            picks: dict[str, int] = {}
            for row in db.execute("SELECT i.username_key AS k, i.id AS id FROM block_images b "
                                  f"JOIN images i ON i.id=b.image_id WHERE b.block_key=?{clause}{rating_clause} "
                                  "ORDER BY i.created_at DESC,i.id DESC", (value, *params, *rating_params)):
                picks.setdefault(row["k"], row["id"])
            return picks

    def has_creator(self, username: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM images WHERE username_key=? LIMIT 1", (username.casefold(),)).fetchone() is not None

    def has_image(self, image_id: int) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM images WHERE id=?", (image_id,)).fetchone() is not None

    def update_stats(self, image_id: int, stats: dict) -> None:
        with self.connect() as db: db.execute("UPDATE images SET stats=? WHERE id=?", (json.dumps(stats), image_id))

    def stats(self, image_id: int) -> dict:
        with self.connect() as db: row = db.execute("SELECT stats FROM images WHERE id=?", (image_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def detail(self, image_id: int) -> dict:
        with self.connect() as db: row = db.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        if row is None: raise ValueError("Image is not in the history archive")
        if not row["details_loaded"]:
            payload, _ = self._request({"imageId": image_id, "withMeta": "true"}, minimum_interval=1.5)
            raw = next((item for item in payload.get("items", []) if int(item.get("id", -1)) == image_id), None)
            if raw:
                item = normalize(raw)
                # Detail responses sometimes report an underlying creation
                # timestamp that differs from the daily-feed sort timestamp.
                # Enrichment must never move an image between archived days.
                with self.connect() as db:
                    db.execute("""UPDATE images SET prompt=?,negative_prompt=?,resources=?,
                        base_model=COALESCE(?,base_model),model_version_ids=?,details_loaded=1 WHERE id=?""",
                        (item.get("prompt") or "", item.get("negativePrompt") or "",
                         json.dumps(item.get("resources") or []), item.get("baseModel"),
                         json.dumps(item.get("modelVersionIds") or []), image_id))
                with self.connect() as db: row = db.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        return self._row_item(row, details=True)
