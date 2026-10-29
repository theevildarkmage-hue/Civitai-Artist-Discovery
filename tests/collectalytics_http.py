"""Collectalytics through the real request handler, with Civitai replaced by a fake.

Covers the owned-or-followed collection list, paging every accepted item with a cursor
that misbehaves, the read cap, item and thumbnail shapes, followers worked out from the
contributor count, stills held back by content levels and hidden creators while counts
stay whole, the cache and its refresh, refusing collections outside the list, and the
signed-out answers. No request leaves the test.
"""
import test_safety  # Fail closed before any test network request.

from http.server import ThreadingHTTPServer
import http.client
import json
import threading
from unittest.mock import patch

import server
from discovery import collectalytics
from discovery.collectalytics import Collectalytics, collection_url, followers_from, item_entry

OWNED, FOLLOWED, PRIVATE, STRANGER = 101, 202, 303, 404


def image_row(item_id, creator_id, name, minute, kind="video", level=1, key=None):
    return {"id": 9000 + item_id, "type": "image",
            "createdAt": f"2026-10-2{minute // 60}T12:{minute % 60:02d}:00.000Z",
            "data": {"id": item_id, "type": kind, "name": f"{name}.mp4", "nsfwLevel": level,
                     "url": key or f"key-{item_id}", "createdAt": "2026-10-01T00:00:00.000Z",
                     "user": {"id": creator_id, "username": name},
                     "metadata": {"duration": 61.5} if kind == "video" else {}}}


class FakeClient:
    calls = []
    pages = {}
    contributors = {OWNED: 31, FOLLOWED: 8}

    def query(self, procedure, payload):
        FakeClient.calls.append((procedure, payload))
        if procedure == "collection.getAllUser":
            assert payload == {"permissions": ["VIEW"]}, payload
            return [{"id": FOLLOWED, "name": "Zebra picks", "userId": 55, "isOwner": False,
                     "ownerUsername": "Curator", "type": "Image", "read": "Public"},
                    {"id": OWNED, "name": "Music Focus 🎸", "userId": 7, "isOwner": True,
                     "ownerUsername": None, "type": "Image", "read": "Public"},
                    {"id": PRIVATE, "name": "Drafts", "userId": 7, "type": "Image",
                     "read": "Private"},
                    {"name": "No id"}]
        if procedure == "collection.getAllCollectionItems":
            assert payload["statuses"] == ["ACCEPTED"] and payload["browsingLevel"] == 31, payload
            assert payload["limit"] == 100, payload
            pages = FakeClient.pages[payload["collectionId"]]
            index = int(payload.get("cursor") or 0)
            return pages[index]
        if procedure == "collection.getById":
            if payload["id"] == PRIVATE:
                raise RuntimeError("detail failed")
            owner = {OWNED: (7, "Owner"), FOLLOWED: (55, "Curator")}[payload["id"]]
            return {"collection": {"id": payload["id"], "userId": owner[0],
                                   "description": "  Music first.  ",
                                   "user": {"username": owner[1]}},
                    "collaborators": [{"userId": 9, "role": "Manager"}] if payload["id"] == OWNED else []}
        if procedure == "collection.getInfinite":
            collection_id = payload["ids"][0]
            if collection_id not in FakeClient.contributors:
                return {"items": []}
            return {"items": [{"id": collection_id,
                               "_count": {"items": 3, "contributors": FakeClient.contributors[collection_id]}}]}
        raise AssertionError(procedure)


signed_in = {"value": False, "id": 7}


def auth_status():
    if not signed_in["value"]:
        raise RuntimeError("Civitai is not connected")
    return {"connected": True, "id": signed_in["id"], "username": "Owner"}


# -- pure helpers -----------------------------------------------------------------------

assert followers_from(31, 1) == 29 and followers_from(1, 0) == 0 and followers_from(0, 3) == 0
assert followers_from(None, 0) is None
assert collection_url(5) == "https://civitai.red/collections/5"

entry = item_entry(image_row(1, 11, "Alpha", 5))
assert entry["url"] == "https://civitai.red/images/1", entry
assert entry["thumbUrl"].endswith("/key-1/anim=false,transcode=true,optimized=true,width=320/key-1.jpeg"), entry
assert entry["addedAt"] == "2026-10-20T12:05:00.000Z" and entry["mediaType"] == "video", entry
assert entry["profileUrl"] == "https://civitai.red/user/Alpha", entry
assert entry["duration"] == 61.5, entry
still = item_entry(image_row(2, 11, "Alpha", 5, kind="image"))
assert "/width=320/" in still["thumbUrl"] and "transcode" not in still["thumbUrl"], still
# Only videos have a length; a missing, zero, absurd or non-numeric one is unknown.
assert still["duration"] is None, still
for metadata in ({}, {"duration": 0}, {"duration": "x"}, {"duration": 90000}, None):
    row = image_row(4, 11, "Alpha", 5)
    row["data"]["metadata"] = metadata
    assert item_entry(row)["duration"] is None, metadata
