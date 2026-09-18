"""The Time Machine serves videos the way the daily archive does.

It collected videos from the start -- the creator listing interleaves them -- but stored
no type and served every row as an image, so a card handed its MP4 to an <img>, which
shows nothing but alt text. Cards need the JPEG still (a plain width= transform on a
video returns another MP4) plus a separate transcoded file to play.
"""

from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.timemachine import PRIME_PAGE_SIZE, TimeMachine

CDN = "https://image.civitai.com/key/uuid"


def item(image_id, kind, name="Ana", day=1):
    extension = "mp4" if kind == "video" else "jpeg"
    return {"id": image_id, "postId": image_id, "username": name,
            "createdAt": f"2025-01-{day:02d}T00:00:00.000Z",
            "url": f"{CDN}/original=true/{image_id}.{extension}", "nsfwLevel": "None",
            "browsingLevel": 1, "width": 8, "height": 8, "type": kind,
            "stats": {"likeCount": 1}, "meta": None}


class FakeArchive:
    def __init__(self, pages):
        self.pages = pages
        self.content_rating = "Soft"
        self.visible_levels = (1, 2)

    def _request(self, params, **_):
        assert params["limit"] == PRIME_PAGE_SIZE, params
        items, nxt = self.pages.get((params["username"], params.get("cursor")), ([], None))
        return {"items": items, "metadata": {"nextCursor": nxt}}, 100


with tempfile.TemporaryDirectory(prefix="civitai-tm-video-", ignore_cleanup_errors=True) as temporary:
    root = Path(temporary)
    # A video first, so it is the creator's representative card.
    pages = {("Ana", None): ([item(1, "video"), item(2, "image", day=2)], None)}
    machine = TimeMachine(root, FakeArchive(pages))
    machine.fetch_page("Ana")

    card = next(entry for entry in machine.cards() if entry["username"] == "Ana")
    representative = card["representative"]
    assert representative["id"] == 1, representative
    assert representative["type"] == "video", representative
    # A still for the card: anim=false, not a width= transform that returns another MP4.
    assert "/anim=false,width=" in representative["thumbnailUrl"], representative
    assert representative["videoUrl"] and "transcode=true" in representative["videoUrl"], representative

    # The image behind it is untouched by any of this.
    machine.advance(["Ana"])
    still = next(entry for entry in machine.cards()
                 if entry["username"] == "Ana")["representative"]
    assert still["id"] == 2 and still["type"] == "image", still
    assert "/width=" in still["thumbnailUrl"] and "anim=false" not in still["thumbnailUrl"], still
    assert "videoUrl" not in still, still

    # The detail dialog needs the larger still and its own playback file.
    detail = machine.detail(1)
    assert detail["type"] == "video", detail
    assert "/anim=false,width=1280/" in detail["detailImageUrl"], detail
    assert "transcode=true,width=768" in detail["detailVideoUrl"], detail
    assert "detailVideoUrl" not in machine.detail(2), "an image needs no playback file"

# A store written before the type column existed keeps its positions and classes its
# existing rows by file extension, rather than refetching every creator's back catalogue.
with tempfile.TemporaryDirectory(prefix="civitai-tm-legacy-", ignore_cleanup_errors=True) as temporary:
    root = Path(temporary)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "timemachine.sqlite3")
    try:
        db.execute("""CREATE TABLE creator_images(
            username_key TEXT NOT NULL, position INTEGER NOT NULL, image_id INTEGER NOT NULL,
            username TEXT NOT NULL, created_at TEXT NOT NULL, url TEXT NOT NULL,
            browsing_level INTEGER NOT NULL DEFAULT 1, post_id INTEGER,
            width INTEGER, height INTEGER, base_model TEXT, stats TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(username_key, position))""")
        db.execute("""CREATE TABLE creator_progress(
            username_key TEXT PRIMARY KEY, username TEXT NOT NULL,
            next_position INTEGER NOT NULL DEFAULT 0, fetched INTEGER NOT NULL DEFAULT 0,
            cursor TEXT, exhausted INTEGER NOT NULL DEFAULT 0,
            primed_at TEXT, updated_at TEXT NOT NULL)""")
        for position, (image_id, extension) in enumerate([(1, "mp4"), (2, "jpeg"), (3, "webm")]):
            db.execute("INSERT INTO creator_images VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("ana", position, image_id, "Ana", "2025-01-01T00:00:00.000Z",
                        f"{CDN}/original=true/{image_id}.{extension}", 1, image_id, 8, 8, None, "{}"))
        db.execute("INSERT INTO creator_progress(username_key, username, fetched, exhausted, "
                   "primed_at, updated_at) VALUES('ana','Ana',3,1,'2025-01-01','2025-01-01')")
        db.commit()
    finally:
        db.close()

    machine = TimeMachine(root, FakeArchive({}))
    stored = {}
    with machine.connect() as db:
        for row in db.execute("SELECT image_id, type FROM creator_images"):
            stored[row["image_id"]] = row["type"]
    assert stored == {1: "video", 2: "image", 3: "video"}, stored
    assert machine.detail(1)["type"] == "video"
    assert "/anim=false,width=" in machine.detail(1)["thumbnailUrl"]
    assert machine.detail(2)["type"] == "image"

print({"videoStillForCards": True, "playbackUrl": True, "imagesUnchanged": True,
       "detailPayload": True, "legacyRowsBackfilled": True})
