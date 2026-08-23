"""Gallery preferences filter locally and give Emerging First distinct modes."""

from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="gallery-preferences-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    import server
    from discovery.history import HistoryArchive
    from discovery.settings import AppSettings

    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    history = HistoryArchive(Path(temporary) / "history")
    server.HISTORY = history
    server.SETTINGS = AppSettings(Path(temporary) / "settings.json")

    profiles = {
        "batch": [1] * 100,
        "breakout": [80, 70, 60, 50, 40],
        "quality": [30, 25, 15, 10, 5],
        "quiet": [4, 3, 2, 1, 0],
    }
    items = []
    image_id = 1
    for creator, reactions in profiles.items():
        for index, reaction_count in enumerate(reactions):
            items.append({"id": image_id, "postId": image_id, "username": creator,
                "createdAt": f"{day}T12:{index % 60:02d}:00Z",
                "url": "https://image.civitai.com/example.jpeg", "width": 8, "height": 8,
                "type": "image", "nsfwLevel": "None",
                "stats": {"reactionCount": reaction_count}})
            image_id += 1
    history._upsert_normalized(items, forced_date=day)
    with history.connect() as db:
        db.execute("INSERT INTO days(day,complete,updated_at) VALUES(?,1,?)",
                   (day, datetime.now().isoformat()))
    history.build_artist_index(day)

    class Followers:
        def follower_counts(self, names):
            return {name.casefold(): 100 for name in names}

    real_taste = server.TASTE
    server.TASTE = Followers()
    signals = {"followed": set(), "reacted": {}}
    try:
        balanced, _ = server.day_view_order(day, "emerging", None, signals)
        assert balanced[0] == "quality", balanced

        server.SETTINGS.update(emerging_reaction_mode_value="unadjusted")
        unadjusted, _ = server.day_view_order(day, "emerging", None, signals)
        assert unadjusted[0] == "breakout", unadjusted

        server.SETTINGS.update(emerging_reaction_mode_value="strict",
                               emerging_reaction_limit_value=250)
        strict, strict_total = server.day_view_order(day, "emerging", None, signals)
        assert "breakout" not in strict and strict_total == 3, strict

        # Strict ranking is useful without necessarily excluding anyone. "None" is
        # represented by zero and is the safe default until a cutoff is chosen.
        server.SETTINGS.update(emerging_reaction_limit_value=0)
        strict_without_cutoff, strict_without_cutoff_total = \
            server.day_view_order(day, "emerging", None, signals)
        assert "breakout" in strict_without_cutoff
        assert strict_without_cutoff_total == 4

        server.SETTINGS.update(hide_high_volume_creators_value=True,
                               high_volume_threshold_value=100)
        popular, popular_total = server.day_view_order(day, "discovery", None, signals)
        assert "batch" not in popular and popular_total == 3, popular
        followed, followed_total = server.day_view_order(day, "followed", None, signals)
        assert "batch" not in followed and followed_total == 3, followed
    finally:
        server.TASTE = real_taste

app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
assert 'id="galleryPreferences"' in app and 'id="preferencesMenu"' in app
assert 'id="prefDimSeen"' in app and 'id="seenDimming"' not in app

print({"cogPanel": True, "dimmingMoved": True, "hundredPlusHidden": True,
       "balancedDemotesBreakout": True, "strictFiltersReactionLimit": True,
       "strictDefaultsToNoCutoff": True, "unadjustedPreservesPopularity": True})