# A full URL instead of a CDN key gives no still rather than a broken one.
assert item_entry(image_row(3, 11, "Alpha", 5, key="https://elsewhere/x.jpeg"))["thumbUrl"] is None
model = item_entry({"id": 1, "type": "model", "createdAt": None,
                    "data": {"id": 77, "name": "Lora", "user": {"id": 3, "username": "Maker"}}})
assert model["url"] == "https://civitai.red/models/77" and model["thumbUrl"] is None, model
assert model["mediaType"] == "model" and model["addedAt"] is None, model
assert item_entry({"type": "image"}) is None and item_entry("junk") is None
# A creator-less row still counts as an item.
assert item_entry({"id": 5, "type": "image", "data": {"id": 6}})["creator"] is None

# -- paging -----------------------------------------------------------------------------

FakeClient.pages = {
    OWNED: [
        {"collectionItems": [image_row(1, 11, "Alpha", 59), image_row(2, 12, "Beta", 58, level=4),
                             image_row(3, 13, "Hidden", 57)], "nextCursor": "1"},
        # The same item on two pages is counted once.
        {"collectionItems": [image_row(3, 13, "Hidden", 57), image_row(4, 11, "Alpha", 30),
                             image_row(5, 14, "Gamma", 20, level=0)], "nextCursor": "2"},
        {"collectionItems": [image_row(6, 11, "Alpha", 10)], "nextCursor": None},
    ],
    # A cursor that repeats ends the read.
    FOLLOWED: [{"collectionItems": [image_row(7, 15, "Delta", 1)], "nextCursor": "0"}],
    # Civitai can drop every item on a page the viewer may not see and still go on.
    PRIVATE: [{"collectionItems": [], "nextCursor": "1"},
              {"collectionItems": [image_row(8, 16, "Epsilon", 3)], "nextCursor": None}],
}

httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def call(path, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=10)
    try:
        connection.request("GET", path, None, {"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        raw = response.read()
        is_json = response.getheader("Content-Type", "").startswith("application/json")
        return response.status, json.loads(raw) if is_json else raw.decode(errors="replace")
    finally:
        connection.close()


stats = Collectalytics(social=lambda: server.collectalytics_client())
with patch.object(server, "COLLECTALYTICS", stats), \
        patch.object(server, "auth_status", auth_status), \
        patch.object(server, "SocialClient", FakeClient), \
        patch.object(server, "hidden_preferences", lambda: ({"hidden"}, {6})), \
        patch.object(server.HISTORY, "visible_levels", (1, 2)):
    try:
        # -- signed out ---------------------------------------------------------------
        status, answer = call("/api/collectalytics/collections")
        assert status == 401 and "Sign in" in answer["error"], answer
        status, answer = call(f"/api/collectalytics/collection?id={OWNED}")
        assert status == 401 and "Sign in" in answer["error"], answer
        assert FakeClient.calls == [], FakeClient.calls
        # The usual browser boundary applies.
        assert call("/api/collectalytics/collections", {"Sec-Fetch-Site": "cross-site"})[0] == 403

        signed_in["value"] = True
        # -- the list: owned first, then by name; a row without an id is dropped -----
        status, listing = call("/api/collectalytics/collections")
        assert status == 200, listing
        assert [(row["id"], row["owned"], row["ownerName"]) for row in listing["collections"]] == [
            (PRIVATE, True, None), (OWNED, True, None), (FOLLOWED, False, "Curator")], listing
        assert listing["collections"][1]["url"] == "https://civitai.red/collections/101"

        assert call("/api/collectalytics/collection?id=abc")[0] == 400
        assert call("/api/collectalytics/collection")[0] == 400
        # Only collections in the account's own list may be read.
        FakeClient.calls = []
        status, answer = call(f"/api/collectalytics/collection?id={STRANGER}")
        assert status == 404 and "own or follow" in answer["error"], answer
        assert not any(name == "collection.getAllCollectionItems" for name, _ in FakeClient.calls)

        # -- a full report ----------------------------------------------------------------
        status, report = call(f"/api/collectalytics/collection?id={OWNED}")
        assert status == 200, report
        assert [item["itemId"] for item in report["items"]] == [1, 2, 3, 4, 5, 6], report
        assert report["pages"] == 3 and report["truncated"] is False, report
        # 31 contributor rows, less the owner's own and one collaborator's.
        assert report["followers"] == 29 and report["contributors"] == 31, report
        assert report["collection"]["name"] == "Music Focus 🎸" and report["collection"]["owned"], report
        assert report["collection"]["ownerName"] == "Owner", report
        assert report["collection"]["description"] == "Music first.", report
        cursors = [payload.get("cursor") for name, payload in FakeClient.calls
                   if name == "collection.getAllCollectionItems"]
        assert cursors == [None, "1", "2"], cursors
        # Stills follow the reader's levels and hidden lists; every item still counts.
        shown = {item["itemId"]: (bool(item["thumbUrl"]), item.get("thumbHidden", False))
                 for item in report["items"]}
        assert shown == {1: (True, False), 2: (False, True), 3: (False, True), 4: (True, False),
                         5: (False, True), 6: (False, True)}, shown

        # -- the detail dialog's reader ------------------------------------------------
        public = []

        def public_listing(params, **options):
            public.append((params, options))
            return {"items": [{"id": params["imageId"], "url": "https://image.civitai.com/x/original=true/a.mp4",
                               "type": "video", "username": "Alpha", "width": 720, "height": 1280,
                               "nsfwLevel": 1, "meta": {"prompt": "a riff"}, "stats": {"heartCount": 2}}]}, 10

        with patch.object(server.HISTORY, "_request", public_listing), \
                patch.object(server.TASTE, "image_tags", lambda image_id: {"known": True, "tags": []}):
            status, detail = call("/api/collectalytics/image?id=1")
            assert status == 200 and detail["prompt"] == "a riff" and detail["type"] == "video", detail
            assert "transcode=true,width=768" in detail["detailVideoUrl"], detail
            assert detail["stats"]["reactionCount"] == 2 and detail["known"] is True, detail
            assert public[0][0] == {"imageId": 1, "withMeta": "true", "browsingLevel": 1}, public
            # Cached: a second open asks nothing.
            assert call("/api/collectalytics/image?id=1")[0] == 200 and len(public) == 1
            # Not listed by this tab, or filtered out for the reader: refused, nothing asked.
            assert call("/api/collectalytics/image?id=999")[0] == 404
            assert call("/api/collectalytics/image?id=2")[0] == 403   # R, above PG-13
            assert call("/api/collectalytics/image?id=3")[0] == 403   # hidden creator
            assert call("/api/collectalytics/image?id=6")[0] == 403   # hidden image
            assert call("/api/collectalytics/image?id=x")[0] == 400
            assert len(public) == 1, public

        # Served from memory until refreshed, and the filtering never touches the cache.
        FakeClient.calls = []
        assert call(f"/api/collectalytics/collection?id={OWNED}")[1]["items"] == report["items"]
        assert FakeClient.calls == [], FakeClient.calls
        assert stats.reports[(7, OWNED)][1]["items"][1]["thumbUrl"], "the cached copy keeps every still"
        status, again = call(f"/api/collectalytics/collection?id={OWNED}&refresh=1")
        assert status == 200 and sum(name == "collection.getAllCollectionItems"
                                     for name, _ in FakeClient.calls) == 3, FakeClient.calls

        # A followed collection; a cursor that comes back again stops the read.
        status, followed = call(f"/api/collectalytics/collection?id={FOLLOWED}")
        assert status == 200 and followed["pages"] == 2 and len(followed["items"]) == 1, followed
        assert followed["followers"] == 7 and followed["collection"]["ownerName"] == "Curator", followed

        # A private collection: details fail and no follower count is reported, yet the
        # items still read, past an empty page that still has a cursor.
        status, private = call(f"/api/collectalytics/collection?id={PRIVATE}")
        assert status == 200 and private["followers"] is None, private
        assert [item["itemId"] for item in private["items"]] == [8] and private["pages"] == 2, private

        # Another account never gets the first account's copy.
        signed_in["id"] = 8
        FakeClient.calls = []
        status, other = call(f"/api/collectalytics/collection?id={OWNED}")
        assert status == 200 and any(name == "collection.getAllCollectionItems"
                                     for name, _ in FakeClient.calls), FakeClient.calls
        assert other["collection"]["owned"], other  # the fake says isOwner for everyone
        signed_in["id"] = 7

        # A read that spans a change of account is refused and not kept.
        accounts = iter([7, 9])
        with patch.object(server, "connected_user_id", lambda: next(accounts)):
            status, answer = call(f"/api/collectalytics/collection?id={FOLLOWED}&refresh=1")
        assert status == 409 and "account changed" in answer["error"], answer
        assert (7, FOLLOWED) not in stats.reports, stats.reports.keys()

        # The read cap reports truncation instead of paging forever.
        with patch.object(collectalytics, "MAX_ITEM_PAGES", 2):
            stats.reports.clear()
            status, capped = call(f"/api/collectalytics/collection?id={OWNED}")
            assert status == 200 and capped["truncated"] and capped["pages"] == 2, capped
            assert len(capped["items"]) == 5, capped

        # Civitai refusing access is the usual renew-your-connection answer.
        def refused(self, procedure, payload):
            raise server.CivitaiHTTPError(401, "nope")
        with patch.object(FakeClient, "query", refused):
            status, answer = call(f"/api/collectalytics/collections?refresh=1")
            assert status == 401 and "Sign out and back in" in answer["error"], answer
    finally:
        httpd.shutdown()
print("collectalytics_http passed")
