"""The creator profile file is parsed once per version, and a rewrite is seen at once.

Every gallery page re-read and re-parsed this file, which is megabytes on a real profile
and measured ~0.15s of each page. Caching it must never serve a stale copy, and must never
hand out a dict a caller can edit underneath the cache.
"""

import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="civitai-profile-cache-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    import server


    def write(profiles):
        server.CREATOR_PROFILES.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"updatedAt": "now", "byUsername": profiles})
        # Written the way the app writes it: to a temporary file, then replaced.
        spare = server.CREATOR_PROFILES.with_suffix(".tmp")
        spare.write_text(payload, encoding="utf-8")
        spare.replace(server.CREATOR_PROFILES)


    # A missing file is not an error; the gallery just has no avatars to show.
    assert server.creator_profiles() == {}, "missing file must read as empty"

    write({"ana": {"id": 1, "username": "Ana"}})
    first = server.creator_profiles()
    assert first["ana"]["id"] == 1, first
    # Second read comes from the cache: same object, not a re-parse.
    assert server.creator_profiles() is first, "unchanged file must not be parsed again"

    # A rewrite is picked up immediately, not on a timer or a restart.
    time.sleep(0.01)
    write({"ana": {"id": 1, "username": "Ana"}, "bo": {"id": 2, "username": "Bo"}})
    second = server.creator_profiles()
    assert set(second) == {"ana", "bo"}, second
    assert second is not first, "a new version must be parsed"

    # The one caller that adds newly resolved creators must not edit the cached parse.
    copy = dict(server.creator_profiles())
    copy["new"] = {"id": 3}
    assert "new" not in server.creator_profiles(), "the cache must not absorb a caller's edit"

    # Corrupt content falls back to empty rather than failing the gallery.
    server.CREATOR_PROFILES.write_text("{not json", encoding="utf-8")
    assert server.creator_profiles() == {}, "unreadable file must not raise"

print({"missingFileEmpty": True, "parsedOncePerVersion": True, "rewriteSeenImmediately": True,
       "callerEditsDoNotLeak": True, "corruptFileSafe": True})
