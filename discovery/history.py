"""Small, checkpointed SQLite calendar history for Civitai discovery."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time as day_time, timedelta, timezone, tzinfo
import gzip
import json
import math
import os
from pathlib import Path
import random
import re
import sqlite3
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from .civitai import API_URL, USER_AGENT, normalize, utcnow
from .search import (SEARCH_CLIENT_KEY, SEARCH_PAGE_SIZE, SEARCH_SLICE_RESULT_LIMIT,
                     SEARCH_URL, normalize_hit, search_body)
from .site import (DEFAULT_CONTENT_RATING, RATING_RANK,
                   browsing_levels, content_rating, image_url, levels_for_rating,
                   rating_for_levels)


PAGE_SIZE = 200
RATE_LIMIT_RETRIES = 8
# A resumable build gets one extra retry cycle because every successful page is
# checkpointed. It must still stop after a sustained outage instead of resetting the
# visible counter forever and continuing to call an unhealthy service indefinitely.
CHECKPOINTED_RETRY_ATTEMPTS = RATE_LIMIT_RETRIES * 2
EMPTY_PROBE_ATTEMPTS = 3
MAX_CURSOR_OFFSET = 50_000
API_FAILURE_BODY_BYTES = 4096
API_FAILURE_LOG_BYTES = 1024 * 1024

# Civitai's browsing-level parameter is a bitmask.  Collect the cumulative build
# choices as non-overlapping feeds so a busy all-ratings day cannot exhaust the
# service's roughly 50,000-result traversal window before reaching the requested
# time boundary.  The saved image rows still retain their individual level and are
# filtered precisely when displayed.
COLLECTION_FEEDS = {
    "Soft": (1, 2),
    "Mature": (1, 2, 4),
    "X": (1, 2, 4, 8, 16),
}
COLLECTION_VERSION = 5

# Every browsing level in one mask. A single-image lookup has to name the level it wants
# or Civitai answers an anonymous caller at the public level only.
ALL_BROWSING_LEVELS = sum(COLLECTION_FEEDS["X"])
# image.get batches like tag.getVotableTags, so a day costs hundreds of requests, not
# hundreds of thousands.
REACTION_SWEEP_BATCH = 100

# Fixed planning benchmarks from the preserved completed runs. These are intentionally
# not updated from runtime rows: later rows can represent partial/resumed work and caused
# implausibly low estimates. The safe benchmark is the corrected 2026-07-24 run; the
# explicit benchmark is the trusted 82,050-image, hour-long full-day run documented in
# README. Listing-page counts account for pages containing non-image records.
BUILD_BENCHMARK = {
    "Soft": {"images": 27_136, "listing_requests": 140},
    "Mature": {"images": 39_712, "listing_requests": 212},
    "X": {"images": 82_050, "listing_requests": 458},
}
# Civitai caps cursor traversal, so every feed has a floor that moves forward in real
# time. 49,000 is the deepest offset measured to still return rows (50,000 is empty).
FEED_FLOOR_PROBE_OFFSET = 49_000
FEED_FLOOR_CACHE_SECONDS = 300

BENCHMARK_SEEK_REQUESTS_PER_HALF = 17
# Even a perfect pull observes the known five-second API request cadence. The delayed
# value reproduces the trusted hour-long all-ratings run across its 492 total requests.
BENCHMARK_CLEAN_REQUEST_SECONDS = 5.0
BENCHMARK_DELAYED_REQUEST_SECONDS = 3600 / 492
POPULAR_INDEX_VERSION = 2


class CollectionCancelled(RuntimeError):
    """Internal control flow used to leave a retry wait when the user stops a build."""


class RetryBudgetExhausted(RuntimeError):
    """A checkpointed request stopped after a sustained upstream outage."""

    def __init__(self, reason: str, attempts: int, last_failure: str | None = None):
        self.reason = reason
        self.attempts = attempts
        self.last_failure = last_failure
        super().__init__(f"Civitai did not respond successfully after {attempts} attempts")


class HistoryWindowUnavailable(RuntimeError):
    """The public feed ended before its cursor window reached the requested date."""

    def __init__(self, browsing_mask: int, target: datetime,
                 oldest_seen: datetime | None = None):
        self.browsing_mask = browsing_mask
        self.target = target
        self.oldest_seen = oldest_seen
        super().__init__(
            f"Civitai feed {browsing_mask} ended before reaching {target.isoformat()}")


def active_backend() -> str:
    """Return the collection backend to use.

    Civitai's Terms of Service (11.4) permit automated access only through interfaces
    they expressly provide for it -- their public API or MCP server -- and only with the
    caller's own credentials. The browser search index is neither: it backs their own web
    UI and is reached with the search key their frontend ships. So the documented v1
    endpoint is the default, and the search collector stays opt-in for local diagnosis of
    the v1 path, never for distributed use.
    """
    requested = os.environ.get("CIVITAI_HISTORY_BACKEND", "v1").strip().casefold()
    return "search" if requested == "search" else "v1-feed"


class SearchBackendUnavailable(RuntimeError):
    """Civitai's browser search service could not serve a compatible response."""


class SearchCompletenessError(RuntimeError):
    """A bounded search slice did not yield its advertised unique result count."""


class AdaptivePacer:
    """Conservative request pacing that responds to live service conditions.

    Backing off and recovering are both proportional, which they were not. Failure
    multiplied the interval by 1.5, so about five errors took it from a second to the
    eight-second ceiling, while recovery subtracted a tenth of a second every ten clean
    responses -- roughly seven hundred requests to come back. A collection is rarely that
    long, so any Civitai hiccup left the app slow for the rest of the run and often the
    run after it: a Time Machine prime of 485 creators crawled at seven seconds each long
    after the service had recovered, and could not have sped up within its own lifetime.
    """

    # Recovery is deliberately slower than the climb -- a service that just failed should
    # be approached carefully -- but it now finishes inside a normal collection.
    RECOVERY_STREAK = 5
    RECOVERY_FACTOR = 0.85

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
        if self.clean_streak >= self.RECOVERY_STREAK:
            self.interval = max(self.minimum, self.interval * self.RECOVERY_FACTOR)
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


def oldest_buildable_day(floor: datetime) -> date:
    """Return the oldest local day whose block still starts at or after ``floor``.

    A block is collected back to its own local midnight, so a day is only buildable when
    that midnight is still reachable -- the floor's own date usually is not.
    """
    local = floor.astimezone(LOCAL_ZONE)
    midnight = datetime.combine(local.date(), day_time.min, LOCAL_ZONE)
    if midnight < floor:
        midnight += timedelta(days=1)
    return midnight.date()


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


BROWSING_LEVEL_SQL = """COALESCE(i.browsing_level, CASE COALESCE(i.nsfw_level,'None')
    WHEN 'None' THEN 1 WHEN 'Soft' THEN 2 WHEN 'Mature' THEN 4 WHEN 'X' THEN 8 END)"""


