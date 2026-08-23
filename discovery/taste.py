"""Local analysis of the connected account's own Civitai reactions.

Everything here is read-only against Civitai and derived data only. The store lives in
its own SQLite file so it can be reset without touching archived daily galleries, and
its contents are bound to one account id so a second account never reads the first
account's discovery data.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import sqlite3
import threading
import time

from .social import CivitaiHTTPError, SocialClient, auth_status

# Civitai's own reaction set. Dislike is collected so reconciliation stays complete,
# but the dashboard only names it when the user actually has some.
ALL_REACTIONS = ["Like", "Dislike", "Heart", "Laugh", "Cry"]
DISPLAY_REACTIONS = ["Like", "Heart", "Laugh", "Cry"]

PAGE_SIZE = 100
BASELINE_PAGES = 10
# How many taste signals the dashboard names, and what counts as an emerging creator.
# 1,000 followers is the project's stated experiment, not a law.
SIGNAL_TAGS = 8
EMERGING_FOLLOWERS = 1000
# The dashboard recommendation is deliberately stronger than the gallery's lightweight
# familiarity marker. Ten distinct reacted-to images says "consider following"; five is
# enough for a heart on an unfollowed creator card without making that stronger recommendation.
WORTH_FOLLOWING_MIN = 10
GALLERY_HEART_MIN = 5
# Version 1 could mark cards when the DOM hid or replaced them. Keep those rows for
# recoverability but do not trust them; a genuine new pass upgrades that creator to v2.
SEEN_TRACKING_VERSION = 2
# How far a distinctive tag may outweigh a common one of equal frequency.
TAG_BOOST_CAP = 3.0
# Profile analysis crosses several Civitai endpoints in succession. Keep the same
# conservative cadence as the daily-history collector so phase changes cannot create
# a fresh burst of requests.
MIN_PAUSE = 5.0
MAX_PAUSE = 5.8
MAX_ATTEMPTS = 5


def _retry_wait(error: CivitaiHTTPError, attempt: int) -> float:
    """Respect a 429 Retry-After value, with a conservative fallback if absent."""
    if error.status == 429:
        try:
            requested = float(error.retry_after) if error.retry_after else 0.0
        except (TypeError, ValueError):
            requested = 0.0
        return max(MIN_PAUSE, requested or min(300.0, 30.0 * (2 ** (attempt - 1))))
    return min(60.0, 4.0 * (2 ** (attempt - 1)))

# Rows derived from one Civitai account. Public archive-tag caches, mirrored Content
# Controls, and local seen-card state deliberately live outside this list: they are either
# account-independent or replaced as part of sign-in. Keeping this list in one place makes
# reset and account switching obey the same privacy boundary.
ACCOUNT_SCOPED_TABLES = (
    "reacted_images", "reacted_reactions", "reacted_tags", "tag_baseline",
    "baseline_sample", "followed_creators", "creator_followers",
    "recent_posts", "recent_post_tags", "recent_post_models", "recent_model_names",
)


def _image_url(value: str, name: str | None, width: int = 768) -> str:
    """Civitai delivers an image id plus a filename; the CDN wants them in a path."""
    from urllib.parse import quote
    return (f"https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/{value}/width={width}/"
            f"{quote(str(name or 'image.jpeg'))}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SyncCancelled(RuntimeError):
    pass


class TasteStore:
    def __init__(self, root: Path):
        self.root = root
        self.db_path = root / "taste.sqlite3"
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.sync_thread: threading.Thread | None = None
        self.job: dict = {"running": False, "phase": "idle", "images": 0, "pages": 0,
                          "message": "", "error": None, "startedAt": None}
        self._initialize()

    # ---------- storage ----------

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
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
                CREATE TABLE IF NOT EXISTS reacted_images (
                    image_id INTEGER PRIMARY KEY,
                    creator_id INTEGER, creator_username TEXT,
                    nsfw_level INTEGER, post_id INTEGER,
                    created_at TEXT, has_meta INTEGER NOT NULL DEFAULT 0,
                    base_model TEXT, model_version_ids TEXT NOT NULL DEFAULT '[]',
                    stats TEXT NOT NULL DEFAULT '{}',
                    first_observed_at TEXT NOT NULL, last_observed_at TEXT NOT NULL,
                    last_sync INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS reacted_creator ON reacted_images(creator_id);
                CREATE TABLE IF NOT EXISTS reacted_reactions (
                    image_id INTEGER NOT NULL, reaction TEXT NOT NULL,
                    PRIMARY KEY(image_id, reaction)
                );
                CREATE TABLE IF NOT EXISTS reacted_tags (
                    image_id INTEGER NOT NULL, tag_id INTEGER NOT NULL,
                    tag_name TEXT NOT NULL, source TEXT,
                    PRIMARY KEY(image_id, tag_id)
                );
                CREATE INDEX IF NOT EXISTS reacted_tags_name ON reacted_tags(tag_name);
                CREATE TABLE IF NOT EXISTS tag_baseline (
                    tag_id INTEGER PRIMARY KEY, tag_name TEXT NOT NULL,
                    image_count INTEGER NOT NULL
                );
                -- Which images the comparison sample has already counted. Without this the
                -- sample cannot accumulate: consecutive syncs read almost the same newest
                -- images, and re-counting them would inflate the totals without adding any
                -- information.
                CREATE TABLE IF NOT EXISTS baseline_sample (
                    image_id INTEGER PRIMARY KEY, fetched_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS followed_creators (creator_id INTEGER PRIMARY KEY);
                CREATE INDEX IF NOT EXISTS followed_name ON followed_creators(creator_id);
                CREATE TABLE IF NOT EXISTS creator_followers (
                    creator_id INTEGER PRIMARY KEY, username TEXT,
                    follower_count INTEGER, fetched_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS creator_followers_name ON creator_followers(username);
                CREATE TABLE IF NOT EXISTS recent_posts (
                    image_id INTEGER PRIMARY KEY, created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS recent_post_tags (
                    image_id INTEGER NOT NULL, tag_name TEXT NOT NULL,
                    PRIMARY KEY(image_id, tag_name)
                );
                CREATE INDEX IF NOT EXISTS recent_post_tags_name ON recent_post_tags(tag_name);
                CREATE TABLE IF NOT EXISTS recent_post_models (
                    image_id INTEGER NOT NULL, model_version_id INTEGER NOT NULL,
                    PRIMARY KEY(image_id, model_version_id)
                );
                CREATE INDEX IF NOT EXISTS recent_post_models_id ON recent_post_models(model_version_id);
                CREATE TABLE IF NOT EXISTS recent_model_names (
                    model_version_id INTEGER PRIMARY KEY, model_id INTEGER,
                    model_name TEXT, version_name TEXT, fetched_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS archive_image_tags (
                    image_id INTEGER NOT NULL, tag_name TEXT NOT NULL,
                    PRIMARY KEY(image_id, tag_name)
                );
                CREATE TABLE IF NOT EXISTS archive_image_seen (
                    image_id INTEGER PRIMARY KEY, fetched_at TEXT NOT NULL
                );
                -- The account's own Content Controls, mirrored from Civitai. Stored rather
                -- than queried per request so the gallery can filter without a round trip,
                -- and refreshed whenever the app signs in.
                CREATE TABLE IF NOT EXISTS hidden_creators (
                    creator_id INTEGER PRIMARY KEY, username_key TEXT, reason TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS hidden_creators_name ON hidden_creators(username_key);
                CREATE TABLE IF NOT EXISTS hidden_tags (
                    tag_id INTEGER PRIMARY KEY, tag_name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hidden_images (image_id INTEGER PRIMARY KEY);
                -- Creators the account has scrolled past on a given calendar day. Keyed
                -- by day rather than by archive block, so seeing a creator in the
                -- evening half also dims them once the full day is built — the user
                -- thinks in terms of "today", not the app's morning/evening split.
                CREATE TABLE IF NOT EXISTS seen_creators (
                    day TEXT NOT NULL, username_key TEXT NOT NULL, seen_at TEXT NOT NULL,
                    tracking_version INTEGER NOT NULL DEFAULT 2,
                    PRIMARY KEY(day, username_key)
                );
                CREATE INDEX IF NOT EXISTS seen_creators_day ON seen_creators(day);
                CREATE TABLE IF NOT EXISTS sync_state (key TEXT PRIMARY KEY, value TEXT);
            """)

            seen_columns = {row[1] for row in db.execute("PRAGMA table_info(seen_creators)")}
            if "tracking_version" not in seen_columns:
                # Existing rows were written by the observer implementation that could
                # confuse a view change with a scroll. Quarantine them as v1 rather than
                # deleting local history; seeing the creator again upgrades the row.
                db.execute("ALTER TABLE seen_creators ADD COLUMN tracking_version "
                           "INTEGER NOT NULL DEFAULT 1")

            # One-time migration. Baselines written before the sample accumulated hold tag
            # counts whose image ids were never recorded, so those images can be neither
            # deduplicated nor counted in the denominator. Keeping them would make every
            # tag look less distinctive than it is. Clear once; it refills on the next sync.
            legacy = db.execute("SELECT COUNT(*) AS n FROM tag_baseline").fetchone()["n"]
            sampled = db.execute("SELECT COUNT(*) AS n FROM baseline_sample").fetchone()["n"]
            if legacy and not sampled:
                db.execute("DELETE FROM tag_baseline")
                db.execute("DELETE FROM sync_state WHERE key='baseline_images'")

    def _state(self, db, key: str, default=None):
        row = db.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def _set_state(self, db, key: str, value) -> None:
        db.execute("INSERT INTO sync_state(key,value) VALUES(?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def reset(self) -> None:
        """Delete derived discovery data only. Archived galleries live in another file."""
        with self.lock, self.connect() as db:
            for table in (*ACCOUNT_SCOPED_TABLES, "sync_state"):
                db.execute(f"DELETE FROM {table}")

    def has_creator(self, creator_id: int) -> bool:
        """True when the dashboard itself surfaced this creator, because the user reacted
        to their work. Those are valid follow targets even when no day on screen contains
        them; anyone else is not."""
        if not creator_id:
            return False
        with self.connect() as db:
            return bool(db.execute(
                "SELECT 1 FROM reacted_images WHERE creator_id=? LIMIT 1",
                (int(creator_id),)).fetchone())

    def set_following(self, creator_id: int, following: bool) -> None:
        """Record a follow made from the dashboard so the panels update without a resync."""
        if not creator_id:
            return
        with self.lock, self.connect() as db:
            if following:
                db.execute("INSERT OR IGNORE INTO followed_creators(creator_id) VALUES(?)",
                           (int(creator_id),))
            else:
                db.execute("DELETE FROM followed_creators WHERE creator_id=?", (int(creator_id),))

    def _require_account(self, db) -> int:
        """Drop stored rows if a different account connected since the last sync."""
        account = int(auth_status().get("id") or 0)
        stored = self._state(db, "account_id")
        if stored is not None and str(stored) != str(account):
            for table in ACCOUNT_SCOPED_TABLES:
                db.execute(f"DELETE FROM {table}")
            # Timestamps, sample totals, and sync cursors describe the old account too.
            # Leaving them behind makes an empty new account look partly initialized.
            db.execute("DELETE FROM sync_state")
        self._set_state(db, "account_id", account)
        return account

    def ensure_current_account(self) -> bool:
        """Bind reads to the connected account before returning account-derived data.

        Sign-out intentionally keeps the analysis so the same account can reconnect
        incrementally. Once a *different* account is connected, however, waiting until a
        background sync begins is too late: the old summary and gallery signals could be
        read in that window. This lightweight guard performs the same isolation on reads.
        """
        try:
            with self.lock, self.connect() as db:
                self._require_account(db)
            return True
        except Exception:  # No stored OAuth session: preserve data for a later reconnect.
            return False

    # ---------- synchronisation ----------

    def status(self) -> dict:
        worker = self.sync_thread
        if worker is not None and worker.is_alive():
            with self.lock:
                reported_running = bool(self.job.get("running"))
            if not reported_running and worker is not threading.current_thread():
                # Do not let a caller treat the final status write as proof that the
                # worker has fully released its stack and shared cancellation event.
                worker.join(timeout=5)
        self.ensure_current_account()
        with self.lock:
            job = dict(self.job)
        with self.connect() as db:
            job["lastSyncAt"] = self._state(db, "last_sync_at")
            job["hasData"] = bool(db.execute("SELECT 1 FROM reacted_images LIMIT 1").fetchone())
        return job

    def start_sync(self) -> dict:
        # `running` becomes false at the end of the worker's final status update, a few
        # bytecodes before the thread itself exits. A fast second refresh (or account
        # switch) must not clear shared cancellation state during that tiny window.
        previous = self.sync_thread
        if previous is not None and previous.is_alive():
            with self.lock:
                still_running = bool(self.job.get("running"))
            if still_running:
                with self.lock:
                    return dict(self.job)
            previous.join(timeout=5)
        with self.lock:
            if self.job.get("running"):
                return dict(self.job)
            self.cancel.clear()
            self.job = {"running": True, "phase": "reactions", "images": 0, "pages": 0,
                        "message": "Reading your reactions from Civitai…", "error": None,
                        "startedAt": _now()}
            job = dict(self.job)
        self.sync_thread = threading.Thread(target=self._run_sync, daemon=True)
        self.sync_thread.start()
        return job

    def stop_sync(self) -> None:
        self.cancel.set()

    def _progress(self, **fields) -> None:
        with self.lock:
            self.job.update(fields)

    def _pause(self) -> None:
        if self.cancel.wait(random.uniform(MIN_PAUSE, MAX_PAUSE)):
            raise SyncCancelled()

    def _page(self, client: SocialClient, **kwargs) -> dict:
        """One page with 429-aware backoff. Never mutates anything on Civitai."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.cancel.is_set():
                raise SyncCancelled()
            try:
                return client.images_page(**kwargs)
            except CivitaiHTTPError as error:
                retryable = error.status == 429 or error.status >= 500
                if not retryable or attempt == MAX_ATTEMPTS:
                    raise
                self._progress(message="Civitai is rate limiting; waiting before retrying…")
                delay = _retry_wait(error, attempt)
                if self.cancel.wait(delay):
                    raise SyncCancelled() from error
        raise RuntimeError("Civitai did not return a page")

    def _run_sync(self) -> None:
        client = SocialClient()
        started = time.monotonic()
        try:
            # status() also binds reads to the active account. Serialize the worker's
            # account switch with that read-side guard so neither can clear the other's
            # first page while both observe the old account id.
            with self.lock, self.connect() as db:
                account = self._require_account(db)
                sync_id = int(self._state(db, "sync_id", 0) or 0) + 1
                self._set_state(db, "sync_id", sync_id)

            # Tags are by far the heaviest part of a page and never change for an image
            # already stored, so a refresh asks for the listing only and fetches tags for
            # genuinely new images afterwards. The first sync has nothing to reuse.
            with self.connect() as db:
                known = {row["image_id"] for row in db.execute("SELECT image_id FROM reacted_tags")}
            incremental = bool(known)

            cursor, pages, total, fresh, observed = None, 0, 0, [], set()
            while True:
                page = self._page(client, cursor=cursor, limit=PAGE_SIZE,
                                  reactions=ALL_REACTIONS, with_tags=not incremental)
                items = page["items"]
                self._store_page(items, sync_id, account)
                observed.update(item["id"] for item in items
                                if isinstance(item, dict) and isinstance(item.get("id"), int))
                if incremental:
                    fresh.extend(item["id"] for item in items
                                 if isinstance(item.get("id"), int) and item["id"] not in known)
                pages += 1
                total += len(items)
                self._progress(images=total, pages=pages,
                               message=f"Read {total:,} reacted images from Civitai…")
                cursor = page["nextCursor"]
                if cursor is None:
                    break
                self._pause()

            # Only a run that reached the final page may retire rows, otherwise a
            # cancelled sync would delete reactions that simply were not reached yet.
            with self.connect() as db:
                # The observed ids are the authoritative completed snapshot. Refreshing
                # their marker in one transaction also protects reconciliation from a
                # status/read transaction landing precisely between two fast mock pages.
                observed_ids = list(observed)
                for start in range(0, len(observed_ids), 800):
                    chunk = observed_ids[start:start + 800]
                    holes = ",".join("?" for _ in chunk)
                    db.execute(f"UPDATE reacted_images SET last_sync=? WHERE image_id IN ({holes})",
                               (sync_id, *chunk))
                db.execute("DELETE FROM reacted_reactions WHERE image_id IN "
                           "(SELECT image_id FROM reacted_images WHERE last_sync<>?)", (sync_id,))
                db.execute("DELETE FROM reacted_tags WHERE image_id IN "
                           "(SELECT image_id FROM reacted_images WHERE last_sync<>?)", (sync_id,))
                db.execute("DELETE FROM reacted_images WHERE last_sync<>?", (sync_id,))

            if incremental and fresh:
                self._progress(message=f"Reading tags for {len(fresh):,} new images…")
                self._fetch_reacted_tags(client, fresh)

            self._progress(phase="following", message="Checking which creators you follow…")
            self._pause()
            self._sync_following(client)

            self._progress(phase="fingerprint", message="Reading your recent creative fingerprint...")
            try:
                username = str(auth_status().get("username") or "").strip()
                if username:
                    self.refresh_recent_work(client, username)
            except Exception:
                # This is enrichment. A temporary failure must not discard a complete
                # reaction sync or prevent the rest of the profile from loading.
                pass

            self._progress(phase="baseline", message="Sampling Civitai to find your distinctive tags…")
            self._sync_baseline(client)

            self._progress(phase="followers", message="Reading creator follower counts…")
            self._sync_followers(client)

            with self.connect() as db:
                self._set_state(db, "last_sync_at", _now())
            self._progress(running=False, phase="complete", error=None,
                           message=f"Updated {total:,} reacted images in "
                                   f"{int(time.monotonic() - started)} seconds.")
        except SyncCancelled:
            self._progress(running=False, phase="stopped",
                           message="Stopped. Everything already read was kept.")
        except Exception as error:  # noqa: BLE001
            self._progress(running=False, phase="error", error=str(error)[:300],
                           message="Could not finish reading your reactions from Civitai.")

    def _store_page(self, items: list, sync_id: int, account: int) -> None:
        stamp = _now()
        with self.connect() as db:
            for item in items:
                image_id = item.get("id")
                if not isinstance(image_id, int):
                    continue
                user = item.get("user") or {}
                db.execute("""
                    INSERT INTO reacted_images(image_id, creator_id, creator_username, nsfw_level,
                        post_id, created_at, has_meta, base_model, model_version_ids, stats,
                        first_observed_at, last_observed_at, last_sync)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(image_id) DO UPDATE SET
                        creator_id=excluded.creator_id, creator_username=excluded.creator_username,
                        nsfw_level=excluded.nsfw_level, post_id=excluded.post_id,
                        created_at=excluded.created_at, has_meta=excluded.has_meta,
                        base_model=excluded.base_model,
                        model_version_ids=excluded.model_version_ids,
                        stats=excluded.stats, last_observed_at=excluded.last_observed_at,
                        last_sync=excluded.last_sync
                """, (image_id, user.get("id"), user.get("username"), item.get("nsfwLevel"),
                      item.get("postId"), item.get("createdAt"), 1 if item.get("hasMeta") else 0,
                      item.get("baseModel"), json.dumps(item.get("modelVersionIds") or []),
                      json.dumps(item.get("stats") or {}), stamp, stamp, sync_id))

                current = {entry.get("reaction") for entry in item.get("reactions") or []
                           if isinstance(entry, dict)
                           and (entry.get("userId") in (None, account))
                           and entry.get("reaction") in ALL_REACTIONS}
                db.execute("DELETE FROM reacted_reactions WHERE image_id=?", (image_id,))
                db.executemany("INSERT OR IGNORE INTO reacted_reactions(image_id,reaction) VALUES(?,?)",
                               [(image_id, name) for name in sorted(current)])

                tags = [(image_id, tag.get("id"), str(tag.get("name") or "").strip().casefold(),
                         tag.get("source"))
                        for tag in item.get("tags") or []
                        if isinstance(tag, dict) and isinstance(tag.get("id"), int)
                        and str(tag.get("name") or "").strip()]
                if tags:
                    db.execute("DELETE FROM reacted_tags WHERE image_id=?", (image_id,))
                    db.executemany("INSERT OR IGNORE INTO reacted_tags(image_id,tag_id,tag_name,source) "
                                   "VALUES(?,?,?,?)", tags)

    def _fetch_reacted_tags(self, client: SocialClient, image_ids: list[int]) -> None:
        """Tags for newly reacted images, batched, so a refresh re-reads nothing it has."""
        wanted = [value for value in dict.fromkeys(image_ids) if value]
        for start in range(0, len(wanted), 100):
            chunk = wanted[start:start + 100]
            self._pause()
            try:
                results = client.batch_query_optional(
                    "tag.getVotableTags", [{"id": value, "type": "image"} for value in chunk])
            except Exception:  # noqa: BLE001
                return  # Tags are enrichment; a failure must not fail the sync.
            rows = []
            for image_id, tags in zip(chunk, results):
                for tag in tags if isinstance(tags, list) else []:
                    name = str((tag or {}).get("name") or "").strip().casefold()
                    tag_id = (tag or {}).get("id")
                    if name and isinstance(tag_id, int):
                        rows.append((image_id, tag_id, name, (tag or {}).get("type")))
            if rows:
                with self.connect() as db:
                    db.executemany("INSERT OR IGNORE INTO reacted_tags(image_id,tag_id,tag_name,source) "
                                   "VALUES(?,?,?,?)", rows)

    def _sync_following(self, client: SocialClient) -> None:
        from .social import following_ids
        ids = following_ids(client.query("user.getFollowingUsers", {}))
        with self.connect() as db:
            db.execute("DELETE FROM followed_creators")
            db.executemany("INSERT OR IGNORE INTO followed_creators(creator_id) VALUES(?)",
                           [(value,) for value in sorted(ids)])
            self._set_state(db, "followed_count", len(ids))
            known = {row["creator_id"] for row in db.execute(
                "SELECT creator_id FROM creator_followers WHERE username IS NOT NULL")}
        # The follow list is ids only, but the daily archive stores usernames. Resolving
        # the difference is what lets the gallery sort a whole day by followed creators.
        missing = sorted(ids - known)
        stamp = _now()
        for start in range(0, len(missing), 100):
            chunk = missing[start:start + 100]
            self._pause()
            self._progress(message=f"Matching your followed creators ({start + len(chunk)}"
                                   f" of {len(missing)})…")
            try:
                profiles = client.batch_query_optional(
                    "user.getCreator", [{"id": value} for value in chunk])
            except Exception:  # noqa: BLE001
                return
            rows = [(creator_id, (profile or {}).get("username"), None, stamp)
                    for creator_id, profile in zip(chunk, profiles)
                    if isinstance(profile, dict) and profile.get("username")]
            if rows:
                with self.connect() as db:
                    db.executemany("""INSERT INTO creator_followers(creator_id, username,
                                      follower_count, fetched_at) VALUES(?,?,?,?)
                                      ON CONFLICT(creator_id) DO UPDATE SET
                                      username=excluded.username,
                                      fetched_at=excluded.fetched_at""", rows)

    def follower_coverage(self, usernames: list[str]) -> tuple[int, int]:
        """How many of these creators already have a cached follower count."""
        keys = {name.casefold() for name in usernames}
        if not keys:
            return 0, 0
        with self.connect() as db:
            known = {row["username"].casefold() for row in db.execute(
                "SELECT username FROM creator_followers "
                "WHERE username IS NOT NULL AND follower_count IS NOT NULL")}
        return len(keys & known), len(keys)

    def sweep_followers(self, client: SocialClient, usernames: list[str],
                        cancel: threading.Event, progress=None) -> int:
        """Cache follower counts for creators the gallery has never had to load.

        The image endpoints do not carry follower counts at any depth, so this is the only
        way to know them for a creator the user has not scrolled past.
        """
        keys = list(dict.fromkeys(name for name in usernames if name))
        with self.connect() as db:
            known = {row["username"].casefold() for row in db.execute(
                "SELECT username FROM creator_followers "
                "WHERE username IS NOT NULL AND follower_count IS NOT NULL")}
        missing = [name for name in keys if name.casefold() not in known]
        done = 0
        for start in range(0, len(missing), 100):
            if cancel.is_set():
                break
            chunk = missing[start:start + 100]
            if cancel.wait(random.uniform(MIN_PAUSE, MAX_PAUSE)):
                break
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    profiles = client.batch_query_optional(
                        "user.getCreator", [{"username": name} for name in chunk])
                    break
                except CivitaiHTTPError as error:
                    if (error.status != 429 and error.status < 500) or attempt == MAX_ATTEMPTS:
                        raise
                    delay = _retry_wait(error, attempt)
                    if cancel.wait(delay):
                        return done
                except Exception:  # noqa: BLE001
                    raise
            else:
                raise RuntimeError("Civitai follower lookup exhausted its retries")
            stamp = _now()
            rows = []
            for name, profile in zip(chunk, profiles):
                value = ((profile or {}).get("stats") or {}).get("followerCountAllTime")
                identifier = (profile or {}).get("id")
                if isinstance(identifier, int):
                    rows.append((identifier, (profile or {}).get("username") or name,
                                 int(value) if isinstance(value, (int, float)) else None, stamp))
            if rows:
                with self.connect() as db:
                    db.executemany("""INSERT INTO creator_followers(creator_id, username,
                                      follower_count, fetched_at) VALUES(?,?,?,?)
                                      ON CONFLICT(creator_id) DO UPDATE SET
                                      username=excluded.username,
                                      follower_count=excluded.follower_count,
                                      fetched_at=excluded.fetched_at""", rows)
            done += len(chunk)
            if progress:
                progress(done, len(missing))
        return done

    # ---------- the account's own Content Controls ----------

    def import_hidden_preferences(self, client: SocialClient | None = None) -> dict:
        """Mirror the account's Civitai Content Controls into the local store.

        Civitai resolves the category switches ("Hide furry", "Hide gore", …) into the
        concrete tags they cover before returning them, so this one read is the whole
        picture and the app never has to model those categories itself.

        Read-only, and it replaces rather than merges: unhiding something on Civitai has
        to take effect here too, which a merge would quietly prevent.
        """
        payload = (client or SocialClient()).query("hiddenPreferences.getHidden", {})
        if not isinstance(payload, dict):
            raise RuntimeError("Civitai returned unexpected content controls")

        def named(rows):
            for row in rows or []:
                if isinstance(row, dict) and row.get("hidden") is not False:
                    yield row

        creators = [(int(row["id"]), str(row.get("username") or "").casefold() or None, reason)
                    for reason, rows in (("hidden", payload.get("hiddenUsers")),
                                         # Someone who blocked this account should not be
                                         # surfaced to them either.
                                         ("blocked", payload.get("blockedByUsers")))
                    for row in named(rows) if row.get("id")]
        tags = [(int(row["id"]), str(row.get("name") or "").casefold())
                for row in named(payload.get("hiddenTags")) if row.get("id") and row.get("name")]
        images = []
        for row in payload.get("hiddenImages") or []:
            value = row.get("id") if isinstance(row, dict) else row
            try:
                images.append((int(value),))
            except (TypeError, ValueError):
                continue

        with self.lock, self.connect() as db:
            db.execute("DELETE FROM hidden_creators")
            db.execute("DELETE FROM hidden_tags")
            db.execute("DELETE FROM hidden_images")
            db.executemany("INSERT OR REPLACE INTO hidden_creators(creator_id, username_key, reason) "
                           "VALUES(?,?,?)", creators)
            db.executemany("INSERT OR REPLACE INTO hidden_tags(tag_id, tag_name) VALUES(?,?)", tags)
            db.executemany("INSERT OR REPLACE INTO hidden_images(image_id) VALUES(?)", images)
            self._set_state(db, "hidden_imported_at", _now())
        return {"creators": len(creators), "tags": len(tags), "images": len(images)}

    def hidden_creator_keys(self) -> set[str]:
        with self.connect() as db:
            return {row["username_key"] for row in
                    db.execute("SELECT username_key FROM hidden_creators WHERE username_key IS NOT NULL")}

    def hidden_tag_names(self) -> set[str]:
        with self.connect() as db:
            return {row["tag_name"] for row in db.execute("SELECT tag_name FROM hidden_tags")}

    def hidden_image_ids(self) -> set[int]:
        """Images hidden outright, plus any whose known tags the account hides.

        Tags are only known for images the app has already read them for, so this is a
        floor rather than a guarantee — which is why creators, where the match is exact,
        do the heavy lifting.
        """
        with self.connect() as db:
            direct = {row["image_id"] for row in db.execute("SELECT image_id FROM hidden_images")}
            tagged = {row["image_id"] for row in db.execute(
                "SELECT DISTINCT t.image_id FROM archive_image_tags t "
                "JOIN hidden_tags h ON h.tag_name = t.tag_name")}
        return direct | tagged

    def hidden_summary(self) -> dict:
        with self.connect() as db:
            counts = {name: db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                      for name, table in (("creators", "hidden_creators"),
                                          ("tags", "hidden_tags"),
                                          ("images", "hidden_images"))}
            counts["importedAt"] = self._state(db, "hidden_imported_at")
        return counts

    # ---------- seen tracking ----------

    def mark_seen(self, day: str, username_keys: list[str]) -> int:
        """Record creators the account has scrolled past on this calendar day.

        Read-only towards Civitai — this never leaves the computer. Insert-or-ignore,
        so re-marking something already seen (the client batches and could resend a
        key across two flushes) is a no-op rather than bumping its timestamp.

        Casefolds internally rather than trusting every caller to have done it already:
        the lookup this feeds (decorate_history_artist) always casefolds, so a stray
        mixed-case key stored here would silently never match and just never dim.
        """
        keys = sorted({key.casefold() for key in username_keys if key})
        if not keys:
            return 0
        stamp = _now()
        with self.lock, self.connect() as db:
            db.executemany("INSERT INTO seen_creators(day, username_key, seen_at, tracking_version) "
                           "VALUES(?,?,?,?) ON CONFLICT(day,username_key) DO UPDATE SET "
                           "seen_at=excluded.seen_at, tracking_version=excluded.tracking_version",
                           [(day, key, stamp, SEEN_TRACKING_VERSION) for key in keys])
        return len(keys)

    def seen_creator_keys(self, day: str) -> set[str]:
        with self.connect() as db:
            return {row["username_key"] for row in
                    db.execute("SELECT username_key FROM seen_creators "
                               "WHERE day=? AND tracking_version=?",
                               (day, SEEN_TRACKING_VERSION))}

    def follower_counts(self, usernames: list[str]) -> dict[str, int]:
        keys = {name.casefold() for name in usernames}
        if not keys:
            return {}
        with self.connect() as db:
            return {row["username"].casefold(): row["follower_count"] for row in db.execute(
                "SELECT username, follower_count FROM creator_followers "
                "WHERE username IS NOT NULL AND follower_count IS NOT NULL")
                if row["username"].casefold() in keys}

    def refresh_recent_work(self, client: SocialClient, username: str) -> int:
        """Archive public uploads once, then stop each refresh at the first known page.

        Every listing page already includes its tags and model ids, so this is one
        paginated historical pass rather than a listing pass followed by per-image tag
        requests. An interrupted first pass resumes safely; a completed profile normally
        costs one page and stores only uploads that appeared since the previous refresh.
        """
        with self.connect() as db:
            known = {row["image_id"] for row in db.execute("SELECT image_id FROM recent_posts")}
            incremental = self._state(db, "recent_work_complete", "0") == "1"
        cursor, added, reached_end, reached_known = None, 0, False, False
        while True:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    page = client.creator_images_page(username, cursor=cursor, limit=200)
                    break
                except CivitaiHTTPError as error:
                    if (error.status != 429 and error.status < 500) or attempt == MAX_ATTEMPTS:
                        raise
                    delay = _retry_wait(error, attempt)
                    if self.cancel.wait(delay):
                        raise SyncCancelled()
            items = page["items"]
            page_ids = {item.get("id") for item in items if isinstance(item, dict)
                        and isinstance(item.get("id"), int)}
            reached_known = bool(known & page_ids)
            posts, tags, models = [], [], []
            for item in items:
                image_id = item.get("id") if isinstance(item, dict) else None
                if not isinstance(image_id, int) or image_id in known:
                    continue
                posts.append((image_id, item.get("createdAt") or item.get("publishedAt")))
                for tag in item.get("tags") or []:
                    name = str((tag or {}).get("name") or "").strip().casefold()
                    if name:
                        tags.append((image_id, name))
                for value in item.get("modelVersionIds") or []:
                    try:
                        models.append((image_id, int(value)))
                    except (TypeError, ValueError):
                        continue
            with self.lock, self.connect() as db:
                db.executemany("INSERT OR IGNORE INTO recent_posts(image_id,created_at) VALUES(?,?)", posts)
                db.executemany("INSERT OR IGNORE INTO recent_post_tags(image_id,tag_name) VALUES(?,?)", tags)
                db.executemany("INSERT OR IGNORE INTO recent_post_models(image_id,model_version_id) VALUES(?,?)", models)
            added += len(posts)
            known.update(page_ids)
            cursor = page["nextCursor"]
            if incremental and reached_known:
                break
            if cursor is None:
                reached_end = True
                break
            self._pause()
        with self.lock, self.connect() as db:
            self._set_state(db, "recent_work_username", username)
            self._set_state(db, "recent_work_at", _now())
            if reached_end or (incremental and reached_known):
                self._set_state(db, "recent_work_complete", 1)
        self.refresh_recent_model_names(client)
        return added

    def refresh_recent_model_names(self, client: SocialClient, limit: int = 8) -> int:
        """Cache labels for only the model signals the profile actually displays."""
        with self.connect() as db:
            rows = db.execute("""SELECT p.model_version_id
                FROM (SELECT model_version_id
                      FROM recent_post_models
                      GROUP BY model_version_id
                      ORDER BY COUNT(DISTINCT image_id) DESC,model_version_id
                      LIMIT ?) p
                LEFT JOIN recent_model_names n
                  ON n.model_version_id=p.model_version_id
                WHERE n.model_version_id IS NULL
                ORDER BY p.model_version_id""", (max(0, int(limit)),)).fetchall()
        stored = 0
        for row in rows:
            if self.cancel.is_set():
                raise SyncCancelled()
            version_id = int(row["model_version_id"])
            try:
                self._pause()
                value = client.public_model_version(version_id)
            except Exception:  # Missing/deleted models must not fail the fingerprint sync.
                continue
            parent = value.get("model") if isinstance(value.get("model"), dict) else {}
            model_name = str(parent.get("name") or "").strip() or None
            version_name = str(value.get("name") or "").strip() or None
            try:
                model_id = int(value.get("modelId") or parent.get("id") or 0) or None
            except (TypeError, ValueError):
                model_id = None
            if not model_name and not version_name:
                continue
            with self.lock, self.connect() as db:
                db.execute("""INSERT INTO recent_model_names(
                    model_version_id,model_id,model_name,version_name,fetched_at)
                    VALUES(?,?,?,?,?) ON CONFLICT(model_version_id) DO UPDATE SET
                    model_id=excluded.model_id,model_name=excluded.model_name,
                    version_name=excluded.version_name,fetched_at=excluded.fetched_at""",
                    (version_id, model_id, model_name, version_name, _now()))
            stored += 1
        return stored

    def _recent_tag_stats(self) -> tuple[dict[str, float], dict[str, dict]]:
        """Weights and evidence for recurring, distinctive recent-work tags.

        Recurrence alone is not enough: ``solo`` can occur in nearly every post and
        still say little about a creator match. A strong tag must recur and be more
        prevalent here than in the comparison sample. Unknown-baseline tags need
        substantial support before they are trusted.
        """
        with self.connect() as db:
            total = db.execute("SELECT COUNT(*) AS n FROM recent_posts").fetchone()["n"]
            sampled = int(self._state(db, "baseline_images", 0) or 0)
            rows = db.execute("""SELECT t.tag_name AS name, COUNT(DISTINCT t.image_id) AS support,
                COALESCE((SELECT b.image_count FROM tag_baseline b
                          WHERE b.tag_name=t.tag_name),0) AS seen
                FROM recent_post_tags t GROUP BY t.tag_name""").fetchall()
        if not total:
            return {}, {}
        weights, evidence = {}, {}
        for row in rows:
            share = row["support"] / total
            baseline_share = row["seen"] / sampled if sampled and row["seen"] else None
            lift = share / baseline_share if baseline_share else None
            recurrence_floor = max(3, math.ceil(total * .10))
            strong = row["support"] >= recurrence_floor and (
                (lift is not None and lift >= 1.50) or
                (lift is None and share >= .10))
            if not strong:
                continue
            boost = min(TAG_BOOST_CAP, 1.0 + math.log2(lift)) if lift and lift > 1 else 1.0
            weights[row["name"]] = share * boost
            evidence[row["name"]] = {
                "images": row["support"], "percent": round(share * 100, 1),
                "lift": round(lift, 1) if lift is not None else None,
                "weight": weights[row["name"]],
            }
        return weights, evidence

    def score_image_components(self, image_ids: list[int]) -> dict[int, dict[str, float]]:
        """Separate reaction-taste and recent-work scores for archived card images."""
        reaction = self.tag_weights()
        recent = self._recent_tag_stats()[0]
        wanted = sorted({int(value) for value in image_ids if value})
        if not wanted or (not reaction and not recent):
            return {}
        scores: dict[int, dict[str, float]] = {}
        with self.connect() as db:
            for start in range(0, len(wanted), 800):
                chunk = wanted[start:start + 800]
                holes = ",".join("?" for _ in chunk)
                for row in db.execute(
                        f"SELECT image_id,tag_name FROM archive_image_tags WHERE image_id IN ({holes})",
                        chunk):
                    reaction_weight = reaction.get(row["tag_name"])
                    recent_weight = recent.get(row["tag_name"])
                    if reaction_weight or recent_weight:
                        value = scores.setdefault(row["image_id"], {"reaction": 0.0, "recent": 0.0})
                        value["reaction"] += reaction_weight or 0.0
                        value["recent"] += recent_weight or 0.0
        return scores

    def recent_model_weights(self) -> dict[int, float]:
        with self.connect() as db:
            total = db.execute("SELECT COUNT(*) AS n FROM recent_posts").fetchone()["n"]
            if not total:
                return {}
            return {row["model_version_id"]: row["n"] / total for row in db.execute(
                "SELECT model_version_id,COUNT(DISTINCT image_id) AS n FROM recent_post_models "
                "GROUP BY model_version_id")}

    def explain_recent_scores(self, image_ids: list[int], limit: int = 4) -> dict[int, list[str]]:
        wanted = sorted({int(value) for value in image_ids if value})
        weights = self._recent_tag_stats()[0]
        names: dict[int, list[str]] = {image_id: [] for image_id in wanted}
        if not wanted or not weights:
            return names
        with self.connect() as db:
            for start in range(0, len(wanted), 800):
                chunk = wanted[start:start + 800]
                holes = ",".join("?" for _ in chunk)
                for row in db.execute(
                        f"SELECT image_id,tag_name FROM archive_image_tags WHERE image_id IN ({holes})", chunk):
                    if row["tag_name"] in weights:
                        names[row["image_id"]].append(row["tag_name"])
        return {image_id: sorted(image_names, key=lambda name: (-weights[name], name))[:limit]
                for image_id, image_names in names.items()}

    def _tag_stats(self) -> tuple[dict[str, float], dict[str, float]]:
        """How much each tag counts toward a taste score.

        Two things matter and they pull in different directions. How *often* a tag appears
        in the user's reactions decides how much of the feed it should shape: if almost
        everything they react to is tagged `woman`, images tagged `woman` belong at the
        top. How *distinctive* a tag is decides how much a match should be rewarded: `gem`
        appearing thirty times more often than Civitai's average says far more about taste
        than `woman` does.

        So weight = share x boost, where share is the tag's reaction-weighted frequency
        and boost grows with lift but only logarithmically and is capped. Hearts carry
        more evidence than Likes, while Dislike-only images contribute nothing. A
        distinctive tag therefore outweighs a common one of equal frequency, while a tag
        carried by nearly every positively reacted image still dominates by sheer share.
        """
        strengths = """WITH strengths AS (
            SELECT i.image_id, CASE
                WHEN MAX(CASE WHEN r.reaction='Heart' THEN 1 ELSE 0 END)=1 THEN 1.5
                WHEN MAX(CASE WHEN r.reaction='Like' THEN 1 ELSE 0 END)=1 THEN 1.0
                WHEN MAX(CASE WHEN r.reaction IN ('Laugh','Cry') THEN 1 ELSE 0 END)=1 THEN 0.8
                WHEN COUNT(r.reaction)=0 THEN 1.0
                ELSE 0.0 END AS strength
            FROM reacted_images i LEFT JOIN reacted_reactions r ON r.image_id=i.image_id
            GROUP BY i.image_id)"""
        with self.connect() as db:
            total = float(db.execute(
                f"{strengths} SELECT COALESCE(SUM(strength),0) AS n FROM strengths"
            ).fetchone()["n"])
            sampled = int(self._state(db, "baseline_images", 0) or 0)
            if not total:
                return {}, {}
            rows = db.execute(f"""{strengths}
                SELECT t.tag_name AS name, SUM(s.strength) AS mine,
                       COUNT(DISTINCT t.image_id) AS support,
                       COALESCE((SELECT b.image_count FROM tag_baseline b
                                 WHERE b.tag_name = t.tag_name), 0) AS seen
                FROM reacted_tags t JOIN strengths s ON s.image_id=t.image_id
                WHERE s.strength>0 GROUP BY t.tag_name
                HAVING support >= 3
            """).fetchall()
        weights: dict[str, float] = {}
        boosts: dict[str, float] = {}
        for row in rows:
            share = row["mine"] / total
            lift = 1.0
            if sampled and row["seen"]:
                lift = share / (row["seen"] / sampled)
            boost = min(TAG_BOOST_CAP, 1.0 + math.log2(lift)) if lift > 1 else 1.0
            weights[row["name"]] = share * boost
            boosts[row["name"]] = boost
        return weights, boosts

    def tag_weights(self) -> dict[str, float]:
        return self._tag_stats()[0]

    def tag_coverage(self, image_ids: list[int]) -> tuple[int, int]:
        wanted = {int(value) for value in image_ids if value}
        if not wanted:
            return 0, 0
        with self.connect() as db:
            seen = {row["image_id"] for row in db.execute("SELECT image_id FROM archive_image_seen")}
        return len(wanted & seen), len(wanted)

    def sweep_image_tags(self, client: SocialClient, image_ids: list[int],
                         cancel: threading.Event, progress=None) -> int:
        """Read tags for archived images. The daily archive endpoint does not carry them."""
        wanted = [int(value) for value in dict.fromkeys(image_ids) if value]
        with self.connect() as db:
            seen = {row["image_id"] for row in db.execute("SELECT image_id FROM archive_image_seen")}
        missing = [value for value in wanted if value not in seen]
        done = 0
        for start in range(0, len(missing), 100):
            if cancel.is_set():
                break
            chunk = missing[start:start + 100]
            if cancel.wait(random.uniform(MIN_PAUSE, MAX_PAUSE)):
                break
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    results = client.batch_query_optional(
                        "tag.getVotableTags", [{"id": value, "type": "image"} for value in chunk])
                    break
                except CivitaiHTTPError as error:
                    if (error.status != 429 and error.status < 500) or attempt == MAX_ATTEMPTS:
                        raise
                    delay = _retry_wait(error, attempt)
                    if cancel.wait(delay):
                        return done
                except Exception:  # noqa: BLE001
                    raise
            else:
                raise RuntimeError("Civitai tag lookup exhausted its retries")
            stamp = _now()
            tag_rows, seen_rows = [], []
            for image_id, tags in zip(chunk, results):
                seen_rows.append((image_id, stamp))
                for tag in tags if isinstance(tags, list) else []:
                    name = str((tag or {}).get("name") or "").strip().casefold()
                    if name:
                        tag_rows.append((image_id, name))
            with self.connect() as db:
                db.executemany("INSERT OR IGNORE INTO archive_image_tags(image_id,tag_name) VALUES(?,?)",
                               tag_rows)
                db.executemany("INSERT OR REPLACE INTO archive_image_seen(image_id,fetched_at) VALUES(?,?)",
                               seen_rows)
            done += len(chunk)
            if progress:
                progress(done, len(missing))
        return done

    def score_images(self, image_ids: list[int]) -> dict[int, float]:
        """Taste score per archived image, from cached tags and the weights above."""
        return {image_id: values["reaction"] for image_id, values in
                self.score_image_components(image_ids).items() if values["reaction"] > 0}

    def explain_score(self, image_id: int, limit: int = 4) -> list[str]:
        """Why this card placed here, ordered by what is *distinctive* about the match.

        Deliberately not the heaviest tags. Weight decides ranking, but `woman` and `solo`
        sit on almost every image the user reacts to, so explaining by weight labels every
        card identically and tells them nothing. Explaining by lift names the tags that
        actually separate this card from the rest.
        """
        return self.explain_scores([image_id], limit).get(int(image_id), [])

    def explain_scores(self, image_ids: list[int], limit: int = 4) -> dict[int, list[str]]:
        """Explain a page of scores while calculating taste statistics only once."""
        wanted = sorted({int(value) for value in image_ids if value})
        if not wanted:
            return {}
        boosts = self._tag_stats()[1]
        names: dict[int, list[str]] = {image_id: [] for image_id in wanted}
        with self.connect() as db:
            for start in range(0, len(wanted), 800):
                chunk = wanted[start:start + 800]
                holes = ",".join("?" for _ in chunk)
                for row in db.execute(
                        f"SELECT image_id,tag_name FROM archive_image_tags WHERE image_id IN ({holes})",
                        chunk):
                    names[row["image_id"]].append(row["tag_name"])
        return {image_id: [name for _, name in sorted(
                    ((boosts.get(name, 0.0), name) for name in image_names if name in boosts),
                    reverse=True)[:limit]]
                for image_id, image_names in names.items()}

    def image_tags(self, image_id: int) -> dict:
        """The tags Civitai lists for one image, and whether the account hides any.

        Tags are only present for images the app has already read them for, so the
        caller is told whether this image was ever looked at — an image with no tags
        and an image nobody asked about are different states and must not look alike.
        """
        with self.connect() as db:
            names = sorted(row["tag_name"] for row in db.execute(
                "SELECT tag_name FROM archive_image_tags WHERE image_id=?", (int(image_id),)))
            # Holding tags is itself proof the image was read; the bookkeeping table is
            # only needed to tell "read, and genuinely untagged" from "never read".
            seen = names or db.execute("SELECT 1 FROM archive_image_seen WHERE image_id=?",
                                       (int(image_id),)).fetchone() is not None
            hidden = {row["tag_name"] for row in db.execute("SELECT tag_name FROM hidden_tags")}
        return {"known": bool(seen), "tags": [{"name": name, "hidden": name in hidden}
                                              for name in names]}

    def ensure_image_tags(self, client: SocialClient, image_id: int) -> dict:
        """Fetch and cache one image's tags when its details are explicitly opened.

        The full-day tag sweep is deliberately background work and may not have reached
        a card yet. Opening that card is a direct request for its information, so it must
        not depend on the sweep having completed first.
        """
        return self.ensure_image_tags_many(client, [image_id])[int(image_id)]

    def ensure_image_tags_many(self, client: SocialClient, image_ids) -> dict[int, dict]:
        """Fetch and cache tags for the images whose previews are about to be shown.

        Browser cards arrive in groups, so doing one request per card needlessly hammers
        Civitai and makes Content Controls race the image download. This bounded batch is
        the same data as ``ensure_image_tags`` but lets the browser verify a whole group
        before assigning any preview URLs.
        """
        wanted = list(dict.fromkeys(int(value) for value in image_ids if value))
        if not wanted:
            return {}
        with self.connect() as db:
            holes = ",".join("?" for _ in wanted)
            known = {row["image_id"] for row in db.execute(
                f"SELECT image_id FROM archive_image_seen WHERE image_id IN ({holes}) UNION "
                f"SELECT DISTINCT image_id FROM archive_image_tags WHERE image_id IN ({holes})",
                (*wanted, *wanted))}
        missing = [value for value in wanted if value not in known]
        if missing:
            results = client.batch_query_optional(
                "tag.getVotableTags", [{"id": value, "type": "image"} for value in missing])
            stamp = _now()
            tag_rows = []
            for image_id, result in zip(missing, results):
                names = {str((tag or {}).get("name") or "").strip().casefold()
                         for tag in (result if isinstance(result, list) else [])
                         if str((tag or {}).get("name") or "").strip()}
                tag_rows.extend((image_id, name) for name in names)
            with self.lock, self.connect() as db:
                db.executemany(
                    "INSERT OR IGNORE INTO archive_image_tags(image_id,tag_name) VALUES(?,?)",
                    tag_rows)
                db.executemany(
                    "INSERT OR REPLACE INTO archive_image_seen(image_id,fetched_at) VALUES(?,?)",
                    [(image_id, stamp) for image_id in missing])
        return {image_id: self.image_tags(image_id) for image_id in wanted}

    def gallery_signals(self) -> dict:
        """Username sets the daily gallery needs to order a day by personal signal."""
        self.ensure_current_account()
        with self.connect() as db:
            followed = {row["username"].casefold() for row in db.execute(
                "SELECT c.username FROM followed_creators f "
                "JOIN creator_followers c ON c.creator_id = f.creator_id "
                "WHERE c.username IS NOT NULL")}
            reacted = {row["name"].casefold(): row["n"] for row in db.execute(
                "SELECT i.creator_username AS name, COUNT(*) AS n FROM reacted_images i "
                "WHERE i.creator_username IS NOT NULL AND ("
                "NOT EXISTS(SELECT 1 FROM reacted_reactions r WHERE r.image_id=i.image_id) OR "
                "EXISTS(SELECT 1 FROM reacted_reactions r WHERE r.image_id=i.image_id "
                "AND r.reaction IN ('Like','Heart','Laugh','Cry'))) GROUP BY i.creator_username")}
        return {"followed": followed, "reacted": reacted}

    def sweep_progress_token(self) -> tuple[int, int]:
        """How much sweep data exists right now, as a cheap, monotonic cache key.

        A personalised order is only trustworthy if it does not change out from under a
        scroll session that is actively paging through it. Both sweeps grow strictly
        over time, so this pair changing is exactly "new data arrived, the order may
        need to move" — nothing else about the archive changes it.
        """
        with self.connect() as db:
            tags = db.execute("SELECT COUNT(*) AS n FROM archive_image_seen").fetchone()["n"]
            followers = db.execute("SELECT COUNT(*) AS n FROM creator_followers").fetchone()["n"]
        return int(tags), int(followers)

    def _sync_baseline(self, client: SocialClient) -> None:
        """Grow the comparison sample used to discount ubiquitous tags.

        The sample accumulates across syncs instead of being replaced. Replacing it made
        every sync's lift estimate a fresh draw from roughly 900 random images, so the
        distinctive tags — and therefore the personalised ordering — shifted between
        runs even when the user's reactions had not changed. Images already counted are
        skipped, so syncing twice in a minute adds almost nothing rather than counting the
        same images twice.
        """
        with self.connect() as db:
            already = {row["image_id"] for row in db.execute("SELECT image_id FROM baseline_sample")}
        counts: dict[int, tuple[str, int]] = {}
        fresh: list[int] = []
        cursor, scanned = None, 0
        for _ in range(BASELINE_PAGES):
            self._pause()
            page = self._page(client, cursor=cursor, limit=PAGE_SIZE, reactions=None, with_tags=True)
            for item in page["items"]:
                scanned += 1
                image_id = item.get("id")
                if not isinstance(image_id, int) or image_id in already:
                    continue
                already.add(image_id)
                fresh.append(image_id)
                seen: set[int] = set()
                for tag in item.get("tags") or []:
                    tag_id, name = tag.get("id"), str(tag.get("name") or "").strip().casefold()
                    if not isinstance(tag_id, int) or not name or tag_id in seen:
                        continue
                    seen.add(tag_id)
                    stored_name, count = counts.get(tag_id, (name, 0))
                    counts[tag_id] = (stored_name, count + 1)
            cursor = page["nextCursor"]
            self._progress(message=f"Sampled {scanned:,} Civitai images for comparison "
                                   f"({len(fresh):,} new)…")
            if cursor is None:
                break
        stamp = _now()
        with self.connect() as db:
            db.executemany("INSERT OR IGNORE INTO baseline_sample(image_id,fetched_at) VALUES(?,?)",
                           [(image_id, stamp) for image_id in fresh])
            db.executemany("""INSERT INTO tag_baseline(tag_id,tag_name,image_count) VALUES(?,?,?)
                              ON CONFLICT(tag_id) DO UPDATE SET
                              tag_name=excluded.tag_name,
                              image_count=tag_baseline.image_count + excluded.image_count""",
                           [(tag_id, name, count) for tag_id, (name, count) in counts.items()])
            total = db.execute("SELECT COUNT(*) AS n FROM baseline_sample").fetchone()["n"]
            self._set_state(db, "baseline_images", total)
            self._set_state(db, "baseline_at", stamp)

    def signal_tags(self, limit: int = SIGNAL_TAGS) -> list[dict]:
        """The tags that most distinguish this user, with the ids needed to search."""
        with self.connect() as db:
            total = db.execute("SELECT COUNT(*) AS n FROM reacted_images").fetchone()["n"]
            sampled = int(self._state(db, "baseline_images", 0) or 0)
            if not total or not sampled:
                return []
            support = max(5, round(total * 0.005))
            rows = db.execute("""
                SELECT t.tag_id AS id, t.tag_name AS name,
                       COUNT(DISTINCT t.image_id) AS mine,
                       COALESCE((SELECT b.image_count FROM tag_baseline b
                                 WHERE b.tag_id = t.tag_id), 0) AS seen
                FROM reacted_tags t GROUP BY t.tag_id, t.tag_name
                HAVING mine >= ? AND seen > 0
            """, (support,)).fetchall()
        scored = [{"id": row["id"], "name": row["name"], "mine": row["mine"],
                   "lift": (row["mine"] / total) / (row["seen"] / sampled)} for row in rows]
        return sorted([tag for tag in scored if tag["lift"] > 1.2],
                      key=lambda tag: (-tag["lift"], -tag["mine"]))[:limit]

    def _sync_followers(self, client: SocialClient, limit: int = 20) -> None:
        """Fetch follower counts for every creator the dashboard is about to name.

        One batched read covers both creator panels. Counts are decoration, so a
        failure here must never fail the sync.
        """
        with self.connect() as db:
            wanted: dict[int, str] = {}
            for query in (
                """SELECT r.creator_id AS id, r.creator_username AS name, COUNT(*) AS n
                   FROM reacted_images r
                   LEFT JOIN followed_creators f ON f.creator_id = r.creator_id
                   WHERE r.creator_username IS NOT NULL AND f.creator_id IS NULL
                   GROUP BY r.creator_id, r.creator_username ORDER BY n DESC LIMIT ?""",
                """SELECT creator_id AS id, creator_username AS name, COUNT(*) AS n
                   FROM reacted_images WHERE creator_username IS NOT NULL
                   GROUP BY creator_id, creator_username ORDER BY n DESC LIMIT ?""",
            ):
                for row in db.execute(query, (limit,)):
                    if row["id"]:
                        wanted.setdefault(row["id"], row["name"])
        if not wanted:
            return
        pairs = list(wanted.items())
        stamp = _now()
        for start in range(0, len(pairs), 100):
            chunk = pairs[start:start + 100]
            self._pause()
            try:
                profiles = client.batch_query_optional(
                    "user.getCreator", [{"username": name} for _, name in chunk])
            except Exception:  # noqa: BLE001
                return
            rows = []
            for (creator_id, name), profile in zip(chunk, profiles):
                value = ((profile or {}).get("stats") or {}).get("followerCountAllTime")
                rows.append((creator_id, name,
                             int(value) if isinstance(value, (int, float)) else None, stamp))
            with self.connect() as db:
                db.executemany("""INSERT INTO creator_followers(creator_id, username,
                                  follower_count, fetched_at) VALUES(?,?,?,?)
                                  ON CONFLICT(creator_id) DO UPDATE SET
                                  username=excluded.username,
                                  follower_count=excluded.follower_count,
                                  fetched_at=excluded.fetched_at""", rows)

    # ---------- analysis ----------

    def recent_work_summary(self, limit: int = 16) -> dict:
        weights, evidence = self._recent_tag_stats()
        with self.connect() as db:
            posts = db.execute("SELECT COUNT(*) AS n,MIN(created_at) AS oldest,"
                               "MAX(created_at) AS newest FROM recent_posts").fetchone()
            tag_count = db.execute("SELECT COUNT(DISTINCT tag_name) AS n "
                                   "FROM recent_post_tags").fetchone()["n"]
            model_rows = db.execute("""SELECT p.model_version_id,
                    COUNT(DISTINCT p.image_id) AS n, n.model_id,
                    n.model_name,n.version_name
                FROM recent_post_models p
                LEFT JOIN recent_model_names n
                  ON n.model_version_id=p.model_version_id
                GROUP BY p.model_version_id
                ORDER BY COUNT(DISTINCT p.image_id) DESC,p.model_version_id LIMIT ?""",
                (limit,)).fetchall()
            refreshed = self._state(db, "recent_work_at")
            complete = self._state(db, "recent_work_complete", "0") == "1"
        strong = [{"name": name, **{key: value for key, value in item.items() if key != "weight"}}
                  for name, item in sorted(evidence.items(),
                      key=lambda pair: (-pair[1]["weight"], -pair[1]["images"], pair[0]))[:limit]]
        total = int(posts["n"] or 0)
        return {"images": total, "complete": complete,
                "oldestAt": posts["oldest"], "newestAt": posts["newest"],
                "refreshedAt": refreshed, "distinctTags": tag_count,
                "strongTagCount": len(weights), "strongTags": strong,
                "models": [{"id": row["model_version_id"], "modelId": row["model_id"],
                            "modelName": row["model_name"], "versionName": row["version_name"],
                            "images": row["n"],
                            "percent": round(100 * row["n"] / total, 1) if total else 0}
                           for row in model_rows]}

    def summary(self, limit: int = 20) -> dict:
        self.ensure_current_account()
        with self.connect() as db:
            total = db.execute("SELECT COUNT(*) AS n FROM reacted_images").fetchone()["n"]
            # Counted from the table rather than a stored total so a follow made from the
            # dashboard is reflected immediately, without waiting for the next sync.
            followed = db.execute("SELECT COUNT(*) AS n FROM followed_creators").fetchone()["n"]
            baseline_images = int(self._state(db, "baseline_images", 0) or 0)
            last_sync = self._state(db, "last_sync_at")
            if not total:
                return {"hasData": False, "reactedImages": 0, "followedCreators": followed,
                        "lastSyncAt": last_sync}

            rows = db.execute("SELECT reaction, COUNT(*) AS n FROM reacted_reactions "
                              "GROUP BY reaction").fetchall()
            counts = {row["reaction"]: row["n"] for row in rows}
            names = list(DISPLAY_REACTIONS)
            if counts.get("Dislike"):
                names.append("Dislike")
            mix = _percentages([(name, counts.get(name, 0)) for name in names])

            creators = db.execute("""
                SELECT r.creator_id AS id, r.creator_username AS username,
                       COUNT(*) AS images,
                       (f.creator_id IS NOT NULL) AS following,
                       c.follower_count AS followers
                FROM reacted_images r
                LEFT JOIN followed_creators f ON f.creator_id = r.creator_id
                LEFT JOIN creator_followers c ON c.creator_id = r.creator_id
                WHERE r.creator_username IS NOT NULL
                GROUP BY r.creator_id, r.creator_username, following, followers
                ORDER BY images DESC, username COLLATE NOCASE
            """).fetchall()

            tags = db.execute("""
                SELECT tag_name AS name, COUNT(DISTINCT image_id) AS images
                FROM reacted_tags GROUP BY tag_name
                ORDER BY images DESC, name
            """).fetchall()
            baseline = {row["tag_name"]: row["image_count"]
                        for row in db.execute("SELECT tag_name, SUM(image_count) AS image_count "
                                              "FROM tag_baseline GROUP BY tag_name")}

        top_tags = [{"name": row["name"], "images": row["images"],
                     "percent": round(100 * row["images"] / total, 1)} for row in tags[:limit]]

        distinctive = []
        if baseline_images:
            support = max(5, round(total * 0.005))
            for row in tags:
                if row["images"] < support:
                    continue
                seen = baseline.get(row["name"], 0)
                if not seen:
                    continue
                lift = (row["images"] / total) / (seen / baseline_images)
                if lift > 1.2:
                    distinctive.append({"name": row["name"], "images": row["images"],
                                        "lift": round(lift, 1)})
            distinctive.sort(key=lambda item: (-item["lift"], -item["images"], item["name"]))

        # A single reaction is not a following signal. Below this, "worth following"
        # would mostly be one-off reactions, not a real pattern.
        not_followed = [row for row in creators
                        if not row["following"] and row["images"] >= WORTH_FOLLOWING_MIN]
        return {
            "hasData": True,
            "lastSyncAt": last_sync,
            "reactedImages": total,
            "reactionRecords": sum(counts.values()),
            "followedCreators": followed,
            "creatorsReactedTo": len(creators),
            "creatorsNotFollowed": len(not_followed),
            "reactionMix": mix,
            "topTags": top_tags,
            "distinctiveTags": distinctive[:limit],
            "topCreators": [{"id": row["id"], "username": row["username"],
                             "images": row["images"], "following": bool(row["following"]),
                             **_followers(row["followers"])}
                            for row in creators[:limit]],
            "reactedNotFollowed": [{"id": row["id"], "username": row["username"],
                                    "images": row["images"], **_followers(row["followers"])}
                                   for row in not_followed[:limit]],
            "baselineImages": baseline_images,
            "distinctTags": len(tags),
            "signalTags": [tag["name"] for tag in self.signal_tags()],
            "recentWork": self.recent_work_summary(),
            "emergingThreshold": EMERGING_FOLLOWERS,
            "worthFollowingThreshold": WORTH_FOLLOWING_MIN,
            "galleryHeartThreshold": GALLERY_HEART_MIN,
        }


def _followers(value) -> dict:
    """A creator's follower count plus the emerging flag derived from it."""
    count = int(value) if isinstance(value, (int, float)) else None
    return {"followers": count,
            "emerging": count is not None and count < EMERGING_FOLLOWERS}


def _percentages(pairs: list[tuple[str, int]]) -> list[dict]:
    """Round percentages so a non-empty distribution still totals exactly 100."""
    total = sum(count for _, count in pairs)
    if not total:
        return [{"reaction": name, "count": 0, "percent": 0.0} for name, _ in pairs]
    exact = [(name, count, 100 * count / total) for name, count in pairs]
    rounded = [(name, count, round(value, 1)) for name, count, value in exact]
    drift = round(100.0 - sum(value for _, _, value in rounded), 1)
    if drift and rounded:
        order = sorted(range(len(rounded)), key=lambda index: -exact[index][2])
        name, count, value = rounded[order[0]]
        rounded[order[0]] = (name, count, round(value + drift, 1))
    return [{"reaction": name, "count": count, "percent": value} for name, count, value in rounded]
