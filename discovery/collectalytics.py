"""Statistics for a collection the signed-in account owns or follows.

Everything is read through the signed-in tRPC client and kept in memory only: a
collection's contents are Civitai's to keep, and a read is cheap enough to repeat.
The page does the counting, so days follow the reader's own time zone.

From Civitai's source (Oct 2026):

- ``collection.getAllUser`` with ``permissions: ["VIEW"]`` lists the account's own
  collections plus every collection it has a VIEW contributor row on. Following a
  collection writes that row, so this is "owned or followed". Rows carry ``isOwner`` and,
  for someone else's collection, ``ownerUsername``.
- ``collection.getAllCollectionItems`` pages accepted items newest added first. Each row
  is ``{id, type, createdAt, data}``, where ``createdAt`` is when the item was added and
  ``data`` is the image, post, model or article with its ``user``; a video's length is
  ``data.metadata.duration`` in seconds, when Civitai measured it. Items the viewer may
  not see are dropped after the cursor is set, so a page can be empty mid-collection.
- No call returns a follower count as such. ``collection.getInfinite`` reports
  ``_count.contributors``, every CollectionContributor row: the owner's own row, written
  when the collection is created, each follower, and each collaborator. ``getById``
  lists the collaborators other than the owner, so followers are what remains. It only
  answers for public collections.
"""

from __future__ import annotations

from datetime import datetime, timezone
import threading
import time
import urllib.parse

from .civitai import normalize
from .history import preview_url, video_playback_url, video_still_url
from .site import SITE_ORIGIN, image_url, profile_url


ITEM_PAGE_SIZE = 100
# 10,000 items. A larger collection is reported as truncated rather than read forever.
MAX_ITEM_PAGES = 100
CACHE_SECONDS = 300
# Every level, so the counts match the collection rather than the reader's filters.
# Thumbnails are still limited to the reader's levels.
ALL_LEVELS = 31
THUMB_WIDTH = 320
CDN_ROOT = "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA"
ITEM_PATHS = {"image": "images", "post": "posts", "model": "models", "article": "articles"}


class NotListed(Exception):
    """The collection or image is not one this tab has listed for the account."""


class Unavailable(Exception):
    """Civitai no longer serves the image."""


def collection_url(collection_id: int) -> str:
    return f"{SITE_ORIGIN}/collections/{int(collection_id)}"


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def choice_entry(row: object, user_id: int | None) -> dict | None:
    if not isinstance(row, dict) or _int(row.get("id")) is None:
        return None
    owner_id = _int(row.get("userId"))
    owned = bool(row.get("isOwner")) or (user_id is not None and owner_id == user_id)
    return {"id": int(row["id"]), "name": _text(row.get("name")) or "Untitled collection",
            "owned": owned, "ownerId": owner_id,
            "ownerName": None if owned else (_text(row.get("ownerUsername")) or None),
            "type": row.get("type"), "read": row.get("read"), "mode": row.get("mode"),
            "url": collection_url(row["id"])}


def thumb_url(key: str, media_type: str) -> str | None:
    """A small still from the item's CDN key; a full URL or an empty key gives none."""
    if not key or "/" in key:
        return None
    quoted = urllib.parse.quote(key)
    if media_type == "video":
        return (f"{CDN_ROOT}/{quoted}/anim=false,transcode=true,optimized=true,"
                f"width={THUMB_WIDTH}/{quoted}.jpeg")
    return f"{CDN_ROOT}/{quoted}/width={THUMB_WIDTH}/{quoted}.jpeg"


def video_duration(data: dict) -> float | None:
    """Seconds, from the video's ``metadata.duration``; None when Civitai has none."""
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    try:
        seconds = float(metadata.get("duration"))
    except (TypeError, ValueError):
        return None
    return seconds if 0 < seconds < 86400 else None


def item_entry(row: object) -> dict | None:
    """One collection item, reduced to what the statistics and the lookup show."""
    if not isinstance(row, dict) or _int(row.get("id")) is None:
        return None
    kind = _text(row.get("type")) or "image"
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    item_id = _int(data.get("id"))
    creator = _text(user.get("username"))
    media = _text(data.get("type")) if kind == "image" else kind
    entry = {
        "collectionItemId": int(row["id"]), "kind": kind, "mediaType": media or kind,
        "itemId": item_id, "addedAt": row.get("createdAt") or None,
        "createdAt": data.get("publishedAt") or data.get("createdAt") or None,
        "creatorId": _int(user.get("id")), "creator": creator or None,
        "profileUrl": profile_url(creator) if creator else None,
        "name": _text(data.get("name") or data.get("title")) or None,
        "nsfwLevel": _int(data.get("nsfwLevel")) or 0,
        "url": f"{SITE_ORIGIN}/{ITEM_PATHS[kind]}/{item_id}" if kind in ITEM_PATHS and item_id
        else None,
        "thumbUrl": thumb_url(_text(data.get("url")), media) if kind == "image" else None,
        "duration": video_duration(data) if media == "video" else None,
    }
    if kind == "image" and item_id:
        entry["url"] = image_url(item_id)
    return entry


