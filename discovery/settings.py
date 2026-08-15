"""Small, atomic local preference store."""

import json
from pathlib import Path
import threading

from .site import (DEFAULT_CONTENT_RATING, browsing_levels, content_rating,
                   levels_for_rating, rating_for_levels)


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
            try:
                levels = browsing_levels(raw.get("browsingLevels"))
            except ValueError:
                levels = levels_for_rating(rating)
            return {"contentRating": rating_for_levels(levels),
                    "browsingLevels": list(levels)}

    def update(self, *, browsing_levels_value: object = None,
               content_rating_value: object = None) -> dict:
        levels = (levels_for_rating(content_rating_value) if browsing_levels_value is None
                  else browsing_levels(browsing_levels_value))
        value = {"contentRating": rating_for_levels(levels),
                 "browsingLevels": list(levels)}
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        return value
