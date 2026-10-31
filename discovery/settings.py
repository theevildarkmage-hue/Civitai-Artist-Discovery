"""Small, atomic local preference store."""

import json
from pathlib import Path
import secrets
import threading

from .selection import media_type
from .site import (CONTENT_RATINGS, DEFAULT_CONTENT_RATING, browsing_levels, content_rating,
                   levels_for_rating, rating_for_levels)

HIGH_VOLUME_THRESHOLDS = (50, 100, 150, 200)
EMERGING_REACTION_MODES = ("balanced", "strict", "unadjusted")
EMERGING_REACTION_LIMITS = (0, 100, 250, 500)
# Civitai's feed can only be paged back a couple of days, so a day not captured inside
# that window is lost for good. Twelve hours leaves roughly four times the margin the
# narrowest coverage allows; twenty-four leaves about two.
AUTO_CAPTURE_INTERVALS = (12, 24)


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
            apply_image_filters = raw.get("applyImageFilters", True)
            if not isinstance(apply_image_filters, bool):
                apply_image_filters = True
            apply_account_filters = raw.get("applyAccountFilters", True)
            if not isinstance(apply_account_filters, bool):
                apply_account_filters = True
            apply_tag_filters = raw.get("applyTagFilters", True)
            if not isinstance(apply_tag_filters, bool):
                apply_tag_filters = True
            dim_seen_cards = raw.get("dimSeenCards", True)
            if not isinstance(dim_seen_cards, bool):
                dim_seen_cards = True
            # Videos in the details view play with sound unless the user turns it off.
            detail_video_sound = raw.get("detailVideoSound", True)
            if not isinstance(detail_video_sound, bool):
                detail_video_sound = True
            check_for_updates = raw.get("checkForUpdates", True)
            if not isinstance(check_for_updates, bool):
                check_for_updates = True
            hide_high_volume = raw.get("hideHighVolumeCreators", True)
            if not isinstance(hide_high_volume, bool):
                hide_high_volume = True
            high_volume_threshold = raw.get("highVolumeThreshold", 50)
            if high_volume_threshold not in HIGH_VOLUME_THRESHOLDS:
                high_volume_threshold = 50
            emerging_reaction_mode = raw.get("emergingReactionMode", "balanced")
            if emerging_reaction_mode not in EMERGING_REACTION_MODES:
                emerging_reaction_mode = "balanced"
            # Zero means that Strict discovery keeps its ranking behavior without
            # hiding creators by reaction count. A cutoff is an explicit opt-in.
            emerging_reaction_limit = raw.get("emergingReactionLimit", 0)
            if emerging_reaction_limit not in EMERGING_REACTION_LIMITS:
                emerging_reaction_limit = 0
            auto_capture = raw.get("autoCapture", True)
            if not isinstance(auto_capture, bool):
                auto_capture = True
            auto_capture_hours = raw.get("autoCaptureHours", 12)
            if auto_capture_hours not in AUTO_CAPTURE_INTERVALS:
                auto_capture_hours = 12
            # A stable per-install number, so this copy of the app always picks the same
            # slot in the interval but a different one from everybody else's copy.
            # None means "pick a slot automatically", which spreads installs across the
            # interval. A number is a local minute past midnight the user chose instead.
            capture_minute = raw.get("autoCaptureMinute")
            if not isinstance(capture_minute, int) or not 0 <= capture_minute < 1440:
                capture_minute = None
            seed = raw.get("captureSeed")
            if not isinstance(seed, int) or not 0 <= seed < 2**31:
                seed = secrets.randbelow(2**31)
            allow_lan_access = raw.get("allowLanAccess", False)
            if not isinstance(allow_lan_access, bool):
                allow_lan_access = False
            # What unattended collection downloads, kept apart from the viewing filter.
            # A day is stamped with the coverage it was collected at and can only widen by
            # being rebuilt, so letting the viewing filter decide this meant narrowing what
            # you look at today permanently narrowed what tomorrow's archive could ever
            # contain. Collect widely once; filter what is shown as often as you like.
            #
            # Owner-approved beta defaults: unset coverage collects all ratings.
            # Explicit saved coverage and viewing levels remain independent.
            capture_coverage = raw.get("captureCoverage")
            if capture_coverage not in CONTENT_RATINGS:
                capture_coverage = "X"
            return {"mediaType": raw.get("mediaType") if raw.get("mediaType") in ("all", "image", "video") else "all",
                    "contentRating": rating_for_levels(levels),
                    "browsingLevels": list(levels),
                    "applyTagFilters": apply_tag_filters,
                    "applyAccountFilters": apply_account_filters,
                    "applyImageFilters": apply_image_filters,
                    "dimSeenCards": dim_seen_cards,
                    "detailVideoSound": detail_video_sound,
                    "checkForUpdates": check_for_updates,
                    "hideHighVolumeCreators": hide_high_volume,
                    "highVolumeThreshold": high_volume_threshold,
                    "emergingReactionMode": emerging_reaction_mode,
                    "emergingReactionLimit": emerging_reaction_limit,
                    "autoCapture": auto_capture,
                    "autoCaptureHours": auto_capture_hours,
                    "autoCaptureMinute": capture_minute,
                    "captureSeed": seed,
                    "allowLanAccess": allow_lan_access,
                    "captureCoverage": capture_coverage}

    def transactional_update(self, *, effect=None, **changes) -> dict:
        """Restore the exact prior preference file if a dependent update fails."""
        with self.lock:
            previous = self.path.read_bytes() if self.path.exists() else None
            try:
                value = self._update(**changes)
                if effect is not None:
                    effect(value)
                return value
            except Exception:
                if previous is None:
                    self.path.unlink(missing_ok=True)
                else:
                    temporary = self.path.with_suffix(".tmp")
                    temporary.write_bytes(previous)
                    temporary.replace(self.path)
                raise

    def update(self, **changes) -> dict:
        # The entire read/modify/write must be one transaction. Locking just the
        # final replace lets concurrent filter/settings requests lose each other.
        with self.lock:
            return self._update(**changes)

    def _update(self, *, media_type_value: object = None, browsing_levels_value: object = None,
               content_rating_value: object = None,
               apply_tag_filters_value: object = None,
               apply_account_filters_value: object = None,
               apply_image_filters_value: object = None,
               dim_seen_cards_value: object = None,
               detail_video_sound_value: object = None,
               check_for_updates_value: object = None,
               hide_high_volume_creators_value: object = None,
               high_volume_threshold_value: object = None,
               emerging_reaction_mode_value: object = None,
               emerging_reaction_limit_value: object = None,
               auto_capture_value: object = None,
               auto_capture_hours_value: object = None,
               auto_capture_minute_value: object = "keep",
               allow_lan_access_value: object = None,
               capture_coverage_value: object = None) -> dict:
        current = self.load()
        if browsing_levels_value is not None:
            levels = browsing_levels(browsing_levels_value)
        elif content_rating_value is not None:
            levels = levels_for_rating(content_rating_value)
        else:
            levels = tuple(current["browsingLevels"])
        apply_image_filters = current["applyImageFilters"] if apply_image_filters_value is None else apply_image_filters_value
        if not isinstance(apply_image_filters, bool):
            raise ValueError("applyImageFilters must be true or false")
        apply_account_filters = (current["applyAccountFilters"] if apply_account_filters_value is None
                                 else apply_account_filters_value)
        if not isinstance(apply_account_filters, bool):
            raise ValueError("applyAccountFilters must be true or false")
        apply_tag_filters = (current["applyTagFilters"] if apply_tag_filters_value is None
                             else apply_tag_filters_value)
        if not isinstance(apply_tag_filters, bool):
            raise ValueError("applyTagFilters must be true or false")
        dim_seen_cards = (current["dimSeenCards"] if dim_seen_cards_value is None
                          else dim_seen_cards_value)
        if not isinstance(dim_seen_cards, bool):
            raise ValueError("dimSeenCards must be true or false")
        detail_video_sound = (current["detailVideoSound"] if detail_video_sound_value is None
                              else detail_video_sound_value)
        if not isinstance(detail_video_sound, bool):
            raise ValueError("detailVideoSound must be true or false")
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
            raise ValueError("highVolumeThreshold must be 50, 100, 150, or 200")
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
        auto_capture = (current["autoCapture"] if auto_capture_value is None
                        else auto_capture_value)
        if not isinstance(auto_capture, bool):
            raise ValueError("autoCapture must be true or false")
        auto_capture_hours = (current["autoCaptureHours"] if auto_capture_hours_value is None
                              else auto_capture_hours_value)
        if auto_capture_hours not in AUTO_CAPTURE_INTERVALS:
            raise ValueError("autoCaptureHours must be 12 or 24")
        capture_minute = (current["autoCaptureMinute"] if auto_capture_minute_value == "keep"
                          else auto_capture_minute_value)
        if capture_minute is not None and (not isinstance(capture_minute, int)
                                           or not 0 <= capture_minute < 1440):
            raise ValueError("autoCaptureMinute must be null or a minute of the day")
        seed = current["captureSeed"]
        allow_lan_access = (current["allowLanAccess"] if allow_lan_access_value is None
                            else allow_lan_access_value)
        if not isinstance(allow_lan_access, bool):
            raise ValueError("allowLanAccess must be true or false")
        capture_coverage = (current["captureCoverage"] if capture_coverage_value is None
                            else capture_coverage_value)
        if capture_coverage not in CONTENT_RATINGS:
            raise ValueError("captureCoverage must be Soft, Mature, or X")
        value = {"mediaType": media_type(current["mediaType"] if media_type_value is None else media_type_value),
                 "contentRating": rating_for_levels(levels),
                 "browsingLevels": list(levels),
                 "applyTagFilters": apply_tag_filters,
                 "applyAccountFilters": apply_account_filters,
                 "applyImageFilters": apply_image_filters,
                 "dimSeenCards": dim_seen_cards,
                 "detailVideoSound": detail_video_sound,
                 "checkForUpdates": check_for_updates,
                 "hideHighVolumeCreators": hide_high_volume,
                 "highVolumeThreshold": high_volume_threshold,
                 "emergingReactionMode": emerging_reaction_mode,
                 "emergingReactionLimit": emerging_reaction_limit,
                 "autoCapture": auto_capture,
                 "autoCaptureHours": auto_capture_hours,
                 "autoCaptureMinute": capture_minute,
                 "captureSeed": seed,
                 "allowLanAccess": allow_lan_access,
                 "captureCoverage": capture_coverage}
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        return value
