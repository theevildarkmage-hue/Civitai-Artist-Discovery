"""Browsing-level preferences migrate safely and preserve independent selections."""

import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from discovery.settings import AppSettings


with tempfile.TemporaryDirectory(prefix="civitai-level-settings-") as temporary:
    path = Path(temporary) / "settings.json"
    settings = AppSettings(path)

    # The capture slot seed is random per install, so copies of the app do not all reach
    # Civitai at the same moment. It has no fixed value to compare against, so check its
    # shape and drop it: these assertions are about the preference contract.
    def view(value):
        assert isinstance(value.get("captureSeed"), int), value
        return {key: item for key, item in value.items() if key != "captureSeed"}

    gallery_defaults = {"hideHighVolumeCreators": False, "highVolumeThreshold": 100,
                        "emergingReactionMode": "balanced", "emergingReactionLimit": 0,
                        "autoCapture": False, "autoCaptureHours": 12,
                        "autoCaptureMinute": None}

    assert view(settings.load()) == {"contentRating": "Soft", "browsingLevels": [1, 2],
                               "dimSeenCards": True, "checkForUpdates": True,
                               **gallery_defaults}

    path.write_text(json.dumps({"contentRating": "X"}), encoding="utf-8")
    assert view(settings.load()) == {"contentRating": "X", "browsingLevels": [1, 2, 4, 8, 16],
                               "dimSeenCards": True, "checkForUpdates": True,
                               **gallery_defaults}

    saved = settings.update(browsing_levels_value=[16, 4])
    assert view(saved) == {"contentRating": "X", "browsingLevels": [4, 16],
                     "dimSeenCards": True, "checkForUpdates": True, **gallery_defaults}
    assert settings.load() == saved

    saved = settings.update(dim_seen_cards_value=False)
    assert view(saved) == {"contentRating": "X", "browsingLevels": [4, 16],
                     "dimSeenCards": False, "checkForUpdates": True, **gallery_defaults}
    saved = settings.update(check_for_updates_value=False)
    assert view(saved) == {"contentRating": "X", "browsingLevels": [4, 16],
                     "dimSeenCards": False, "checkForUpdates": False, **gallery_defaults}
    saved = settings.update(hide_high_volume_creators_value=True,
                            high_volume_threshold_value=200,
                            emerging_reaction_mode_value="strict",
                            emerging_reaction_limit_value=500)
    gallery_saved = {"hideHighVolumeCreators": True, "highVolumeThreshold": 200,
                     "emergingReactionMode": "strict", "emergingReactionLimit": 500,
                     "autoCapture": False, "autoCaptureHours": 12,
                     "autoCaptureMinute": None}
    saved = settings.update(content_rating_value="Soft")
    assert view(saved) == {"contentRating": "Soft", "browsingLevels": [1, 2],
                     "dimSeenCards": False, "checkForUpdates": False, **gallery_saved}

    try:
        settings.update(browsing_levels_value=[])
        raise AssertionError("an empty browsing-level selection was saved")
    except ValueError as error:
        assert "at least one" in str(error), error

print({"safeDefault": [1, 2], "legacySettingMigrates": True,
       "independentLevelsPersist": True, "dimmingPreferencePersists": True,
       "updatePreferencePersists": True, "galleryPreferencesPersist": True,
       "emptySelectionRejected": True})
