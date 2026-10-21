"""Ephemeral video thumbnail hints; no persistence or additional upstream requests."""

from collections import OrderedDict
import re
import threading
import time
from urllib.parse import urlsplit, urlunsplit


def supported_thumbnail(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    if any(ord(char) < 33 for char in value):
        return None
    try:
        parts = urlsplit(value)
        if (parts.scheme != "https" or parts.hostname != "image.civitai.com"
                or parts.username or parts.password or parts.port not in (None, 443)
                or not parts.path.startswith("/") or parts.path == "/"):
            return None
        return value
    except ValueError:
        return None


def thumbnail_size(url, width):
    """Bound recognized unsigned CDN transforms; leave opaque/signed URLs intact."""
    parts = urlsplit(url)
    pieces = parts.path.rsplit("/", 2)
    if parts.query or parts.fragment or len(pieces) != 3:
        return url
    options = pieces[-2].split(",")
    if not options or not all(re.fullmatch(
            r"(?:anim|optimized|transcode)=(?:true|false)|(?:width|height|quality|skip)=\d+", option)
            for option in options):
        return url
    options = [option for option in options if not option.startswith("width=")]
    pieces[-2] = ",".join([*options, f"width={width}"])
    return urlunsplit(parts._replace(path="/".join(pieces)))


class ThumbnailHints:
    def __init__(self, max_entries=2048, max_bytes=2 * 1024 * 1024,
                 ttl=24 * 60 * 60, clock=time.monotonic):
        self.entries = OrderedDict()
        self.max_entries, self.max_bytes, self.ttl = max_entries, max_bytes, ttl
        self.clock, self.bytes = clock, 0
        self.lock = threading.Lock()

    def _drop(self, key):
        self.bytes -= self.entries.pop(key)[2]

    def capture(self, payload, mask):
        if not isinstance(mask, int) or mask <= 0 or mask & ~31:
            return
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return
        with self.lock:
            now = self.clock()
            for key in list(self.entries):
                if now - self.entries[key][0] >= self.ttl:
                    self._drop(key)
            for raw in items:
                if not isinstance(raw, dict) or raw.get("type") != "video":
                    continue
                thumb = raw.get("thumbnail")
                url = supported_thumbnail(thumb.get("url")) if isinstance(thumb, dict) else None
                original, image_id = raw.get("url"), raw.get("id")
                if not url or not isinstance(original, str) or not isinstance(image_id, int):
                    continue
                key = (image_id, original, mask)
                size = len(original.encode()) + len(url.encode())
                if size > self.max_bytes:
                    continue
                if key in self.entries:
                    self._drop(key)
                self.entries[key] = (now, url, size)
                self.bytes += size
                while len(self.entries) > self.max_entries or self.bytes > self.max_bytes:
                    self._drop(next(iter(self.entries)))

    def get(self, image_id, original, levels, width=768):
        allowed = sum(set(levels))
        with self.lock:
            for key in reversed(list(self.entries)):
                stamp, url, _ = self.entries[key]
                if self.clock() - stamp >= self.ttl:
                    self._drop(key)
                    continue
                if key[:2] == (image_id, original) and not key[2] & ~allowed:
                    self.entries.move_to_end(key)
                    return thumbnail_size(url, width)
        return None
