"""Collectalytics through the real request handler, with Civitai replaced by a fake.

Covers the owned-or-followed collection list, paging every accepted item with a cursor
that misbehaves, the read cap, item and thumbnail shapes, followers worked out from the
contributor count, stills held back by content levels and hidden creators while counts
stay whole, the cache and its refresh, refusing collections outside the list, and the
signed-out answers. The review queue: read only for owned collections, as Civitai's own
review page asks for it, paged, cached briefly, with each submitter, and Civitai's refusal
passed on plainly. Accepting with thanks: the comment's markup, its preview, the write
grant, comment before accept, no second comment after a failed accept, and refusing items
that are gone. No request leaves the test.
"""
import test_safety  # Fail closed before any test network request.

from http.server import ThreadingHTTPServer
import http.client
import json
import re
import threading
from unittest.mock import patch

import server
from discovery import collectalytics
from discovery.collectalytics import (DEFAULT_COMMENT, Collectalytics, collection_url,
                                      comment_html, default_comment, followers_from, item_entry)

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
    review = {}
    contributors = {OWNED: 31, FOLLOWED: 8}
    # Image id -> the collection rows Civitai reports for it; user id -> username.
    adders = {}
    users = {}
    batches = []
    writes = []
    fail = set()

    def batch_query_optional(self, procedure, payloads):
        FakeClient.batches.append((procedure, payloads))
        if procedure == "collection.getUserCollectionItemsByItem":
            return [FakeClient.adders.get(payload["imageId"]) for payload in payloads]
        if procedure == "user.getCreator":
            return [{"id": payload["id"], "username": FakeClient.users[payload["id"]]}
                    if payload["id"] in FakeClient.users else None for payload in payloads]
        raise AssertionError(procedure)

    def comment_on_image(self, image_id, content):
        if "comment" in FakeClient.fail:
            raise RuntimeError("Civitai mutation failed")
        FakeClient.writes.append(("comment", image_id, content))

    def set_collection_items_status(self, collection_id, collection_item_ids, status):
        if "status" in FakeClient.fail:
            raise RuntimeError("Some of the items selected have not been given a NSFW rating.")
        FakeClient.writes.append(("status", collection_id, collection_item_ids, status))
        # Civitai drops it from the queue.
        for page in FakeClient.review[collection_id]:
            page["collectionItems"] = [row for row in page["collectionItems"]
                                       if row["id"] not in collection_item_ids]

    def query(self, procedure, payload):
        FakeClient.calls.append((procedure, payload))
        if procedure == "collection.getAllUser":
            assert payload == {"permissions": ["VIEW"]}, payload
            return [{"id": FOLLOWED, "name": "Zebra picks", "userId": 55, "isOwner": False,
                     "ownerUsername": "Curator", "type": "Image", "read": "Public"},
                    {"id": OWNED, "name": "Music Focus 🎸", "userId": 7, "isOwner": True,
                     "ownerUsername": None, "type": "Image", "read": "Public",
                     "write": "Review"},
                    {"id": PRIVATE, "name": "Drafts", "userId": 7, "type": "Image",
                     "read": "Private"},
                    {"name": "No id"}]
        if procedure == "collection.getAllCollectionItems" and payload["statuses"] == ["REVIEW"]:
            assert payload["forReview"] is True and payload["reviewSort"] == "Oldest", payload
            assert payload["browsingLevel"] == 31 and payload["limit"] == 100, payload
            pages = FakeClient.review[payload["collectionId"]]
            if isinstance(pages, Exception):
                raise pages
            return pages[int(payload.get("cursor") or 0)]
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


signed_in = {"value": False, "id": 7, "write": False}


def auth_status():
    if not signed_in["value"]:
        raise RuntimeError("Civitai is not connected")
    return {"connected": True, "id": signed_in["id"], "username": "Owner",
            "collectionsWrite": signed_in["write"], "socialWrite": signed_in["write"]}


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

# -- the thanks comment ------------------------------------------------------------------

