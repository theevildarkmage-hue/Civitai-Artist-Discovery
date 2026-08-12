"""Small, atomic local preference store."""

import json
from pathlib import Path
import threading

from .site import DEFAULT_CONTENT_RATING, content_rating


class AppSettings:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()

    def load(self) -> dict:
        with self.lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            try:
                rating = content_rating(raw.get("contentRating"))
            except ValueError:
                rating = DEFAULT_CONTENT_RATING
            return {"contentRating": rating}

    def update(self, *, content_rating_value: object) -> dict:
        value = {"contentRating": content_rating(content_rating_value)}
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        return value
