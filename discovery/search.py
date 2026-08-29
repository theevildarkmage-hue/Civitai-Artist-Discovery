"""Read-only access to the browser search service used by Civitai's own UI."""

from __future__ import annotations

from datetime import datetime
import os


# These values are intentionally public: Civitai ships the same host and search-only
# client key to every browser that opens /search/images. Environment overrides give a
# packaged hotfix path if Civitai rotates the browser key or index before an app update.
SEARCH_HOST = os.environ.get("CIVITAI_SEARCH_HOST", "https://search-new.civitai.com").rstrip("/")
SEARCH_CLIENT_KEY = os.environ.get(
    "CIVITAI_SEARCH_CLIENT_KEY",
    "8c46eb2508e21db1e9828a97968d91ab1ca1caa5f70a00e88a2ba1e286603b61",
)
SEARCH_INDEX = os.environ.get("CIVITAI_SEARCH_INDEX", "images_v6")
SEARCH_URL = f"{SEARCH_HOST}/indexes/{SEARCH_INDEX}/search"
IMAGE_LOCATION = os.environ.get(
    "CIVITAI_IMAGE_LOCATION",
    "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA",
).rstrip("/")

SEARCH_PAGE_SIZE = 1000
SEARCH_SLICE_RESULT_LIMIT = 20_000
SEARCH_ATTRIBUTES = (
    "id", "postId", "createdAt", "url", "name", "hash", "width", "height",
    "type", "nsfwLevel", "baseModel", "modelVersionId", "user", "stats",
)


def milliseconds(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def search_body(start: datetime, end: datetime, browsing_level: int, *,
                limit: int, offset: int = 0) -> dict:
    """Build the same bounded numeric-range query used by Civitai image search."""
    return {
        "q": "",
        "limit": int(limit),
        "offset": int(offset),
        "filter": [
            f"createdAtUnix >= {milliseconds(start)}",
            f"createdAtUnix < {milliseconds(end)}",
            "type = image",
            f"nsfwLevel = {int(browsing_level)}",
        ],
        # The id tie-breaker prevents page drift when many images share createdAt.
        "sort": ["createdAt:asc", "id:asc"],
        "attributesToRetrieve": list(SEARCH_ATTRIBUTES),
    }


def image_delivery_url(source: object) -> str:
    value = str(source or "").strip()
    if not value or value.startswith(("http://", "https://")):
        return value
    return f"{IMAGE_LOCATION}/{value}/original=true/{value}.jpeg"


def normalize_hit(hit: dict) -> dict:
    """Translate an images_v6 document into the archive's listing contract."""
    level = int(hit.get("nsfwLevel") or 0)
    stats = hit.get("stats") if isinstance(hit.get("stats"), dict) else {}
    user = hit.get("user") if isinstance(hit.get("user"), dict) else {}
    model_version = hit.get("modelVersionId")
    mapped_stats = {
        "likeCount": int(stats.get("likeCountAllTime") or 0),
        "heartCount": int(stats.get("heartCountAllTime") or 0),
        "laughCount": int(stats.get("laughCountAllTime") or 0),
        "cryCount": int(stats.get("cryCountAllTime") or 0),
        "dislikeCount": int(stats.get("dislikeCountAllTime") or 0),
        "commentCount": int(stats.get("commentCountAllTime") or 0),
        "collectedCount": int(stats.get("collectedCountAllTime") or 0),
    }
    mapped_stats["reactionCount"] = sum(mapped_stats[name] for name in
        ("likeCount", "heartCount", "laughCount", "cryCount"))
    return {
        "id": int(hit["id"]),
        "postId": hit.get("postId"),
        "username": user.get("username") or "Unknown",
        "createdAt": hit.get("createdAt"),
        "url": image_delivery_url(hit.get("url")),
        "visualHash": str(hit.get("hash") or "").strip() or None,
        "width": hit.get("width"),
        "height": hit.get("height"),
        "type": hit.get("type") or "image",
        "nsfwLevel": {1: "None", 2: "Soft", 4: "Mature", 8: "X", 16: "X"}.get(level, "X"),
        "browsingLevel": level,
        "baseModel": hit.get("baseModel") or "Unknown",
        "modelVersionIds": [int(model_version)] if model_version else [],
        "prompt": "",
        "negativePrompt": "",
        "resources": [],
        "stats": mapped_stats,
    }
