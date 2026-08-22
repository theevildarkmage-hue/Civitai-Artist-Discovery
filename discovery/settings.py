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
            dim_seen_cards = raw.get("dimSeenCards", True)
            if not isinstance(dim_seen_cards, bool):
                dim_seen_cards = True
            check_for_updates = raw.get("checkForUpdates", True)
            if not isinstance(check_for_updates, bool):
                check_for_updates = True
            return {"contentRating": rating_for_levels(levels),
                    "browsingLevels": list(levels),
                    "dimSeenCards": dim_seen_cards,
                    "checkForUpdates": check_for_updates}

    def update(self, *, browsing_levels_value: object = None,
               content_rating_value: object = None,
               dim_seen_cards_value: object = None,
               check_for_updates_value: object = None) -> dict:
        current = self.load()
        if browsing_levels_value is not None:
            levels = browsing_levels(browsing_levels_value)
        elif content_rating_value is not None:
            levels = levels_for_rating(content_rating_value)
        else:
            levels = tuple(current["browsingLevels"])
        dim_seen_cards = (current["dimSeenCards"] if dim_seen_cards_value is None
                          else dim_seen_cards_value)
        if not isinstance(dim_seen_cards, bool):
            raise ValueError("dimSeenCards must be true or false")
        check_for_updates = (current["checkForUpdates"] if check_for_updates_value is None
                             else check_for_updates_value)
        if not isinstance(check_for_updates, bool):
            raise ValueError("checkForUpdates must be true or false")
        value = {"contentRating": rating_for_levels(levels),
                 "browsingLevels": list(levels),
                 "dimSeenCards": dim_seen_cards,
                 "checkForUpdates": check_for_updates}
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        return value
