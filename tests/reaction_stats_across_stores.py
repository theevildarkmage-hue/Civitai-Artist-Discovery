"""Reacting reports Civitai's live counts and writes through every store holding the image.

An image collected by both the daily archive and the Time Machine carries two snapshots,
taken whenever each collector fetched it. Reacting used to answer with whichever store was
checked first, so a card built from the other one jumped to an older, lower count.
"""

import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="civitai-reaction-stores-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    import server

    # Civitai's own shape: counts suffixed AllTime, and no total.
    live = server.live_reaction_stats({"stats": {
        "likeCountAllTime": 22, "heartCountAllTime": 6, "laughCountAllTime": 0,
        "cryCountAllTime": 1, "dislikeCountAllTime": 0, "commentCountAllTime": 3,
        "viewCountAllTime": 900}})
    assert live["likeCount"] == 22 and live["heartCount"] == 6, live
    # The total counts reactions only: comments and views are not reactions.
    assert live["reactionCount"] == 29, live
    assert server.live_reaction_stats({}) == {}, "no stats means no claim about counts"
    assert server.live_reaction_stats({"stats": {"viewCountAllTime": 5}}) == {}, "views alone are not counts"

    class FakeStore:
        def __init__(self, stats, held=True):
            self.saved, self._stats, self.held = None, dict(stats), held

        def has_image(self, image_id):
            return self.held

        def stats(self, image_id):
            return dict(self._stats)

        def update_stats(self, image_id, stats):
            self._stats = dict(stats)
            self.saved = dict(stats)

    # The archive holds the stale copy; the Time Machine a newer one.
    archive = FakeStore({"likeCount": 13, "heartCount": 2, "reactionCount": 16})
    machine = FakeStore({"likeCount": 22, "heartCount": 6, "reactionCount": 29})
    server.HISTORY, server.TIME_MACHINE = archive, machine
    assert server.image_stores(1) == [archive, machine], "both copies must be found"

    # Both stores end up holding the live counts plus this reaction, so neither can serve
    # the older number afterwards.
    stats = {**server.image_stores(1)[0].stats(1), **live}
    stats["likeCount"] += 1
    stats["reactionCount"] += 1
    for store in server.image_stores(1):
        store.update_stats(1, {**store.stats(1), **stats})
    assert archive.saved["likeCount"] == 23 and machine.saved["likeCount"] == 23, (archive.saved, machine.saved)
    assert archive.saved["reactionCount"] == 30 and machine.saved["reactionCount"] == 30
    # Fields Civitai did not report are preserved rather than dropped.
    assert archive.saved["heartCount"] == 6, archive.saved

    # An image only one collector holds still works.
    machine.held = False
    assert server.image_stores(1) == [archive], server.image_stores(1)

print({"liveCountsPreferred": True, "totalExcludesComments": True, "missingStatsIgnored": True,
       "writesThroughEveryStore": True, "singleStoreStillWorks": True})
