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

    assert settings.load() == {"contentRating": "Soft", "browsingLevels": [1, 2]}

    path.write_text(json.dumps({"contentRating": "X"}), encoding="utf-8")
    assert settings.load() == {"contentRating": "X", "browsingLevels": [1, 2, 4, 8, 16]}

    saved = settings.update(browsing_levels_value=[16, 4])
    assert saved == {"contentRating": "X", "browsingLevels": [4, 16]}
    assert settings.load() == saved

    try:
        settings.update(browsing_levels_value=[])
        raise AssertionError("an empty browsing-level selection was saved")
    except ValueError as error:
        assert "at least one" in str(error), error

print({"safeDefault": [1, 2], "legacySettingMigrates": True,
       "independentLevelsPersist": True, "emptySelectionRejected": True})