def with_visible_thumbs(report: dict, levels, hidden_creators: set, hidden_images: set) -> dict:
    """A copy whose stills are limited to the reader's levels and hidden lists.

    Counting covers every item either way: leaving one out would misstate the collection.
    An unrated item has level 0 and shows no still.
    """
    items = []
    for item in report["items"]:
        if item["thumbUrl"] and not thumb_allowed(item, levels, hidden_creators, hidden_images):
            item = {**item, "thumbUrl": None, "thumbHidden": True}
        items.append(item)
    return {**report, "items": items}


def thumb_allowed(item: dict, levels, hidden_creators: set, hidden_images: set) -> bool:
    mask = sum(int(level) for level in levels)
    return bool(item["nsfwLevel"] & mask) and not item["nsfwLevel"] & ~mask and \
        (item["creator"] or "").casefold() not in hidden_creators and \
        item["itemId"] not in hidden_images


def image_detail(raw: dict) -> dict:
    """The detail dialog's shape, as the daily archive gives it, from a public listing row."""
    item = normalize(raw)
    video = item["type"] == "video"
    url = item["url"]
    item.update({
        "thumbnailUrl": video_still_url(url) if video else preview_url(url),
        "detailImageUrl": video_still_url(url, 1280) if video else preview_url(url, 1280),
        "detailsLoaded": True,
    })
    if video:
        item["videoUrl"] = video_playback_url(url)
        item["detailVideoUrl"] = video_playback_url(url, 768)
    return item


def followers_from(contributors: int | None, collaborators: int) -> int | None:
    """Contributor rows less the owner's own row and the collaborators'."""
    if contributors is None:
        return None
    return max(0, int(contributors) - 1 - max(0, int(collaborators)))


