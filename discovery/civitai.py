"""Read-only Civitai image collection with resumable cursor checkpoints."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
import urllib.parse
import urllib.error
import urllib.request


from .site import API_URL, image_url
USER_AGENT = "CivitaiArtistDiscovery/1.0 (local Windows artist discovery app; sequential requests)"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def thumbnail_url(url: str, width: int = 1280) -> str:
    if "/original=true/" in url:
        return url.replace("/original=true/", f"/width={width}/", 1)
    return url


def normalize(item: dict) -> dict:
    stats = item.get("stats") or {}
    reactions = max(0, sum(
        int(stats.get(name) or 0)
        for name in ("likeCount", "heartCount", "laughCount", "cryCount")
    ))
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    # The public API currently wraps generation data as {id, meta:{...}} for
    # single-image detail reads while older responses used the inner object.
    if isinstance(meta.get("meta"), dict):
        meta = meta["meta"]
    raw_resources = meta.get("resources") or meta.get("additionalResources")
    resources = raw_resources if isinstance(raw_resources, list) else []
    image_id = int(item["id"])
    original = str(item.get("url") or "")
    return {
        "id": image_id,
        "postId": item.get("postId"),
        "username": item.get("username") or "Unknown",
        "createdAt": item.get("createdAt"),
        "url": original,
        # Civitai includes this visual placeholder hash in listing responses.  It is
        # stable when the same artwork is reposted under a different image id or CDN
        # URL, which makes it useful for measuring duplicates without another request.
        "visualHash": str(item.get("hash") or "").strip() or None,
        "thumbnailUrl": thumbnail_url(original),
        "civitaiUrl": image_url(image_id),
        "width": item.get("width"),
        "height": item.get("height"),
        "type": item.get("type") or "image",
        "nsfwLevel": item.get("nsfwLevel"),
        "browsingLevel": item.get("browsingLevel"),
        "baseModel": item.get("baseModel") or "Unknown",
        "modelVersionIds": item.get("modelVersionIds") or [],
        "prompt": meta.get("prompt") or "",
        "negativePrompt": meta.get("negativePrompt") or "",
        "resources": resources,
        "stats": {**stats, "reactionCount": reactions},
    }


class CandidateCache:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "items": [], "nextCursor": None, "updatedAt": None}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"version": 1, "items": [], "nextCursor": None, "updatedAt": None}
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "items": [], "nextCursor": None, "updatedAt": None}

    def save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def collect(
        self,
        target: int = 500,
        *,
        refresh: bool = False,
        period: str = "Week",
        nsfw: str = "None",
        delay_seconds: float = 1.0,
    ) -> dict:
        started = time.monotonic()
        state = self.load()
        by_id = {int(item["id"]): item for item in state.get("items", [])}
        # nextCursor is retained for compatibility. backfillCursor is the
        # durable checkpoint used to walk progressively farther into history.
        backfill_cursor = state.get("backfillCursor", state.get("nextCursor"))
        cursor = None if refresh else backfill_cursor
        pages = 0
        newest_added = 0
        older_added = 0
        while len(by_id) < target or (
            refresh and (
                pages == 0
                or (cursor is not None and newest_added == 0 and older_added == 0 and pages < 11)
            )
        ):
            params = {
                "limit": 100,
                "sort": "Newest",
                "period": period,
                "nsfw": nsfw,
                "withMeta": "true",
            }
            if cursor:
                params["cursor"] = cursor
            request = urllib.request.Request(
                f"{API_URL}?{urllib.parse.urlencode(params)}",
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            )
            payload = None
            for attempt in range(5):
                try:
                    with urllib.request.urlopen(request, timeout=60) as response:
                        payload = json.loads(response.read())
                    break
                except urllib.error.HTTPError as error:
                    if error.code != 429 and error.code < 500:
                        raise
                    retry_after = error.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after and retry_after.isdigit() else min(30, 2 ** attempt)
                    time.sleep(wait)
                except (TimeoutError, urllib.error.URLError):
                    if attempt == 4:
                        raise
                    time.sleep(min(30, 2 ** attempt))
            if payload is None:
                raise RuntimeError("Civitai image collection exhausted its retry budget")
            added = 0
            for raw in payload.get("items", []):
                if raw.get("type") != "image" or not raw.get("url"):
                    continue
                try:
                    item = normalize(raw)
                except (TypeError, ValueError, KeyError):
                    continue
                if item["id"] not in by_id:
                    added += 1
                by_id[item["id"]] = item
            next_cursor = (payload.get("metadata") or {}).get("nextCursor")
            pages += 1
            if refresh and pages == 1:
                newest_added = added
                # A no-change head page triggers one historical backfill page.
                # Keep the older checkpoint independent from the head cursor.
                if added == 0 and backfill_cursor:
                    cursor = backfill_cursor
                else:
                    cursor = None
            else:
                older_added += added
                backfill_cursor = next_cursor
                cursor = next_cursor
            state = {
                "version": 1,
                "updatedAt": utcnow(),
                "period": period,
                "nsfw": nsfw,
                "nextCursor": backfill_cursor,
                "backfillCursor": backfill_cursor,
                "items": list(by_id.values()),
                "lastCollect": {
                    "refresh": refresh,
                    "requests": pages,
                    "newestAdded": newest_added,
                    "olderAdded": older_added,
                    "totalAdded": newest_added + older_added,
                    "durationSeconds": round(time.monotonic() - started, 3),
                },
            }
            self.save(state)
            if not cursor:
                break
            if refresh and pages > 1 and (older_added > 0 or pages >= 11):
                break
            if not refresh and added == 0:
                break
            if not refresh and len(by_id) >= target:
                break
            time.sleep(delay_seconds)
        return state
