"""Combined media/model/rating filters precede counting, ranking and pagination."""
import test_safety  # Refuse live application endpoints and isolate all test data.

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from discovery.history import HistoryArchive
from discovery.selection import GallerySelection
from discovery.models import base_labels, normalize_base_model

DAY = (datetime.now() - timedelta(days=1)).date().isoformat()


def item(i, username, kind, model, reactions, level=1):
    return dict(
        id=i,
        username=username,
        type=kind,
        baseModel=model,
        browsingLevel=level,
        createdAt=DAY + "T13:00:00Z",
        url="https://example.invalid/art",
        stats={"reactionCount": reactions},
    )


with tempfile.TemporaryDirectory(
    prefix="cad3-selection-test-", ignore_cleanup_errors=True
) as temporary:
    root = Path(temporary)
    archive = HistoryArchive(root / "history")
    items = [
        item(1, "Mixed", "image", "Foo", 1000),
        item(2, "Mixed", "video", "Foo", 1),
        item(3, "Mixed", "video", "Bar", 100),
        item(4, "VideoBest", "video", "Foo", 20),
        item(5, "AboveRating", "video", "Foo", 999, 8),
        item(6, "Compound", "video", "MiniMax H3YuE2", 2),
        item(7, "Unknown", "video", "Unknown", 2),
    ]
    items += [item(100 + i, f"Extra{i:02}", "video", "Foo", 0) for i in range(55)]
    archive._upsert_normalized(items, forced_date=DAY)
    with archive.connect() as db:
        db.execute(
            "INSERT INTO days(day,complete,content_rating) VALUES(?,1,?)", (DAY, "X")
        )
    archive.build_artist_index(DAY)
    original = archive.day_artist_keys(DAY)
    selection = GallerySelection(archive, DAY, ["Foo"], "video")
    rows = selection.day_artist_keys(DAY)
    assert len(rows) == 57 and rows[0]["username"] == "VideoBest", rows[:2]
    assert rows[1]["username"] == "Mixed" and rows[1]["imageCount"] == 1
    assert selection.creator_reaction_totals(DAY)["mixed"] == 1
    assert len(selection.artist_images(DAY, "Mixed")) == 1
    images_only = GallerySelection(archive, DAY, ["Foo"], "image")
    assert images_only.order == ["mixed"] and images_only.covers["mixed"] == 1
    both_models = GallerySelection(archive, DAY, ["Foo", "Bar"], "video")
    assert both_models.totals["mixed"] == 101 and len(both_models.groups["mixed"]) == 2
    all_media = GallerySelection(archive, DAY, ["Foo"], "all")
    assert all_media.totals["mixed"] == 1001 and len(all_media.groups["mixed"]) == 2
    assert all_media.order[0] == "mixed" and selection.order[0] == "videobest"
    assert len(GallerySelection(archive, DAY, ["Foo"], "video", [2]).groups) == 56
    assert archive.day_artist_keys(DAY) == original, (
        "filtered reads changed the saved artist index"
    )
    assert base_labels("MiniMax H3YuE2") == ("MiniMax H3", "YuE2")
    assert base_labels("NotReallyYuE2Model") == ("NotReallyYuE2Model",)
    assert normalize_base_model(["YuE2", "YuE2", "MiniMax H3"]) == "YuE2 | MiniMax H3"
    assert list(GallerySelection(archive, DAY, ["YuE2"], "video").groups) == [
        "compound"
    ]
    assert not GallerySelection(archive, DAY, ["Imaginary Resource"], "video").groups
    # An explicit filtered Popular order retains the existing own-creator pin.
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    import server
    from unittest.mock import patch

    with patch.object(server, "SETTINGS") as settings:
        settings.load.return_value = {
            "hideHighVolumeCreators": False,
            "highVolumeThreshold": 100,
        }
        ordered, total = server.day_view_order(
            DAY,
            "discovery",
            "Mixed",
            {"followed": set(), "reacted": {}},
            archive=selection,
        )
    assert ordered[0] == "mixed" and total == 57
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py"), "--port", str(port), "--no-browser"],
        cwd=ROOT,
        env={**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def get(path):
        return json.load(urllib.request.urlopen(base + path, timeout=10))

    try:
        for _ in range(80):
            try:
                get("/api/history/config")
                break
            except OSError:
                time.sleep(0.1)
        query = f"date={DAY}&segment=all&model=Foo&media=video&view=discovery&session=selection"
        first = get("/api/history/artists?" + query + "&offset=0&limit=50")
        second = get("/api/history/artists?" + query + "&offset=50&limit=50")
        assert (
            first["total"] == 57
            and first["imageCount"] == 57
            and len(first["artists"]) == 50
        ), first
        assert len(second["artists"]) == 7 and not second["hasMore"]
        assert get("/api/history/artists?" + query + "&offset=0&limit=50") == first
        names = [a["username"] for a in first["artists"] + second["artists"]]
        assert len(set(names)) == 57 and names[:2] == ["VideoBest", "Mixed"]
        assert first["artists"][1]["imageCount"] == 1
        assert first["artists"][1]["representativeIndex"] == 0
        assert (
            len(get("/api/history/artist?" + query + "&username=Mixed")["images"]) == 1
        )
        assert get("/api/history/day?" + query)["imageCount"] == 57
        facets = get(f"/api/history/models?date={DAY}&segment=all&media=video")[
            "models"
        ]
        assert "YuE2" in [entry["model"] for entry in facets]
        assert "MiniMax H3YuE2" not in [entry["model"] for entry in facets]
        for view in ["foryou", "emerging", "new", "followed"]:
            result = get(
                "/api/history/artists?"
                + query.replace("view=discovery", "view=" + view)
            )
            assert all(
                a["representative"]["type"] == "video" and a["imageCount"] == 1
                for a in result["artists"]
            )
        request = urllib.request.Request(
            base + "/api/settings",
            data=b'{"mediaType":"video"}',
            headers={"Content-Type": "application/json"},
        )
        assert json.load(urllib.request.urlopen(request))["mediaType"] == "video"
        assert get("/api/settings")["mediaType"] == "video"
        assert json.loads((root / "settings.json").read_text())["mediaType"] == "video"
    finally:
        process.terminate()
        process.wait(timeout=15)

print(
    {
        "combinedFilters": True,
        "countsAndPopularTotals": True,
        "pagination": True,
        "carousels": True,
        "canonicalModelLabels": True,
        "missingMetadataNotInvented": True,
        "preferencesPersisted": True,
    }
)
