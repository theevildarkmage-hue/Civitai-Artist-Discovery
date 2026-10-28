"""Community API v1: trusted verification, transactional local simulation, adapters.

No identity, owner, timestamp or quota is accepted from a browser request. SQLite
serializes simulator writes; production has its own PostgreSQL implementation.
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import cached_property
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Protocol
import urllib.error
import urllib.parse
import urllib.request

from .models import base_labels, normalize_base_model

MESSAGES = {
    "INVITE_ONLY": "Community Beta is Invite Only",
    "INVITE_REVOKED": "Your Community Beta invitation has been revoked.",
    "INVITE_EXPIRED": "Your Community Beta invitation has expired.",
    "BETA_DISABLED": "Community Beta is temporarily disabled.",
    "READ_ONLY": "Community Beta is read-only. Please try again later.",
    "INVALID_ARTWORK": "Artwork unavailable or ineligible. Use a public Civitai artwork URL or numeric image ID; check that it opens on Civitai.",
    "UNVERIFIED_OWNER": "We couldn't verify this artwork's creator with Civitai. Please try another link.",
    "SELF_RECOMMENDATION": "You can't recommend your own artwork. Let's spotlight someone else!",
    "MEMBER_QUOTA": "You've used all three recommendations for this week. Come back Monday!",
    "ARTIST_QUOTA": "This artist has already been featured three times this week. Pick another artist!",
    "DUPLICATE": "This artwork has already been recommended. Great taste, though!",
    "AUTH_REQUIRED": "Sign in to Civitai to recommend artwork.",
    "AUTH_EXPIRED": "Your Civitai connection has expired. Please sign in again.",
    "UNAVAILABLE": "The community service is temporarily unavailable. Your personal galleries still work.",
    "RATE_LIMITED": "Please wait a moment before trying again.",
    "CONTENT_FILTERED": "This artwork is outside your current content controls.",
    "FORBIDDEN": "This community operation is not allowed.",
    "SUBMITTER_BLOCKED": "This account cannot submit Community recommendations.",
    "INVALID_REQUEST": "Please check the request and try again.",
}


class CommunityError(Exception):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(MESSAGES[code])

    def response(self):
        return {"apiVersion": 1, "code": self.code, "error": str(self)}


def positive_id(value):
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]{0,14}", str(value)):
        raise CommunityError("INVALID_ARTWORK")
    return int(value)


def parse_artwork(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return positive_id(value)
    if not isinstance(value, str) or len(value) > 512:
        raise CommunityError("INVALID_ARTWORK")
    value = value.strip()
    if re.fullmatch(r"[1-9][0-9]{0,14}", value):
        return positive_id(value)
    # Match the complete URL, including its authority; no credentials, ports,
    # queries, fragments, escapes, redirects or user-controlled fetch destinations.
    match = re.fullmatch(r"https://(?:www\.)?civitai\.(?:com|red)/images/([1-9][0-9]{0,14})/?", value)
    if not match:
        raise CommunityError("INVALID_ARTWORK")
    return positive_id(match[1])


def utc_week(now):
    now = now.astimezone(timezone.utc)
    return (now - timedelta(days=now.weekday())).date().isoformat()


@dataclass(frozen=True)
class Member:
    id: int
    username: str


@dataclass(frozen=True)
class Artwork:
    id: int
    creator_id: int
    username: str
    rating: int
    preview: str
    tags: tuple[str, ...] = ()
    tag_ids: tuple[int, ...] = ()
    tags_known: bool = True
    owner_verified: bool = True
    base_model: str = "Unknown"
    media_type: str = "image"

    def browser(self):
        from .history import video_still_url, video_playback_url
        preview = video_still_url(self.preview) if self.media_type == 'video' else self.preview
        value = {"id": self.id, "creatorId": self.creator_id if self.owner_verified else None, "username": self.username,
                "browsingLevel": self.rating, "thumbnailUrl": preview,
                "url": preview, "imageUrl": f"https://civitai.red/images/{self.id}",
                "profileUrl": f"https://civitai.red/user/{urllib.parse.quote(self.username, safe='')}",
                "type": self.media_type, "baseModel": self.base_model,
                "modelLabels": base_labels(self.base_model)}
        if self.media_type == 'video':
            value['videoUrl'] = video_playback_url(self.preview)
            value['detailVideoUrl'] = video_playback_url(self.preview, 768)
        return value


@dataclass(frozen=True)
class ContentProfile:
    levels: frozenset[int] = frozenset((1, 2))
    creators: frozenset[int] = frozenset()
    usernames: frozenset[str] = frozenset()
    images: frozenset[int] = frozenset()
    tags: frozenset[str] = frozenset()
    tag_ids: frozenset[int] = frozenset()
    blocked_creators: frozenset[int] = frozenset()
    blocked_usernames: frozenset[str] = frozenset()
    apply_tag_filters: bool = True
    apply_account_filters: bool = True
    apply_image_filters: bool = True
    implicit_images: frozenset[tuple[int, int]] = frozenset()
    rating_tags: frozenset[tuple[int, int]] = frozenset()
    unknown_images: frozenset[int] = frozenset()
    known_tag_ids: frozenset[int] = frozenset()

    @cached_property
    def _image_votes(self):
        votes = defaultdict(set)
        for image, tag in self.implicit_images:
            votes[image].add(tag)
        return votes

    @cached_property
    def _restricted_rating_tags(self):
        ceiling = max(self.levels, default=0)
        return frozenset(tag for tag, level in self.rating_tags if level > ceiling)

    @cached_property
    def _known_vote_tags(self):
        return self.known_tag_ids | self.tag_ids | frozenset(tag for tag, _ in self.rating_tags)

    def image_exclusions(self, image):
        reasons = []
        if self.apply_image_filters and image in self.images: reasons.append('hiddenArtwork')
        votes = self._image_votes.get(image, set())
        if self.apply_tag_filters and votes & self.tag_ids: reasons.append('hiddenTag')
        if votes & self._restricted_rating_tags:
            reasons.append('ratingRestriction')
        if image in self.unknown_images or not votes <= self._known_vote_tags:
            reasons.append('incompleteMetadata')
        return reasons

    def excluded_image_ids(self):
        candidates = self.images | self.unknown_images | self._image_votes.keys()
        return {image for image in candidates if self.image_exclusions(image)}

    def allows(self, art):
        return not self.exclusions(art)

    def exclusions(self, art):
        """All applicable reasons, deliberately overlapping; no artwork leaves this gate."""
        reasons = []
        if art.rating not in self.levels: reasons.append('ratingRestriction')
        if self.apply_account_filters and (art.creator_id in self.blocked_creators or art.username.casefold() in self.blocked_usernames):
            reasons.append('blockedUser')
        if self.apply_account_filters and ((art.creator_id in self.creators and art.creator_id not in self.blocked_creators)
                or (art.username.casefold() in self.usernames and art.username.casefold() not in self.blocked_usernames)):
            reasons.append('hiddenCreator')
        reasons.extend(self.image_exclusions(art.id))
        if self.apply_tag_filters and (self.tags.intersection(art.tags) or self.tag_ids.intersection(art.tag_ids)): reasons.append('hiddenTag')
        if ((self.apply_tag_filters and not art.tags_known and (self.tags or self.tag_ids)) or
                (self.apply_account_filters and not art.owner_verified and self.creators and len(self.usernames-{''}) < len(self.creators))):
            reasons.append('incompleteMetadata')
        return list(dict.fromkeys(reasons))


def content_profile(hidden, levels):
    """Normalize upstream legacy/compact controls, including implicit tag-vote hides."""
    if not isinstance(hidden, dict) or not all(isinstance(hidden.get(k), list)
            for k in ("hiddenUsers", "hiddenTags", "hiddenImages", "blockedByUsers", "blockedUsers")):
        raise CommunityError("UNAVAILABLE", 503)
    users = hidden["hiddenUsers"] + hidden["blockedByUsers"] + hidden["blockedUsers"]
    tags = hidden["hiddenTags"]
    implicit = hidden.get("hiddenImagesImplicit", [])
    if not isinstance(implicit, list) or hidden.get("__v") not in (None, 2):
        raise CommunityError("UNAVAILABLE", 503)
    try:
        images, unknown_images, votes = set(), set(), set()
        for is_implicit, rows in ((False, hidden['hiddenImages']), (True, implicit)):
            for row in rows:
                if isinstance(row, dict) and row.get('hidden') is False:
                    continue
                image = positive_id(row['id'] if isinstance(row, dict) else row)
                tag = row.get('tagId') if isinstance(row, dict) else None
                if tag is None:
                    (unknown_images if is_implicit else images).add(image)
                    continue
                try:
                    tag = positive_id(tag)
                except CommunityError:
                    unknown_images.add(image)
                else:
                    votes.add((image, tag))
        return ContentProfile(levels=frozenset(levels),
            creators=frozenset(positive_id(x["id"]) for x in users if x.get("hidden") is not False),
            usernames=frozenset(str(x.get("username", "")).casefold() for x in users if x.get("hidden") is not False),
            images=frozenset(images), unknown_images=frozenset(unknown_images),
            implicit_images=frozenset(votes),
            known_tag_ids=frozenset(positive_id(x['id']) for x in tags),
            rating_tags=frozenset((positive_id(x["id"]),positive_id(x["nsfwLevel"]))
                for x in tags if x.get("nsfwLevel")),
            tags=frozenset(x["name"].casefold() for x in tags if x.get("hidden") is not False and not x.get("nsfwLevel")),
            tag_ids=frozenset(positive_id(x["id"]) for x in tags if x.get("hidden") is not False and not x.get("nsfwLevel")),
            blocked_creators=frozenset(positive_id(x['id']) for x in hidden['blockedByUsers']+hidden['blockedUsers'] if x.get('hidden') is not False),
            blocked_usernames=frozenset(str(x.get('username','')).casefold() for x in hidden['blockedByUsers']+hidden['blockedUsers'] if x.get('hidden') is not False))
    except (KeyError, TypeError, AttributeError, CommunityError):
        raise CommunityError("UNAVAILABLE", 503) from None


class Verifier(Protocol):
    def member(self, token: str) -> Member: ...
    def artwork(self, image_id: int) -> Artwork: ...


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CommunityError("UNAVAILABLE", 503)


def bounded_json(url, token=None, body=None):
    headers = {"Accept": "application/json", "User-Agent": "CivitaiDiscoveryBeta/3.0.0-beta.7"}
    if token:
        if not isinstance(token, str) or len(token) > 8192 or any(c.isspace() for c in token):
            raise CommunityError("AUTH_EXPIRED", 401)
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=8) as response:
            raw = response.read(1_048_577)
        if len(raw) > 1_048_576:
            raise CommunityError("UNAVAILABLE", 503)
        return json.loads(raw)
    except urllib.error.HTTPError as error:
        if re.fullmatch(r"https://[a-z0-9-]+\.supabase\.co/functions/v1/community-v1", url):
            try:
                value = json.loads(error.read(8193))
                if value.get("code") in MESSAGES:
                    raise CommunityError(value["code"], error.code) from None
            except (ValueError, AttributeError):
                pass
        if error.code in (401, 403):
            raise CommunityError("AUTH_EXPIRED", 401) from None
        if error.code == 429:
            raise CommunityError("RATE_LIMITED", 429) from None
        if error.code == 404:
            raise CommunityError("INVALID_ARTWORK", 404) from None
        raise CommunityError("UNAVAILABLE", 503) from None
    except (TimeoutError, OSError, ValueError):
        raise CommunityError("UNAVAILABLE", 503) from None


class CivitaiVerifier:
    """REST confirms exact public media; upstream image.get supplies numeric owner.

    image.get is an upstream-supported tRPC procedure already used by Discovery,
    not a stable REST contract. Schema drift fails closed; see source notes.
    OAuth is sent only to fixed official API hosts, never media URLs.
    """
    def __init__(self, fetch=bounded_json, token_provider=None):
        self.fetch, self.token_provider = fetch, token_provider

    def query(self, procedure, payload, token=None):
        from .flatjson import unwrap_result
        encoded = urllib.parse.quote(json.dumps({"json": payload}, separators=(",", ":")))
        value = self.fetch(f"https://civitai.red/api/trpc/{procedure}?input={encoded}", token)
        if not isinstance(value, dict) or "error" in value or "result" not in value:
            if isinstance(value, dict) and isinstance(value.get("error"), dict):
                data = value["error"].get("json", {}).get("data", {})
                if isinstance(data, dict) and data.get("code") in ("UNAUTHORIZED", "FORBIDDEN"):
                    raise CommunityError("AUTH_EXPIRED", 401)
                if isinstance(data, dict) and data.get("code") == "TOO_MANY_REQUESTS":
                    raise CommunityError("RATE_LIMITED", 429)
                if isinstance(data, dict) and data.get('code') in ('INTERNAL_SERVER_ERROR','TIMEOUT','SERVICE_UNAVAILABLE'):
                    raise CommunityError('UNAVAILABLE',503)
            raise CommunityError("INVALID_ARTWORK")
        try:
            return unwrap_result(value["result"])
        except (IndexError, KeyError, TypeError, ValueError, RecursionError):
            raise CommunityError("INVALID_ARTWORK") from None

    def member(self, token):
        if not token:
            raise CommunityError("AUTH_REQUIRED", 401)
        value = self.fetch("https://auth.civitai.com/api/auth/oauth/userinfo", token)
        if not isinstance(value, dict):
            raise CommunityError("AUTH_EXPIRED", 401)
        try:
            identity = positive_id(value.get("sub"))
            if value.get("id") is not None and positive_id(value["id"]) != identity:
                raise CommunityError("AUTH_EXPIRED", 401)
        except CommunityError:
            raise CommunityError("AUTH_EXPIRED", 401) from None
        username = value.get("preferred_username") or value.get("username")
        if not isinstance(username, str) or not username or len(username) > 200:
            raise CommunityError("AUTH_EXPIRED", 401)
        return Member(identity, username)

    def artwork(self, image_id):
        image_id = positive_id(image_id)
        token = self.token_provider() if self.token_provider else None
        value = self.fetch(f"https://civitai.red/api/v1/images?imageId={image_id}&limit=1&browsingLevel=31", token)
        items = value.get("items") if isinstance(value, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise CommunityError("INVALID_ARTWORK")
        item = items[0]
        if type(item.get("id")) is not int or item["id"] != image_id or item.get("type") not in ("image", "video"):
            raise CommunityError("INVALID_ARTWORK")
        detail = self.query("image.get", {"id": image_id}, token)
        if not isinstance(detail, dict) or detail.get("id") != image_id:
            raise CommunityError("INVALID_ARTWORK")
        if detail.get('type') is not None and detail['type'] != item['type']:
            raise CommunityError('INVALID_ARTWORK')
        user = detail.get("user")
        if not isinstance(user, dict):
            raise CommunityError("UNVERIFIED_OWNER")
        try:
            creator = positive_id(user.get("id"))
        except CommunityError:
            raise CommunityError("UNVERIFIED_OWNER") from None
        if (not isinstance(user.get("username"), str) or not user["username"]
                or user["username"] != item.get("username") or user.get("deletedAt")):
            raise CommunityError("UNVERIFIED_OWNER")
        # Exact image lookup can return unlisted/early-access entries. Require the
        # public availability and completed ingestion from the authoritative detail.
        if (detail.get("availability") != "Public" or detail.get("ingestion") != "Scanned"
                or detail.get("needsReview") is not None or detail.get("blockedFor")):
            raise CommunityError("INVALID_ARTWORK")
        published = detail.get("publishedAt")
        try:
            stamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
                raise ValueError()
        except (AttributeError, ValueError, TypeError):
            raise CommunityError("INVALID_ARTWORK") from None
        rating = item.get("browsingLevel")
        if type(rating) is not int or rating not in (1, 2, 4, 8, 16) or detail.get("nsfwLevel") != rating:
            raise CommunityError("INVALID_ARTWORK")
        preview = item.get("url")
        try:
            parsed = urllib.parse.urlsplit(preview)
            if (parsed.scheme != "https" or parsed.hostname != "image.civitai.com"
                    or parsed.username or parsed.password or parsed.port or not parsed.path):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise CommunityError("INVALID_ARTWORK") from None
        tags = self.query("tag.getVotableTags", {"id": image_id, "type": "image"}, token)
        if not isinstance(tags, list) or any(not isinstance(t, dict) or not isinstance(t.get("name"), str)
                                            or type(t.get("id")) is not int for t in tags):
            raise CommunityError("INVALID_ARTWORK")
        return Artwork(image_id, creator, user["username"], rating, preview,
                       tuple(t["name"].casefold() for t in tags), tuple(t["id"] for t in tags),
                       base_model=normalize_base_model(item.get("baseModel")), media_type=item['type'])


# Frozen packages exclude the development implementation and its member fixtures.
import sys
if not getattr(sys,'frozen',False):
    from .community_fixtures import FixtureVerifier
else:
    class FixtureVerifier:
        def __init__(self,*args,**kwargs):
            raise RuntimeError('Fixture identities are excluded from production')


class RateLimiter:
    def __init__(self, limit=30, seconds=60):
        self.limit, self.seconds = limit, seconds
        self.buckets = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key):
        now = time.monotonic()
        with self.lock:
            for old in list(self.buckets):
                if not self.buckets[old] or self.buckets[old][-1] <= now - self.seconds:
                    del self.buckets[old]
            bucket = self.buckets[key]
            while bucket and bucket[0] <= now - self.seconds:
                bucket.popleft()
            if len(bucket) >= self.limit or len(self.buckets) > 10000:
                raise CommunityError("RATE_LIMITED", 429)
            bucket.append(now)


class CommunityBackend(Protocol):
    def request(self, operation: str, token: str, payload: dict) -> dict: ...


class LocalCommunityBackend:
    def __init__(self, path: Path, verifier: Verifier, *, clock=None):
        self.path, self.verifier = Path(path), verifier
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.rate = RateLimiter()
        self.read_rate = RateLimiter(120)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS recommendations (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, image_id INTEGER NOT NULL UNIQUE,
                  creator_id INTEGER NOT NULL, member_id INTEGER NOT NULL,
                  member_name TEXT NOT NULL, submitted_at TEXT NOT NULL, week TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','withdrawn','hidden','removed')));
                CREATE INDEX IF NOT EXISTS community_member_week ON recommendations(member_id,week);
                CREATE INDEX IF NOT EXISTS community_artist_week ON recommendations(creator_id,week);
                CREATE TABLE IF NOT EXISTS audit (
                  seq INTEGER PRIMARY KEY, image_id INTEGER NOT NULL, action TEXT NOT NULL, at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS submission_requests (
                  member_id INTEGER NOT NULL, request_key TEXT NOT NULL, image_id INTEGER NOT NULL,
                  PRIMARY KEY(member_id,request_key));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _usage(self, db, member_id, now):
        used = db.execute("SELECT count(*) FROM recommendations WHERE member_id=? AND week=?",
                          (member_id, utc_week(now))).fetchone()[0]
        reset = datetime.fromisoformat(utc_week(now)).replace(tzinfo=timezone.utc) + timedelta(days=7)
        return {"used": used, "limit": 3, "remaining": max(0, 3 - used), "week": utc_week(now),
                "resetsAt": reset.isoformat()}

    @staticmethod
    def _eligible(db, member, art, now):
        if db.execute("SELECT 1 FROM recommendations WHERE image_id=?", (art.id,)).fetchone():
            raise CommunityError("DUPLICATE", 409)
        if art.creator_id == member.id:
            raise CommunityError("SELF_RECOMMENDATION", 403)
        for column, identity, error in (("member_id", member.id, "MEMBER_QUOTA"),
                                        ("creator_id", art.creator_id, "ARTIST_QUOTA")):
            count = db.execute(f"SELECT count(*) FROM recommendations WHERE {column}=? AND week=?",
                               (identity, utc_week(now))).fetchone()[0]
            if count >= 3:
                raise CommunityError(error, 409)

    def request(self, operation, token, payload):
        # A transport/IP limiter is also applied before upstream identity verification.
        member = self.verifier.member(token)
        (self.read_rate if operation in ('status', 'gallery') else self.rate).check(member.id)
        now = self.clock()
        if operation == "status":
            with self.connect() as db:
                return {"apiVersion": 1, "member": {"id": member.id, "username": member.username},
                        "quota": self._usage(db, member.id, now),
                        "development": isinstance(self.verifier, FixtureVerifier)}
        if operation in ("verify", "recommend"):
            art = self.verifier.artwork(parse_artwork(payload.get("artwork")))
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                now = self.clock()
                request_key = payload.get('idempotencyKey') if operation == 'recommend' else None
                if request_key is not None:
                    if not isinstance(request_key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',request_key):
                        raise CommunityError('INVALID_REQUEST')
                    prior=db.execute('SELECT image_id FROM submission_requests WHERE member_id=? AND request_key=?',
                                     (member.id,request_key)).fetchone()
                    if prior:
                        if prior['image_id'] != art.id: raise CommunityError('INVALID_REQUEST')
                        return {'apiVersion':1,'imageId':art.id,'quota':self._usage(db,member.id,now),'replayed':True}
                self._eligible(db, member, art, now)
                if operation == "recommend":
                    db.execute("INSERT INTO recommendations(image_id,creator_id,member_id,member_name,submitted_at,week) "
                               "VALUES(?,?,?,?,?,?)", (art.id, art.creator_id, member.id, member.username,
                                                      now.isoformat(), utc_week(now)))
                    db.execute("INSERT INTO audit(image_id,action,at) VALUES(?, 'accepted', ?)",
                               (art.id, now.isoformat()))
                    if request_key:
                        db.execute('INSERT INTO submission_requests VALUES(?,?,?)',(member.id,request_key,art.id))
                return {"apiVersion": 1, "imageId": art.id, "quota": self._usage(db, member.id, now)}
        if operation == "gallery":
            cursor = payload.get("cursor")
            if cursor is not None:
                cursor = positive_id(cursor)
            limit = payload.get("limit", 24)
            if type(limit) is not int or not 1 <= limit <= 50:
                raise CommunityError("INVALID_REQUEST")
            with self.connect() as db:
                rows = db.execute("SELECT seq,image_id,creator_id,member_name,submitted_at FROM recommendations "
                                  "WHERE status='active' AND seq<? ORDER BY seq DESC LIMIT ?",
                                  (cursor or 9223372036854775807, limit + 1)).fetchall()
            # Shared contract deliberately returns canonical IDs, never thumbnails.
            return {"apiVersion": 1, "items": [{"imageId": r["image_id"], "creatorId": r["creator_id"],
                        "recommender": r["member_name"], "submittedAt": r["submitted_at"]} for r in rows[:limit]],
                    "nextCursor": str(rows[limit - 1]["seq"]) if len(rows) > limit else None}
        if operation == "withdraw":
            image_id = parse_artwork(payload.get("artwork"))
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT member_id,status FROM recommendations WHERE image_id=?", (image_id,)).fetchone()
                if not row or row["member_id"] != member.id:
                    raise CommunityError("FORBIDDEN", 403)
                if row["status"] == "active":
                    db.execute("UPDATE recommendations SET status='withdrawn' WHERE image_id=?", (image_id,))
                    db.execute("INSERT INTO audit(image_id,action,at) VALUES(?, 'withdrawn', ?)", (image_id, now.isoformat()))
            return {"apiVersion": 1, "imageId": image_id}
        raise CommunityError("INVALID_REQUEST")

    def moderate(self, image_id, status):
        """Operator-only Python interface, deliberately absent from the browser API."""
        if status not in ("hidden", "removed"):
            raise CommunityError("INVALID_REQUEST")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE recommendations SET status=? WHERE image_id=?", (status, image_id))
            db.execute("INSERT INTO audit(image_id,action,at) VALUES(?,?,?)",
                       (image_id, status, self.clock().isoformat()))


class SupabaseCommunityBackend:
    def __init__(self, url, fetch=bounded_json, *, identity_token_provider=None):
        if not re.fullmatch(r"https://[a-z0-9-]+\.supabase\.co/functions/v1/community-v1", url or ""):
            raise ValueError("Use the HTTPS community-v1 Edge Function URL")
        self.url, self.fetch = url, fetch
        self.identity_token_provider = identity_token_provider

    def request(self, operation, token, payload):
        restricted=None
        try:
            if self.identity_token_provider is None:raise CommunityError('AUTH_REQUIRED',401)
            from .oauth import OAuthReauthenticationRequired,OAuthTemporarilyUnavailable
            try:restricted=self.identity_token_provider()
            except OAuthReauthenticationRequired:raise CommunityError('AUTH_EXPIRED',401) from None
            except OAuthTemporarilyUnavailable:raise CommunityError('UNAVAILABLE',503) from None
            from .cloud_identity import SESSION
            if not isinstance(restricted,str) or not SESSION.fullmatch(restricted):
                raise CommunityError('AUTH_EXPIRED',401)
            value = self.fetch(self.url, restricted, {"apiVersion": 1, "operation": operation, **payload})
        except CommunityError as error:
            if restricted is not None and error.code=='AUTH_EXPIRED':
                from .oauth import note_community_rejection
                note_community_rejection(restricted)
            raise
        if not isinstance(value, dict) or value.get("apiVersion") != 1:
            raise CommunityError("UNAVAILABLE", 503)
        if value.get("code") in MESSAGES:
            raise CommunityError(value["code"], value.get("status", 400))
        return value


class CommunityClient:
    """Viewer controls gate cached reads; writes always require fresh verification."""
    def __init__(self, backend, verifier, token_provider, profile_provider):
        self.backend, self.verifier = backend, verifier
        self.token_provider, self.profile_provider = token_provider, profile_provider
        self.rate = RateLimiter(60)
        # Bounded process-local admission list, scoped to the authenticated viewer.
        # Shared personal actions cannot turn arbitrary browser IDs into lookups.
        self.presented = OrderedDict()
        self.presented_lock = threading.Lock()
        self.accepted = OrderedDict()

    def remember_presented(self, rows, viewer):
        with self.presented_lock:
            for row in rows:
                key = (viewer, int(row['artwork']['id']))
                self.presented[key] = row['artwork']['username']
                self.presented.move_to_end(key)
            while len(self.presented) > 2000:
                self.presented.popitem(last=False)

    def presented_artwork(self, image_id, viewer, token=None):
        if getattr(self,'mode',None)=='supabase' and self.listed(image_id):
            try:return self.browse_artwork(image_id,self.profile_provider(token))[0]
            except CommunityError:return None
        with self.presented_lock:
            if (viewer, image_id) not in self.presented:
                return None
        try:
            art, _ = self.browse_artwork(image_id, self.profile_provider(token))
            return art
        except CommunityError:
            return None

    def presented_creator(self, username, viewer, token=None):
        with self.presented_lock:
            ids = [image for (owner, image), name in self.presented.items()
                   if owner == viewer and name.casefold() == username.casefold()]
        return next((art for image in ids if (art := self.presented_artwork(image, viewer, token))), None)

    def _identity(self,token):
        if isinstance(self.verifier,FixtureVerifier) and token in self.verifier.members:
            return self.verifier.members[token].id
        try:return self.identity_provider() if hasattr(self,'identity_provider') else None
        except Exception:return None

    def _read(self,operation,token,payload):
        store=getattr(self,'store',None);identity=self._identity(token)
        if getattr(self,'mode',None)=='supabase':
            # Admission/moderation/availability are server-owned. A 24-hour local
            # feed/status cache must not mask revocation or disabled policy.
            try:
                value=self.backend.request(operation,None,
                    {k:v for k,v in payload.items() if k not in ('refresh','refreshContext')})
            except CommunityError as error:
                if error.code in ('AUTH_REQUIRED','AUTH_EXPIRED','INVITE_ONLY','INVITE_REVOKED','INVITE_EXPIRED','SUBMITTER_BLOCKED','BETA_DISABLED','FORBIDDEN'):
                    with self.presented_lock:self.accepted.clear();self.presented.clear()
                raise
            return {**value,'cache':{'state':'current'}}
        if not store or not identity:
            if getattr(self,'dev_offline',False):raise CommunityError('UNAVAILABLE',503)
            return self.backend.request(operation,token,payload)
        key=f'{self.mode}:member:{identity}:{operation}:{payload.get("cursor") or "first"}:{payload.get("limit",24)}'
        entry=store.get(key)
        def load():
            if getattr(self,'dev_offline',False):raise CommunityError('UNAVAILABLE',503)
            return self.backend.request(operation,self.token_provider() if not isinstance(self.verifier,FixtureVerifier) else token,
                                        {k:v for k,v in payload.items() if k not in ('refresh','refreshContext')})
        if entry and not isinstance(self.verifier,FixtureVerifier):
            if entry['stale'] or payload.get('refresh'):store.refresh(key,load,background=True)
            return {**entry['value'],'cache':{'state':'stale' if entry['stale'] else 'cached',
                'verifiedAt':entry['verified_at'],'refreshing':entry['stale'] or bool(payload.get('refresh'))}}
        try:
            value=load();store.put(key,value)
            return {**value,'cache':{'state':'current','verifiedAt':store.clock()}}
        except CommunityError:
            if entry:return {**entry['value'],'cache':{'state':'stale','verifiedAt':entry['verified_at'],'offline':True}}
            raise

    def listed(self,image):
        with self.presented_lock:
            entry=self.accepted.get((self._identity(None),image))
        return bool(entry and entry[1]>time.time())

    def listed_creator(self,image):
        with self.presented_lock:entry=self.accepted.get((self._identity(None),image))
        return entry[0]['creatorId'] if entry and entry[1]>time.time() else None

    def browse_artwork(self,image,profile,*,force=False):
        if getattr(self,'mode',None)=='supabase':
            if not self.listed(image):raise CommunityError('INVALID_ARTWORK',404)
            resolver=getattr(self,'gallery_resolver',None)
            if not resolver:raise CommunityError('INVALID_ARTWORK',404)
            return resolver(image),{'state':'shared-gallery'}
        cache=getattr(self,'artwork_cache',None)
        art,info=cache.browse(image,force=force) if cache else (self.verifier.artwork(image),{'state':'current'})
        if not profile.allows(art):raise CommunityError('CONTENT_FILTERED',403)
        return art,info

    @staticmethod
    def browser_artwork(art, *, thumbnail_proxy=True):
        value=art.browser()
        value['tagState']={'known':art.tags_known,'tags':[{'name':name,'hidden':False} for name in art.tags]}
        if thumbnail_proxy and urllib.parse.urlsplit(art.preview).hostname == 'image.civitai.com':
            value['thumbnailUrl']=value['url']=f'/api/community/thumbnail/{art.id}'
        return value

    def request(self, operation, payload, source="loopback", *, fixture_token=None):
        self.rate.check((source, fixture_token) if fixture_token else source)
        try:
            token = None if operation in ('status','gallery') and not isinstance(self.verifier,FixtureVerifier) else self.token_provider()
        except Exception as error:
            from .oauth import OAuthReauthenticationRequired, OAuthTemporarilyUnavailable
            if operation not in ('status','gallery'):
                code='AUTH_EXPIRED' if isinstance(error,OAuthReauthenticationRequired) else 'UNAVAILABLE' if isinstance(error,OAuthTemporarilyUnavailable) else 'AUTH_REQUIRED'
                raise CommunityError(code,503 if code=='UNAVAILABLE' else 401) from None
            token=None
        if fixture_token is not None:
            if not isinstance(self.verifier, FixtureVerifier) or getattr(self, 'mode', None) != 'fixture':
                raise CommunityError('FORBIDDEN', 403)
            token = fixture_token
        if operation=='report':
            if getattr(self,'mode',None)!='supabase':raise CommunityError('UNAVAILABLE',503)
            if payload.get('reason') not in ('inappropriate','abuse','ownership','unavailable'):
                raise CommunityError('INVALID_REQUEST')
            return self.backend.request('report',token,{'artwork':parse_artwork(payload.get('artwork')),'reason':payload['reason']})
        request_identity=self._identity(token) if getattr(self,'mode',None)=='supabase' else None
        if operation=='status' and getattr(self,'mode',None)=='supabase':
            return self._read(operation,None,payload)
        if operation=='gallery' and getattr(self,'mode',None)=='supabase':
            from .community_feed import decode
            identity=request_identity
            if identity is None:raise CommunityError('AUTH_REQUIRED',401)
            result=self._read('gallery',None,{'cursor':payload.get('cursor'),'limit':payload.get('limit',50)})
            if result.get('feedVersion')!=3 or not isinstance(result.get('items'),list) or len(result['items'])>50:
                raise CommunityError('UNAVAILABLE',503)
            decoded=[(row,*decode(row)) for row in result['items']]
            if identity!=self._identity(None):raise CommunityError('AUTH_EXPIRED',401)
            with self.presented_lock:
                for row,image,creator,expires in decoded:
                    key=(identity,image);self.accepted[key]=(row,expires);self.accepted.move_to_end(key)
                while len(self.accepted)>2000:self.accepted.popitem(last=False)
            return {**result,'counts':{'acceptedPage':len(decoded),'returned':len(decoded),'contentControls':0}}
        profile = self.profile_provider(token)
        if getattr(self,'mode',None)=='supabase' and request_identity!=self._identity(token):
            raise CommunityError('AUTH_EXPIRED',401)
        if operation=='detail':
            art,_=self.browse_artwork(parse_artwork(payload.get('artwork')),profile)
            value=self.browser_artwork(art)
            return {**value,'detailImageUrl':value['thumbnailUrl'],'civitaiUrl':value['imageUrl'],
                'known':art.tags_known,'tags':[{'name':name,'hidden':False} for name in art.tags],
                'prompt':'','stats':{}}
        if operation in ("verify", "recommend"):
            if getattr(self,'dev_offline',False):raise CommunityError('UNAVAILABLE',503)
            art = self.verifier.artwork(parse_artwork(payload.get("artwork")))
            if hasattr(self.verifier,'live_verifier'):
                live=self.verifier.live_verifier
                actual=live.member(live.token_provider())
                if art.creator_id == actual.id:raise CommunityError('SELF_RECOMMENDATION',403)
            if not profile.allows(art):
                raise CommunityError("CONTENT_FILTERED", 403)
            result = self.backend.request(operation, token, {"artwork": art.id,
                **({'idempotencyKey':payload['idempotencyKey']} if 'idempotencyKey' in payload else {})})
            if hasattr(self,'artwork_cache'):self.artwork_cache.remember(art)
            if operation == "verify":
                result["artwork"] = self.browser_artwork(art, thumbnail_proxy=False)
            if operation=='recommend' and hasattr(self,'store'):
                identity=self._identity(token)
                key=f'{self.mode}:member:{identity}:status:first:24'
                prior=self.store.get(key)
                if prior:self.store.put(key,{**prior['value'],'quota':result['quota']})
                gallery_key=f'{self.mode}:member:{identity}:gallery:first:24'
                prior=self.store.get(gallery_key)
                if prior:
                    with self.store.connect() as db:db.execute('UPDATE metadata_cache SET verified_at=0 WHERE key=?',(gallery_key,))
            return result
        result = self._read(operation,token,payload) if operation in ('status','gallery') else self.backend.request(operation, token, payload)
        if operation == "gallery":
            allowed = []
            cache=getattr(self,'artwork_cache',None)
            boundary=None
            if cache and payload.get('refresh') and payload.get('refreshContext') is not None:
                identity=self._identity(token)
                if identity is None:raise CommunityError('AUTH_REQUIRED',401)
                boundary=cache.refresh_boundary(payload['refreshContext'],(identity,profile))
            hydrated=cache.gallery((positive_id(row['imageId']) for row in result['items']),
                force=bool(payload.get('refresh')),verified_after=boundary) if cache and not isinstance(self.verifier,FixtureVerifier) else None
            counts={'acceptedPage':len(result['items']),'contentControls':0,'unavailableMetadata':0,'ownerMismatch':0}
            exclusions={name:0 for name in ('ratingRestriction','hiddenCreator','hiddenArtwork','hiddenTag','blockedUser','incompleteMetadata','other')}
            for row in result["items"]:
                try:
                    image=positive_id(row['imageId'])
                    if hydrated is not None:
                        entry=hydrated[image]
                        if isinstance(entry,CommunityError):raise entry
                        art,info=entry
                    else:
                        art,info=cache.browse(image,force=bool(payload.get('refresh'))) if cache else (self.verifier.artwork(image),{'state':'current'})
                    reasons=profile.exclusions(art)
                    if reasons:
                        counts['contentControls']+=1
                        for reason in reasons:exclusions[reason]+=1
                        continue
                except CommunityError as error:
                    if error.code in ("INVALID_ARTWORK", "UNVERIFIED_OWNER",'CONTENT_FILTERED'):
                        counts['contentControls' if error.code=='CONTENT_FILTERED' else 'unavailableMetadata']+=1
                        continue
                    raise
                if art.creator_id == row["creatorId"] and profile.allows(art):
                    allowed.append({**row,'creatorId':art.creator_id if art.owner_verified else None,
                        "artwork": self.browser_artwork(art),'metadata':info})
                else:counts['ownerMismatch']+=1
            result["items"] = allowed
            result['counts']={**counts,'returned':len(allowed),'exclusions':exclusions}
            result['controls']={'hiddenCreators':len(profile.creators-profile.blocked_creators),
                'blockedUsers':len(profile.blocked_creators),'hiddenArtworks':len(profile.images),'hiddenTags':len(profile.tag_ids)}
            if hasattr(self,'store'):
                with self.store.lock:result['metadataRefreshing']=bool(self.store.pending) or result.get('metadataRefreshing',False) or result.get('reviewPending',False)
        return result


def build_community(data_root, profile_provider, token_provider):
    mode = os.environ.get("CAD_COMMUNITY_MODE", "local")
    if getattr(sys,'frozen',False):
        if mode=='fixture':raise RuntimeError('Fixture community is excluded from production')
        from .community_beta import BETA_ENDPOINT
        mode='supabase' if BETA_ENDPOINT else 'offline'
    fixture = mode == "fixture"
    if fixture and (os.environ.get("CAD_DEVELOPMENT") != "1" or getattr(__import__('sys'),'frozen',False)):
        raise RuntimeError("Fixture community requires CAD_DEVELOPMENT=1")
    if mode not in ("local", "fixture", "supabase", "offline"):
        raise RuntimeError("Unknown CAD_COMMUNITY_MODE")
    manifest = None
    if fixture and os.environ.get('CAD_COMMUNITY_SAMPLE'):
        sample_path = Path(os.environ['CAD_COMMUNITY_SAMPLE']).resolve()
        if Path(data_root).resolve() not in sample_path.parents:
            raise RuntimeError('Development sample must stay inside isolated data root')
        manifest = json.loads(sample_path.read_text(encoding='utf-8'))
    verifier = FixtureVerifier(development=True, manifest=manifest) if fixture else CivitaiVerifier(token_provider=token_provider)
    if fixture and os.environ.get('CAD_COMMUNITY_TESTING')!='1':
        real_ids={positive_id(row['id']) for row in (manifest or {}).get('artworks',[])}
        verifier.artworks={image:art for image,art in verifier.artworks.items() if image in real_ids}
        verifier.live_verifier=CivitaiVerifier(token_provider=token_provider)
    if mode == "supabase":
        from .community_beta import BETA_ENDPOINT
        from .oauth import get_community_session
        backend = SupabaseCommunityBackend(BETA_ENDPOINT if getattr(sys,'frozen',False) else os.environ.get("CAD_COMMUNITY_URL"),identity_token_provider=get_community_session)
    else:
        backend = LocalCommunityBackend(Path(data_root) / "community" / ("fixtures.sqlite3" if fixture else "local.sqlite3"), verifier)
        if fixture:
            state_path = backend.path.parent / 'fixture-upstream-state.json'
            if state_path.exists():
                state = json.loads(state_path.read_text(encoding='utf-8'))
                for token, controls in state.get('hidden', {}).items():
                    if token not in verifier.members: raise RuntimeError('Unknown fixture member in saved controls')
                    content_profile(controls, (1,2,4,8,16))
                    verifier.hidden[token] = controls
                for image in state.get('removed', []):
                    verifier.removed.add(positive_id(image)); verifier.artworks.pop(image, None)
        if fixture and os.environ.get('CAD_COMMUNITY_TESTING') == '1':
            clock_path = backend.path.parent / 'test-clock.json'
            value = (json.loads(clock_path.read_text(encoding='utf-8')) if clock_path.exists()
                     else '2026-10-14T12:00:00+00:00')
            fixed = datetime.fromisoformat(value)
            if fixed.tzinfo is None: raise RuntimeError('Invalid isolated test clock')
            backend.clock = lambda: fixed
    if fixture:
        token_provider = lambda: "fixture:1"
        # Fixtures use local viewing levels, with synthetic hidden profiles in tests.
        base_provider = profile_provider
        def profile_provider(token):
            base = base_provider(None)
            hidden = verifier.hidden.get(token)
            if hidden is None:return base
            extra=content_profile(hidden,base.levels)
            return ContentProfile(**{name:(getattr(base,name) if name in ('apply_tag_filters', 'apply_account_filters', 'apply_image_filters')
                                         else getattr(base,name)|getattr(extra,name))
                for name in ContentProfile.__dataclass_fields__})
    client = CommunityClient(backend, verifier, token_provider, profile_provider)
    client.mode = mode
    from .cache import MetadataCache,ArtworkCache,ThumbnailCache
    client.store=MetadataCache(Path(data_root)/'cache'/'community-metadata.sqlite3')
    if mode!='supabase':client.artwork_cache=ArtworkCache(client.store,verifier,mode)
    client.thumbnails=ThumbnailCache(Path(data_root)/'cache'/'community-thumbnails',allow_downscale=True)
    # Bootstrap public fixture metadata once; never refresh existing cache on launch.
    if fixture:
        for art in verifier.artworks.values():
            if client.store.get(client.artwork_cache.key(art.id)) is None:client.artwork_cache.remember(art)
    if not fixture:
        from .oauth import status
        client.identity_provider=lambda: status().get('id')
    return client
