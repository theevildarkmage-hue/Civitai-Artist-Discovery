"""Walk each followed creator's history from its beginning.

Everything else in this app pages Civitai's *global* feed, which is why it fights a
~49,000 offset ceiling and a roughly two-day reachable window. A per-creator query is a
different feed: short, and ``sort=Oldest`` starts at the far end of it. A creator's 2024
work is one request away, which the daily archive structurally cannot reach.

The design consequence worth knowing is that the median creator's whole back-catalogue
fits in the single 200-image page the prime already fetches, so most creators are complete
after priming and never need a refill. The cost sits in a heavy tail, and refills are
per-creator and on demand, so it is only paid for creators actually read to the end.

Storage is its own database. Progress here is long-lived personal state and must not be
discarded when the taste profile is reset, nor be able to corrupt a collected day.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading

from .civitai import normalize, thumbnail_url, utcnow
from .history import video_playback_url, video_still_url
from .site import image_url, levels_for_rating


# One request per creator, the largest page the API allows.
PRIME_PAGE_SIZE = 200
# user.getCreator batches, so 483 follows resolve to usernames in five requests.
CREATOR_BATCH = 100
# How far past the pointer to look for an image the reader does not hide. Tags are only
# known for images already swept, so this bounds the scan rather than promising a find.
HIDDEN_SCAN_LIMIT = 40


def _row_type(row) -> str:
    """The stored media type, tolerating rows written before the column existed."""
    try:
        return row["type"] or "image"
    except (IndexError, KeyError):
        return "image"


def _representative(image) -> dict:
    # A video needs the same treatment the daily archive gives it: a still for the card,
    # because a plain width= transform returns another MP4 that an <img> cannot show, and
    # a separate transcoded file to play.
    video = _row_type(image) == "video"
    item = {"id": image["image_id"], "createdAt": image["created_at"],
            "url": image["url"],
            "thumbnailUrl": video_still_url(image["url"]) if video else thumbnail_url(image["url"]),
            "civitaiUrl": image_url(image["image_id"]),
            "browsingLevel": image["browsing_level"], "postId": image["post_id"],
            "width": image["width"], "height": image["height"],
            "type": _row_type(image),
            "baseModel": image["base_model"], "stats": json.loads(image["stats"])}
    if video:
        item["videoUrl"] = video_playback_url(image["url"])
    return item


class TimeMachine:
    """Per-creator history, walked oldest first."""

    def __init__(self, root: Path, archive, taste=None):
        self.root = root
        self.db_path = root / "timemachine.sqlite3"
        # Sharing the archive's request path is deliberate: it carries the adaptive pacer
        # and the single API lane, so priming queues behind a day build rather than
        # racing it. Two collectors hammering Civitai at once is the thing to avoid.
        self.archive = archive
        self.taste = taste
        self.lock = threading.RLock()
        self.prime_thread: threading.Thread | None = None
        self.cancel = threading.Event()
        self._initialize()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS creator_images(
                username_key TEXT NOT NULL, position INTEGER NOT NULL, image_id INTEGER NOT NULL,
                username TEXT NOT NULL, created_at TEXT NOT NULL, url TEXT NOT NULL,
                browsing_level INTEGER NOT NULL DEFAULT 1, post_id INTEGER,
                width INTEGER, height INTEGER, base_model TEXT, stats TEXT NOT NULL DEFAULT '{}',
                type TEXT NOT NULL DEFAULT 'image',
                PRIMARY KEY(username_key, position))""")
            # Videos were always collected here, but stored with no type, so every one was
            # served as an image and its MP4 handed to an <img> that cannot show it. Rows
            # written before this column exists are classed by their file extension, which
            # is what the CDN URL carries -- no refetch, and the walk keeps its positions.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(creator_images)")}
            if "type" not in columns:
                db.execute("ALTER TABLE creator_images ADD COLUMN "
                           "type TEXT NOT NULL DEFAULT 'image'")
                # Not .gif: an <img> shows one perfectly well, and Civitai types it as an
                # image, so treating it as video would break what currently works.
                db.execute("UPDATE creator_images SET type='video' WHERE "
                           "url LIKE '%.mp4' OR url LIKE '%.webm'")
            db.execute("""CREATE TABLE IF NOT EXISTS creator_progress(
                username_key TEXT PRIMARY KEY, username TEXT NOT NULL,
                next_position INTEGER NOT NULL DEFAULT 0, fetched INTEGER NOT NULL DEFAULT 0,
                cursor TEXT, exhausted INTEGER NOT NULL DEFAULT 0,
                primed_at TEXT, updated_at TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS creator_images_level "
                       "ON creator_images(username_key, browsing_level, position)")

    # -- the follow list --------------------------------------------------------

    def followed_usernames(self, client) -> list[str]:
        """Resolve the followed-creator ids to usernames.

        ``followed_creators`` in the taste profile stores ids; the images endpoint takes a
        username. ``user.getCreator`` accepts an id and batches, so the whole follow list
        resolves in a handful of requests. Names are cached, so this is paid once.
        """
        with self.taste.connect() as db:
            ids = [row[0] for row in db.execute("SELECT creator_id FROM followed_creators")]
        with self.connect() as db:
            known = {row["username_key"]: row["username"] for row in
                     db.execute("SELECT username_key, username FROM creator_progress")}
        if known and len(known) >= len(ids):
            return sorted(known.values())
        names: list[str] = list(known.values())
        for start in range(0, len(ids), CREATOR_BATCH):
            chunk = ids[start:start + CREATOR_BATCH]
            results = client.batch_query_optional(
                "user.getCreator", [{"id": value} for value in chunk])
            for result in results:
                name = (result or {}).get("username") if isinstance(result, dict) else None
                if name:
                    names.append(str(name))
        unique = sorted({name for name in names if name}, key=str.casefold)
        stamp = utcnow()
        with self.connect() as db:
            db.executemany("""INSERT INTO creator_progress(username_key, username, updated_at)
                              VALUES(?,?,?) ON CONFLICT(username_key) DO NOTHING""",
                           [(name.casefold(), name, stamp) for name in unique])
        return unique

    # -- fetching ---------------------------------------------------------------

    def _mask(self) -> int:
        return sum(levels_for_rating(self.archive.content_rating))

    def fetch_page(self, username: str, cursor: str | None = None) -> int:
        """Store one page of a creator's images, oldest first. Returns how many were new."""
        key = username.casefold()
        params = {"username": username, "sort": "Oldest", "limit": PRIME_PAGE_SIZE,
                  "browsingLevel": self._mask(), "withMeta": "false"}
        if cursor:
            params["cursor"] = cursor
        payload, _ = self.archive._request(params)
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        next_cursor = (payload.get("metadata") or {}).get("nextCursor")
        with self.connect() as db:
            row = db.execute("SELECT fetched FROM creator_progress WHERE username_key=?",
                             (key,)).fetchone()
            start = int(row["fetched"]) if row else 0
            rows = []
            for offset, raw in enumerate(items):
                item = normalize(raw)
                if not item.get("id") or not item.get("createdAt") or not item.get("url"):
                    continue
                rows.append((key, start + len(rows), int(item["id"]), username,
                             item["createdAt"], item["url"],
                             int(item.get("browsingLevel") or 1), item.get("postId"),
                             item.get("width"), item.get("height"), item.get("baseModel"),
                             json.dumps(item.get("stats") or {}), item.get("type") or "image"))
            db.executemany("""INSERT OR IGNORE INTO creator_images(
                username_key, position, image_id, username, created_at, url,
                browsing_level, post_id, width, height, base_model, stats, type)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
            # No next cursor, or a page that produced nothing, means the end of this
            # creator's history: the walk is complete rather than merely paused.
            done = 1 if (not next_cursor or not rows) else 0
            db.execute("""INSERT INTO creator_progress(
                    username_key, username, fetched, cursor, exhausted, primed_at, updated_at)
                    VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(username_key) DO UPDATE SET
                    fetched=excluded.fetched, cursor=excluded.cursor,
                    exhausted=excluded.exhausted,
                    primed_at=COALESCE(creator_progress.primed_at, excluded.primed_at),
                    updated_at=excluded.updated_at""",
                (key, username, start + len(rows), next_cursor, done, utcnow(), utcnow()))
        return len(rows)

    # -- reading ----------------------------------------------------------------

    def hidden_tag_names(self) -> set[str]:
        """Tag names the reader hides on Civitai, if the profile knows any."""
        if self.taste is None:
            return set()
        try:
            with self.taste.connect() as db:
                return {str(row[0]).casefold() for row in
                        db.execute("SELECT tag_name FROM hidden_tags") if row[0]}
        except Exception:  # noqa: BLE001
            return set()

    def _hidden_image_ids(self, image_ids: list[int], hidden: set[str]) -> set[int]:
        """Which of these images carry a tag the reader hides, per the cached sweep."""
        if not image_ids or not hidden or self.taste is None:
            return set()
        holes = ",".join("?" for _ in image_ids)
        try:
            with self.taste.connect() as db:
                rows = db.execute(
                    f"SELECT image_id, tag_name FROM archive_image_tags "
                    f"WHERE image_id IN ({holes})", image_ids).fetchall()
        except Exception:  # noqa: BLE001
            return set()
        blocked = set()
        for row in rows:
            if str(row[1]).casefold() in hidden:
                blocked.add(int(row[0]))
        return blocked

    def cards(self, levels=None) -> list[dict]:
        """The oldest unseen image for each primed creator, one per creator.

        Creators with nothing at the visible levels are omitted rather than shown empty,
        as are creators who have been read to the end.
        """
        visible = sorted(levels or self.archive.visible_levels)
        holes = ",".join("?" for _ in visible)
        hidden_tags = self.hidden_tag_names()
        out = []
        with self.connect() as db:
            for row in db.execute("""SELECT * FROM creator_progress
                                     ORDER BY updated_at ASC, username_key ASC"""):
                candidates = db.execute(
                    f"""SELECT * FROM creator_images
                        WHERE username_key=? AND position>=? AND browsing_level IN ({holes})
                        ORDER BY position LIMIT ?""",
                    (row["username_key"], row["next_position"], *visible,
                     HIDDEN_SCAN_LIMIT)).fetchall()
                blocked = self._hidden_image_ids(
                    [int(entry["image_id"]) for entry in candidates], hidden_tags)
                image = next((entry for entry in candidates
                              if int(entry["image_id"]) not in blocked), None)
                if image is None:
                    continue
                seen = db.execute(
                    f"""SELECT COUNT(*) FROM creator_images
                        WHERE username_key=? AND position<? AND browsing_level IN ({holes})""",
                    (row["username_key"], row["next_position"], *visible)).fetchone()[0]
                total = db.execute(
                    f"""SELECT COUNT(*) FROM creator_images
                        WHERE username_key=? AND browsing_level IN ({holes})""",
                    (row["username_key"], *visible)).fetchone()[0]
                out.append({
                    "username": row["username"],
                    "imageCount": 1, "representativeIndex": 0,
                    "representative": _representative(image),
                    "seenCount": seen, "knownCount": total,
                    # False while more of this creator remains unfetched, so the counts can
                    # be shown as "of N so far" rather than implying a complete history.
                    "complete": bool(row["exhausted"])})
        return out

    def search_creators(self, text: str, limit: int = 12) -> list[dict]:
        """Followed creators whose name contains ``text``, name-prefix matches first.

        Only creators the Time Machine already tracks, so a suggestion never costs a
        Civitai request and never adds anyone to the one-card-per-creator walk.
        """
        needle = str(text or "").strip().casefold()
        if not needle:
            return []
        # Usernames can contain "_", which LIKE would otherwise treat as a wildcard.
        escaped = needle.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        with self.connect() as db:
            rows = db.execute(
                """SELECT p.username, p.next_position, p.exhausted,
                          (SELECT COUNT(*) FROM creator_images i
                           WHERE i.username_key=p.username_key) AS known
                   FROM creator_progress p WHERE p.username_key LIKE ? ESCAPE '!'
                   ORDER BY p.username_key NOT LIKE ? ESCAPE '!', p.username_key LIMIT ?""",
                (f"%{escaped}%", f"{escaped}%", max(1, min(int(limit), 50)))).fetchall()
        return [{"username": row["username"], "knownCount": int(row["known"]),
                 "seenCount": min(int(row["next_position"]), int(row["known"])),
                 "complete": bool(row["exhausted"])} for row in rows]

    def creator_history(self, username: str, after: int | None = None, limit: int = 40,
                        refill: bool = True, levels=None) -> dict:
        """One followed creator's images, oldest first, from where the reader left off.

        ``after`` is the last position the page already holds, so paging continues from
        the list the reader is scrolling rather than from the saved spot, which moves
        underneath it as cards are passed. When the saved images run out, the next page
        is fetched from Civitai here, so scrolling never stalls at a cached boundary.
        """
        key = str(username or "").casefold()
        with self.connect() as db:
            progress = db.execute("SELECT * FROM creator_progress WHERE username_key=?",
                                  (key,)).fetchone()
        if progress is None:
            raise KeyError("That creator is not one the Time Machine follows")
        start = int(progress["next_position"]) if after is None else int(after) + 1
        limit = max(1, min(int(limit), 100))
        visible = sorted(levels or self.archive.visible_levels)
        holes = ",".join("?" for _ in visible)
        hidden_tags = self.hidden_tag_names()
        images: list[dict] = []
        position = start
        # Bounded: each pass either reads saved rows or fetches one more page.
        for _ in range(6):
            with self.connect() as db:
                rows = db.execute(
                    f"""SELECT * FROM creator_images
                        WHERE username_key=? AND position>=? AND browsing_level IN ({holes})
                        ORDER BY position LIMIT ?""",
                    (key, position, *visible, limit - len(images))).fetchall()
                exhausted, cursor = db.execute(
                    "SELECT exhausted, cursor FROM creator_progress WHERE username_key=?",
                    (key,)).fetchone()
            blocked = self._hidden_image_ids([int(row["image_id"]) for row in rows], hidden_tags)
            for row in rows:
                if int(row["image_id"]) not in blocked:
                    images.append({"position": int(row["position"]), **_representative(row)})
            if rows:
                position = int(rows[-1]["position"]) + 1
            if len(images) >= limit or exhausted or not cursor or not refill:
                break
            if not rows and self.fetch_page(progress["username"], cursor=cursor) == 0:
                break
        with self.connect() as db:
            known = db.execute(
                f"""SELECT COUNT(*) FROM creator_images
                    WHERE username_key=? AND browsing_level IN ({holes})""",
                (key, *visible)).fetchone()[0]
            seen = db.execute(
                f"""SELECT COUNT(*) FROM creator_images WHERE username_key=? AND position<?
                    AND browsing_level IN ({holes})""",
                (key, progress["next_position"], *visible)).fetchone()[0]
            final = db.execute("SELECT exhausted FROM creator_progress WHERE username_key=?",
                               (key,)).fetchone()[0]
        return {"username": progress["username"], "images": images,
                "knownCount": known, "seenCount": seen, "complete": bool(final),
                "hasMore": len(images) >= limit or not final}

    def advance_to(self, username: str, position: int) -> bool:
        """Move a creator's saved spot past ``position``; never backwards.

        Monotonic so batched or out-of-order flushes from a scrolled list are harmless.
        """
        key = str(username or "").casefold()
        with self.connect() as db:
            changed = db.execute(
                "UPDATE creator_progress SET next_position=?, updated_at=? "
                "WHERE username_key=? AND next_position<=?",
                (int(position) + 1, utcnow(), key, int(position))).rowcount
        return bool(changed)

    def has_image(self, image_id: int) -> bool:
        """Whether this image was collected here.

        The content-control check refuses images the app has not collected, which is
        what keeps that endpoint from being a general-purpose tag lookup. Time Machine
        images are collected, just into a different database, so they have to be
        recognised or every card fails its check and shows no artwork at all.
        """
        with self.connect() as db:
            return db.execute("SELECT 1 FROM creator_images WHERE image_id=? LIMIT 1",
                              (int(image_id),)).fetchone() is not None

    def stats(self, image_id: int) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT stats FROM creator_images WHERE image_id=? LIMIT 1",
                             (int(image_id),)).fetchone()
        return json.loads(row["stats"]) if row else {}

    def update_stats(self, image_id: int, stats: dict) -> None:
        with self.connect() as db:
            db.execute("UPDATE creator_images SET stats=? WHERE image_id=?",
                       (json.dumps(stats), int(image_id)))

    def detail(self, image_id: int) -> dict:
        """The detail-dialog payload for an image collected here.

        Prompts and resources are not stored: the listing does not carry them and this
        walks whole back-catalogues, so fetching them for every image would be wasteful.
        They are read once, on demand, exactly as the daily archive does -- and asked for
        at this image's own browsing level, since an anonymous caller is otherwise
        answered at the public level only and gets nothing back.
        """
        with self.connect() as db:
            row = db.execute("SELECT * FROM creator_images WHERE image_id=? LIMIT 1",
                             (int(image_id),)).fetchone()
        if row is None:
            raise ValueError("Image is not in the history archive")
        video = _row_type(row) == "video"
        item = {"id": row["image_id"], "postId": row["post_id"], "username": row["username"],
                "createdAt": row["created_at"], "url": row["url"],
                "thumbnailUrl": video_still_url(row["url"]) if video else thumbnail_url(row["url"]),
                "detailImageUrl": (video_still_url(row["url"], 1280) if video
                                   else thumbnail_url(row["url"], 1280)),
                "civitaiUrl": image_url(row["image_id"]),
                "width": row["width"], "height": row["height"], "type": _row_type(row),
                "browsingLevel": row["browsing_level"],
                "baseModel": row["base_model"] or "Unknown",
                "stats": json.loads(row["stats"]), "visualHash": None,
                "modelVersionIds": [], "prompt": "", "negativePrompt": "",
                "resources": [], "detailsLoaded": False}
        if video:
            item["detailVideoUrl"] = video_playback_url(row["url"], 768)
        try:
            payload, _ = self.archive._request(
                {"imageId": int(image_id), "withMeta": "true",
                 "browsingLevel": int(row["browsing_level"] or 0) or self._mask()})
            raw = next((entry for entry in payload.get("items", [])
                        if int(entry.get("id", -1)) == int(image_id)), None)
            if raw:
                extra = normalize(raw)
                item.update({"prompt": extra.get("prompt") or "",
                             "negativePrompt": extra.get("negativePrompt") or "",
                             "resources": extra.get("resources") or [],
                             "modelVersionIds": extra.get("modelVersionIds") or [],
                             "baseModel": extra.get("baseModel") or item["baseModel"],
                             "visualHash": extra.get("visualHash"),
                             "detailsLoaded": True})
        except Exception:  # noqa: BLE001
            pass   # The dialog still opens; it simply has no generation details.
        return item

    def advance(self, usernames) -> int:
        """Move each named creator past the image currently shown."""
        keys = [str(name).casefold() for name in usernames if str(name).strip()]
        if not keys:
            return 0
        stamp = utcnow()
        moved = 0
        with self.connect() as db:
            for key in keys:
                row = db.execute("SELECT next_position FROM creator_progress WHERE username_key=?",
                                 (key,)).fetchone()
                if row is None:
                    continue
                nxt = db.execute("""SELECT position FROM creator_images
                                    WHERE username_key=? AND position>=? ORDER BY position LIMIT 1""",
                                 (key, row["next_position"])).fetchone()
                if nxt is None:
                    continue
                db.execute("UPDATE creator_progress SET next_position=?, updated_at=? "
                           "WHERE username_key=?", (int(nxt["position"]) + 1, stamp, key))
                moved += 1
        return moved

    def status(self) -> dict:
        """Priming progress, counted in creators rather than images.

        A per-creator history percentage is not available: the images endpoint returns no
        total and paging a prolific creator to their end would cost hundreds of requests.
        Creators primed out of creators followed is both meaningful and free.
        """
        with self.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM creator_progress").fetchone()[0]
            primed = db.execute(
                "SELECT COUNT(*) FROM creator_progress WHERE primed_at IS NOT NULL").fetchone()[0]
            images = db.execute("SELECT COUNT(*) FROM creator_images").fetchone()[0]
        with self.lock:
            running = bool(self.prime_thread and self.prime_thread.is_alive())
        return {"creators": total, "primed": primed, "images": images,
                "priming": running,
                "progress": round(primed / total * 100, 1) if total else 0.0}

    # -- priming ----------------------------------------------------------------

    def prime(self, client, on_progress=None) -> dict:
        """Fetch one page for every creator that has never been primed."""
        self.followed_usernames(client)
        with self.connect() as db:
            pending = [row["username"] for row in db.execute(
                "SELECT username FROM creator_progress WHERE primed_at IS NULL ORDER BY username")]
        done = 0
        for username in pending:
            if self.cancel.is_set():
                break
            try:
                self.fetch_page(username)
            except Exception:  # noqa: BLE001
                # One creator failing must not abandon the rest; the next prime retries it
                # because primed_at is still null.
                continue
            done += 1
            if on_progress:
                on_progress(done, len(pending))
        return {"primed": done, "pending": len(pending)}

    def start_prime(self, client) -> dict:
        with self.lock:
            if self.prime_thread and self.prime_thread.is_alive():
                return self.status()
            self.cancel.clear()
            self.prime_thread = threading.Thread(
                target=lambda: self.prime(client), daemon=True, name="timemachine-prime")
            self.prime_thread.start()
        return self.status()

    def stop_prime(self) -> None:
        self.cancel.set()

    def refill(self, username: str) -> int:
        """Fetch the next page for a creator whose cached history has run out."""
        key = str(username).casefold()
        with self.connect() as db:
            row = db.execute("SELECT cursor, exhausted FROM creator_progress WHERE username_key=?",
                             (key,)).fetchone()
        if row is None or row["exhausted"] or not row["cursor"]:
            return 0
        return self.fetch_page(username, cursor=row["cursor"])