# Music Focus starts from the comment Music Scout posts, word for word, then the thanks.
focus = {"id": 17980935, "name": "Music Focus 🎸"}
MUSIC_FOCUS_COMMENT = ('<p>Your track has <a target="_blank" '
                       'href="https://civitai.red/collections/17980935?sort=Recently+Added"><strong>Music '
                       'Focus</strong> 🎸</a> and is added to the collection! 🎧 Give it '
                       'a listen and share more jams if you like.</p>')
rendered = comment_html(default_comment(focus["id"]), focus,
                        {"submitterId": 77, "submitter": "Friend", "creatorId": 11, "creator": "Alpha"})
assert rendered == (MUSIC_FOCUS_COMMENT[:-len("</p>")] + ' Thanks <span data-type="mention" '
                    'data-id="mention:77" data-label="Friend">@Friend</span> for the recommendation.</p>'), rendered
# The mention survives Civitai's comment sanitizer and is what its notification job reads:
# a span keeps only data-type, data-id and data-label, and "mention:ID" names the recipient.
mention = re.search(r"<span([^>]*)>@Friend</span>", rendered).group(1)
assert re.findall(r'([\w-]+)="', mention) == ["data-type", "data-id", "data-label"], mention
assert re.findall(r'"mention:(\d+)"', rendered) == ["77"], rendered
generic = comment_html(DEFAULT_COMMENT, {"id": OWNED, "name": "Picks"}, {"submitterId": 5, "submitter": "Sam"})
assert generic.startswith('<p>It is added to <a ') and generic.endswith('@Sam</span> for the recommendation.</p>'), generic
assert default_comment(OWNED) == DEFAULT_COMMENT
# Typed markup is escaped; only the template's own syntax becomes markup.
rendered = comment_html('<b>Hi</b> {creator} [**{collection}** & co] {unknown}\n\n  second  ',
                        {"id": 5, "name": "A<B"}, {"creatorId": 3, "creator": 'x"y'})
assert rendered == ('<p>&lt;b&gt;Hi&lt;/b&gt; <span data-type="mention" data-id="mention:3" '
                    'data-label="x&quot;y">@x&quot;y</span> <a target="_blank" '
                    'href="https://civitai.red/collections/5?sort=Recently+Added"><strong>A&lt;B</strong> '
                    '&amp; co</a> {unknown}</p><p>second</p>'), rendered
# No one to mention: a name without an id is plain text, and no name is nothing.
assert comment_html("{submitter}|{creator}", {"id": 5, "name": "C"},
                    {"creator": "Plain"}) == "<p>|@Plain</p>"

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