class Collectalytics:
    def __init__(self, social):
        # Returns a signed-in client, or raises PermissionError when there is none.
        self.social = social
        self.lock = threading.Lock()
        # By account: when the list was read and the collections in it.
        self.choices: dict[int | None, tuple[float, list[dict]]] = {}
        # By account and collection id: when it was read and the answer served for it.
        # Per account, because Civitai filters items by viewer and ``owned`` is theirs.
        self.reports: dict[tuple[int | None, int], tuple[float, dict]] = {}
        self.report_locks: dict[tuple[int | None, int], threading.Lock] = {}
        # Images in any report served to an account, by account and image id. Only these
        # open in the detail dialog, so the route is not a general Civitai lookup.
        self.listed: dict[tuple[int | None, int], dict] = {}
        # Details by image id; they are public and the same for every account.
        self.details: dict[int, tuple[float, dict]] = {}

    def collections(self, user_id: int | None, refresh: bool = False) -> list[dict]:
        with self.lock:
            cached = self.choices.get(user_id)
        if cached and not refresh and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        value = self.social().query("collection.getAllUser", {"permissions": ["VIEW"]})
        if not isinstance(value, list):
            raise RuntimeError("Civitai returned an unexpected collections response")
        found = {entry["id"]: entry for entry in (choice_entry(row, user_id) for row in value)
                 if entry}
        result = sorted(found.values(), key=lambda entry: (not entry["owned"],
                                                           entry["name"].casefold()))
        with self.lock:
            self.choices[user_id] = (time.monotonic(), result)
        return result

    def report(self, user_id: int | None, collection_id: int, refresh: bool = False) -> dict:
        collection_id = int(collection_id)
        choice = next((entry for entry in self.collections(user_id)
                       if entry["id"] == collection_id), None)
        if choice is None:
            # Only what the account owns or follows; anything else is not this tab's to read.
            raise NotListed("That collection is not one you own or follow")
        with self.lock:
            lock = self.report_locks.setdefault((user_id, collection_id), threading.Lock())
        with lock:
            cached = self.reports.get((user_id, collection_id))
            if cached and not refresh and time.monotonic() - cached[0] < CACHE_SECONDS:
                return cached[1]
            client = self.social()
            items, pages, truncated = self._items(client, collection_id)
            detail = self._detail(client, collection_id, choice)
            report = {"collection": {**choice, **detail.pop("collection")}, **detail,
                      "items": items, "pages": pages, "truncated": truncated,
                      "fetchedAt": datetime.now(timezone.utc).isoformat()}
            self.reports[(user_id, collection_id)] = (time.monotonic(), report)
            with self.lock:
                for item in items:
                    if item["kind"] == "image" and item["itemId"]:
                        self.listed[(user_id, item["itemId"])] = item
            return report

    def listed_image(self, user_id: int | None, image_id: int) -> dict:
        with self.lock:
            item = self.listed.get((user_id, int(image_id)))
        if item is None:
            raise NotListed("Only images from a collection opened here can be shown")
        return item

    def detail(self, user_id: int | None, image_id: int, request) -> dict:
        """One listed image with its generation details, read from the public API.

        ``request`` is the daily archive's paced reader. The image's own level is asked
        for, since an anonymous read otherwise answers at the public level only.
        """
        item = self.listed_image(user_id, image_id)
        with self.lock:
            cached = self.details.get(int(image_id))
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        payload, _ = request({"imageId": int(image_id), "withMeta": "true",
                              "browsingLevel": item["nsfwLevel"] or ALL_LEVELS},
                             minimum_interval=1.5, request_attempts=3, request_timeout=20)
        rows = payload.get("items") if isinstance(payload, dict) else None
        raw = next((row for row in rows or [] if isinstance(row, dict)
                    and _int(row.get("id")) == int(image_id)), None)
        if raw is None:
            raise Unavailable("Civitai no longer lists this image")
        detail = image_detail(raw)
        with self.lock:
            self.details[int(image_id)] = (time.monotonic(), detail)
        return detail

    def forget(self, user_id: int | None, collection_id: int) -> None:
        with self.lock:
            self.reports.pop((user_id, int(collection_id)), None)

    @staticmethod
    def _items(client, collection_id: int) -> tuple[list[dict], int, bool]:
        items: dict[int, dict] = {}
        cursor, seen = None, set()
        for page in range(1, MAX_ITEM_PAGES + 1):
            payload = {"collectionId": collection_id, "statuses": ["ACCEPTED"],
                       "limit": ITEM_PAGE_SIZE, "browsingLevel": ALL_LEVELS}
            if cursor is not None:
                payload["cursor"] = cursor
            value = client.query("collection.getAllCollectionItems", payload)
            value = value if isinstance(value, dict) else {}
            rows = value.get("collectionItems")
            rows = rows if isinstance(rows, list) else []
            for entry in (item_entry(row) for row in rows):
                if entry:
                    items.setdefault(entry["collectionItemId"], entry)
            cursor = value.get("nextCursor")
            # Civitai sets the cursor before dropping items the viewer may not see, so an
            # empty page can still have more behind it. Only a missing or repeated cursor
            # ends the read; the page cap bounds the rest.
            if cursor is None or str(cursor) in seen:
                return list(items.values()), page, False
            seen.add(str(cursor))
        return list(items.values()), MAX_ITEM_PAGES, True

    @staticmethod
    def _detail(client, collection_id: int, choice: dict) -> dict:
        """Owner name, description and followers. Missing pieces are left unknown."""
        detail = {"collection": {}, "followers": None, "contributors": None,
                  "collaborators": 0}
        owner_id = choice.get("ownerId")
        try:
            value = client.query("collection.getById", {"id": collection_id})
            value = value if isinstance(value, dict) else {}
            found = value.get("collection") if isinstance(value.get("collection"), dict) else {}
            user = found.get("user") if isinstance(found.get("user"), dict) else {}
            owner_id = _int(found.get("userId")) or owner_id
            detail["collection"] = {
                "description": _text(found.get("description")) or None,
                "ownerName": _text(user.get("username")) or choice.get("ownerName"),
            }
            collaborators = value.get("collaborators")
            detail["collaborators"] = len(collaborators) if isinstance(collaborators, list) else 0
        except Exception:
            pass
        if owner_id is None:
            return detail
        try:
            value = client.query("collection.getInfinite", {
                "userId": owner_id, "ids": [collection_id], "limit": 1})
            rows = value.get("items") if isinstance(value, dict) else None
            row = next((row for row in rows or [] if isinstance(row, dict)
                        and _int(row.get("id")) == collection_id), None)
            counts = row.get("_count") if isinstance(row, dict) else None
            if isinstance(counts, dict):
                detail["contributors"] = _int(counts.get("contributors"))
                detail["followers"] = followers_from(detail["contributors"],
                                                     detail["collaborators"])
        except Exception:
            pass
        return detail