def _rating_clause(levels: tuple[int, ...]) -> tuple[str, tuple[int, ...]]:
    return (f" AND {BROWSING_LEVEL_SQL} IN ({','.join('?' for _ in levels)})", levels)


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

    def __init__(self, root: Path, selected_content_rating: str = DEFAULT_CONTENT_RATING,
                 selected_browsing_levels: object = None):
        self.root = root
        self.db_path = root / "history.sqlite3"
        self.api_failure_log = root.parent / "api-failures.jsonl"
        self.jobs: dict[str, dict] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.lock = threading.RLock()
        self.index_lock = threading.RLock()
        self._active_index_levels: dict[str, str] = {}
        self.api_lock = threading.Lock()
        self.last_api_request = 0.0
        self.api_pacer = AdaptivePacer()
        # Feed floors move forward continuously; a short cache keeps the build screen
        # responsive without re-probing Civitai on every status poll.
        self._floor_cache: dict[int, tuple[float, datetime | None]] = {}
        self.content_rating = content_rating(selected_content_rating)
        self.visible_levels = browsing_levels(selected_browsing_levels) \
            if selected_browsing_levels is not None else levels_for_rating(self.content_rating)
        self.content_rating = rating_for_levels(self.visible_levels)
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
                    resources TEXT NOT NULL DEFAULT '[]', details_loaded INTEGER NOT NULL DEFAULT 0,
                    visual_hash TEXT
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
                CREATE TABLE IF NOT EXISTS day_artist_cache (
                    day TEXT NOT NULL, levels_key TEXT NOT NULL,
                    username_key TEXT NOT NULL, username TEXT NOT NULL,
                    image_count INTEGER NOT NULL, representative_id INTEGER NOT NULL,
                    newest_at TEXT NOT NULL, rank_order INTEGER NOT NULL,
                    PRIMARY KEY(day, levels_key, username_key)
                );
                CREATE INDEX IF NOT EXISTS day_artist_cache_rank
                    ON day_artist_cache(day, levels_key, rank_order);
                CREATE TABLE IF NOT EXISTS day_artist_cache_state (
                    day TEXT NOT NULL, levels_key TEXT NOT NULL,
                    PRIMARY KEY(day, levels_key)
                );
                CREATE TABLE IF NOT EXISTS block_images (
                    block_key TEXT NOT NULL, image_id INTEGER NOT NULL,
                    PRIMARY KEY(block_key,image_id), FOREIGN KEY(image_id) REFERENCES images(id)
                );
                CREATE INDEX IF NOT EXISTS block_images_image ON block_images(image_id);
                CREATE TABLE IF NOT EXISTS block_feeds (
                    block_key TEXT NOT NULL, browsing_mask INTEGER NOT NULL,
                    complete INTEGER NOT NULL DEFAULT 0, scan_cursor TEXT,
                    older_cursor TEXT, top_cursor TEXT, pages INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT, PRIMARY KEY(block_key,browsing_mask)
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(days)")}
            for name in ("timezone", "start_utc", "end_utc", "top_cursor", "content_rating",
                         "artist_levels"):
                if name not in columns: db.execute(f"ALTER TABLE days ADD COLUMN {name} TEXT")
            numeric_metrics = {
                "seek_pages": "INTEGER NOT NULL DEFAULT 0", "seek_bytes": "INTEGER NOT NULL DEFAULT 0",
                "collect_pages": "INTEGER NOT NULL DEFAULT 0", "collect_bytes": "INTEGER NOT NULL DEFAULT 0",
                "api_seconds": "REAL NOT NULL DEFAULT 0", "retry_seconds": "REAL NOT NULL DEFAULT 0",
                "retry_count": "INTEGER NOT NULL DEFAULT 0", "elapsed_seconds": "REAL NOT NULL DEFAULT 0",
                "seek_seconds": "REAL NOT NULL DEFAULT 0", "organize_seconds": "REAL NOT NULL DEFAULT 0",
                "pace_seconds": "REAL NOT NULL DEFAULT 0", "response_seconds": "REAL NOT NULL DEFAULT 0",
                "rate_limit_count": "INTEGER NOT NULL DEFAULT 0",
                "service_retry_count": "INTEGER NOT NULL DEFAULT 0",
                "network_retry_count": "INTEGER NOT NULL DEFAULT 0",
                "final_pacer_interval": "REAL NOT NULL DEFAULT 0",
                "wire_bytes": "INTEGER NOT NULL DEFAULT 0",
                "decoded_bytes": "INTEGER NOT NULL DEFAULT 0",
            }
            columns = {row[1] for row in db.execute("PRAGMA table_info(days)")}
            for name, declaration in numeric_metrics.items():
                if name not in columns: db.execute(f"ALTER TABLE days ADD COLUMN {name} {declaration}")
            columns = {row[1] for row in db.execute("PRAGMA table_info(days)")}
            if "collection_version" not in columns:
                db.execute("ALTER TABLE days ADD COLUMN collection_version INTEGER NOT NULL DEFAULT 0")
            image_columns = {row[1] for row in db.execute("PRAGMA table_info(images)")}
            if "visual_hash" not in image_columns:
                db.execute("ALTER TABLE images ADD COLUMN visual_hash TEXT")
            if "stats_fetched_at" not in image_columns:
                db.execute("ALTER TABLE images ADD COLUMN stats_fetched_at TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS images_visual_hash "
                       "ON images(visual_hash,width,height) WHERE visual_hash IS NOT NULL")
            db.execute("INSERT OR IGNORE INTO block_images(block_key,image_id) SELECT local_date,id FROM images")
            # Popular v2 ranks by total daily reactions and uses the most-reacted image.
            # Cached indexes are derived data, so invalidate the old hash-ranked version
            # once and rebuild each saved day locally when it is next opened.
            index_version = db.execute("PRAGMA user_version").fetchone()[0]
            if index_version < POPULAR_INDEX_VERSION:
                db.execute("DELETE FROM day_artists")
                db.execute("DELETE FROM day_artist_cache")
                db.execute("DELETE FROM day_artist_cache_state")
                db.execute("UPDATE days SET artist_levels=NULL")
                db.execute(f"PRAGMA user_version={POPULAR_INDEX_VERSION}")
        self._audit_legacy_block_coverage()

    def _audit_legacy_block_coverage(self) -> None:
        """Reopen legacy half-days whose saved timestamps do not cover the block.

        Older collectors considered a missing ``nextCursor`` successful even when
        Civitai stopped at its deep-traversal ceiling.  Those rows have no per-feed
        checkpoints, so timestamp coverage is the only durable evidence available.
        A valid crossing page leaves saved images close to both boundaries; a generous
        thirty-minute tolerance avoids invalidating normal sparse edges.
        """
        tolerance = timedelta(minutes=30)
        invalid_days: set[str] = set()
        with self.connect() as db:
            rows = list(db.execute("""SELECT day,start_utc,end_utc FROM days
                WHERE complete=1 AND collection_version=0
                  AND (day LIKE '%#morning' OR day LIKE '%#evening')
                  AND start_utc IS NOT NULL AND end_utc IS NOT NULL"""))
            for row in rows:
                limits = db.execute("""SELECT MIN(i.created_at),MAX(i.created_at),COUNT(*)
                    FROM block_images b JOIN images i ON i.id=b.image_id
                    WHERE b.block_key=?""", (row["day"],)).fetchone()
                start, end = parse_timestamp(row["start_utc"]), parse_timestamp(row["end_utc"])
                earliest = parse_timestamp(limits[0]) if limits[0] else None
                latest = parse_timestamp(limits[1]) if limits[1] else None
                if not earliest or not latest or earliest > start + tolerance or latest < end - tolerance:
                    db.execute("UPDATE days SET complete=0,scan_cursor=NULL,updated_at=? WHERE day=?",
                               (utcnow(), row["day"]))
                    invalid_days.add(row["day"].split("#", 1)[0])
            for day in invalid_days:
                db.execute("UPDATE days SET complete=0,updated_at=? WHERE day=?", (utcnow(), day))

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
                json.dumps(item.get("resources") or []), int(bool(item.get("prompt") or item.get("resources"))),
                str(item.get("visualHash") or "").strip() or None))
        with self.connect() as db:
            # Name every column: archives written by older versions carry columns this
            # schema no longer declares, and a positional insert breaks against them.
            db.executemany("""INSERT INTO images(
                    id, local_date, post_id, username, username_key, created_at,
                    url, width, height, type, nsfw_level, browsing_level, base_model,
                    model_version_ids, stats, prompt, negative_prompt, resources, details_loaded,
                    visual_hash)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET local_date=excluded.local_date,post_id=excluded.post_id,
                username=excluded.username,username_key=excluded.username_key,created_at=excluded.created_at,
                url=excluded.url,width=excluded.width,height=excluded.height,type=excluded.type,
                nsfw_level=excluded.nsfw_level,browsing_level=excluded.browsing_level,
                base_model=excluded.base_model,model_version_ids=excluded.model_version_ids,
                stats=excluded.stats,prompt=CASE WHEN excluded.prompt!='' THEN excluded.prompt ELSE images.prompt END,
                negative_prompt=CASE WHEN excluded.negative_prompt!='' THEN excluded.negative_prompt ELSE images.negative_prompt END,
                resources=CASE WHEN excluded.resources!='[]' THEN excluded.resources ELSE images.resources END,
                details_loaded=MAX(images.details_loaded,excluded.details_loaded),
                visual_hash=COALESCE(excluded.visual_hash,images.visual_hash)""", rows)
            db.executemany("INSERT OR IGNORE INTO block_images(block_key,image_id) VALUES(?,?)",
                [(row[1], row[0]) for row in rows])
        return [row[0] for row in rows]

    def _row_item(self, row: sqlite3.Row, details: bool = False) -> dict:
        item = {"id": row["id"], "postId": row["post_id"], "username": row["username"], "createdAt": row["created_at"],
            "url": row["url"], "thumbnailUrl": preview_url(row["url"]), "civitaiUrl": image_url(row["id"]),
            "width": row["width"], "height": row["height"], "type": row["type"], "nsfwLevel": row["nsfw_level"],
            "browsingLevel": row["browsing_level"], "baseModel": row["base_model"] or "Unknown",
            "modelVersionIds": json.loads(row["model_version_ids"]), "stats": json.loads(row["stats"]),
            "visualHash": row["visual_hash"]}
        if details:
            item.update({"prompt": row["prompt"], "negativePrompt": row["negative_prompt"], "resources": json.loads(row["resources"]), "detailsLoaded": bool(row["details_loaded"]), "detailImageUrl": preview_url(row["url"], 1280)})
        return item

    def status(self, value: str, required_content_rating: str | None = None) -> dict:
        with self.connect() as db:
            day = db.execute("SELECT * FROM days WHERE day=?", (value,)).fetchone()
            rating_clause, rating_params = _rating_clause(self.visible_levels)
            count = db.execute("SELECT COUNT(*) FROM block_images b JOIN images i ON i.id=b.image_id "
                f"WHERE b.block_key=?{rating_clause}", (value, *rating_params)).fetchone()[0]
            creators = db.execute("SELECT COUNT(DISTINCT i.username_key) FROM block_images b JOIN images i ON i.id=b.image_id "
                f"WHERE b.block_key=?{rating_clause}", (value, *rating_params)).fetchone()[0]
            feed_complete = True
            if day and "#" in value and int(day["collection_version"] or 0) >= COLLECTION_VERSION:
                expected = COLLECTION_FEEDS[content_rating(day["content_rating"])]
                states = {int(row["browsing_mask"]): bool(row["complete"])
                          for row in db.execute("SELECT browsing_mask,complete FROM block_feeds WHERE block_key=?",
                                                (value,))}
                feed_complete = all(states.get(mask, False) for mask in expected)
        with self.lock:
            job = dict(self.jobs.get(value, {}))
        coverage = (day["content_rating"] if day and day["content_rating"] else DEFAULT_CONTENT_RATING)
        active_job_rating = job.get("contentRating") if job.get("state") == "loading" else None
        required = content_rating(required_content_rating or active_job_rating or self.content_rating)
        needs_upgrade = bool(day and day["complete"] and RATING_RANK[coverage] < RATING_RANK[required])
        archive_complete = bool(day and day["complete"] and feed_complete)
        complete = archive_complete and not needs_upgrade and job.get("state") not in {"loading", "error"}
        started = job.get("startedMonotonic")
        # Freeze completed durations at the persisted database value. Keeping the live
        # monotonic clock running after completion made yesterday's 32-minute block look
        # eighteen hours long when status was read the following morning.
        prior_elapsed = float(job.get("priorElapsedSeconds") or 0)
        elapsed = (round(max(0.0, prior_elapsed + time.monotonic() - started))
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
        metrics.update({"paceSeconds": round(float(day["pace_seconds"] or 0), 2) if day else 0,
            "responseSeconds": round(float(day["response_seconds"] or 0), 2) if day else 0,
            "rateLimitCount": int(day["rate_limit_count"] or 0) if day else 0,
            "serviceRetryCount": int(day["service_retry_count"] or 0) if day else 0,
            "networkRetryCount": int(day["network_retry_count"] or 0) if day else 0,
            "finalPacerInterval": round(float(day["final_pacer_interval"] or 0), 2) if day else 0})
        metrics.update({"wireBytes": int(day["wire_bytes"] or 0) if day else 0,
                        "decodedBytes": int(day["decoded_bytes"] or 0) if day else 0})
        metrics["collectSeconds"] = round(max(0, metrics["elapsedSeconds"] - metrics["seekSeconds"] - metrics["organizeSeconds"]), 2)
        metrics["localProcessingSeconds"] = round(max(0, metrics["elapsedSeconds"]
            - metrics["paceSeconds"] - metrics["responseSeconds"] - metrics["retrySeconds"]
            - metrics["organizeSeconds"]), 2)
        return {"date": value, "state": job.get("state") or ("complete" if complete else "not_started"),
            "progress": job.get("progress", 100 if complete else 0), "pages": job.get("pages", 0),
            "phase": job.get("phase", "complete" if complete else "waiting"), "itemCount": count,
            "creatorCount": creators, "elapsedSeconds": elapsed,
            "etaSeconds": job.get("etaSeconds"), "etaLowSeconds": job.get("etaLowSeconds"),
            "etaHighSeconds": job.get("etaHighSeconds"), "delayReason": job.get("delayReason"),
            "retryInSeconds": retry_in, "retryAttempt": job.get("retryAttempt") or 0,
            "retryAttempts": job.get("retryAttempts") or RATE_LIMIT_RETRIES,
            "listingsChecked": int(job.get("listingsChecked",
                int(job.get("pages", 0)) * (SEARCH_PAGE_SIZE
                    if job.get("collectionBackend") == "search" else PAGE_SIZE))),
            "plannedImages": job.get("plannedImages"),
            "searchReachedAt": job.get("searchReachedAt"),
            "error": job.get("error"), "errorKind": job.get("errorKind"),
            "complete": complete, "archiveComplete": archive_complete,
            "rebuilding": bool(job.get("rebuilding")),
            "collectionBackend": job.get("collectionBackend") or
                ("search" if day and int(day["collection_version"] or 0) >= COLLECTION_VERSION else "v1-feed"),
            "contentRating": self.content_rating,
            "browsingLevels": list(self.visible_levels),
            "metrics": metrics,
            "archiveContentRating": coverage if day else None, "needsUpgrade": needs_upgrade}

    def set_content_filter(self, levels: object) -> None:
        """Change exact visible levels and derive the grouped collection ceiling."""
        selected = browsing_levels(levels)
        rating = rating_for_levels(selected)
        with self.lock:
            if any(job.get("state") == "loading" for job in self.jobs.values()):
                raise ValueError("Stop the current history build before changing content ratings")
            self.content_rating = rating
            self.visible_levels = selected

    @staticmethod
    def _levels_key(levels: tuple[int, ...]) -> str:
        return ",".join(str(level) for level in levels)

    def _invalidate_artist_cache(self, value: str) -> None:
        """Discard indexes after a block's membership may have changed."""
        with self.index_lock, self.connect() as db:
            db.execute("DELETE FROM day_artist_cache WHERE day=?", (value,))
            db.execute("DELETE FROM day_artist_cache_state WHERE day=?", (value,))
            db.execute("UPDATE days SET artist_levels=NULL WHERE day=?", (value,))
            self._active_index_levels.pop(value, None)

    def _ensure_artist_index(self, value: str) -> None:
        """Activate the selected filter's cached index, building it only once."""
        levels = self.visible_levels
        levels_key = self._levels_key(levels)
        with self.index_lock:
            if self._active_index_levels.get(value) == levels_key:
                return
            with self.connect() as db:
                day = db.execute("SELECT artist_levels FROM days WHERE day=?", (value,)).fetchone()
                if day and day["artist_levels"] == levels_key:
                    self._active_index_levels[value] = levels_key
                    return
                cached = db.execute(
                    "SELECT 1 FROM day_artist_cache_state WHERE day=? AND levels_key=?",
                    (value, levels_key)).fetchone()
                if cached:
                    db.execute("DELETE FROM day_artists WHERE day=?", (value,))
                    db.execute("""INSERT INTO day_artists(day,username_key,username,image_count,
                                  representative_id,newest_at,rank_order)
                                  SELECT day,username_key,username,image_count,representative_id,
                                         newest_at,rank_order FROM day_artist_cache
                                  WHERE day=? AND levels_key=?""", (value, levels_key))
                    db.execute("UPDATE days SET artist_levels=? WHERE day=?", (levels_key, value))
                    self._active_index_levels[value] = levels_key
                    return
            self.build_artist_index(value, levels)

    def set_content_rating(self, value: str) -> None:
        """Compatibility wrapper for callers using the former cumulative selector."""
        self.set_content_filter(levels_for_rating(value))

    def start(self, value: str, start_utc: str, end_utc: str, timezone_name: str,
              segment: str = "all", requested_content_rating: str | None = None) -> dict:
        key = self.archive_key(value, segment)
        start, end = parse_bounds(value, start_utc, end_utc)
        requested_rating = content_rating(requested_content_rating or self.content_rating)
        current = self.status(key, requested_rating)
        if current["complete"] or current["state"] == "loading":
            return current
        with self.connect() as db:
            existing = db.execute(
                "SELECT complete,elapsed_seconds,collection_version FROM days WHERE day=?", (key,)).fetchone()
            has_feed_rows = bool(db.execute("SELECT 1 FROM block_feeds WHERE block_key=? LIMIT 1",
                                            (key,)).fetchone())
        resuming = bool(existing and int(existing["collection_version"] or 0) >= COLLECTION_VERSION
                        and has_feed_rows)
        prior_elapsed = float(existing["elapsed_seconds"] or 0) if resuming else 0.0
        with self.lock:
            self.jobs[key] = {"state": "loading", "phase": "locating", "progress": 0, "pages": 0,
                "startedMonotonic": time.monotonic(), "etaLowSeconds": None, "etaHighSeconds": None,
                "delayReason": None, "contentRating": requested_rating,
                "priorElapsedSeconds": prior_elapsed,
                "collectionBackend": "search" if active_backend() == "search" else "v1-feed"}
            self.cancel_events[key] = threading.Event()
        with self.connect() as db:
            db.execute("""INSERT INTO days(day,complete,timezone,start_utc,end_utc,content_rating,
                        collection_version,updated_at) VALUES(?,0,?,?,?,?,?,?)
                ON CONFLICT(day) DO UPDATE SET complete=0,timezone=excluded.timezone,start_utc=excluded.start_utc,
                end_utc=excluded.end_utc,content_rating=excluded.content_rating,
                collection_version=excluded.collection_version,updated_at=excluded.updated_at""",
                (key, timezone_name[:100], start.isoformat(), end.isoformat(), requested_rating,
                 COLLECTION_VERSION, utcnow()))
            db.executemany("""INSERT OR IGNORE INTO block_feeds(
                    block_key,browsing_mask,complete,updated_at) VALUES(?,?,0,?)""",
                [(key, mask, utcnow()) for mask in COLLECTION_FEEDS[requested_rating]])
            if not resuming:
                # Preserve proven feeds when upgrading a completed lower-coverage archive.
                # An unfinished older collector carries incompatible offset cursors and
                # must restart its feeds under the exact-range format.
                if not (existing and existing["complete"]):
                    db.execute("""UPDATE block_feeds SET complete=0,scan_cursor=NULL,
                               older_cursor=NULL,top_cursor=NULL,pages=0,updated_at=?
                               WHERE block_key=?""", (utcnow(), key))
                db.execute("""UPDATE days SET seek_pages=0,seek_bytes=0,collect_pages=0,collect_bytes=0,
                    api_seconds=0,retry_seconds=0,retry_count=0,pace_seconds=0,response_seconds=0,
                    rate_limit_count=0,service_retry_count=0,network_retry_count=0,
                    final_pacer_interval=0,wire_bytes=0,decoded_bytes=0,elapsed_seconds=0,
                    seek_seconds=0,organize_seconds=0 WHERE day=?""", (key,))
        threading.Thread(target=self._collect, args=(key, value, start, end), daemon=True, name=f"history-{key}").start()
        return self.status(key)

    def build_estimate(self, segment: str, requested_content_rating: str,
                       value: str | None = None) -> dict:
        """Return a fixed request-capacity estimate from the known clean benchmark."""
        if segment not in {"morning", "evening", "all"}:
            raise ValueError("Invalid day segment")
        rating = content_rating(requested_content_rating)

        parts = list(("morning", "evening") if segment == "all" else (segment,))
        if value and segment == "all":
            parse_day(value)
            with self.connect() as db:
                ready = set()
                for part in parts:
                    row = db.execute("SELECT complete,content_rating FROM days WHERE day=?",
                                     (f"{value}#{part}",)).fetchone()
                    coverage = content_rating(row[1] or DEFAULT_CONTENT_RATING) if row else None
                    if row and row[0] and RATING_RANK[coverage] >= RATING_RANK[rating]:
                        ready.add(part)
            parts = [part for part in parts if part not in ready]
        benchmark = BUILD_BENCHMARK[rating]
        half_count = len(parts)
        if half_count == 2:
            images = benchmark["images"]
            listing_requests = benchmark["listing_requests"]
        elif half_count == 1:
            images = math.ceil(benchmark["images"] / 2)
            listing_requests = math.ceil(benchmark["listing_requests"] / 2)
        else:
            images = listing_requests = 0
        backend = active_backend()
        if backend == "search":
            # Search returns up to 1,000 documents and accepts exact creation-time ranges.
            # One fast count request per rating feed and half plans safe bounded slices.
            listing_requests = math.ceil(images / SEARCH_PAGE_SIZE) if images else 0
            planning_requests = half_count * len(COLLECTION_FEEDS[rating])
            seek_requests = 0
            low = listing_requests * 3.0 + planning_requests * .5
            high = listing_requests * 5.0 + planning_requests * 1.0
            page_size, clean, delayed = SEARCH_PAGE_SIZE, 3.0, 5.0
        else:
            planning_requests = 0
            seek_requests = BENCHMARK_SEEK_REQUESTS_PER_HALF * half_count * len(COLLECTION_FEEDS[rating])
            requests = listing_requests + seek_requests
            low = requests * BENCHMARK_CLEAN_REQUEST_SECONDS
            high = requests * BENCHMARK_DELAYED_REQUEST_SECONDS
            page_size = PAGE_SIZE
            clean, delayed = BENCHMARK_CLEAN_REQUEST_SECONDS, BENCHMARK_DELAYED_REQUEST_SECONDS
        return {"segment": segment, "contentRating": rating, "backend": backend,
                "seconds": round((low + high) / 2), "lowSeconds": round(low),
                "highSeconds": round(high), "benchmarkImages": images,
                "listingRequests": listing_requests, "planningRequests": planning_requests,
                "seekRequests": seek_requests, "pageSize": page_size,
                "cleanRequestSeconds": round(clean, 2), "delayedRequestSeconds": round(delayed, 2),
                "measured": True, "fixedBenchmark": True}

    def rebuild(self, value: str, start_utc: str, end_utc: str, timezone_name: str, segment: str = "all") -> dict:
        key = self.archive_key(value, segment)
        start, end = parse_bounds(value, start_utc, end_utc)
        current = self.status(key)
        if not current["archiveComplete"]:
            raise ValueError("Build this day before rebuilding it")
        if current["state"] == "loading":
            return current
        with self.connect() as db:
            day_row = db.execute(
                "SELECT content_rating,collection_version,top_cursor FROM days WHERE day=?",
                (key,)).fetchone()
            rating = content_rating(day_row["content_rating"] or self.content_rating)
            saved_feed_cursors = {
                int(row["browsing_mask"]): row["top_cursor"]
                for row in db.execute(
                    "SELECT browsing_mask,top_cursor FROM block_feeds WHERE block_key=?",
                    (key,))
            }
            # Format 3 used one combined PG/PG-13 cursor. It cannot safely seed either
            # format-4 shard, so rebuilding an older archive locates both exact feeds.

        # A rebuild clears this block's feeds before it collects anything. If Civitai can
        # no longer reach the date, that collection can never succeed, so the day would be
        # left marked incomplete with no way back -- a working gallery destroyed by asking
        # to refresh it. Check reach while everything is still untouched.
        if active_backend() != "search":
            for mask in COLLECTION_FEEDS[rating]:
                floor = self.feed_floor(mask)
                if floor is not None and floor > start:
                    oldest = oldest_buildable_day(floor)
                    raise ValueError(
                        "Civitai's public image feed can no longer reach this date, so it "
                        "cannot be rebuilt. The oldest day it can rebuild at this coverage "
                        f"is {oldest:%B} {oldest.day}, {oldest.year}. Your saved gallery for "
                        "this day has been left exactly as it was.")

        with self.lock:
            self.jobs[key] = {"state": "loading", "phase": "locating", "progress": 0, "pages": 0,
                "startedMonotonic": time.monotonic(), "etaLowSeconds": None, "etaHighSeconds": None,
                "delayReason": None, "rebuilding": True,
                "collectionBackend": "search" if active_backend() == "search" else "v1-feed"}
            self.cancel_events[key] = threading.Event()
        with self.connect() as db:
            db.execute("UPDATE days SET timezone=?,start_utc=?,end_utc=?,seek_pages=0,seek_bytes=0,"
                "collect_pages=0,collect_bytes=0,api_seconds=0,retry_seconds=0,retry_count=0,elapsed_seconds=0,"
                "seek_seconds=0,organize_seconds=0,pace_seconds=0,response_seconds=0,rate_limit_count=0,"
                "service_retry_count=0,network_retry_count=0,final_pacer_interval=0,wire_bytes=0,"
                "decoded_bytes=0,collection_version=?,updated_at=? WHERE day=?",
                (timezone_name[:100], start.isoformat(), end.isoformat(), COLLECTION_VERSION, utcnow(), key))
            db.execute("DELETE FROM block_feeds WHERE block_key=?", (key,))
            db.executemany("""INSERT INTO block_feeds(
                    block_key,browsing_mask,complete,top_cursor,updated_at) VALUES(?,?,0,?,?)""",
                [(key, mask, saved_feed_cursors.get(mask), utcnow())
                 for mask in COLLECTION_FEEDS[rating]])
        self._invalidate_artist_cache(key)
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

    def _wait_api_lane(self, minimum_interval: float | None = None) -> float:
        interval = max(self.api_pacer.interval, minimum_interval or 0.0)
        remaining = interval - (time.monotonic() - self.last_api_request)
        if remaining > 0:
            started = time.monotonic()
            time.sleep(remaining)
            return time.monotonic() - started
        return 0.0

    def _record_api_failure(self, params: dict, kind: str, *, status: int | None = None,
                            reason: object = None, headers: object = None,
                            body: bytes = b"") -> None:
        """Keep bounded, credential-free upstream diagnostics for later support."""
        safe_headers = {}
        for name in ("Content-Type", "Content-Encoding", "Retry-After", "CF-Ray",
                     "X-Request-Id", "X-Correlation-Id", "Server"):
            try:
                value = headers.get(name) if headers else None
            except (AttributeError, TypeError):
                value = None
            if value:
                safe_headers[name] = str(value)[:300]
        encoding = safe_headers.get("Content-Encoding", "").casefold()
        if body and encoding == "gzip":
            excerpt = f"[gzip-compressed response; {len(body)} bytes captured]"
        else:
            excerpt = body.decode("utf-8", errors="replace") if body else ""
            excerpt = "".join(character if character in "\r\n\t" or ord(character) >= 32 else " "
                              for character in excerpt)
            # The public image endpoint should not return credentials, but redact common
            # secret-shaped fields before anything reaches disk as a defense in depth.
            excerpt = re.sub(
                r'(?i)(access[_-]?token|refresh[_-]?token|authorization|client_secret)'
                r'(["\']?\s*[:=]\s*["\']?)[^"\'\s,}]+', r'\1\2[redacted]', excerpt)
            excerpt = excerpt[:API_FAILURE_BODY_BYTES]
        context = {name: params.get(name) for name in
                   ("backend", "limit", "offset", "sort", "period", "browsingLevel", "withMeta")
                   if params.get(name) is not None}
        if params.get("filter"):
            context["filter"] = [str(value)[:200] for value in params["filter"][:8]]
        if params.get("cursor"):
            context["cursor"] = str(params["cursor"])[:160]
        record = {"timestamp": utcnow(), "kind": kind, "status": status,
                  "reason": str(reason or "")[:500], "request": context,
                  "headers": safe_headers, "bodyExcerpt": excerpt,
                  "bodyTruncated": len(body) > API_FAILURE_BODY_BYTES}
        line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self.api_failure_log.parent.mkdir(parents=True, exist_ok=True)
            if self.api_failure_log.exists() and \
                    self.api_failure_log.stat().st_size + len(line) > API_FAILURE_LOG_BYTES:
                previous = self.api_failure_log.with_name(self.api_failure_log.name + ".1")
                try:
                    previous.unlink()
                except FileNotFoundError:
                    pass
                self.api_failure_log.replace(previous)
            with self.api_failure_log.open("ab") as output:
                output.write(line)
        except OSError:
            # Diagnostics must never turn a recoverable API outage into a local failure.
            pass

    def _request(self, params: dict, minimum_interval: float | None = None,
                 on_delay: Callable[[str, float, int, int], None] | None = None,
                 cancel_event: threading.Event | None = None,
                 on_timing: Callable[[str, float], None] | None = None,
                 on_transfer: Callable[[int, int], None] | None = None) -> tuple[dict, int]:
        """Fetch one page, retrying transient failures.

        ``on_delay`` receives the attempt number so callers can tell a single
        hiccup apart from a service that has genuinely stopped answering.
        """
        request = urllib.request.Request(f"{API_URL}?{urllib.parse.urlencode(params)}",
            headers={"Accept": "application/json", "Accept-Encoding": "gzip", "User-Agent": USER_AGENT})
        with self.api_lock:
            attempt = 0
            last_failure = None
            while True:
                if cancel_event and cancel_event.is_set():
                    raise CollectionCancelled()
                try:
                    paced = self._wait_api_lane(minimum_interval)
                    if on_timing and paced:
                        on_timing("pace", paced)
                    self.last_api_request = time.monotonic()
                    started = time.monotonic()
                    with urllib.request.urlopen(request, timeout=60) as response:
                        raw = response.read()
                        wire_size = len(raw)
                        if response.headers.get("Content-Encoding") == "gzip":
                            raw = gzip.decompress(raw)
                    if on_transfer:
                        on_transfer(wire_size, len(raw))
                    response_seconds = time.monotonic() - started
                    if on_timing:
                        on_timing("response", response_seconds)
                    self.api_pacer.success(response_seconds)
                    return json.loads(raw), len(raw)
                except urllib.error.HTTPError as error:
                    if on_timing:
                        on_timing("response", time.monotonic() - started)
                    try:
                        error_body = error.read(API_FAILURE_BODY_BYTES + 1)
                    except Exception:
                        error_body = b""
                    last_failure = f"HTTP {error.code} {error.reason or ''}".strip()
                    self._record_api_failure(params, "http", status=error.code,
                        reason=error.reason, headers=error.headers, body=error_body)
                    if error.code != 429 and error.code < 500:
                        raise
                    reason = "rate_limited" if error.code == 429 else "service_retry"
                    self.api_pacer.failure(reason)
                    retry = error.headers.get("Retry-After")
                    backoff_attempt = attempt % RATE_LIMIT_RETRIES
                    if error.code == 429:
                        try: wait = max(60.0, float(retry)) if retry else min(600.0, 60.0 * (2**backoff_attempt))
                        except ValueError: wait = min(600.0, 60.0 * (2**backoff_attempt))
                    else: wait = min(60.0, 2.0**backoff_attempt)
                    delay = wait + random.uniform(0, 2)
                except (TimeoutError, urllib.error.URLError) as error:
                    if on_timing:
                        on_timing("response", time.monotonic() - started)
                    self.api_pacer.failure("network_retry")
                    reason = "network_retry"
                    last_failure = f"{type(error).__name__}: {error}"
                    self._record_api_failure(params, "network", reason=error)
                    wait = min(60, 2**(attempt % RATE_LIMIT_RETRIES))
                    delay = wait + random.uniform(0, 1)
                attempts = (CHECKPOINTED_RETRY_ATTEMPTS
                            if cancel_event and reason != "rate_limited"
                            else RATE_LIMIT_RETRIES)
                attempt += 1
                if attempt >= attempts:
                    raise RetryBudgetExhausted(reason, attempts, last_failure)
                if on_delay:
                    on_delay(reason, delay, attempt, attempts)
                if cancel_event:
                    if cancel_event.wait(delay):
                        raise CollectionCancelled()
                else:
                    time.sleep(delay)

    def _search_request(self, body: dict,
                        on_delay: Callable[[str, float, int, int], None] | None = None,
                        cancel_event: threading.Event | None = None,
                        on_timing: Callable[[str, float], None] | None = None,
                        on_transfer: Callable[[int, int], None] | None = None) -> tuple[dict, int]:
        """POST one bounded query to the search-only endpoint used by Civitai's UI."""
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(SEARCH_URL, data=encoded, method="POST", headers={
            "Accept": "application/json", "Accept-Encoding": "gzip",
            "Authorization": f"Bearer {SEARCH_CLIENT_KEY}",
            "Content-Type": "application/json", "User-Agent": USER_AGENT,
        })
        diagnostic = {**body, "backend": "browser-search"}
        with self.api_lock:
            attempt = 0
            last_failure = None
            while True:
                if cancel_event and cancel_event.is_set():
                    raise CollectionCancelled()
                started = time.monotonic()
                try:
                    paced = self._wait_api_lane(.25)
                    if on_timing and paced:
                        on_timing("pace", paced)
                    self.last_api_request = time.monotonic()
                    started = time.monotonic()
                    with urllib.request.urlopen(request, timeout=60) as response:
                        raw = response.read()
                        wire_size = len(raw)
                        if response.headers.get("Content-Encoding") == "gzip":
                            raw = gzip.decompress(raw)
                    if on_transfer:
                        on_transfer(wire_size, len(raw))
                    response_seconds = time.monotonic() - started
                    if on_timing:
                        on_timing("response", response_seconds)
                    self.api_pacer.success(response_seconds)
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise SearchBackendUnavailable("Civitai search returned an invalid response")
                    return payload, len(raw)
                except urllib.error.HTTPError as error:
                    if on_timing:
                        on_timing("response", time.monotonic() - started)
                    try:
                        error_body = error.read(API_FAILURE_BODY_BYTES + 1)
                    except Exception:
                        error_body = b""
                    last_failure = f"HTTP {error.code} {error.reason or ''}".strip()
                    self._record_api_failure(diagnostic, "search_http", status=error.code,
                        reason=error.reason, headers=error.headers, body=error_body)
                    if error.code in {400, 401, 403, 404}:
                        raise SearchBackendUnavailable(
                            f"Civitai browser search rejected the app (HTTP {error.code})") from error
                    if error.code != 429 and error.code < 500:
                        raise
                    reason = "rate_limited" if error.code == 429 else "service_retry"
                    self.api_pacer.failure(reason)
                    retry = error.headers.get("Retry-After")
                    backoff_attempt = attempt % RATE_LIMIT_RETRIES
                    if error.code == 429:
                        try:
                            wait = max(30.0, float(retry)) if retry else min(300.0, 30.0 * 2**backoff_attempt)
                        except ValueError:
                            wait = min(300.0, 30.0 * 2**backoff_attempt)
                    else:
                        wait = min(60.0, 2.0**backoff_attempt)
                    delay = wait + random.uniform(0, 2)
                except (TimeoutError, urllib.error.URLError) as error:
                    if on_timing:
                        on_timing("response", time.monotonic() - started)
                    self.api_pacer.failure("network_retry")
                    reason = "network_retry"
                    last_failure = f"{type(error).__name__}: {error}"
                    self._record_api_failure(diagnostic, "search_network", reason=error)
                    delay = min(60, 2**(attempt % RATE_LIMIT_RETRIES)) + random.uniform(0, 1)
                attempt += 1
                attempts = CHECKPOINTED_RETRY_ATTEMPTS if cancel_event else RATE_LIMIT_RETRIES
                if attempt >= attempts:
                    raise RetryBudgetExhausted(reason, attempts, last_failure)
                if on_delay:
                    on_delay(reason, delay, attempt, attempts)
                if cancel_event:
                    if cancel_event.wait(delay):
                        raise CollectionCancelled()
                else:
                    time.sleep(delay)

    @staticmethod
    def _search_checkpoint(start: datetime, end: datetime, offset: int, expected: int) -> str:
        return "search-v1|{}|{}|{}|{}".format(
            int(start.timestamp() * 1000), int(end.timestamp() * 1000), int(offset), int(expected))

    @staticmethod
    def _parse_search_checkpoint(value: object) -> tuple[int, int, int, int] | None:
        try:
            parts = str(value or "").split("|")
            if len(parts) != 5 or parts[0] != "search-v1":
                return None
            parsed = tuple(int(part) for part in parts[1:])
            return parsed if parsed[0] <= parsed[1] and parsed[2] >= 0 and parsed[3] >= 0 else None
        except (TypeError, ValueError):
            return None

    def _collect(self, key: str, value: str, start: datetime, end: datetime,
                 rebuilding: bool = False) -> None:
        """Use exact date-range search by default; retain the old feed for recovery."""
        # Instance-level request replacement is the collector's established test and
        # diagnostic injection seam; keep those deterministic legacy simulations local.
        injected_legacy_request = "_request" in self.__dict__ and "_search_request" not in self.__dict__
        if active_backend() != "search" or injected_legacy_request:
            with self.lock:
                self.jobs[key]["collectionBackend"] = "v1-feed"
                self.jobs[key]["phase"] = "locating"
            return self._collect_v1(key, value, start, end, rebuilding)
        return self._collect_search(key, value, start, end, rebuilding)

    def _collect_search(self, key: str, value: str, start: datetime, end: datetime,
                        rebuilding: bool = False) -> None:
        """Collect exact bounded time ranges from Civitai's browser image index."""
        with self.connect() as db:
            stored = db.execute("SELECT content_rating,elapsed_seconds FROM days WHERE day=?",
                                (key,)).fetchone()
        request_rating = content_rating(stored["content_rating"] if stored else self.content_rating)
        prior_elapsed = float(stored["elapsed_seconds"] or 0) if stored else 0.0
        masks = COLLECTION_FEEDS[request_rating]
        cancel_event = self.cancel_events[key]
        scan_started = time.monotonic()
        total_pages = 0
        planned_images = 0
        eta_samples: list[float] = []
        displayed_eta: tuple[int, int] | None = None
        last_eta_update = 0.0

        def report_delay(reason: str, wait: float, attempt: int, attempts: int) -> None:
            with self.lock:
                self.jobs[key].update({"delayReason": reason, "retryInSeconds": round(wait),
                    "retryUntilMonotonic": time.monotonic() + wait,
                    "retryAttempt": attempt, "retryAttempts": attempts,
                    "etaLowSeconds": None, "etaHighSeconds": None})
            with self.connect() as db:
                db.execute("""UPDATE days SET retry_seconds=retry_seconds+?,retry_count=retry_count+1,
                           rate_limit_count=rate_limit_count+?,service_retry_count=service_retry_count+?,
                           network_retry_count=network_retry_count+? WHERE day=?""",
                    (wait, int(reason == "rate_limited"), int(reason == "service_retry"),
                     int(reason == "network_retry"), key))

        def fetch(body: dict, checkpoint: str | None) -> dict:
            nonlocal total_pages
            timing = {"pace": 0.0, "response": 0.0, "wire": 0, "decoded": 0}

            def record_timing(kind: str, seconds: float) -> None:
                timing[kind] += seconds

            def record_transfer(wire: int, decoded: int) -> None:
                timing["wire"] += wire
                timing["decoded"] += decoded

            request_started = time.monotonic()
            payload, size = self._search_request(body, on_delay=report_delay,
                cancel_event=cancel_event, on_timing=record_timing, on_transfer=record_transfer)
            if cancel_event.is_set():
                raise CollectionCancelled()
            request_seconds = time.monotonic() - request_started
            total_pages += 1
            with self.connect() as db:
                db.execute("""UPDATE days SET scan_cursor=?,pages=pages+1,
                           metadata_bytes=metadata_bytes+?,collect_pages=collect_pages+1,
                           collect_bytes=collect_bytes+?,api_seconds=api_seconds+?,
                           pace_seconds=pace_seconds+?,response_seconds=response_seconds+?,
                           wire_bytes=wire_bytes+?,decoded_bytes=decoded_bytes+?,
                           final_pacer_interval=?,elapsed_seconds=?,updated_at=? WHERE day=?""",
                    (checkpoint, size, size, request_seconds, timing["pace"], timing["response"],
                     timing["wire"], timing["decoded"], self.api_pacer.interval,
                     prior_elapsed + time.monotonic() - scan_started, utcnow(), key))
            return payload

        def plan_range(mask: int, range_start: datetime, range_end: datetime,
                       checkpoint: str | None, depth: int = 0) -> list[tuple[datetime, datetime, int]]:
            body = search_body(range_start, range_end, mask, limit=0)
            payload = fetch(body, checkpoint)
            try:
                expected = int(payload["estimatedTotalHits"])
            except (KeyError, TypeError, ValueError) as error:
                raise SearchBackendUnavailable(
                    "Civitai search did not report a result count") from error
            if expected < 0:
                raise SearchBackendUnavailable("Civitai search reported an invalid result count")
            if expected > SEARCH_SLICE_RESULT_LIMIT or expected >= 100_000:
                if depth >= 12 or (range_end - range_start) <= timedelta(minutes=1):
                    raise SearchCompletenessError(
                        f"A one-minute Civitai search range still contains {expected:,} images")
                middle = range_start + (range_end - range_start) / 2
                return (plan_range(mask, range_start, middle, checkpoint, depth + 1) +
                        plan_range(mask, middle, range_end, checkpoint, depth + 1))
            # Report the planned size as each leaf range is measured: the planning step
            # is several requests long for a full day, and would otherwise sit silent.
            nonlocal planned_images
            planned_images += expected
            with self.lock:
                self.jobs[key].update({"phase": "locating", "pages": total_pages,
                    "plannedImages": planned_images, "feedIndex": feed_index + 1,
                    "feedCount": len(masks), "browsingMask": mask,
                    "collectionBackend": "search", "delayReason": None,
                    "retryInSeconds": None, "retryUntilMonotonic": None, "retryAttempt": 0})
            return [(range_start, range_end, expected)]

        try:
            for feed_index, mask in enumerate(masks):
                with self.connect() as db:
                    feed = db.execute("SELECT * FROM block_feeds WHERE block_key=? AND browsing_mask=?",
                                      (key, mask)).fetchone()
                if feed and feed["complete"]:
                    continue
                saved_cursor = (feed["top_cursor"] if rebuilding and feed else
                                feed["scan_cursor"] if feed else None)
                saved = self._parse_search_checkpoint(saved_cursor)
                initial_checkpoint = saved_cursor if saved else self._search_checkpoint(start, start, 0, 0)
                slices = plan_range(mask, start, end, initial_checkpoint)
                feed_expected = sum(expected for _, _, expected in slices)
                feed_done = 0

                for slice_start, slice_end, expected in slices:
                    start_ms = int(slice_start.timestamp() * 1000)
                    end_ms = int(slice_end.timestamp() * 1000)
                    if saved and end_ms <= saved[0]:
                        feed_done += expected
                        continue
                    offset = saved[2] if saved and saved[:2] == (start_ms, end_ms) else 0
                    if saved and saved[:2] == (start_ms, end_ms) and saved[3] != expected:
                        # The completed-day index changed between attempts. Restart this
                        # small idempotent slice so its count and pages share one snapshot.
                        offset = 0
                    while offset < expected:
                        if cancel_event.is_set():
                            raise CollectionCancelled()
                        page_limit = min(SEARCH_PAGE_SIZE, expected - offset)
                        checkpoint = self._search_checkpoint(
                            slice_start, slice_end, offset, expected)
                        payload = fetch(search_body(slice_start, slice_end, mask,
                                                    limit=page_limit, offset=offset), checkpoint)
                        hits = payload.get("hits")
                        if not isinstance(hits, list) or len(hits) != page_limit:
                            raise SearchCompletenessError(
                                f"Civitai search returned {len(hits) if isinstance(hits, list) else 0:,} "
                                f"of {page_limit:,} expected images at offset {offset:,}")
                        normalized = [normalize_hit(hit) for hit in hits
                                      if isinstance(hit, dict) and hit.get("id") and
                                      hit.get("createdAt") and hit.get("url")]
                        if len(normalized) != len(hits):
                            raise SearchCompletenessError(
                                "Civitai search returned an incomplete image document")
                        image_ids = self._upsert_normalized(normalized, forced_date=value)
                        offset += len(hits)
                        next_checkpoint = self._search_checkpoint(
                            slice_start, slice_end, offset, expected)
                        with self.connect() as db:
                            db.executemany(
                                "INSERT OR IGNORE INTO block_images(block_key,image_id) VALUES(?,?)",
                                [(key, image_id) for image_id in image_ids])
                            db.execute("""UPDATE block_feeds SET scan_cursor=?,pages=pages+1,
                                       updated_at=? WHERE block_key=? AND browsing_mask=?""",
                                (next_checkpoint, utcnow(), key, mask))

                        completed = feed_done + offset
                        feed_fraction = completed / feed_expected if feed_expected else 1.0
                        overall = (feed_index + feed_fraction) / len(masks)
                        elapsed = max(.001, time.monotonic() - scan_started)
                        eta = round(elapsed * (1 - overall) / overall) if overall > 0 else None
                        if eta is not None:
                            eta_samples = (eta_samples + [float(eta)])[-5:]
                        now = time.monotonic()
                        if elapsed >= 5 and total_pages >= 3 and eta_samples and \
                                (displayed_eta is None or now - last_eta_update >= 10):
                            displayed_eta = conservative_eta_range(
                                sorted(eta_samples)[len(eta_samples) // 2])
                            last_eta_update = now
                        with self.lock:
                            self.jobs[key].update({"pages": total_pages, "phase": "collecting",
                                "progress": round(overall * 100, 1), "etaSeconds": eta,
                                "listingsChecked": completed,
                                "searchReachedAt": slice_end.isoformat(),
                                "feedIndex": feed_index + 1, "feedCount": len(masks),
                                "browsingMask": mask, "collectionBackend": "search",
                                "etaLowSeconds": displayed_eta[0] if displayed_eta else None,
                                "etaHighSeconds": displayed_eta[1] if displayed_eta else None,
                                "delayReason": None, "retryInSeconds": None,
                                "retryUntilMonotonic": None, "retryAttempt": 0})

                    with self.connect() as db:
                        slice_start_text = slice_start.isoformat(timespec="milliseconds").replace("+00:00", "Z")
                        slice_end_text = slice_end.isoformat(timespec="milliseconds").replace("+00:00", "Z")
                        saved_count = db.execute("""SELECT COUNT(*) FROM block_images b
                            JOIN images i ON i.id=b.image_id WHERE b.block_key=?
                              AND i.browsing_level=? AND i.created_at>=? AND i.created_at<?""",
                            (key, mask, slice_start_text, slice_end_text)).fetchone()[0]
                    if saved_count != expected:
                        raise SearchCompletenessError(
                            f"Civitai search advertised {expected:,} unique images for a range "
                            f"but {saved_count:,} were saved")
                    feed_done += expected
                    saved = (end_ms, end_ms, 0, 0)
                    with self.connect() as db:
                        db.execute("UPDATE block_feeds SET scan_cursor=?,updated_at=? "
                                   "WHERE block_key=? AND browsing_mask=?",
                            (self._search_checkpoint(slice_end, slice_end, 0, 0),
                             utcnow(), key, mask))

                with self.connect() as db:
                    db.execute("""UPDATE block_feeds SET complete=1,scan_cursor=NULL,
                               older_cursor=NULL,top_cursor=NULL,updated_at=?
                               WHERE block_key=? AND browsing_mask=?""",
                        (utcnow(), key, mask))

            with self.connect() as db:
                db.execute("""UPDATE days SET complete=1,scan_cursor=NULL,content_rating=?,
                           collection_version=?,elapsed_seconds=?,updated_at=? WHERE day=?""",
                    (request_rating, COLLECTION_VERSION,
                     prior_elapsed + time.monotonic() - scan_started, utcnow(), key))
            with self.lock:
                self.jobs[key].update({"phase": "organizing", "progress": 100,
                    "etaSeconds": None, "etaLowSeconds": None, "etaHighSeconds": None})
            organize_started = time.monotonic()
            self._invalidate_artist_cache(key)
            self.build_artist_index(key)
            merged = self.merge_completed_halves(key)
            if self.on_block_complete:
                try:
                    self.on_block_complete(key, merged)
                except Exception:
                    pass
            with self.connect() as db:
                db.execute("UPDATE days SET organize_seconds=organize_seconds+?,elapsed_seconds=? WHERE day=?",
                    (time.monotonic() - organize_started,
                     prior_elapsed + time.monotonic() - scan_started, key))
            with self.lock:
                self.jobs[key].update({"state": "complete", "phase": "complete",
                    "progress": 100, "etaSeconds": 0})
        except CollectionCancelled:
            return
        except Exception as error:
            with self.connect() as db:
                db.execute("UPDATE days SET complete=0,elapsed_seconds=?,updated_at=? WHERE day=?",
                    (prior_elapsed + time.monotonic() - scan_started, utcnow(), key))
            try:
                with (self.root.parent / "error.log").open("a", encoding="utf-8") as output:
                    output.write(f"\n[{utcnow()}] Daily search collection: {type(error).__name__}\n"
                                 f"{traceback.format_exc()}")
            except OSError:
                pass
            outage = isinstance(error, RetryBudgetExhausted)
            configuration = isinstance(error, SearchBackendUnavailable)
            incomplete = isinstance(error, SearchCompletenessError)
            last_failure = (f" Last response: {error.last_failure}." if outage and
                            error.last_failure else "")
            message = (f"Civitai search is still unavailable after {error.attempts} attempts."
                       f"{last_failure} Everything collected so far is saved; Continue building "
                       "will resume it. Details are in data/api-failures.jsonl." if outage else
                       "Civitai changed or rejected its browser search service, so the app cannot "
                       "safely verify this date. No incomplete gallery was published. Details are "
                       "in data/api-failures.jsonl." if configuration else
                       "Civitai search changed while this range was being collected. Everything "
                       "received is saved, but the gallery was not marked complete; Continue "
                       "building will verify the range again." if incomplete else
                       "Civitai stopped before the full time range was collected. Your progress "
                       "was saved; Continue building will resume it.")
            with self.lock:
                self.jobs[key].update({"state": "error", "error": message,
                    "errorKind": "service_unavailable" if outage else
                                 "search_configuration" if configuration else
                                 "search_incomplete" if incomplete else "collection_failed",
                    "delayReason": None, "retryUntilMonotonic": None})

    def feed_floor(self, mask: int) -> datetime | None:
        """Return the oldest image one browsing-level feed can still reach.

        A single one-row probe at the deepest reachable offset costs under a kilobyte and
        answers in well under a second. Discovering the same limit by seeking costs tens
        of requests and only reveals it after the user has already waited for them.
        Returns None when the probe is inconclusive, so an unclear answer never blocks a
        build that might have succeeded.
        """
        cached = self._floor_cache.get(mask)
        if cached and time.monotonic() - cached[0] < FEED_FLOOR_CACHE_SECONDS:
            return cached[1]
        params = {"limit": 1, "sort": "Newest", "period": "AllTime",
                  "browsingLevel": mask, "withMeta": "false",
                  "cursor": f"{FEED_FLOOR_PROBE_OFFSET}|0"}
        try:
            payload, _ = self._request(params)
            stamps = [parse_timestamp(row["createdAt"])
                      for row in payload.get("items", []) if row.get("createdAt")]
            floor = min(stamps) if stamps else None
        except Exception:
            floor = None
        self._floor_cache[mask] = (time.monotonic(), floor)
        return floor

    def history_window(self, requested_content_rating: str | None = None) -> dict:
        """Report how far back Civitai can currently be collected for a coverage level."""
        rating = content_rating(requested_content_rating or self.content_rating)
        floors = {mask: self.feed_floor(mask) for mask in COLLECTION_FEEDS[rating]}
        known = {mask: value for mask, value in floors.items() if value}
        # Every required feed must reach the boundary, so the newest floor is the binding
        # one: the most restrictive level decides what the whole block can do.
        binding_level = max(known, key=known.get) if known else None
        binding = known[binding_level] if binding_level else None
        oldest_day = oldest_buildable_day(binding).isoformat() if binding else None
        return {"contentRating": rating,
                "floor": binding.isoformat() if binding else None,
                "oldestBuildableDay": oldest_day, "bindingLevel": binding_level,
                "perLevel": {str(mask): (value.isoformat() if value else None)
                             for mask, value in floors.items()},
                "measured": bool(known)}

    def _seek_cursor(self, value: str, end: datetime, cancel_event: threading.Event,
                     browsing_mask: int,
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
        boundary_found = False
        oldest_valid: datetime | None = None

        def probe(offset: int) -> tuple[datetime | None, datetime | None]:
            nonlocal pages, transferred, boundary_found, oldest_valid
            if cancel_event.is_set():
                return None, None
            params = {"limit": PAGE_SIZE, "sort": "Newest", "period": "AllTime",
                "browsingLevel": browsing_mask, "withMeta": "false",
                "cursor": f"{offset}|{timestamp_ms}"}
            timestamps = []
            for empty_attempt in range(1, EMPTY_PROBE_ATTEMPTS + 1):
                timing = {"pace": 0.0, "response": 0.0, "wire": 0, "decoded": 0}
                def record_timing(kind: str, seconds: float) -> None:
                    timing[kind] += seconds
                def record_transfer(wire: int, decoded: int) -> None:
                    timing["wire"] += wire; timing["decoded"] += decoded
                request_started = time.monotonic()
                payload, size = self._request(params, on_delay=report_delay,
                                              cancel_event=cancel_event, on_timing=record_timing,
                                              on_transfer=record_transfer)
                request_seconds = time.monotonic() - request_started
                pages += 1; transferred += size
                with self.connect() as db:
                    db.execute("""UPDATE days SET seek_pages=seek_pages+1,seek_bytes=seek_bytes+?,api_seconds=api_seconds+?,
                               pace_seconds=pace_seconds+?,response_seconds=response_seconds+?,
                               wire_bytes=wire_bytes+?,decoded_bytes=decoded_bytes+?,
                               final_pacer_interval=? WHERE day=?""",
                        (size, request_seconds, timing["pace"], timing["response"],
                         timing["wire"], timing["decoded"], self.api_pacer.interval, value))
                timestamps = [parse_timestamp(row["createdAt"])
                              for row in payload.get("items", []) if row.get("createdAt")]
                if timestamps:
                    break
                self._record_api_failure(params, "premature_empty_page", status=200,
                    reason=f"Empty locator response {empty_attempt} of {EMPTY_PROBE_ATTEMPTS}",
                    body=json.dumps(payload, ensure_ascii=False).encode("utf-8"))
                if empty_attempt < EMPTY_PROBE_ATTEMPTS:
                    report_delay("service_retry", 0, empty_attempt, EMPTY_PROBE_ATTEMPTS)
            oldest = min(timestamps) if timestamps else None
            newest = max(timestamps) if timestamps else None
            if oldest is not None:
                oldest_valid = min(oldest_valid, oldest) if oldest_valid else oldest
                boundary_found = boundary_found or oldest < end
            with self.lock:
                job = self.jobs.get(value)
                if job is not None:
                    job.update({"pages": pages, "searchReachedAt": oldest.isoformat() if oldest else None,
                        "delayReason": None, "retryInSeconds": None,
                        "retryUntilMonotonic": None, "retryAttempt": 0})
            return oldest, newest

        # The feed has no date filter, so reaching a past day means finding the offset it
        # starts at, and the only way to read an offset's date is to fetch it. Doubling
        # from one page spends ~16 requests per feed doing that, each pulling 200 images
        # to read their timestamps and discard them.
        #
        # The floor probe already knows the date at FEED_FLOOR_PROBE_OFFSET, and the feed
        # runs close to linear in time (measured ~7% drift across a 2-day span), so that
        # anchor turns the search into an estimate plus a short confirmation. A poor
        # estimate can only cost extra probes: every bound below is still verified against
        # real timestamps by the same binary search, so it cannot land on the wrong day.
        lower, upper, oldest = 0, PAGE_SIZE, None
        bracketed = False
        floor = self.feed_floor(browsing_mask)
        if floor is not None:
            now = datetime.now(timezone.utc)
            span = (now - floor).total_seconds()
            wanted = (now - end).total_seconds()
            if span > 0 and 0 < wanted < span:
                estimate = FEED_FLOOR_PROBE_OFFSET * wanted / span
                margin = max(4 * PAGE_SIZE, estimate * .25)
                low = int(max(0, estimate - margin) // PAGE_SIZE) * PAGE_SIZE
                high = int(min(MAX_CURSOR_OFFSET, estimate + margin) // PAGE_SIZE) * PAGE_SIZE
                if high > low:
                    high_oldest, _ = probe(high)
                    # An empty page means the probe passed Civitai's traversal ceiling,
                    # which bounds the search from above exactly as a crossing page does.
                    if high_oldest is None or high_oldest < end:
                        if low <= 0:
                            lower, upper, oldest, bracketed = 0, high, high_oldest, True
                        else:
                            low_oldest, _ = probe(low)
                            if low_oldest is None or low_oldest < end:
                                # The estimate overshot: the boundary sits at or above
                                # `low`, so bisect everything down to it. Bisection needs
                                # a lower bound that has *not* crossed, and offset 0 is
                                # the newest page, so it never has.
                                lower, upper, oldest, bracketed = 0, low, low_oldest, True
                            else:
                                lower, upper, oldest, bracketed = low, high, high_oldest, True
                    else:
                        # Undershot: keep doubling, but from the estimate, not page one.
                        lower = upper = max(PAGE_SIZE, high)
        while not bracketed and not cancel_event.is_set():
            oldest, _ = probe(upper)
            if oldest is None or oldest < end:
                break
            if upper >= MAX_CURSOR_OFFSET:
                raise HistoryWindowUnavailable(browsing_mask, end, oldest_valid)
            lower, upper = upper, min(MAX_CURSOR_OFFSET, upper * 2)
        if cancel_event.is_set():
            return None, pages, transferred
        while upper - lower > PAGE_SIZE and not cancel_event.is_set():
            span_pages = (upper - lower) // PAGE_SIZE
            middle = lower + max(1, span_pages // 2) * PAGE_SIZE
            oldest, _ = probe(middle)
            # Civitai returns an empty page once a cursor probe passes its deep
            # traversal ceiling. Treat that as an upper bound and binary-search
            # back toward the last valid page. Returning None here used to restart
            # sequential collection from the newest image, wasting hundreds of
            # requests and producing a wildly incorrect ETA.
            if oldest is None or oldest < end:
                upper = middle
            else:
                lower = middle
        if not boundary_found:
            raise HistoryWindowUnavailable(browsing_mask, end, oldest_valid)
        return f"{upper}|{timestamp_ms}", pages, transferred

    def _collect_v1(self, key: str, value: str, start: datetime, end: datetime,
                    rebuilding: bool = False) -> None:
        """Collect every non-overlapping browsing-level feed before publishing a block."""
        with self.connect() as db:
            stored = db.execute("SELECT content_rating,elapsed_seconds FROM days WHERE day=?",
                                (key,)).fetchone()
        request_rating = content_rating(stored["content_rating"] if stored else self.content_rating)
        prior_elapsed = float(stored["elapsed_seconds"] or 0) if stored else 0.0
        masks = COLLECTION_FEEDS[request_rating]
        cancel_event = self.cancel_events[key]
        scan_started = time.monotonic()
        total_pages = 0
        with self.connect() as db:
            pending_masks = [row["browsing_mask"] for row in db.execute(
                "SELECT browsing_mask FROM block_feeds WHERE block_key=? AND complete=0",
                (key,)).fetchall()] or list(masks)

        def report_delay(reason: str, wait: float, attempt: int, attempts: int) -> None:
            with self.lock:
                self.jobs[key].update({"delayReason": reason, "retryInSeconds": round(wait),
                    "retryUntilMonotonic": time.monotonic() + wait,
                    "retryAttempt": attempt, "retryAttempts": attempts,
                    "etaLowSeconds": None, "etaHighSeconds": None})
            with self.connect() as db:
                db.execute("""UPDATE days SET retry_seconds=retry_seconds+?,retry_count=retry_count+1,
                           rate_limit_count=rate_limit_count+?,service_retry_count=service_retry_count+?,
                           network_retry_count=network_retry_count+? WHERE day=?""",
                    (wait, int(reason == "rate_limited"), int(reason == "service_retry"),
                     int(reason == "network_retry"), key))

        try:
            # Ask each unfinished feed how far back it can reach before spending a seek on
            # it. Without this the same limit is only discovered after tens of requests,
            # and only after the user has watched them all go by.
            for mask in pending_masks:
                if cancel_event.is_set():
                    return
                floor = self.feed_floor(mask)
                if floor is not None and floor > start:
                    raise HistoryWindowUnavailable(mask, start, floor)

            for feed_index, mask in enumerate(masks):
                with self.connect() as db:
                    feed = db.execute("SELECT * FROM block_feeds WHERE block_key=? AND browsing_mask=?",
                                      (key, mask)).fetchone()
                if feed and feed["complete"]:
                    continue
                cursor = feed["top_cursor"] if rebuilding and feed else feed["scan_cursor"] if feed else None
                top_cursor = cursor
                newest_seen = None
                reached_target_day = False
                eta_samples: list[float] = []
                displayed_eta: tuple[int, int] | None = None
                last_eta_update = 0.0

                if cursor is None:
                    seek_started = time.monotonic()
                    cursor, seek_pages, _ = self._seek_cursor(
                        key, end, cancel_event, mask, report_delay)
                    with self.connect() as db:
                        db.execute("UPDATE days SET seek_seconds=seek_seconds+? WHERE day=?",
                                   (time.monotonic() - seek_started, key))
                    total_pages += seek_pages
                    top_cursor = cursor
                    if cancel_event.is_set():
                        return

                while True:
                    if cancel_event.is_set():
                        return
                    params = {"limit": PAGE_SIZE, "sort": "Newest", "period": "AllTime",
                              "browsingLevel": mask, "withMeta": "false"}
                    if cursor:
                        params["cursor"] = cursor
                    timing = {"pace": 0.0, "response": 0.0, "wire": 0, "decoded": 0}

                    def record_timing(kind: str, seconds: float) -> None:
                        timing[kind] += seconds

                    def record_transfer(wire: int, decoded: int) -> None:
                        timing["wire"] += wire
                        timing["decoded"] += decoded

                    request_started = time.monotonic()
                    payload, size = self._request(params, on_delay=report_delay,
                        cancel_event=cancel_event, on_timing=record_timing,
                        on_transfer=record_transfer)
                    # A request may finish just after Stop loading was pressed. Do not
                    # save that response into the previously published gallery.
                    if cancel_event.is_set():
                        return
                    request_seconds = time.monotonic() - request_started
                    rows = payload.get("items", [])
                    next_cursor = (payload.get("metadata") or {}).get("nextCursor")
                    timestamps = [parse_timestamp(row["createdAt"]) for row in rows
                                  if row.get("createdAt")]
                    oldest = min(timestamps) if timestamps else None
                    newest = max(timestamps) if timestamps else None
                    normalized = [normalize(row) for row in rows
                        if row.get("type") == "image" and row.get("url") and row.get("createdAt")
                        and start <= parse_timestamp(row["createdAt"]) < end]
                    image_ids = self._upsert_normalized(normalized, forced_date=value)
                    with self.connect() as db:
                        db.executemany("INSERT OR IGNORE INTO block_images(block_key,image_id) VALUES(?,?)",
                                       [(key, image_id) for image_id in image_ids])
                        db.execute("""UPDATE block_feeds SET scan_cursor=?,pages=pages+1,
                                   updated_at=? WHERE block_key=? AND browsing_mask=?""",
                                   (next_cursor, utcnow(), key, mask))
                        db.execute("""UPDATE days SET scan_cursor=?,pages=pages+1,
                                   metadata_bytes=metadata_bytes+?,collect_pages=collect_pages+1,
                                   collect_bytes=collect_bytes+?,api_seconds=api_seconds+?,
                                   pace_seconds=pace_seconds+?,response_seconds=response_seconds+?,
                                   wire_bytes=wire_bytes+?,decoded_bytes=decoded_bytes+?,
                                   final_pacer_interval=?,elapsed_seconds=?,updated_at=? WHERE day=?""",
                            (next_cursor, size, size, request_seconds, timing["pace"], timing["response"],
                             timing["wire"], timing["decoded"], self.api_pacer.interval,
                             prior_elapsed + time.monotonic() - scan_started, utcnow(), key))
                    total_pages += 1

                    if oldest is not None:
                        newest_seen = max(newest_seen, newest) if newest_seen and newest else newest
                        covered = max(0.0, min(1.0,
                            (end - oldest).total_seconds() / (end - start).total_seconds()))
                        if not reached_target_day and oldest < end:
                            top_cursor = cursor
                        reached_target_day = reached_target_day or oldest < end
                    else:
                        covered = 0.0
                    phase = "collecting" if reached_target_day else "locating"
                    if oldest is not None and newest_seen is not None:
                        scanned_seconds = max(0.0, (newest_seen - oldest).total_seconds())
                        remaining_seconds = max(0.0,
                            ((oldest - end) if phase == "locating" else (oldest - start)).total_seconds())
                    else:
                        scanned_seconds = remaining_seconds = 0.0
                    elapsed = max(0.001, time.monotonic() - scan_started)
                    eta = round(elapsed * remaining_seconds / scanned_seconds) \
                        if total_pages >= 2 and scanned_seconds > 0 else None
                    if eta is not None:
                        eta_samples = (eta_samples + [float(eta)])[-5:]
                    now = time.monotonic()
                    if elapsed >= 10 and total_pages >= 5 and eta_samples and \
                            (displayed_eta is None or now - last_eta_update >= 10):
                        displayed_eta = conservative_eta_range(
                            sorted(eta_samples)[len(eta_samples) // 2])
                        last_eta_update = now
                    overall = (feed_index + covered) / len(masks) * 100
                    with self.lock:
                        self.jobs[key].update({"pages": total_pages, "phase": phase,
                            "progress": round(overall, 1), "etaSeconds": eta,
                            "searchReachedAt": oldest.isoformat() if oldest else None,
                            "feedIndex": feed_index + 1, "feedCount": len(masks),
                            "browsingMask": mask,
                            "etaLowSeconds": displayed_eta[0] if displayed_eta else None,
                            "etaHighSeconds": displayed_eta[1] if displayed_eta else None,
                            "delayReason": None, "retryInSeconds": None,
                            "retryUntilMonotonic": None, "retryAttempt": 0})

                    if oldest is not None and oldest < start:
                        with self.connect() as db:
                            db.execute("""UPDATE block_feeds SET complete=1,scan_cursor=NULL,
                                       older_cursor=?,top_cursor=?,updated_at=?
                                       WHERE block_key=? AND browsing_mask=?""",
                                       (cursor, top_cursor, utcnow(), key, mask))
                        break
                    if not next_cursor:
                        self._record_api_failure(params, "premature_feed_end", status=200,
                            reason="No next cursor before the requested time boundary",
                            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"))
                        raise HistoryWindowUnavailable(mask, start, oldest)
                    cursor = next_cursor

            with self.connect() as db:
                db.execute("""UPDATE days SET complete=1,scan_cursor=NULL,content_rating=?,
                           collection_version=?,elapsed_seconds=?,updated_at=? WHERE day=?""",
                    (request_rating, COLLECTION_VERSION,
                     prior_elapsed + time.monotonic() - scan_started, utcnow(), key))
            with self.lock:
                self.jobs[key].update({"phase": "organizing", "progress": 100,
                    "etaSeconds": None, "etaLowSeconds": None, "etaHighSeconds": None})
            organize_started = time.monotonic()
            self._invalidate_artist_cache(key)
            self.build_artist_index(key)
            merged = self.merge_completed_halves(key)
            if self.on_block_complete:
                try:
                    self.on_block_complete(key, merged)
                except Exception:
                    pass
            with self.connect() as db:
                db.execute("UPDATE days SET organize_seconds=organize_seconds+?,elapsed_seconds=? WHERE day=?",
                    (time.monotonic() - organize_started,
                     prior_elapsed + time.monotonic() - scan_started, key))
            with self.lock:
                self.jobs[key].update({"state": "complete", "phase": "complete",
                    "progress": 100, "etaSeconds": 0})
        except CollectionCancelled:
            return
        except Exception as error:
            with self.connect() as db:
                db.execute("UPDATE days SET complete=0,elapsed_seconds=?,updated_at=? WHERE day=?",
                    (prior_elapsed + time.monotonic() - scan_started, utcnow(), key))
            try:
                with (self.root.parent / "error.log").open("a", encoding="utf-8") as output:
                    output.write(f"\n[{utcnow()}] Daily history collection: {type(error).__name__}\n"
                                 f"{traceback.format_exc()}")
            except OSError:
                pass
            outage = isinstance(error, RetryBudgetExhausted)
            window = isinstance(error, HistoryWindowUnavailable)
            last_failure = (f" Last response: {error.last_failure}." if outage and
                            error.last_failure else "")
            window_hint = ""
            if window and error.oldest_seen:
                reachable = oldest_buildable_day(error.oldest_seen)
                window_hint = (". The oldest day Civitai can currently build at this "
                               f"coverage is {reachable:%B} {reachable.day}, {reachable.year}")
            message = (f"Civitai is still unavailable after {error.attempts} attempts."
                       f"{last_failure} "
                       "Everything collected so far is saved; Continue building will "
                       "resume from the last successful page. Details are in "
                       "data/api-failures.jsonl." if outage else
                       "Civitai's public image feed cannot reach back this far. Its API "
                       "limits how deep the image list can be paged, and that limit moves "
                       "forward as new artwork is posted, so older days pass out of reach. "
                       "This is not a problem with your device or connection"
                       f"{window_hint}. Days already in your archive are unaffected and "
                       "stay viewable. Details are in data/api-failures.jsonl."
                       if window else
                       "Civitai stopped before the full time range was collected. Your "
                       "progress was saved; Continue building will resume it.")
            with self.lock:
                self.jobs[key].update({"state": "error", "error": message,
                    "errorKind": "service_unavailable" if outage else
                                 "history_window" if window else "collection_failed",
                    "delayReason": None, "retryUntilMonotonic": None})

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
                ,COALESCE(SUM(seek_seconds),0),COALESCE(SUM(organize_seconds),0),
                COALESCE(SUM(pace_seconds),0),COALESCE(SUM(response_seconds),0),
                COALESCE(SUM(rate_limit_count),0),COALESCE(SUM(service_retry_count),0),
                COALESCE(SUM(network_retry_count),0),COALESCE(MAX(final_pacer_interval),0),
                COALESCE(SUM(wire_bytes),0),COALESCE(SUM(decoded_bytes),0)
                FROM days WHERE day IN (?,?)""", (f"{day}#morning", f"{day}#evening")).fetchone()
            db.execute("""INSERT INTO days(day,complete,content_rating,collection_version,
                          seek_pages,seek_bytes,collect_pages,
                          collect_bytes,api_seconds,retry_seconds,retry_count,elapsed_seconds,seek_seconds,
                          organize_seconds,pace_seconds,response_seconds,rate_limit_count,
                          service_retry_count,network_retry_count,final_pacer_interval,wire_bytes,
                          decoded_bytes,updated_at)
                          VALUES(?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET complete=1,
                          content_rating=excluded.content_rating,seek_pages=excluded.seek_pages,seek_bytes=excluded.seek_bytes,
                          collection_version=excluded.collection_version,
                          collect_pages=excluded.collect_pages,collect_bytes=excluded.collect_bytes,
                          api_seconds=excluded.api_seconds,retry_seconds=excluded.retry_seconds,
                          retry_count=excluded.retry_count,elapsed_seconds=excluded.elapsed_seconds,
                          seek_seconds=excluded.seek_seconds,organize_seconds=excluded.organize_seconds,
                          pace_seconds=excluded.pace_seconds,response_seconds=excluded.response_seconds,
                          rate_limit_count=excluded.rate_limit_count,
                          service_retry_count=excluded.service_retry_count,
                          network_retry_count=excluded.network_retry_count,
                          final_pacer_interval=excluded.final_pacer_interval,
                          wire_bytes=excluded.wire_bytes,decoded_bytes=excluded.decoded_bytes,
                          updated_at=excluded.updated_at""",
                       (day, coverage, COLLECTION_VERSION, *totals, utcnow()))
        self._invalidate_artist_cache(day)
        self.build_artist_index(day)
        return day

    def build_artist_index(self, value: str, levels: tuple[int, ...] | None = None) -> None:
        with self.index_lock, self.connect() as db:
            levels = levels or self.visible_levels
            levels_key = self._levels_key(levels)
            holes = ",".join("?" for _ in levels)
            rows = db.execute(f"SELECT i.id,i.username,i.username_key,i.created_at,i.stats FROM block_images b JOIN images i ON i.id=b.image_id WHERE b.block_key=? AND {BROWSING_LEVEL_SQL} IN ({holes}) ORDER BY i.created_at DESC", (value, *levels)).fetchall()
            groups: dict[str, list[sqlite3.Row]] = {}
            for row in rows: groups.setdefault(row["username_key"], []).append(row)
            ranked = []
            for key, items in groups.items():
                def reactions(item) -> int:
                    return max(0, int(json.loads(item["stats"]).get("reactionCount", 0)))
                representative = max(items, key=lambda item: (
                    reactions(item), item["created_at"], item["id"]))
                newest = items[0]["created_at"]
                total_reactions = sum(reactions(item) for item in items)
                ranked.append((total_reactions, key, items[0]["username"], len(items),
                               representative["id"], newest))
            ranked.sort(key=lambda row: (-row[0], -parse_timestamp(row[5]).timestamp(), row[1]))
            db.execute("DELETE FROM day_artists WHERE day=?", (value,))
            db.executemany("""INSERT INTO day_artists(day, username_key, username,
                    image_count, representative_id, newest_at, rank_order)
                VALUES(?,?,?,?,?,?,?)""",
                [(value, key, username, count, rep, newest, order) for order, (_, key, username, count, rep, newest) in enumerate(ranked)])
            db.execute("DELETE FROM day_artist_cache WHERE day=? AND levels_key=?",
                       (value, levels_key))
            db.executemany("""INSERT INTO day_artist_cache(day,levels_key,username_key,username,
                    image_count,representative_id,newest_at,rank_order) VALUES(?,?,?,?,?,?,?,?)""",
                [(value, levels_key, key, username, count, rep, newest, order)
                 for order, (_, key, username, count, rep, newest) in enumerate(ranked)])
            db.execute("INSERT OR REPLACE INTO day_artist_cache_state(day,levels_key) VALUES(?,?)",
                       (value, levels_key))
            db.execute("UPDATE days SET artist_levels=? WHERE day=?", (levels_key, value))
            self._active_index_levels[value] = levels_key

    def day_summary(self, value: str) -> dict:
        self._ensure_artist_index(value)
        with self.connect() as db:
            day = db.execute("SELECT complete,updated_at FROM days WHERE day=?", (value,)).fetchone()
            counts = db.execute("SELECT COALESCE(SUM(image_count),0),COUNT(*) FROM day_artists "
                                "WHERE day=?", (value,)).fetchone()
            images, artists = counts[0], counts[1]
        return {"date": value, "complete": bool(day and day["complete"]), "imageCount": images, "artistCount": artists, "updatedAt": day["updated_at"] if day else None}

    def day_artist_keys(self, value: str) -> list[dict]:
        """Every creator in the day, in the archive's own order, without image hydration.

        Cheap enough to sort in full for a view change: a large day is a few thousand rows.
        """
        self._ensure_artist_index(value)
        with self.connect() as db:
            return [{"key": row["username_key"], "username": row["username"],
                     "imageCount": row["image_count"], "rank": row["rank_order"],
                     "representativeId": row["representative_id"]}
                    for row in db.execute(
                        "SELECT username_key, username, image_count, rank_order, representative_id "
                        "FROM day_artists WHERE day=? ORDER BY rank_order", (value,))]

    def creator_quality_scores(self, value: str, excluded_images=None) -> dict[str, float]:
        """Volume-resistant daily artwork quality for the personalised gallery.

        Total reactions are useful for the explicit Popular view, but they let a creator
        posting a hundred merely-average images dominate a creator posting a few strong
        ones.  For You instead considers at most the five best images, gives each lower
        ranked image diminishing influence, and shrinks small samples toward the day's
        per-image average.  A separate soft upload penalty is applied by the recommender.
        """
        hidden = set(excluded_images or ())
        with self.connect() as db:
            rating_clause, rating_params = _rating_clause(self.visible_levels)
            rows = db.execute(
                "SELECT i.username_key AS key,i.id AS id,"
                "CAST(COALESCE(json_extract(i.stats,'$.reactionCount'),0) AS INTEGER) AS reactions "
                "FROM block_images b JOIN images i ON i.id=b.image_id "
                f"WHERE b.block_key=?{rating_clause}", (value, *rating_params)).fetchall()
        groups: dict[str, list[int]] = {}
        all_reactions = []
        for row in rows:
            if row["id"] in hidden:
                continue
            reactions = max(0, int(row["reactions"] or 0))
            groups.setdefault(row["key"], []).append(reactions)
            all_reactions.append(reactions)
        prior = sum(all_reactions) / len(all_reactions) if all_reactions else 0.0
        weights = (1.0, .8, .6, .4, .2)
        prior_weight = 2.0
        result = {}
        for key, reactions in groups.items():
            best = sorted(reactions, reverse=True)[:len(weights)]
            used_weights = weights[:len(best)]
            weighted = sum(value * weight for value, weight in zip(best, used_weights))
            adjusted = (weighted + prior * prior_weight) / (sum(used_weights) + prior_weight)
            # Compress reaction-count outliers before scores are normalized together.
            result[key] = math.log1p(adjusted)
        return result

    def creator_reaction_totals(self, value: str, excluded_images=None) -> dict[str, int]:
        """Total daily reactions per creator from the already-saved listing metadata."""
        hidden = set(excluded_images or ())
        with self.connect() as db:
            rating_clause, rating_params = _rating_clause(self.visible_levels)
            rows = db.execute(
                "SELECT i.username_key AS key,i.id AS id,"
                "CAST(COALESCE(json_extract(i.stats,'$.reactionCount'),0) AS INTEGER) AS reactions "
                "FROM block_images b JOIN images i ON i.id=b.image_id "
                f"WHERE b.block_key=?{rating_clause}", (value, *rating_params)).fetchall()
        totals = {}
        for row in rows:
            if row["id"] not in hidden:
                totals[row["key"]] = totals.get(row["key"], 0) + max(0, int(row["reactions"] or 0))
        return totals

    def creators_with_visible_images(self, value: str, excluded_images) -> set[str]:
        """Creator keys left with at least one image once hidden artwork is removed.

        A creator whose every image is hidden has no card, so they must also leave the
        count. Otherwise the gallery advertises a total that cannot be scrolled to.
        """
        self._ensure_artist_index(value)
        hidden = set(excluded_images or ())
        if not hidden:
            with self.connect() as db:
                return {row["username_key"] for row in db.execute(
                    "SELECT DISTINCT username_key FROM day_artists WHERE day=?", (value,))}
        with self.connect() as db:
            rating_clause, rating_params = _rating_clause(self.visible_levels)
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
        self._ensure_artist_index(value)
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
                rating_clause, rating_params = _rating_clause(self.visible_levels)
                for row in db.execute(
                        f"SELECT i.username_key AS key, i.id AS id FROM block_images b "
                        f"JOIN images i ON i.id=b.image_id WHERE b.block_key=? AND i.username_key IN ({holes}) "
                        f"{rating_clause} ORDER BY CAST(COALESCE(json_extract(i.stats,'$.reactionCount'),0) "
                        "AS INTEGER) DESC,i.created_at DESC,i.id DESC", (value, *affected, *rating_params)):
                    if row["key"] not in result and row["id"] not in hidden:
                        result[row["key"]] = row["id"]
            return result

    def image_model_versions(self, image_ids) -> dict[int, set[int]]:
        """Generation model-version ids for a bounded set of archived images."""
        wanted = sorted({int(value) for value in image_ids if value})
        result: dict[int, set[int]] = {image_id: set() for image_id in wanted}
        if not wanted:
            return result
        with self.connect() as db:
            for start in range(0, len(wanted), 800):
                chunk = wanted[start:start + 800]
                holes = ",".join("?" for _ in chunk)
                for row in db.execute(
                        f"SELECT id,model_version_ids FROM images WHERE id IN ({holes})", chunk):
                    try:
                        result[row["id"]] = {int(value) for value in
                                             json.loads(row["model_version_ids"] or "[]")}
                    except (TypeError, ValueError, json.JSONDecodeError):
                        result[row["id"]] = set()
        return result

    def artists_page(self, value: str, offset: int, limit: int, pinned_username: str | None = None,
                     order: list[str] | None = None, representatives: dict[str, int] | None = None,
                     excluded_images=None) -> list[dict]:
        self._ensure_artist_index(value)
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
                    rating_clause, rating_params = _rating_clause(self.visible_levels)
                    image = next((row for row in db.execute(
                        "SELECT i.* FROM block_images b JOIN images i ON i.id=b.image_id "
                        f"WHERE b.block_key=? AND i.username_key=?{rating_clause} "
                        "ORDER BY CAST(COALESCE(json_extract(i.stats,'$.reactionCount'),0) "
                        "AS INTEGER) DESC,i.created_at DESC,i.id DESC",
                        (value, summary["username_key"], *rating_params)) if row["id"] not in hidden), None)
                    # Every image this creator posted is hidden, so there is no card to show.
                    if image is None:
                        continue
                rating_clause, rating_params = _rating_clause(self.visible_levels)
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
        rating_clause, rating_params = _rating_clause(self.visible_levels)
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
            rating_clause, rating_params = _rating_clause(self.visible_levels)
            return [{"model": row["base_model"] or "Unknown", "images": row["n"]}
                    for row in db.execute(
                        "SELECT COALESCE(NULLIF(i.base_model,''),'Unknown') AS base_model, "
                        "COUNT(*) AS n FROM block_images b JOIN images i ON i.id=b.image_id "
                        f"WHERE b.block_key=?{rating_clause} GROUP BY base_model ORDER BY n DESC", (value, *rating_params))]

    def duplicate_report(self, value: str | None = None) -> dict:
        """Summarize saved visual hashes without fetching or decoding artwork.

        Civitai image ids and CDN URLs change when an unchanged file is reposted.  Its
        listing hash remains stable, so grouping that hash with the source dimensions
        gives us a conservative, inexpensive duplicate signal.  Older archive rows do
        not have a hash and remain explicitly visible in the coverage count.
        """
        if value is not None:
            # Besides validating the segment suffix, archive_key rejects malformed dates.
            day, separator, segment = value.partition("#")
            value = self.archive_key(day, segment if separator else "all")
            source = ("SELECT i.visual_hash,i.width,i.height,i.username_key,i.local_date "
                      "FROM block_images b JOIN images i ON i.id=b.image_id "
                      "WHERE b.block_key=?")
            params = (value,)
            total_source = ("SELECT COUNT(*) FROM block_images WHERE block_key=?", params)
        else:
            source = ("SELECT visual_hash,width,height,username_key,local_date FROM images")
            params = ()
            total_source = ("SELECT COUNT(*) FROM images", params)
        with self.connect() as db:
            total = int(db.execute(total_source[0], total_source[1]).fetchone()[0])
            hashed = int(db.execute(
                f"SELECT COUNT(*) FROM ({source}) WHERE visual_hash IS NOT NULL AND visual_hash!=''",
                params).fetchone()[0])
            row = db.execute(f"""
                WITH hashed AS ({source}), duplicate_groups AS (
                    SELECT visual_hash,width,height,COUNT(*) AS copies,
                           COUNT(DISTINCT username_key) AS creators,
                           COUNT(DISTINCT local_date) AS dates
                    FROM hashed WHERE visual_hash IS NOT NULL AND visual_hash!=''
                    GROUP BY visual_hash,width,height HAVING COUNT(*) > 1
                )
                SELECT COUNT(*) AS groups,
                       COALESCE(SUM(copies-1),0) AS uploads,
                       COALESCE(SUM(CASE WHEN creators>1 THEN 1 ELSE 0 END),0) AS cross_creators,
                       COALESCE(SUM(CASE WHEN dates>1 THEN 1 ELSE 0 END),0) AS cross_days
                FROM duplicate_groups""", params).fetchone()
        return {"scope": value or "all", "imageCount": total, "hashedImages": hashed,
                "hashCoveragePercent": round((hashed * 100 / total) if total else 0, 2),
                "duplicateGroups": int(row["groups"] or 0),
                "duplicateUploads": int(row["uploads"] or 0),
                "crossCreatorGroups": int(row["cross_creators"] or 0),
                "crossDayGroups": int(row["cross_days"] or 0)}

    def creators_using_models(self, value: str, models: list[str]) -> dict[str, int]:
        """Creator keys with at least one image from the chosen models, and a matching image."""
        clause, params = _model_clause(models)
        rating_clause, rating_params = _rating_clause(self.visible_levels)
        if not clause:
            return {}
        with self.connect() as db:
            picks: dict[str, int] = {}
            for row in db.execute("SELECT i.username_key AS k, i.id AS id FROM block_images b "
                                  f"JOIN images i ON i.id=b.image_id WHERE b.block_key=?{clause}{rating_clause} "
                                  "ORDER BY CAST(COALESCE(json_extract(i.stats,'$.reactionCount'),0) "
                                  "AS INTEGER) DESC,i.created_at DESC,i.id DESC",
                                  (value, *params, *rating_params)):
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

    def stale_reaction_ids(self, block_key: str, max_age_hours: float = 12.0) -> list[int]:
        """Archived images in a block whose reaction counts have never been refreshed,
        or were refreshed longer ago than ``max_age_hours``."""
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(hours=max_age_hours)).isoformat().replace("+00:00", "Z")
        with self.connect() as db:
            return [row[0] for row in db.execute(
                """SELECT i.id FROM block_images b JOIN images i ON i.id=b.image_id
                   WHERE b.block_key=? AND (i.stats_fetched_at IS NULL OR i.stats_fetched_at<?)
                   ORDER BY i.id""", (block_key, cutoff)).fetchall()]

    def sweep_image_reactions(self, client, image_ids, cancel=None, progress=None) -> int:
        """Refresh reaction counts for archived images, 100 per request.

        The daily listing captures reactions at collection time, but they keep
        accumulating for weeks afterwards, so the archived snapshot is the least settled
        figure an image will ever have. ``image.get`` batches like the tag sweep, which
        makes a whole day affordable: ~820 requests for a full day rather than one each.
        """
        wanted = [int(value) for value in dict.fromkeys(image_ids) if value]
        done = 0
        for start in range(0, len(wanted), REACTION_SWEEP_BATCH):
            if cancel is not None and cancel.is_set():
                break
            chunk = wanted[start:start + REACTION_SWEEP_BATCH]
            results = client.batch_query_optional("image.get", [{"id": value} for value in chunk])
            stamp = utcnow()
            updates = []
            for image_id, result in zip(chunk, results):
                if not isinstance(result, dict):
                    continue
                counts = result.get("stats") if isinstance(result.get("stats"), dict) else {}
                mapped = {name: int(counts.get(f"{key}CountAllTime") or 0) for name, key in (
                    ("likeCount", "like"), ("heartCount", "heart"), ("laughCount", "laugh"),
                    ("cryCount", "cry"), ("dislikeCount", "dislike"),
                    ("commentCount", "comment"), ("collectedCount", "collected"))}
                mapped["reactionCount"] = sum(mapped[name] for name in
                    ("likeCount", "heartCount", "laughCount", "cryCount"))
                updates.append((json.dumps(mapped), stamp, image_id))
            with self.connect() as db:
                # Images that answered are stamped; ones that did not keep their old
                # timestamp so the next sweep retries them rather than skipping them.
                db.executemany(
                    "UPDATE images SET stats=?,stats_fetched_at=? WHERE id=?", updates)
            done += len(updates)
            if progress:
                progress(min(start + len(chunk), len(wanted)), len(wanted))
        return done

    def stats(self, image_id: int) -> dict:
        with self.connect() as db: row = db.execute("SELECT stats FROM images WHERE id=?", (image_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def detail(self, image_id: int) -> dict:
        with self.connect() as db: row = db.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        if row is None: raise ValueError("Image is not in the history archive")
        if not row["details_loaded"]:
            # Without an explicit browsing level Civitai answers an anonymous caller at the
            # public level only, so every Mature/X/XXX image came back empty -- no prompt,
            # no resources -- and details_loaded was never set, which re-sent the same
            # request on every reopen of the dialog. Ask for the level this image is on.
            level = int(row["browsing_level"] or 0) or ALL_BROWSING_LEVELS
            payload, _ = self._request(
                {"imageId": image_id, "withMeta": "true", "browsingLevel": level},
                minimum_interval=1.5)
            raw = next((item for item in payload.get("items", []) if int(item.get("id", -1)) == image_id), None)
            if raw is None:
                # The request succeeded and simply did not contain this image, so it is
                # gone rather than delayed. Record the attempt so it is not retried forever.
                with self.connect() as db:
                    db.execute("UPDATE images SET details_loaded=1 WHERE id=?", (image_id,))
                with self.connect() as db:
                    row = db.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
            if raw:
                item = normalize(raw)
                # Detail responses sometimes report an underlying creation
                # timestamp that differs from the daily-feed sort timestamp.
                # Enrichment must never move an image between archived days.
                with self.connect() as db:
                    db.execute("""UPDATE images SET prompt=?,negative_prompt=?,resources=?,
                        base_model=COALESCE(?,base_model),model_version_ids=?,
                        visual_hash=COALESCE(?,visual_hash),details_loaded=1 WHERE id=?""",
                        (item.get("prompt") or "", item.get("negativePrompt") or "",
                         json.dumps(item.get("resources") or []), item.get("baseModel"),
                         json.dumps(item.get("modelVersionIds") or []), item.get("visualHash"), image_id))
                with self.connect() as db: row = db.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        return self._row_item(row, details=True)