def call(path, headers=None, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=10)
    try:
        data = json.dumps(body).encode() if body is not None else None
        connection.request("GET" if body is None else "POST", path, data,
                           {"Content-Type": "application/json", **(headers or {})})
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

        # -- the review queue --------------------------------------------------------------
        FakeClient.review = {
            OWNED: [{"collectionItems": [image_row(21, 31, "Newcomer", 1, level=0),
                                         image_row(22, 11, "Alpha", 2)], "nextCursor": "1"},
                    {"collectionItems": [image_row(23, 13, "Hidden", 3)], "nextCursor": None}],
            # Civitai's FORBIDDEN, as it answers anyone but the owner and managers.
            PRIVATE: server.CivitaiHTTPError(403, "You do not have permission to view review items"),
        }
        # 21 was submitted by someone else, 22 by its creator; Civitai has no row for 23.
        FakeClient.adders = {21: [{"collectionId": FOLLOWED, "addedById": 5},
                                  {"collectionId": OWNED, "addedById": 77}],
                             22: [{"collectionId": OWNED, "addedById": 11}]}
        FakeClient.users = {77: "Friend", 11: "Alpha"}
        FakeClient.calls = []
        status, queue = call(f"/api/collectalytics/review?id={OWNED}")
        assert status == 200, queue
        assert [item["itemId"] for item in queue["items"]] == [21, 22, 23], queue
        assert queue["pages"] == 2 and not queue["truncated"] and queue["acceptsSubmissions"], queue
        assert queue["collection"]["write"] == "Review", queue
        assert queue["defaultComment"] == DEFAULT_COMMENT and queue["canAccept"] is False, queue
        submitters = {item["itemId"]: (item["submitterId"], item["submitter"]) for item in queue["items"]}
        assert submitters == {21: (77, "Friend"), 22: (11, "Alpha"), 23: (None, None)}, submitters
        assert queue["items"][0]["submitterProfileUrl"] == "https://civitai.red/user/Friend", queue
        # Review stills are the larger ones.
        assert ",width=450/" in queue["items"][1]["thumbUrl"], queue["items"][1]
        cursors = [payload.get("cursor") for name, payload in FakeClient.calls
                   if name == "collection.getAllCollectionItems"]
        assert cursors == [None, "1"], FakeClient.calls
        # The same still rules: an unrated item and a hidden creator show none.
        shown = {item["itemId"]: bool(item["thumbUrl"]) for item in queue["items"]}
        assert shown == {21: False, 22: True, 23: False}, shown
        # A queued image opens in the detail dialog like an accepted one.
        assert stats.listed_image(7, 22)["creator"] == "Alpha"
        # Read only: nothing but queries was sent.
        assert all(name.split(".")[1].startswith("get") for name, _ in FakeClient.calls), FakeClient.calls
        assert {name for name, _ in FakeClient.batches} == {
            "collection.getUserCollectionItemsByItem", "user.getCreator"}, FakeClient.batches
        assert FakeClient.writes == []
        # Cached briefly; refresh reads again.
        FakeClient.calls = []
        assert call(f"/api/collectalytics/review?id={OWNED}")[0] == 200 and FakeClient.calls == []
        assert call(f"/api/collectalytics/review?id={OWNED}&refresh=1")[0] == 200
        assert len(FakeClient.calls) == 2, FakeClient.calls
        # Only owned collections have a queue here; nothing is asked for the others.
        FakeClient.calls = []
        for other in (FOLLOWED, STRANGER):
            status, answer = call(f"/api/collectalytics/review?id={other}")
            assert status == 404 and "you own" in answer["error"], answer
        assert FakeClient.calls == [], FakeClient.calls
        status, answer = call(f"/api/collectalytics/review?id={PRIVATE}")
        assert status == 403 and "owner and managers" in answer["error"], answer
        assert call("/api/collectalytics/review?id=x")[0] == 400
        # -- accepting with thanks ---------------------------------------------------------
        template = "Thanks {submitter}!\nIn [**{collection}**]."
        status, preview = call("/api/collectalytics/comment-preview",
                               body={"collectionId": OWNED, "collectionItemId": 9021, "template": template})
        assert status == 200 and 'data-id="mention:77"' in preview["html"], preview
        assert "<strong>Music Focus 🎸</strong>" in preview["html"], preview
        assert FakeClient.writes == []
        # Without the write grant nothing is attempted.
        status, answer = call("/api/collectalytics/accept",
                              body={"collectionId": OWNED, "collectionItemId": 9021, "template": template})
        assert status == 403 and "Sign out and back in" in answer["error"], answer
        assert FakeClient.writes == []
        signed_in["write"] = True
        assert call(f"/api/collectalytics/review?id={OWNED}")[1]["canAccept"] is True
        # Someone else's collection, an item no longer waiting, and too long a comment.
        assert call("/api/collectalytics/accept", body={"collectionId": FOLLOWED, "collectionItemId": 1,
                                                        "template": ""})[0] == 404
        status, answer = call("/api/collectalytics/accept", body={"collectionId": OWNED,
                                                                  "collectionItemId": 9999, "template": ""})
        assert status == 409 and "no longer waiting" in answer["error"], answer
        assert call("/api/collectalytics/accept", body={"collectionId": OWNED, "collectionItemId": 9021,
                                                        "template": "x" * 2001})[0] == 400
        assert FakeClient.writes == []
        # The comment first, then the accept.
        status, answer = call("/api/collectalytics/accept",
                              body={"collectionId": OWNED, "collectionItemId": 9021, "template": template})
        assert status == 200 and answer == {"collectionItemId": 9021, "accepted": True, "commented": True}, answer
        assert [write[0] for write in FakeClient.writes] == ["comment", "status"], FakeClient.writes
        assert FakeClient.writes[0][:2] == ("comment", 21) and 'mention:77' in FakeClient.writes[0][2]
        assert FakeClient.writes[1] == ("status", OWNED, [9021], "ACCEPTED"), FakeClient.writes
        # Gone from the queue served next, and the accepted count is reread.
        assert [item["itemId"] for item in call(f"/api/collectalytics/review?id={OWNED}")[1]["items"]] == [22, 23]
        assert (7, OWNED) not in stats.reports
        # Accepting it again is refused: the queue is reread first.
        FakeClient.writes = []
        status, answer = call("/api/collectalytics/accept",
                              body={"collectionId": OWNED, "collectionItemId": 9021, "template": template})
        assert status == 409 and FakeClient.writes == [], answer
        # Nobody to thank: refused before anything is posted. Without a comment it is accepted.
        status, answer = call("/api/collectalytics/accept",
                              body={"collectionId": OWNED, "collectionItemId": 9023, "template": template})
        assert status == 409 and "who submitted" in answer["error"] and FakeClient.writes == [], answer
        status, answer = call("/api/collectalytics/accept",
                              body={"collectionId": OWNED, "collectionItemId": 9023, "template": "  "})
        assert status == 200 and answer["commented"] is False, answer
        assert FakeClient.writes == [("status", OWNED, [9023], "ACCEPTED")], FakeClient.writes
        # A failed comment accepts nothing; a failed accept after the comment says so once.
        FakeClient.writes, FakeClient.fail = [], {"comment"}
        status, answer = call("/api/collectalytics/accept",
                              body={"collectionId": OWNED, "collectionItemId": 9022, "template": template})
        assert status == 502 and FakeClient.writes == [] and not answer.get("commented"), answer
        FakeClient.fail = {"status"}
        status, answer = call("/api/collectalytics/accept",
                              body={"collectionId": OWNED, "collectionItemId": 9022, "template": template})
        assert status == 502 and answer["commented"] is True and "Accept it on Civitai" in answer["error"], answer
        assert [write[0] for write in FakeClient.writes] == ["comment"], FakeClient.writes
        FakeClient.fail = set()
        # Civitai's own refusal without a comment is passed on.
        FakeClient.writes, FakeClient.fail = [], {"status"}
        status, answer = call("/api/collectalytics/accept",
                              body={"collectionId": OWNED, "collectionItemId": 9022, "template": ""})
        assert status == 502 and "NSFW rating" in answer["error"], answer
        FakeClient.fail = set()
        signed_in["write"] = False

        # The review read is capped like the item read.
        FakeClient.review[OWNED] = [{"collectionItems": [image_row(30 + n, 11, "Alpha", n)],
                                     "nextCursor": str(n + 1)} for n in range(3)]
        with patch.object(collectalytics, "MAX_REVIEW_PAGES", 2):
            status, capped = call(f"/api/collectalytics/review?id={OWNED}&refresh=1")
            assert status == 200 and capped["truncated"] and len(capped["items"]) == 2, capped
        signed_in["value"] = False
        assert call(f"/api/collectalytics/review?id={OWNED}")[0] == 401
        signed_in["value"] = True

        # Civitai refusing access is the usual renew-your-connection answer.
        def refused(self, procedure, payload):
            raise server.CivitaiHTTPError(401, "nope")
        with patch.object(FakeClient, "query", refused):
            status, answer = call(f"/api/collectalytics/collections?refresh=1")
            assert status == 401 and "Sign out and back in" in answer["error"], answer
    finally:
        httpd.shutdown()
print("collectalytics_http passed")
