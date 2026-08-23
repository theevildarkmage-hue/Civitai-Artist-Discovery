"""Small, atomic local preference store."""

import json
from pathlib import Path
import threading

from .site import (DEFAULT_CONTENT_RATING, browsing_levels, content_rating,
                   levels_for_rating, rating_for_levels)

HIGH_VOLUME_THRESHOLDS = (50, 100, 200)
EMERGING_REACTION_MODES = ("balanced", "strict", "unadjusted")
EMERGING_REACTION_LIMITS = (0, 100, 250, 500)


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
            hide_high_volume = raw.get("hideHighVolumeCreators", False)
            if not isinstance(hide_high_volume, bool):
                hide_high_volume = False
            high_volume_threshold = raw.get("highVolumeThreshold", 100)
            if high_volume_threshold not in HIGH_VOLUME_THRESHOLDS:
                high_volume_threshold = 100
            emerging_reaction_mode = raw.get("emergingReactionMode", "balanced")
            if emerging_reaction_mode not in EMERGING_REACTION_MODES:
                emerging_reaction_mode = "balanced"
            # Zero means that Strict discovery keeps its ranking behavior without
            # hiding creators by reaction count. A cutoff is an explicit opt-in.
            emerging_reaction_limit = raw.get("emergingReactionLimit", 0)
            if emerging_reaction_limit not in EMERGING_REACTION_LIMITS:
                emerging_reaction_limit = 0
            return {"contentRating": rating_for_levels(levels),
                    "browsingLevels": list(levels),
                    "dimSeenCards": dim_seen_cards,
                    "checkForUpdates": check_for_updates,
                    "hideHighVolumeCreators": hide_high_volume,
                    "highVolumeThreshold": high_volume_threshold,
                    "emergingReactionMode": emerging_reaction_mode,
                    "emergingReactionLimit": emerging_reaction_limit}

    def update(self, *, browsing_levels_value: object = None,
               content_rating_value: object = None,
               dim_seen_cards_value: object = None,
               check_for_updates_value: object = None,
               hide_high_volume_creators_value: object = None,
               high_volume_threshold_value: object = None,
               emerging_reaction_mode_value: object = None,
               emerging_reaction_limit_value: object = None) -> dict:
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
        hide_high_volume = (current["hideHighVolumeCreators"]
                            if hide_high_volume_creators_value is None
                            else hide_high_volume_creators_value)
        if not isinstance(hide_high_volume, bool):
            raise ValueError("hideHighVolumeCreators must be true or false")
        high_volume_threshold = (current["highVolumeThreshold"]
                                 if high_volume_threshold_value is None
                                 else high_volume_threshold_value)
        if high_volume_threshold not in HIGH_VOLUME_THRESHOLDS:
            raise ValueError("highVolumeThreshold must be 50, 100, or 200")
        emerging_reaction_mode = (current["emergingReactionMode"]
                                  if emerging_reaction_mode_value is None
                                  else emerging_reaction_mode_value)
        if emerging_reaction_mode not in EMERGING_REACTION_MODES:
            raise ValueError("emergingReactionMode must be balanced, strict, or unadjusted")
        emerging_reaction_limit = (current["emergingReactionLimit"]
                                   if emerging_reaction_limit_value is None
                                   else emerging_reaction_limit_value)
        if emerging_reaction_limit not in EMERGING_REACTION_LIMITS:
            raise ValueError("emergingReactionLimit must be none, 100, 250, or 500")
        value = {"contentRating": rating_for_levels(levels),
                 "browsingLevels": list(levels),
                 "dimSeenCards": dim_seen_cards,
                 "checkForUpdates": check_for_updates,
                 "hideHighVolumeCreators": hide_high_volume,
                 "highVolumeThreshold": high_volume_threshold,
                 "emergingReactionMode": emerging_reaction_mode,
                 "emergingReactionLimit": emerging_reaction_limit}
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        return value
