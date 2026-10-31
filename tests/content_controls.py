"""The account's Civitai Content Controls are mirrored and obeyed.

Civitai lets people hide creators and topics. Ignoring that in a gallery built from
Civitai's own artwork puts back exactly what the user asked not to see, so the settings
are imported and enforced on every view rather than treated as advisory.
"""

import test_safety  # Fail closed before any test network request.

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = 8896

HIDDEN = {
    "__v": 2,
    "hiddenTags": [{"id": 1, "name": "furry", "hidden": True},
                   {"id": 2, "name": "gore", "hidden": True},
                   # A row Civitai marks as not hidden must be ignored.
                   {"id": 3, "name": "landscape", "hidden": False}],
    "hiddenUsers": [{"id": 501, "username": "HiddenArtist", "hidden": True}],
    "blockedByUsers": [{"id": 502, "username": "BlockedMe", "hidden": True}],
    "hiddenImages": [9302],
    "hiddenModels": [], "hiddenModel3Ds": [], "hiddenImagesImplicit": [], "blockedUsers": [],
}

with tempfile.TemporaryDirectory(prefix="civitai-controls-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    import discovery.taste as taste
    from discovery.history import HistoryArchive
    from discovery.taste import TasteStore
    from discovery.timemachine import TimeMachine

    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='8'%3E%3C/svg%3E"
    history = HistoryArchive(Path(temporary) / "history")

    def image(image_id, username, minute):
        return {"id": image_id, "postId": image_id, "username": username,
                "createdAt": f"{day}T13:{minute:02d}:00Z", "url": pixel, "width": 8, "height": 8,
                "type": "image", "nsfwLevel": "None", "stats": {"reactionCount": 1}}

    history._upsert_normalized([
        image(9101, "VisibleArtist", 10),
        image(9201, "HiddenArtist", 20),      # hidden creator: never shown
        image(9251, "BlockedMe", 25),         # blocked this account: never shown
        image(9301, "TaggedArtist", 30),      # newest, but carries a hidden tag
        image(9302, "TaggedArtist", 29),      # hidden outright by id
        image(9303, "TaggedArtist", 28),      # the one that should be shown
        image(9401, "AllHiddenArtist", 40),   # every image hidden -> no card at all
    ], forced_date=day)
    with history.connect() as db:
        db.execute("INSERT INTO days(day,complete,updated_at) VALUES(?,1,?)",
                   (day, datetime.now().isoformat()))
    history.build_artist_index(day)

    store = TasteStore(Path(temporary) / "discovery")
    # Tags come from the archive sweep, which is what makes tag hiding possible at all.
    with store.connect() as db:
        db.executemany("INSERT INTO archive_image_tags(image_id, tag_name) VALUES(?,?)",
                       [(9301, "furry"), (9401, "gore"), (9101, "landscape")])

    taste.SocialClient.query = lambda self, procedure, payload: (
        HIDDEN if procedure == "hiddenPreferences.getHidden" else {})
    imported = store.import_hidden_preferences()
    assert imported == {"creators": 2, "tags": 2, "images": 1}, imported

    # Only genuinely hidden rows are stored; "landscape" was marked hidden: False.
    assert store.hidden_tag_names() == {"furry", "gore"}, store.hidden_tag_names()
    assert store.hidden_creator_keys() == {"hiddenartist", "blockedme"}, store.hidden_creator_keys()
    assert store.hidden_image_ids() == {9301, 9302, 9401}, store.hidden_image_ids()

    # Re-importing replaces rather than merges, so unhiding on Civitai takes effect here.
    taste.SocialClient.query = lambda self, procedure, payload: (
        {**HIDDEN, "hiddenUsers": [], "hiddenTags": []}
        if procedure == "hiddenPreferences.getHidden" else {})
    store.import_hidden_preferences()
    assert store.hidden_creator_keys() == {"blockedme"}, store.hidden_creator_keys()
    assert store.hidden_tag_names() == set(), store.hidden_tag_names()
    taste.SocialClient.query = lambda self, procedure, payload: (
        HIDDEN if procedure == "hiddenPreferences.getHidden" else {})
    store.import_hidden_preferences()

    # Real Time Machine HTTP reads must use the same saved switches as Daily.
    machine = TimeMachine(Path(temporary) / 'discovery', history, store)
    with machine.connect() as db:
        for name, ids in [('VisibleArtist', [9101]), ('HiddenArtist', [9201]),
                          ('BlockedMe', [9251]), ('TaggedArtist', [9301, 9302, 9303]),
                          ('AllHiddenArtist', [9401])]:
            db.execute('INSERT INTO creator_progress(username_key,username,updated_at,exhausted) '
                       'VALUES(?,?,?,1)', (name.casefold(), name, day))
            for position, image_id in enumerate(ids):
                db.execute('INSERT INTO creator_images(username_key,position,image_id,username,created_at,url) '
                           'VALUES(?,?,?,?,?,?)', (name.casefold(), position, image_id, name, day, pixel))

    # Rating votes are not personal hidden tags or direct image hides.
    rating_controls = {**HIDDEN, 'hiddenTags': HIDDEN['hiddenTags'] + [
        {'id': 99, 'name': 'rating-topic', 'nsfwLevel': 16}],
        'hiddenImagesImplicit': [{'id': 9303, 'tagId': 99}]}
    taste.SocialClient.query = lambda *args: rating_controls
    store.import_hidden_preferences()
    assert 'rating-topic' not in store.hidden_tag_names()
    assert 9303 in store.hidden_image_ids(levels=(1, 2), include_tags=False)
    assert 9303 not in store.hidden_image_ids(levels=(1, 2, 4, 8, 16))
    assert 9302 not in store.hidden_image_ids(include_images=False)
    taste.SocialClient.query = lambda *args: HIDDEN
    store.import_hidden_preferences()

    env = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    process = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(PORT),
                                "--no-browser"], cwd=ROOT, env=env)
    try:
        deadline = time.monotonic() + 25
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/history/config", timeout=1).read()
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(.2)

        def get(path):
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=30) as response:
                return json.loads(response.read())

        def post(path, body):
            request = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}",
                data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())

        def tm_ids():
            return {card['username']: card['representative']['id']
                    for card in get('/api/timemachine')['cards']}

        def tm_history(name):
            return [image['id'] for image in get(
                f'/api/timemachine/creator?username={name}&savedOnly=1')['images']]

        assert tm_ids() == {'VisibleArtist': 9101, 'TaggedArtist': 9303}
        assert tm_history('TaggedArtist') == [9303]
        assert tm_history('HiddenArtist') == []
        assert tm_history('BlockedMe') == []
        post('/api/settings', {'applyTagFilters': False})
        assert tm_ids()['TaggedArtist'] == 9301
        assert tm_ids()['AllHiddenArtist'] == 9401
        assert tm_history('TaggedArtist') == [9301, 9303]
        post('/api/settings', {'applyAccountFilters': False})
        assert tm_ids()['HiddenArtist'] == 9201 and tm_ids()['BlockedMe'] == 9251
        assert tm_history('HiddenArtist') == [9201]
        assert tm_history('BlockedMe') == [9251]
        post('/api/settings', {'applyImageFilters': False})
        assert tm_history('TaggedArtist') == [9301, 9302, 9303]
        post('/api/settings', {'applyTagFilters': True, 'applyAccountFilters': True,
                               'applyImageFilters': True})
        assert tm_ids() == {'VisibleArtist': 9101, 'TaggedArtist': 9303}
        assert tm_history('TaggedArtist') == [9303]
        with machine.connect() as db:
            assert db.execute('SELECT count(*) FROM creator_images').fetchone()[0] == 7
            assert db.execute('SELECT sum(next_position) FROM creator_progress').fetchone()[0] == 0

        # Every view, including the plain default, leaves hidden creators out.
        for view in ("discovery", "foryou", "followed", "new", "emerging"):
            page = get(f"/api/history/artists?date={day}&segment=all&view={view}&offset=0&limit=50")
            names = {artist["username"] for artist in page["artists"]}
            assert "HiddenArtist" not in names, (view, names)
            assert "BlockedMe" not in names, (view, names)
            # A creator whose every image is hidden has nothing left to show.
            assert "AllHiddenArtist" not in names, (view, names)
            assert "VisibleArtist" in names, (view, names)

        # The card opens on an image that is not hidden, rather than being dropped.
        page = get(f"/api/history/artists?date={day}&segment=all&view=discovery&offset=0&limit=50")
        tagged = next(a for a in page["artists"] if a["username"] == "TaggedArtist")
        assert tagged["representative"]["id"] == 9303, tagged["representative"]

        # The carousel excludes hidden artwork too.
        images = get(f"/api/history/artist?date={day}&segment=all"
                     f"&username={urllib.parse.quote('TaggedArtist')}")["images"]
        assert [item["id"] for item in images] == [9303], images

        # The count on screen matches what can actually be scrolled to, and says why.
        summary = get(f"/api/history/day?date={day}&segment=all")
        # Three creators are gone: two hidden by name, and one whose every image is
        # hidden. All three are equally absent, so all three are reported as hidden.
        assert summary["hiddenCreators"] == 3, summary
        assert summary["artistCount"] == len(get(
            f"/api/history/artists?date={day}&segment=all&view=discovery&offset=0&limit=50")["artists"]), summary

        # Tags are inspectable on the image itself, marked when the account hides them,
        # so the reason something is filtered is visible rather than mysterious.
        tagged = get(f"/api/history/image?id=9301")
        assert tagged["known"] is True, tagged
        assert tagged["tags"] == [{"name": "furry", "hidden": True}], tagged["tags"]
        plain = get(f"/api/history/image?id=9101")
        assert plain["tags"] == [{"name": "landscape", "hidden": False}], plain["tags"]
        # An image whose tags were never read is a different state from one with none,
        # and must not be presented as "no tags".
        unread = get(f"/api/history/image?id=9303")
        assert unread["known"] is False and unread["tags"] == [], unread

        # The mirror is reportable, so the UI can explain what it removed.
        state = get("/api/discovery/hidden")
        assert state["creators"] == 2 and state["tags"] == 2, state
        assert state["importedAt"], state

        # Changing exact browsing levels invalidates the visible-creator cache. The cache
        # must retain its required shape or accounts with hidden images get a 500 and a
        # completely blank gallery immediately after using the selector.
        changed = post("/api/settings", {"browsingLevels": [1]})
        assert changed["browsingLevels"] == [1], changed
        after_filter = get(f"/api/history/day?date={day}&segment=all")
        assert after_filter["artistCount"] == summary["artistCount"], after_filter
        assert get(f"/api/history/artists?date={day}&segment=all&view=discovery&offset=0&limit=50")["artists"]

        # The two local viewing switches are independent and never erase the
        # imported rules. Explicitly hidden images remain hidden in every mode.
        before_controls = get('/api/discovery/hidden')
        post('/api/settings', {'applyTagFilters': False})
        for view in ('discovery', 'foryou', 'followed', 'new', 'emerging'):
            rows = get(f'/api/history/artists?date={day}&segment=all&view={view}')['artists']
            names = {row['username'] for row in rows}
            assert 'AllHiddenArtist' in names, (view, names)
            assert not {'HiddenArtist', 'BlockedMe'} & names, (view, names)
        images = get(f'/api/history/artist?date={day}&segment=all&username=TaggedArtist')['images']
        assert {row['id'] for row in images} == {9301, 9303}, images
        post('/api/settings', {'applyTagFilters': True, 'applyAccountFilters': False})
        rows = get(f'/api/history/artists?date={day}&segment=all&view=discovery')['artists']
        names = {row['username'] for row in rows}
        assert {'HiddenArtist', 'BlockedMe'} <= names, names
        assert 'AllHiddenArtist' not in names, names
        post('/api/settings', {'applyTagFilters': False})
        images = get(f'/api/history/artist?date={day}&segment=all&username=TaggedArtist')['images']
        assert 9302 not in {row['id'] for row in images}, images
        assert get('/api/discovery/hidden') == before_controls
        post('/api/settings', {'applyTagFilters': True, 'applyAccountFilters': True})
        assert get(f'/api/history/day?date={day}&segment=all')['artistCount'] == summary['artistCount']

        # If tags arrive later (an on-demand details read or the background sweep), a
        # fresh gallery session must immediately enforce them rather than retaining the
        # image in the frozen order. With all three TaggedArtist images now hidden, the
        # entire card disappears.
        with store.connect() as db:
            db.execute("INSERT INTO archive_image_tags(image_id,tag_name) VALUES(?,?)",
                       (9303, "furry"))
            db.execute("INSERT INTO archive_image_seen(image_id,fetched_at) VALUES(?,?)",
                       (9303, datetime.now().isoformat()))
        refreshed = get(f"/api/history/artists?date={day}&segment=all&view=foryou"
                        "&offset=0&limit=50&session=new-hidden-tag")
        assert "TaggedArtist" not in {row["username"] for row in refreshed["artists"]}, refreshed
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()

print({"importedAndResolved": True, "notHiddenRowsIgnored": True, "reimportReplaces": True,
       "hiddenCreatorsExcludedEveryView": True, "blockedByExcluded": True,
       "fullyHiddenCreatorDropped": True, "coverFallsBackToVisible": True,
       "carouselFiltered": True, "countMatchesWhatIsShown": True,
       "filterChangePreservesVisibleCache": True})
