"""Local artist-first Civitai discovery feed."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque, OrderedDict
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
import json
import math
import mimetypes
import os
import re
import socket
import subprocess
from pathlib import Path
from statistics import mean
import threading
import time
import sys
import urllib.parse
import urllib.error
import webbrowser
import traceback
from discovery.community import (build_community, CommunityError, ContentProfile)
from discovery.cache import ThumbnailCache

from discovery.civitai import API_LANE, CandidateCache
from discovery.capture import AutoCapture
from discovery.archive_calendar import build_request as archive_build_request
from discovery.availability import ArtworkAvailability
from discovery.timemachine import TimeMachine
from discovery.history import HistoryArchive, RetryBudgetExhausted, parse_day, previous_local_day
from discovery.selection import GallerySelection, media_type
from discovery.oauth import (CALLBACK_PORT, OAuthSetupError, client_info,
                             disconnect as oauth_disconnect, login as oauth_login,
                             set_client_id)
from discovery.paths import application_root, data_root
from discovery.settings import AppSettings
from discovery.site import SITE_ORIGIN, profile_url
from discovery.diagnostics import append_log, log_browser_events, community_failure
from discovery.social import (CivitaiHTTPError, SocialClient, auth_status,
                              following_ids, reaction_names)
from discovery.taste import (EMERGING_FOLLOWERS, GALLERY_HEART_MIN, TasteStore,
                             WORTH_FOLLOWING_MIN)
from discovery.tray import start_windows_tray, stop_windows_tray
from discovery.updater import UpdateManager, apply_staged_update


ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
STATIC = ROOT / "static"
APP_NAME = "Civitai Discovery 3.0 Beta"
APP_VERSION = "3.0.0-beta.13"
from discovery.dev_diagnostics import enabled as development_diagnostics_enabled, git_commit
DEVELOPMENT_COMMIT = git_commit(ROOT) if development_diagnostics_enabled() else 'unavailable'
DATA_ROOT = data_root()
DATA_ROOT.mkdir(parents=True, exist_ok=True)
from discovery.registry import RegistryClient
REGISTRY = RegistryClient(APP_VERSION)
PERSONAL_THUMBNAILS=ThumbnailCache(DATA_ROOT/'cache'/'artwork-thumbnails',allow_downscale=True)
CACHE = CandidateCache(DATA_ROOT / "cache" / "candidates.json")
FOLLOW_CACHE = DATA_ROOT / "following.json"
CREATOR_PROFILES = DATA_ROOT / "cache" / "creator_profiles.json"
SETTINGS = AppSettings(DATA_ROOT / "settings.json")
INITIAL_SETTINGS = SETTINGS.load()
HISTORY = HistoryArchive(DATA_ROOT / "history", INITIAL_SETTINGS["contentRating"],
                         INITIAL_SETTINGS["browsingLevels"])
TASTE = TasteStore(DATA_ROOT / "discovery")
UPDATES = UpdateManager(DATA_ROOT, application_root(), APP_VERSION)
# The listing sweep is the only step that expires, so it is the only one run unattended.
CAPTURE = AutoCapture(HISTORY, SETTINGS)
# Shares HISTORY's paced request lane, so priming queues behind a day build rather
# than racing it for the same API budget.
TIME_MACHINE = TimeMachine(DATA_ROOT / "discovery", HISTORY, TASTE)
WRITE_LOCK = threading.RLock()
ARTWORK_AVAILABILITY = ArtworkAvailability()
HISTORY.on_block_complete = lambda key, merged: prepare_finished_block(key, merged)


def renew_session_if_stale() -> None:
    """Refresh the Civitai authorization before it lapses, if one is stored.

    `get_access_token` renews when the token is nearly expired, so simply asking for it is
    the renewal. Failures are the caller's to ignore: a session that cannot be renewed
    should surface as disconnected, not as an error while browsing.
    """
    from discovery.oauth import get_access_token
    get_access_token()


def connected_or_false() -> bool:
    """auth_status() raises when no session is stored, which is not a server fault."""
    try:
        return bool(auth_status().get("connected"))
    except Exception:
        return False
OAUTH_LOCK = threading.Lock()
OAUTH_JOB = {"state": "idle", "error": None}
REACTIONS = {"Like": "likeCount", "Heart": "heartCount", "Laugh": "laughCount", "Cry": "cryCount"}
INSTANCE_FILE = DATA_ROOT / "running-instance.json"
INSTANCE_MUTEX = None
RUNNING_PORT = None
COMMUNITY = None
COMMUNITY_LOCK = threading.Lock()
COMMUNITY_VIEW_SESSION = os.urandom(16).hex()  # Cache generation, never authentication material.


def community_content_profile(token):
    """Both galleries use the same imported account controls and settings."""
    isolated=os.environ.get('CAD_COMMUNITY_MODE')=='fixture' and os.environ.get('CAD_COMMUNITY_TESTING')=='1'
    if not isolated and not TASTE.ensure_current_account():raise CommunityError('AUTH_REQUIRED',401)
    with TASTE.connect() as db:imported=TASTE._state(db,'hidden_imported_at')
    if not imported and not isolated:TASTE.import_hidden_preferences()
    if not isolated:TASTE.ensure_hidden_preferences_current()
    with TASTE.connect() as db:
        settings = SETTINGS.load()
        raw = TASTE._state(db, 'hidden_image_preferences')
        from discovery.community import content_profile
        rules = content_profile({**json.loads(raw), 'hiddenUsers': [], 'blockedUsers': [],
            'blockedByUsers': []}, settings['browsingLevels']) if raw else ContentProfile()
        return ContentProfile(levels=frozenset(settings['browsingLevels']),
            apply_tag_filters=settings['applyTagFilters'],
            apply_account_filters=settings['applyAccountFilters'],
            # Only legacy mirrors have unknown image sources to preserve.
            apply_image_filters=settings['applyImageFilters'] if raw or not imported else True,
            implicit_images=rules.implicit_images, rating_tags=rules.rating_tags,
            unknown_images=rules.unknown_images, known_tag_ids=rules.known_tag_ids,
            creators=frozenset(r[0] for r in db.execute('select creator_id from hidden_creators')),
            usernames=frozenset(r[0] for r in db.execute('select username_key from hidden_creators where username_key is not null')),
            images=frozenset(r[0] for r in db.execute('select image_id from hidden_images')),
            tags=frozenset(r[0] for r in db.execute('select tag_name from hidden_tags')),
            tag_ids=frozenset(r[0] for r in db.execute('select tag_id from hidden_tags')),
            blocked_creators=frozenset(r[0] for r in db.execute("select creator_id from hidden_creators where reason='blocked'")),
            blocked_usernames=frozenset(r[0] for r in db.execute("select username_key from hidden_creators where reason='blocked' and username_key is not null")))

def shared_cached_artwork(image):
    """Compatibility for local actions; source is the same Gallery image cache."""
    from discovery.community import Artwork
    store=image_store(image)
    if not store:raise CommunityError('INVALID_ARTWORK',404)
    value=HISTORY.gallery_images([image])[0] if store is HISTORY else store.detail(image)
    tags=TASTE.image_tags(image)
    if value['browsingLevel'] not in SETTINGS.load()['browsingLevels']:raise CommunityError('CONTENT_FILTERED',403)
    hidden,images=hidden_preferences()
    creator=COMMUNITY.listed_creator(image) if COMMUNITY else None
    profile=community_content_profile(None)
    if (profile.apply_account_filters and creator in profile.creators|profile.blocked_creators) or value['username'].casefold() in hidden or image in images or (profile.apply_tag_filters and TASTE.hidden_tag_names() and (not tags['known'] or any(t['hidden'] for t in tags['tags']))):raise CommunityError('CONTENT_FILTERED',403)
    return Artwork(image,creator or 0,value['username'],value['browsingLevel'],value['url'],tuple(t['name'] for t in tags['tags']),tags_known=tags['known'],base_model=value['baseModel'],media_type=value['type'])


def community_client():
    global COMMUNITY
    with COMMUNITY_LOCK:
        if COMMUNITY is None:
            from discovery.oauth import get_access_token
            COMMUNITY = build_community(DATA_ROOT, community_content_profile, get_access_token)
            COMMUNITY.gallery_resolver=shared_cached_artwork
            if COMMUNITY.mode=='supabase':COMMUNITY.thumbnails=PERSONAL_THUMBNAILS
    return COMMUNITY


def community_detail_metadata(client, artwork):
    """Reuse local collector metadata, otherwise cache one on-demand detail locally."""
    image_id = int(artwork['id'])
    store = image_store(image_id)
    fields = ('prompt', 'negativePrompt', 'resources', 'baseModel', 'width', 'height', 'createdAt', 'stats')
    if store:
        detail = store.detail(image_id)
    else:
        from discovery.community_security import test_identity
        if test_identity(DATA_ROOT):
            return {}  # No outbound request or fabricated generation metadata in fixtures.
        key = f'{client.mode}:detail:{image_id}'
        def load():
            from discovery.civitai import normalize
            payload, _ = HISTORY._request({'imageId':image_id, 'withMeta':'true',
                                           'browsingLevel':artwork['browsingLevel']},
                                          minimum_interval=1.5, request_attempts=3, request_timeout=20)
            raw = next((row for row in payload.get('items', []) if row.get('id') == image_id), None)
            if not raw or str(raw.get('username', '')).casefold() != artwork['username'].casefold():
                raise CommunityError('INVALID_ARTWORK', 404)
            value = normalize(raw)
            return {name:value[name] for name in fields if name in value}
        entry = client.store.get(key)
        if entry:
            if entry['stale']: client.store.refresh(key, load, background=True)
            detail = entry['value']
        else:
            detail = load(); client.store.put(key, detail)
    return {name:detail[name] for name in fields if name in detail}
# How long a restarting copy waits for the outgoing one to release the single-instance
# mutex and its port. Both are held until that process finishes its shutdown, and a
# replacement that gave up immediately would leave the reader with no app running.
RESTART_HANDOVER_SECONDS = 30.0


# Parsed once per version of the file rather than on every request: every gallery page
# read and re-parsed several megabytes of JSON that changes only when a sweep resolves new
# creators, which measured ~0.15s of each page's second. The file is replaced atomically,
# so its (modified time, size) identifies the version and a rewrite is picked up at once.
PROFILE_CACHE: dict = {"key": None, "value": {}}


def creator_profiles() -> dict:
    with WRITE_LOCK:
        try:
            info = CREATOR_PROFILES.stat()
        except OSError:
            return {}
        key = (info.st_mtime_ns, info.st_size)
        with WRITE_LOCK:
            if PROFILE_CACHE["key"] == key:
                return PROFILE_CACHE["value"]
        try:
            value = json.loads(CREATOR_PROFILES.read_text(encoding="utf-8"))
            profiles = value.get("byUsername", {}) if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
        with WRITE_LOCK:
            PROFILE_CACHE["key"], PROFILE_CACHE["value"] = key, profiles
        return profiles

def profile_avatar(profile: dict | None) -> str | None:
    picture = (profile or {}).get("profilePicture") or {}
    value = picture.get("url")
    if not value:
        return None
    if str(value).startswith(("http://", "https://")):
        # Some creators sign in through Google and their avatar is hosted off Civitai.
        # The page's Content Security Policy blocks those, so returning the URL only
        # produces a console error before the card falls back to initials. Return nothing
        # and let the card render initials directly.
        host = urllib.parse.urlparse(str(value)).hostname or ""
        allowed = host == "image.civitai.com" or host.endswith(".civitai.com")
        return str(value) if allowed else None
    filename = urllib.parse.quote(str(picture.get("name") or "avatar.jpeg"))
    return f"https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/{value}/width=160/{filename}"


def image_stores(image_id: int) -> list:
    """Every collector holding this image, in preference order.

    The same image can sit in both the daily archive and the Time Machine, each with the
    counts that were true when that collector fetched it. A reaction has to write through
    all of them, or the copy it missed keeps serving its older numbers.
    """
    return [store for store in (HISTORY, TIME_MACHINE) if store.has_image(image_id)]


def live_reaction_stats(payload: object) -> dict:
    """Civitai's own counts for an image, named as the app stores them.

    image.get reports "likeCountAllTime" and friends, and it is the freshest number
    anyone has: both stored copies are snapshots from whenever they were collected.
    """
    stats = payload.get("stats") if isinstance(payload, dict) else None
    if not isinstance(stats, dict):
        return {}
    result = {}
    for key in ("likeCount", "heartCount", "laughCount", "cryCount", "dislikeCount",
                "commentCount"):
        value = stats.get(f"{key}AllTime")
        if isinstance(value, (int, float)):
            result[key] = max(0, int(value))
    if not result:
        return {}
    result["reactionCount"] = sum(result.get(key, 0) for key in REACTIONS.values())
    return result


def image_store(image_id: int):
    """Which collector holds this image, or None.

    Guarding on "the app collected this" is not enough on its own: the code behind the
    guard then has to read and write through the store that actually holds the row.
    Reading stats from the daily archive for an image it does not have returns {}, so a
    reaction wrote 1 instead of the previous count plus one.
    """
    if HISTORY.has_image(image_id):
        return HISTORY
    if TIME_MACHINE.has_image(image_id):
        return TIME_MACHINE
    return None


def collected_image(image_id: int) -> bool:
    """Whether this image came from somewhere the app collected, not just anywhere.

    The reaction and content-control endpoints refuse images the app has not collected,
    which is what keeps them from being general-purpose lookups against Civitai. Every
    such guard has to consult every collector, or a feature works in one place and fails
    in another for no reason a user could understand.
    """
    return image_store(image_id) is not None


def decorate_history_artist(artist: dict, profiles: dict | None = None, follows: set[str] | None = None,
                            signals: dict | None = None, seen: set | None = None) -> dict:
    username = artist["username"]
    profile = (profiles if profiles is not None else creator_profiles()).get(username.casefold())
    followed = follows if follows is not None else followed_usernames()
    following = username.casefold() in followed
    # How many of this creator's images the account has reacted to, regardless of the
    # day on screen. Five earns the gallery familiarity heart; the dashboard's stronger
    # "Worth following" recommendation starts at ten. Both disappear once followed.
    reacted_count = (signals or {}).get("reacted", {}).get(username.casefold(), 0)
    return {**artist, "profileUrl": profile_url(username),
        "avatarUrl": profile_avatar(profile), "following": following,
        "userId": (profile or {}).get("id"),
        "reactedCount": reacted_count,
        "reactedOften": not following and reacted_count >= GALLERY_HEART_MIN,
        "worthFollowing": not following and reacted_count >= WORTH_FOLLOWING_MIN,
        # Read fresh on every request, unlike the order: dimming a card the moment it
        # is marked seen is the whole point, while the order it sits in only moves on
        # the next fresh load — see cached_day_view_order.
        "seen": username.casefold() in (seen or ()),
        **_follower_fields(profile)}


def connected_user_id() -> int | None:
    try:
        value = auth_status().get("id")
        return int(value) if value is not None else None
    except (RuntimeError, TypeError, ValueError):
        return None


def attach_preview_tags(artists: list[dict]) -> list[dict]:
    """Ship already-known safety decisions with cards, avoiding a second browser read.

    Missing tags still go through the normal viewport verification endpoint. Attaching
    tags never initiates a remote lookup or treats unknown content as verified.
    """
    ids = [(artist.get("representative") or {}).get("id") for artist in artists]
    tags = TASTE.image_tags_many(value for value in ids if value is not None)
    for artist, image_id in zip(artists, ids):
        if artist.get('representative'):artist['representative']=personal_preview(artist['representative'])
        state = tags.get(image_id)
        if state and state["known"]:
            artist["representative"] = {**artist["representative"], "tagState": state}
    return artists


def personal_preview(image):
    # Preserve anonymous/mock fixture transports; the authenticated desktop uses
    # a bounded local cache for actual Civitai image previews, including carousels.
    if (image.get('type')=='image' and urllib.parse.urlsplit(image.get('url','')).hostname=='image.civitai.com'
            and connected_user_id()):
        return {**image,'url':f'/api/history/thumbnail/{image["id"]}',
            'thumbnailUrl':f'/api/history/thumbnail/{image["id"]}',
            # CDN hints allow bounded probes if a cached preview fails. Display still
            # requires the existing rating, account and complete-tag checks.
            'availabilitySources':list(dict.fromkeys(filter(None,(image.get('thumbnailUrl'),image['url']))))}
    return image


def followed_usernames(user_id: int | None = None) -> set[str]:
    expected_user_id = user_id if user_id is not None else connected_user_id()
    if expected_user_id is None:
        return set()
    with WRITE_LOCK:
        if FOLLOW_CACHE.exists():
            try:
                data = json.loads(FOLLOW_CACHE.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or int(data.get("userId", -1)) != expected_user_id:
                    return set()
                return {str(name).casefold() for name in data.get("usernames", [])} if isinstance(data, dict) else set()
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                return set()
        return set()


# The account's own Civitai Content Controls. Read from the local mirror on every
# gallery request — it is a couple of SQLite reads — so unhiding something on Civitai
# and re-importing takes effect immediately rather than after a restart.
# Working out which creators still have a visible image means a pass over the whole
# block, so the answer is cached until the mirrored settings change.
VISIBLE_CACHE: dict = {"token": None, "keys": {}}


def visible_creator_keys(key: str, hidden_images: set) -> set:
    try:
        token = (TASTE.hidden_summary().get("importedAt"), len(hidden_images))
    except Exception:
        return set()
    with WRITE_LOCK:
        if VISIBLE_CACHE["token"] != token:
            VISIBLE_CACHE.update({"token": token, "keys": {}})
        cache_key = (key, tuple(HISTORY.visible_levels))
        cached = VISIBLE_CACHE["keys"].get(cache_key)
    if cached is None:
        cached = HISTORY.creators_with_visible_images(key, hidden_images)
        with WRITE_LOCK:
            if VISIBLE_CACHE["token"] == token:
                VISIBLE_CACHE["keys"][cache_key] = cached
    return cached


def hidden_preferences() -> tuple[set, set]:
    """Active local exclusions; imported rules remain stored when paused."""
    TASTE.ensure_hidden_preferences_current()
    settings = SETTINGS.load()
    creators = TASTE.hidden_creator_keys() if settings["applyAccountFilters"] else set()
    return creators, TASTE.hidden_image_ids(include_tags=settings["applyTagFilters"],
        include_images=settings["applyImageFilters"], levels=settings["browsingLevels"])


GALLERY_VIEWS = ("discovery", "followed", "new", "emerging", "foryou")
RECENT_MODEL_MIN_SHARE = .10
# Two personalised views need data the daily archive never collected: follower counts for
# every creator, and tags for the image on each card. Both are fetched once per day in the
# background and cached permanently.
SWEEP_KINDS = ("followers", "tags")
SWEEP_JOBS: dict[str, dict] = {kind: {"running": False, "done": 0, "total": 0, "day": None,
                                      "error": None, "attemptedAll": False,
                                      "kind": kind} for kind in SWEEP_KINDS}
SWEEP_CANCEL: dict[str, threading.Event] = {kind: threading.Event() for kind in SWEEP_KINDS}
SWEEP_LOCK = threading.Lock()


def sweep_targets(kind: str, key: str):
    rows = HISTORY.day_artist_keys(key)
    if kind == "followers":
        names = [row["username"] for row in rows]
        return names, TASTE.follower_coverage(names)
    ids = [row["representativeId"] for row in rows if row["representativeId"]]
    return ids, TASTE.tag_coverage(ids)


def sweep_status(kind: str, key: str) -> dict:
    _, (known, total) = sweep_targets(kind, key)
    with SWEEP_LOCK:
        job = dict(SWEEP_JOBS[kind])
    attempted = (job.get("day") == key and not job.get("running")
                 and not job.get("error") and job.get("attemptedAll"))
    return {"kind": kind, "known": known, "total": total,
            "complete": known >= total or bool(attempted),
            "job": job}


def start_sweep(kind: str, key: str) -> dict:
    """Fetch the missing half of a day's personalisation data, in the background."""
    if kind not in SWEEP_KINDS:
        raise ValueError("Unknown sweep")
    with SWEEP_LOCK:
        if SWEEP_JOBS[kind]["running"]:
            return dict(SWEEP_JOBS[kind])
        targets, (known, total) = sweep_targets(kind, key)
        if known >= total:
            SWEEP_JOBS[kind].update({"running": False, "done": total, "total": total,
                                     "day": key, "error": None, "attemptedAll": True})
            return dict(SWEEP_JOBS[kind])
        SWEEP_CANCEL[kind].clear()
        SWEEP_JOBS[kind].update({"running": True, "done": known, "total": total,
                                 "day": key, "error": None, "attemptedAll": False})
        job = dict(SWEEP_JOBS[kind])
    threading.Thread(target=run_sweep, args=(kind, key, targets, known, total), daemon=True).start()
    return job


def run_sweep(kind: str, key: str, targets, known: int, total: int) -> None:
    """The sweep itself. Runs on its own thread from the view, or inline after a build."""
    try:
        def progress(done: int, outstanding: int) -> None:
            with SWEEP_LOCK:
                SWEEP_JOBS[kind]["done"] = min(total, known + done)
        client = SocialClient(API_LANE)
        if kind == "followers":
            processed = TASTE.sweep_followers(client, targets, SWEEP_CANCEL[kind], progress)
        else:
            processed = TASTE.sweep_image_tags(client, targets, SWEEP_CANCEL[kind], progress)
        _, (fresh, _total) = sweep_targets(kind, key)
        attempted_all = processed >= max(0, total - known)
        with SWEEP_LOCK:
            SWEEP_JOBS[kind].update({"running": False, "done": fresh,
                                     "attemptedAll": attempted_all,
                                     "error": None if attempted_all else
                                     "Preparation stopped before it finished."})
    except Exception as error:  # noqa: BLE001
        _, (fresh, _total) = sweep_targets(kind, key)
        with SWEEP_LOCK:
            SWEEP_JOBS[kind].update({"running": False, "done": fresh,
                                     "error": str(error)[:200], "attemptedAll": False})


def prepare_finished_block(key: str, merged: str | None) -> None:
    """After a block is collected, read its tags so 'For you' is ready without waiting.

    Each archive key picks its own representative image per creator, so a merged all-day
    view needs its own pass even though both halves were already swept.
    """
    if not connected_or_false():
        return

    def run() -> None:
        for target in [value for value in (key, merged) if value]:
            SWEEP_CANCEL["tags"].clear()
            with SWEEP_LOCK:
                if SWEEP_JOBS["tags"]["running"]:
                    return
                targets, (known, total) = sweep_targets("tags", target)
                if known >= total:
                    continue
                SWEEP_JOBS["tags"].update({"running": True, "done": known, "total": total,
                                           "day": target, "error": None,
                                           "attemptedAll": False})
            run_sweep("tags", target, targets, known, total)

    threading.Thread(target=run, daemon=True).start()


def gallery_signals() -> dict:
    """Personal signals for gallery ordering; an empty set simply disables the views.

    Follow state has two local sources: the taste store, which knows every followed id but
    only the usernames it has resolved, and the follow cache, which knows usernames for
    creators seen in the gallery. Using either alone leaks followed creators into the
    "new to you" view, so both are combined.
    """
    try:
        signals = TASTE.gallery_signals()
    except Exception:
        signals = {"followed": set(), "reacted": {}}
    try:
        signals["followed"] = set(signals["followed"]) | followed_usernames()
    except Exception:
        pass
    return signals


def balance_posting_volume(rows: list[dict], threshold: int = 20,
                           window: int = 5, maximum: int = 2) -> list[dict]:
    """Stably space very high-volume creators without excluding or demoting a lane.

    Scores still decide the order.  When the next scored creator would make a five-card
    window majority high-volume, the first ordinary-volume creator waiting behind them
    fills that slot instead.  Once no alternative remains, all remaining rows are kept.
    """
    pending = list(rows)
    balanced = []
    while pending:
        recent = balanced[-(window - 1):] if window > 1 else []
        high_in_window = sum(int(row.get("imageCount") or 0) > threshold for row in recent)
        pick = 0
        if high_in_window >= maximum and int(pending[0].get("imageCount") or 0) > threshold:
            pick = next((index for index, row in enumerate(pending)
                         if int(row.get("imageCount") or 0) <= threshold), 0)
        balanced.append(pending.pop(pick))
    return balanced


def day_view_order(key: str, view: str, pinned_username: str | None,
                   signals: dict, hidden_images: set | None = None,
                   hidden_creators: set | None = None,
                   eligible_creators: set | None = None,
                   seen: set | None = None, archive=None) -> tuple[list[str] | None, int | None]:
    """Order (and for 'new', filter) a whole day's creators by personal signal.

    Returns None to mean "use the archive's own order", which keeps the default view on
    its existing SQL path.
    """
    archive = archive or HISTORY
    if view not in GALLERY_VIEWS:
        return None, None
    followed, reacted = signals["followed"], signals["reacted"]
    rows = archive.day_artist_keys(key)
    preferences = SETTINGS.load()
    hide_high_volume = preferences["hideHighVolumeCreators"]
    high_volume_threshold = preferences["highVolumeThreshold"]
    if hide_high_volume:
        # "100+" is inclusive: a creator posting exactly the selected limit is hidden.
        rows = [row for row in rows if int(row.get("imageCount") or 0) < high_volume_threshold]
    hidden_creators = set(hidden_creators or ())
    if hidden_creators or eligible_creators is not None:
        rows = [row for row in rows if row["key"] not in hidden_creators and
                (eligible_creators is None or row["key"] in eligible_creators)]
    pinned = (pinned_username or "").casefold()
    if view == "discovery":
        # Popular is already the archive's native rank. It only needs an explicit order
        # when a gallery preference removes creators from that native list.
        if isinstance(archive, GallerySelection) and not hide_high_volume and pinned:
            rows.sort(key=lambda row: row["key"] != pinned)
        return (([row["key"] for row in rows], len(rows))
                if hide_high_volume or isinstance(archive, GallerySelection) else (None, None))
    if view == "foryou":
        # Score whichever image will actually be the card's cover, not the raw archive
        # pick: a hidden cover can carry the score while a different image renders, and
        # scoring the wrong one produces a top-ranked card with no visible explanation.
        effective = archive.effective_representative_ids(key, hidden_images)
        image_ids = [effective[row["key"]] for row in rows if row["key"] in effective]
        if hasattr(TASTE, "score_image_components"):
            components = TASTE.score_image_components(image_ids)
        else:
            components = {image_id: {"reaction": score, "recent": 0.0}
                          for image_id, score in TASTE.score_images(image_ids).items()}
        model_weights = TASTE.recent_model_weights() if hasattr(TASTE, "recent_model_weights") else {}
        image_models = archive.image_model_versions(image_ids)
        counts = TASTE.follower_counts([row["username"] for row in rows])
        quality = archive.creator_quality_scores(key, hidden_images)
        quality_values = [quality.get(row["key"], 0.0) for row in rows]
        quality_low = min(quality_values, default=0.0)
        quality_high = max(quality_values, default=0.0)

        def quality_score(row):
            value = quality.get(row["key"], 0.0)
            return ((value - quality_low) / (quality_high - quality_low)
                    if quality_high > quality_low else 0.0)

        def volume_penalty(row):
            # Ordinary posting volume is neutral. Above twenty daily uploads, apply a
            # logarithmic correction: a strong personal match can still win, but sheer
            # opportunity no longer floods the first page.
            images = max(1, int(row.get("imageCount") or 1))
            return min(.15, .04 * math.log2(images / 20)) if images > 20 else 0.0

        def affinity(row):
            reacted_strength = math.log1p(min(20, int(reacted.get(row["key"], 0)))) / math.log(21)
            return max(.65 if row["key"] in followed else 0.0, reacted_strength)

        seen = set(seen or ())

        def fallback_rows(candidates) -> list[dict]:
            """Balanced personal/quality order when tag coverage is absent or partial."""
            candidates = [row for row in candidates if row["key"] != pinned]
            familiar = [row for row in candidates
                        if row["key"] in followed or row["key"] in reacted]
            emerging = [row for row in candidates if row not in familiar and
                        counts.get(row["key"]) is not None and
                        counts[row["key"]] < EMERGING_FOLLOWERS]
            emerging_keys = {row["key"] for row in emerging}
            new = [row for row in candidates if row not in familiar and
                   row["key"] not in emerging_keys]
            score = lambda row: (.55 * affinity(row) + .45 * quality_score(row) -
                                 volume_penalty(row))
            for lane in (familiar, emerging, new):
                lane.sort(key=lambda row: (-score(row), row["key"]))

            def blend(is_seen: bool) -> list[dict]:
                queues = {"new": deque(row for row in new if (row["key"] in seen) == is_seen),
                          "familiar": deque(row for row in familiar
                                            if (row["key"] in seen) == is_seen),
                          "emerging": deque(row for row in emerging
                                            if (row["key"] in seen) == is_seen)}
                group = []
                while any(queues.values()):
                    for lane in ("new", "new", "familiar", "emerging"):
                        if queues[lane]:
                            group.append(queues[lane].popleft())
                return balance_posting_volume(group)
            return blend(False) + blend(True)

        if not components:
            ordered = fallback_rows(rows)
            ordered += [row for row in rows if row["key"] == pinned]
            return [row["key"] for row in ordered], len(ordered)
        def component_for(row, name):
            image_id = effective.get(row["key"], row["representativeId"])
            return components.get(image_id, {}).get(name, 0.0)
        def model_for(row):
            image_id = effective.get(row["key"], row["representativeId"])
            value = max((model_weights.get(value, 0.0)
                         for value in image_models.get(image_id, set())), default=0.0)
            return value if value >= RECENT_MODEL_MIN_SHARE else 0.0
        scored = [row for row in rows if component_for(row, "reaction") > 0 or
                  component_for(row, "recent") > 0]
        if not scored:
            ordered = fallback_rows(rows)
            ordered += [row for row in rows if row["key"] == pinned]
            return [row["key"] for row in ordered], len(ordered)

        def normalized(name):
            values = [component_for(row, name) for row in scored]
            low, high = min(values), max(values)
            return lambda row: (1.0 if high == low and high > 0 else
                                (component_for(row, name) - low) / (high - low) if high > low else 0.0)
        reaction_score, recent_score = normalized("reaction"), normalized("recent")

        similar, familiar, emerging, new = [], [], [], []
        for row in scored:
            key_name = row["key"]
            if key_name == pinned:
                continue
            if component_for(row, "recent") > 0 and model_for(row) > 0:
                similar.append(row)
            elif key_name in followed or key_name in reacted:
                familiar.append(row)
            elif counts.get(key_name) is not None and counts[key_name] < EMERGING_FOLLOWERS:
                emerging.append(row)
            else:
                new.append(row)

        discovery_score = lambda row: (.60 * reaction_score(row) + .35 * recent_score(row) +
                                       .05 * quality_score(row) - volume_penalty(row))
        similar_score = lambda row: (.55 * recent_score(row) + .30 * model_for(row) +
                                     .10 * reaction_score(row) + .05 * quality_score(row) -
                                     volume_penalty(row))
        familiar_score = lambda row: (.50 * reaction_score(row) + .20 * recent_score(row) +
                                      .25 * affinity(row) + .05 * quality_score(row) -
                                      volume_penalty(row))
        similar.sort(key=lambda row: (-similar_score(row), row["key"]))
        new.sort(key=lambda row: (-discovery_score(row), row["key"]))
        emerging.sort(key=lambda row: (-discovery_score(row), row["key"]))
        familiar.sort(key=lambda row: (-familiar_score(row), row["key"]))

        # Two current-style matches, one proven favorite, then one emerging creator.
        # A reaction-taste match fills a style slot when no model-backed match remains.
        def blend_group(is_seen: bool) -> list[dict]:
            queues = {"similar": deque(row for row in similar if (row["key"] in seen) == is_seen),
                      "new": deque(row for row in new if (row["key"] in seen) == is_seen),
                      "familiar": deque(row for row in familiar if (row["key"] in seen) == is_seen),
                      "emerging": deque(row for row in emerging if (row["key"] in seen) == is_seen)}
            group = []
            while any(queues.values()):
                for lane in ("similar", "similar", "familiar", "emerging"):
                    source = lane if queues[lane] else "new" if lane == "similar" and queues["new"] else None
                    if source:
                        group.append(queues[source].popleft())
                if queues["new"] and not any(
                        queues[name] for name in ("similar", "familiar", "emerging")):
                    group.append(queues["new"].popleft())
            return group
        # Keep unseen and previously-seen groups separate while preventing either from
        # becoming a wall of prolific accounts. Scores and lanes remain authoritative;
        # this only spaces high-volume rows when an ordinary-volume alternative exists.
        blended = balance_posting_volume(blend_group(False)) + \
                  balance_posting_volume(blend_group(True))
        chosen = {row["key"] for row in blended}
        # Partial tag coverage must not make the rest silently fall back to raw total
        # reactions. Use the same personal/quality fallback and volume spacing instead.
        remainder = fallback_rows(row for row in rows if row["key"] not in chosen)
        remainder += [row for row in rows if row["key"] == pinned and row["key"] not in chosen]
        ordered = blended + remainder
        return [row["key"] for row in ordered], len(ordered)
    if view == "emerging":
        # Emerging is the personalised For You ranking narrowed to smaller creators.
        # A known follower count is required so a missing profile is never presented as
        # a small account. Followed creators and the signed-in user's own card are out;
        # people they have reacted to may remain because that affinity is valuable.
        rows = [row for row in rows if row["key"] not in followed and row["key"] != pinned]
        counts = TASTE.follower_counts([row["username"] for row in rows])
        emerging_keys = {row["key"] for row in rows
                         if counts.get(row["key"]) is not None and
                         counts[row["key"]] < EMERGING_FOLLOWERS}
        if eligible_creators is not None:
            emerging_keys &= set(eligible_creators)
        if not emerging_keys:
            return [], 0
        return day_view_order(key, "foryou", pinned_username, signals, hidden_images,
                              hidden_creators, emerging_keys, seen)
    if not followed and not reacted:
        return (([row["key"] for row in rows], len(rows))
                if hide_high_volume else (None, None))
    if view == "new":
        # The connected user is excluded too: this view is about creators you have never
        # engaged with, and your own card is the opposite of that.
        kept = [row for row in rows if row["key"] != pinned
                and row["key"] not in followed and row["key"] not in reacted]
        return [row["key"] for row in kept], len(kept)
    # "followed": the user's own card, then followed, then creators they have reacted to,
    # then everyone else. Rank order is preserved inside each tier.
    def tier(row):
        if row["key"] == pinned:
            return 0
        if row["key"] in followed:
            return 1
        if row["key"] in reacted:
            return 2
        return 3
    ordered = sorted(rows, key=lambda row: (tier(row), row["rank"]))
    return [row["key"] for row in ordered], len(ordered)


# "For you" and "Emerging first" depend on tag and follower data that a background
# sweep keeps adding to after a day finishes building (see ensureViewData in app.js —
# switching to either view starts one if it is not already complete). day_view_order
# recomputes its order from scratch on every single page request, reading whatever
# sweep data exists at that instant. Scrolling is normal while a sweep runs, so two
# page fetches seconds apart could each see a different amount of data — and if a
# creator's rank shifts as a result, whoever was sitting at the page boundary gets
# served twice: once under the old order, once under the new.
#
# The fix is to freeze the order for one continuous scroll session and only re-derive
# it for a genuinely new one. A first attempt keyed the freeze on how much sweep data
# existed, on the theory that unchanged data meant a safe cache hit — but that data
# changes continuously while the sweep it's guarding against is actually running, so it
# invalidated on nearly every request and protected nothing exactly when protection
# mattered. What actually distinguishes "same session" from "new session" is the
# client's own session id (PAGE_SESSION + galleryToken in app.js, sent as ?session=),
# which changes precisely when a day or view is freshly opened and not otherwise — so
# that is the cache key, and no attempt is made to infer freshness from server state.
#
# Every view is frozen this way now, not just the two the sweep affects: follow state
# (Followed first / New to you) and now seen-tracking are exactly the same shape of
# problem — data that can change mid-session and would otherwise reshuffle pages the
# user has already scrolled past. Marking someone seen still dims their card
# immediately, since decorate_history_artist reads that fresh on every request; only
# their *position* waits for a new session, matching "dims now, moves to the bottom on
# refresh" rather than shifting the list while they are actively scrolling it.
ORDER_CACHE_LIMIT = 64
ORDER_CACHE: OrderedDict = OrderedDict()


def _seen_last(key: str, order: list[str] | None, total: int | None,
               seen: set, archive=None) -> tuple[list[str] | None, int | None]:
    """Push already-seen creators to the end without disturbing anyone else's order.

    Applies after whatever the view produced, including a view that returned None to
    mean "the archive's own order needs no override" — seen-tracking is independent of
    which view is active. sorted() is stable, so "not yet seen" and "seen" each keep
    exactly the relative order the view gave them.
    """
    if not seen:
        return order, total
    base = order if order is not None else [row["key"] for row in (archive or HISTORY).day_artist_keys(key)]
    ordered = sorted(base, key=lambda item: item in seen)
    return ordered, len(ordered)


def _finish_order(key: str, view: str, order: list[str] | None, total: int | None,
                  hidden_images: set | None, seen: set, archive=None) -> tuple[list[str] | None, int | None]:
    if view == "foryou" and order is not None:
        return order, total
    return _seen_last(key, order, total, seen, archive)


def cached_day_view_order(key: str, view: str, pinned_username: str | None, signals: dict,
                          hidden_images: set | None, session_token: str | None, seen: set,
                          hidden_creators: set | None = None,
                          eligible_creators: set | None = None, archive=None
                          ) -> tuple[list[str] | None, int | None]:
    if not session_token:
        order, total = day_view_order(key, view, pinned_username, signals, hidden_images,
                                      hidden_creators, eligible_creators, seen, archive)
        return _finish_order(key, view, order, total, hidden_images, seen, archive)
    cache_key = (key, view, pinned_username, getattr(archive, "signature", None))
    with WRITE_LOCK:
        cached = ORDER_CACHE.get(cache_key)
        if cached is not None and cached[0] == session_token:
            ORDER_CACHE.move_to_end(cache_key)
            return cached[1], cached[2]
    order, total = day_view_order(key, view, pinned_username, signals, hidden_images,
                                  hidden_creators, eligible_creators, seen, archive)
    order, total = _finish_order(key, view, order, total, hidden_images, seen, archive)
    with WRITE_LOCK:
        ORDER_CACHE[cache_key] = (session_token, order, total)
        ORDER_CACHE.move_to_end(cache_key)
        while len(ORDER_CACHE) > ORDER_CACHE_LIMIT:
            ORDER_CACHE.popitem(last=False)
    return order, total


def _follower_fields(profile: object) -> dict:
    """Follower count and emerging flag from a cached user.getCreator profile."""
    stats = (profile or {}).get("stats") if isinstance(profile, dict) else None
    value = (stats or {}).get("followerCountAllTime")
    count = int(value) if isinstance(value, (int, float)) else None
    return {"followers": count, "emerging": count is not None and count < EMERGING_FOLLOWERS}


FOLLOW_STATUS_LOCK = threading.Lock()
FOLLOW_STATUS_CACHE = {}


def creator_follow_status(user_ids: list[int]) -> dict:
    """One account-scoped following-list read; no per-creator profile lookups."""
    account = connected_user_id()
    if account is None: raise CommunityError('AUTH_REQUIRED', 401)
    with FOLLOW_STATUS_LOCK:
        if FOLLOW_STATUS_CACHE.get('account') != account or time.monotonic() >= FOLLOW_STATUS_CACHE.get('expires', 0):
            payload = SocialClient().query('user.getFollowingUsers', {})
            if not isinstance(payload, list) and not (isinstance(payload, dict) and
                    any(isinstance(payload.get(key), list) for key in ('items', 'users', 'following'))):
                raise RuntimeError('Civitai follow status is unavailable')
            if connected_user_id() != account: raise CommunityError('AUTH_EXPIRED', 401)
            FOLLOW_STATUS_CACHE.update(account=account, expires=time.monotonic()+60, ids=following_ids(payload))
        return {'accountId': account, 'following': {str(user): user in FOLLOW_STATUS_CACHE['ids'] for user in user_ids}}


def enrich_creator_metadata(usernames: list[str], include_follow: bool = True) -> dict[str, dict]:
    """Resolve visible creators and cache their avatars and, optionally, follow state."""
    clean = list(dict.fromkeys(name.strip() for name in usernames if name.strip()))[:100]
    # Copied because this adds newly resolved creators before writing the file back, and
    # creator_profiles() hands out the shared cached parse.
    profiles = dict(creator_profiles())
    missing = [name for name in clean if name.casefold() not in profiles]
    client = SocialClient()
    if include_follow:
        identity = auth_status()
        account_user_id = int(identity["id"])
        followed_ids = following_ids(client.query("user.getFollowingUsers", {}))

    if missing:
        # A deleted or renamed creator is an ordinary condition in a saved daily
        # archive. Keep every successful row instead of failing all 50 visible cards.
        resolved = client.batch_query_optional(
            "user.getCreator", [{"username": name} for name in missing])
        for name, profile in zip(missing, resolved):
            if isinstance(profile, dict) and profile.get("id"):
                profiles[name.casefold()] = profile
        with WRITE_LOCK:
            CREATOR_PROFILES.parent.mkdir(parents=True, exist_ok=True)
            temporary = CREATOR_PROFILES.with_suffix(".tmp")
            temporary.write_text(json.dumps({"updatedAt": datetime.now(timezone.utc).isoformat(), "byUsername": profiles}, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(CREATOR_PROFILES)

    if include_follow:
        followed_names = {
            str(profile.get("username") or name)
            for name, profile in profiles.items()
            if profile.get("id") is not None and int(profile["id"]) in followed_ids
        }
        with WRITE_LOCK:
            temporary = FOLLOW_CACHE.with_suffix(".tmp")
            temporary.write_text(json.dumps({"updatedAt": datetime.now(timezone.utc).isoformat(), "userId": account_user_id,
                "username": identity.get("username"), "count": len(followed_names),
                "usernames": sorted(followed_names, key=str.casefold)}, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(FOLLOW_CACHE)

    follower_counts = TASTE.follower_counts(clean)

    def result_for(name: str) -> dict:
        key = name.casefold()
        profile = profiles.get(key)
        fields = _follower_fields(profile)
        cached_count = follower_counts.get(key)
        if fields["followers"] is None and cached_count is not None:
            fields = {"followers": cached_count,
                      "emerging": cached_count < EMERGING_FOLLOWERS}
        result = {
            "username": name,
            "avatarUrl": profile_avatar(profile),
            "userId": (profile or {}).get("id"),
            # The all-day sweep stores counts in SQLite. The profile cache carries
            # avatars and ids, but is populated only for cards the user has loaded.
            **fields,
        }
        if include_follow:
            result["following"] = bool(
                (profile or {}).get("id") is not None
                and int(profile["id"]) in followed_ids
            )
        return result
    return {name.casefold(): result_for(name) for name in clean}


def start_oauth_login(*,community_only=False) -> None:
    with OAUTH_LOCK:
        if OAUTH_JOB["state"] == "loading": return
        OAUTH_JOB.update({"state": "loading", "error": None})
    def run():
        try:
            if community_only:
                from discovery.oauth import reconnect_community
                identity=reconnect_community()
            else:identity=oauth_login()
            if not community_only and SETTINGS.load()["autoCapture"]:CAPTURE.start()
            REGISTRY.reauthenticated(identity)
            # Mirror the account's Content Controls straight away, so the first gallery
            # after signing in already respects them. A failure here must not fail the
            # sign-in: the filter is a refinement, not the point of connecting.
            try:
                if not community_only:TASTE.import_hidden_preferences()
            except Exception as error:
                log_internal_error("Content controls import", error)
            with OAUTH_LOCK: OAUTH_JOB["state"] = "complete"
        except Exception as error:
            community_failure(DATA_ROOT,'reconnect' if community_only else 'exchange',error)
            # Most failures stay generic on purpose, but setup problems are the user's to
            # fix and unfixable by retrying, so those say what is actually wrong.
            # Never expose arbitrary provider exception text through the job.
            message = ("Community reconnect did not complete. Daily remains connected. Check your computer clock, use the same account and retry." if community_only else
                       "Civitai authorization failed. Check the callback port and try again.")
            with OAUTH_LOCK: OAUTH_JOB.update({"state": "error", "error": message})
    threading.Thread(target=run, daemon=True, name="civitai-oauth").start()


def age_hours(value: str | None) -> float:
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return 24 * 30
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 3600)


def stable_variety(image_id: int) -> float:
    digest = hashlib.sha256(f"discovery-feed-v1:{image_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / (2**64 - 1)


def discovery_order(items: list[dict]) -> list[dict]:
    """Popularity-resistant ordering without AI, embeddings, or user matching."""
    valid = []
    for item in items:
        try:
            if not isinstance(item, dict) or not str(item.get("username") or "").strip(): continue
            int(item.get("id")); int((item.get("stats") or {}).get("reactionCount", 0))
            valid.append(item)
        except (TypeError, ValueError, AttributeError):
            continue
    reactions: dict[str, list[int]] = defaultdict(list)
    for item in valid:
        reactions[item["username"].casefold()].append(max(0, int(item.get("stats", {}).get("reactionCount", 0))))
    baselines = {name: mean(values) for name, values in reactions.items()}
    scored = []
    for item in valid:
        reaction_count = max(0, int(item.get("stats", {}).get("reactionCount", 0)))
        baseline = max(0.0, baselines.get(item["username"].casefold(), 0.0))
        relative = (reaction_count + 1) / (baseline + 1)
        hidden_gem = min(1.0, math.log1p(relative) / math.log(3))
        freshness = math.exp(-age_hours(item.get("createdAt")) / (24 * 7))
        uncertainty = 1 / math.sqrt(reaction_count + 1)
        score = .34 * freshness + .26 * hidden_gem + .20 * uncertainty + .20 * stable_variety(int(item["id"]))
        scored.append((score, item))
    scored.sort(key=lambda row: row[0], reverse=True)
    return [item for _, item in scored]


def artist_gallery(items: list[dict], limit: int, nonce: str) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        groups[item["username"].casefold()].append(item)
    profiles = creator_profiles()
    follows = followed_usernames()
    creators: set[str] = set()
    result = []
    for ranked_item in discovery_order(items):
        username = ranked_item["username"]
        key = username.casefold()
        if key in creators:
            continue
        creators.add(key)
        images = sorted(groups[key], key=lambda image: image.get("createdAt") or "", reverse=True)
        digest = hashlib.sha256(f"{key}:{nonce}".encode()).digest()
        representative = dict(images[int.from_bytes(digest[:4], "big") % len(images)])
        profile = profiles.get(key)
        result.append({
            "username": username,
            "profileUrl": profile_url(username),
            "avatarUrl": profile_avatar(profile),
            "representative": representative,
            "images": images,
            "imageCount": len(images),
            "following": key in follows,
            "userId": (profile or {}).get("id"),
        })
        if len(result) >= limit:
            break
    return result


def log_internal_error(context: str, error: BaseException) -> None:
    message = f"\n[{datetime.now(timezone.utc).isoformat()}] {context}: {type(error).__name__}\n{traceback.format_exc()}"
    append_log(DATA_ROOT, message)


def claim_single_instance(no_browser: bool, replacing: bool = False) -> bool:
    """Return False after directing a second Windows launch to the first one.

    ``replacing`` marks the copy a restart just started. It waits for the outgoing
    process to let go rather than handing itself back to it: the two overlap by however
    long that one takes to finish its shutdown, and a replacement that treated the
    overlap as "already running" would exit silently and leave no app at all.
    """
    global INSTANCE_MUTEX
    if os.name != "nt":
        return True
    import ctypes
    identity = hashlib.sha256(str(DATA_ROOT.resolve()).casefold().encode()).hexdigest()[:20]
    name = f"Local\\CivitaiArtistDiscovery-{identity}"
    deadline = time.monotonic() + (RESTART_HANDOVER_SECONDS if replacing else 0)
    while True:
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
        if not handle:
            raise ctypes.WinError()
        if ctypes.windll.kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
            INSTANCE_MUTEX = handle
            return True
        ctypes.windll.kernel32.CloseHandle(handle)
        if time.monotonic() >= deadline:
            break
        threading.Event().wait(.2)
    for _ in range(30):
        try:
            existing = json.loads(INSTANCE_FILE.read_text(encoding="utf-8"))
            url = str(existing.get("url") or "")
            if is_own_app_url(url):
                if not no_browser: webbrowser.open(url, new=1)
                return False
        except (OSError, json.JSONDecodeError):
            pass
        threading.Event().wait(.1)
    return False


def lan_addresses() -> list[str]:
    """Return this computer's own network addresses, for reaching the app from a phone.

    The route lookup is asked first and answered alone when it succeeds: a machine with
    virtual adapters (VMware, Hyper-V, WSL) also owns addresses no other device on the
    network can reach, and listing those alongside the real one only invites typing the
    wrong one. Nothing is sent; connecting a UDP socket just resolves the route.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 53))  # Reserved documentation address.
            address = probe.getsockname()[0]
        if not address.startswith("127."):
            return [address]
    except OSError:
        pass
    try:
        found = {entry[4][0] for entry in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except (OSError, UnicodeError):
        return []
    return sorted(value for value in found if not value.startswith("127."))


def is_own_address(hostname: str | None) -> bool:
    """Decide whether a Host or Origin header names this computer.

    Both the DNS-rebinding guard and the cross-site guard ask this one question, so they
    cannot drift apart and leave one of them accepting an address the other refuses.
    Enabling LAN access widens it to this computer's own network addresses and no
    further: a name that merely resolves to one of them is still a stranger.
    """
    host = (hostname or "").lower().rstrip(".")
    if host in {"127.0.0.1", "localhost", "::1"}:
        return True
    return bool(host) and SETTINGS.load()["allowLanAccess"] and host in set(lan_addresses())


def is_own_app_url(url: str) -> bool:
    """Guard what a second launch is willing to open: only this computer's own app."""
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "http" or not port:
        return False
    return host in {"127.0.0.1", "localhost", "::1"} or host in set(lan_addresses())


def save_instance_url(url: str) -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = INSTANCE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"url": url, "pid": os.getpid()}), encoding="utf-8")
    os.replace(temporary, INSTANCE_FILE)


def clear_instance_url(url: str) -> None:
    try:
        value = json.loads(INSTANCE_FILE.read_text(encoding="utf-8"))
        if value.get("url") == url: INSTANCE_FILE.unlink()
    except (OSError, json.JSONDecodeError):
        pass


def bind_http_server(host: str, port: int, replacing: bool) -> ThreadingHTTPServer:
    """Bind the app's socket, waiting out a restart's overlap with the outgoing copy."""
    deadline = time.monotonic() + (RESTART_HANDOVER_SECONDS if replacing else 0)
    while True:
        try:
            return ThreadingHTTPServer((host, port), Handler)
        except OSError:
            if time.monotonic() >= deadline:
                raise
            threading.Event().wait(.2)


def request_app_shutdown(server: ThreadingHTTPServer) -> None:
    """Stop background work and ask the local HTTP server to exit."""
    CAPTURE.stop()
    HISTORY.cancel()
    TASTE.stop_sync()
    for event in SWEEP_CANCEL.values():
        event.set()
    threading.Thread(target=server.shutdown, daemon=True, name="app-shutdown").start()


def update_busy_reason() -> str | None:
    """Explain why replacing the app now could interrupt saved background work."""
    with HISTORY.lock:
        if any(job.get("state") == "loading" for job in HISTORY.jobs.values()):
            return "Wait for the daily gallery build to finish or stop it first."
    if TASTE.status().get("running"):
        return "Wait for the My Profile refresh to finish or stop it first."
    with SWEEP_LOCK:
        if any(job.get("running") for job in SWEEP_JOBS.values()):
            return "Wait for gallery preparation to finish before updating."
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "CivitaiDiscovery/1.0"
    timeout = 15

    def host_allowed(self) -> bool:
        try:
            hostname = urllib.parse.urlsplit(f"//{self.headers.get('Host') or ''}").hostname
        except ValueError:
            return False
        return is_own_address(hostname)

    def internal_error(self, context: str, error: BaseException) -> None:
        log_internal_error(context, error)
        if isinstance(error, CivitaiHTTPError) and error.status in (401, 403):
            self.json_response({"error": "Civitai refused access. Sign out and back in to renew your connection, then try again."}, error.status)
            return
        if isinstance(error, (TimeoutError, urllib.error.URLError, RetryBudgetExhausted)) or (
                isinstance(error, CivitaiHTTPError) and (error.status == 429 or error.status >= 500)):
            self.json_response({"error": "Civitai is temporarily unavailable or rate limiting requests. Your saved gallery is safe; please try again shortly."}, 503)
            return
        self.json_response({"error": "The app encountered an internal error. Details were saved to the local error log."}, 500)

    def json_response(self, value: object, status: int = 200, headers=None) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items(): self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Browsers routinely cancel an obsolete fetch when the user changes day or
            # view. That is not an application failure and must not create an error-log
            # entry or trigger a futile second response on the closed socket.
            pass

    def do_GET(self) -> None:  # noqa: N802
        from discovery.local_security import browser_request_allowed, static_relative_path
        if not self.host_allowed() or not browser_request_allowed(self.headers):
            self.send_error(403); return
        if not self.path.startswith('/') or self.path.startswith('//'):
            self.send_error(400); return
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if getattr(sys,'frozen',False) and (parsed.path.startswith(('/__dev/','/api/community/development/')) or parsed.path in ('/api/test-isolation','/community-fixture.svg')):
            self.send_error(404);return
        if parsed.path=='/api/community/beta':
            from discovery.community_beta import configuration
            self.json_response(configuration(DATA_ROOT));return
        if parsed.path == '/api/development/diagnostics':
            if not development_diagnostics_enabled() or self.client_address[0] not in ('127.0.0.1','::1'):
                self.send_error(404);return
            from discovery.community_security import authorize_test
            from discovery.dev_diagnostics import snapshot
            try:
                authorize_test(self.headers,DATA_ROOT)
                with TASTE.lock:job=dict(TASTE.job)
                self.json_response(snapshot(ROOT,DATA_ROOT,APP_VERSION,DEVELOPMENT_COMMIT,
                    auth_status=auth_status,job=job,probe=query.get('probe')==['1']))
            except PermissionError:self.send_error(403)
            except Exception as error:self.internal_error('Development diagnostics',error)
            return
        if parsed.path.startswith('/api/history/thumbnail/'):
            try:
                if not auth_status().get('id'):raise CommunityError('AUTH_REQUIRED',401)
                from discovery.community import positive_id
                image_id=positive_id(parsed.path.rsplit('/',1)[-1])
                with HISTORY.connect() as db:row=db.execute('SELECT * FROM images WHERE id=?',(image_id,)).fetchone()
                if not row or row['type']!='image':raise CommunityError('INVALID_ARTWORK',404)
                level=row['browsing_level'] or {'None':1,'Soft':2,'Mature':4,'X':8}.get(row['nsfw_level'])
                hidden_names,hidden_images=hidden_preferences()
                tags=TASTE.image_tags_many([image_id])[image_id]
                if (level not in SETTINGS.load()['browsingLevels'] or row['username_key'] in hidden_names or image_id in hidden_images
                        or (SETTINGS.load()['applyTagFilters'] and TASTE.hidden_tag_names() and (not tags['known'] or any(tag['hidden'] for tag in tags['tags'])))):
                    raise CommunityError('CONTENT_FILTERED',403)
                body,kind=PERSONAL_THUMBNAILS.get(row['url'])
                self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
                self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
                self.end_headers();self.wfile.write(body)
            except CommunityError as error:self.json_response(error.response(),error.status)
            except Exception as error:self.community_unavailable('history_thumbnail',error)
            return
        if parsed.path.startswith('/api/history/') and os.environ.get('CAD_COMMUNITY_TESTING')!='1':
            requested_day=query.get('date',[None])[0]
            requested_id=query.get('id',[None])[0]
            with HISTORY.connect() as db:
                if requested_day:
                    fixture=db.execute("SELECT 1 FROM block_images b JOIN images i ON i.id=b.image_id WHERE b.block_key=? AND i.url LIKE '%/community-fixture.svg%' LIMIT 1",(requested_day,)).fetchone()
                elif requested_id:
                    fixture=db.execute("SELECT 1 FROM images WHERE id=? AND url LIKE '%/community-fixture.svg%'",(requested_id,)).fetchone()
                else:fixture=None
            if fixture:
                if parsed.path=='/api/history/status':
                    self.json_response({'complete':False,'date':requested_day,'fixtureArchiveExcluded':True});return
                self.json_response({'error':'This archive contains isolated test artwork, not personal Civitai metadata.','code':'TEST_ARCHIVE'},409);return
        if parsed.path.startswith('/api/community/'):
            try:self.require_community_session()
            except CommunityError as error:
                self.json_response(error.response(),error.status);return
            except Exception as error:
                self.community_unavailable('status',error);return
        if parsed.path.startswith('/api/community/thumbnail/'):
            try:
                client=community_client()
                token=None
                if client.mode=='fixture':
                    cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''))
                    member=cookie.get('cad_fixture_member')
                    token=f'fixture:{member.value if member else "1"}'
                    if token not in client.verifier.members:raise CommunityError('AUTH_REQUIRED',401)
                profile=client.profile_provider(token)
                from discovery.community import positive_id
                art,_=client.browse_artwork(positive_id(parsed.path.rsplit('/',1)[-1]),profile)
                body,kind=client.thumbnails.get(art.preview,video=art.media_type=='video')
                self.send_response(200);self.send_header('Content-Type',kind)
                self.send_header('Content-Length',str(len(body)))
                self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
                self.end_headers();self.wfile.write(body)
            except CommunityError as error:self.json_response(error.response(),error.status)
            except Exception as error:self.community_unavailable('thumbnail',error)
            return
        if parsed.path == '/api/test-isolation':
            from discovery.community_security import test_identity
            identity = test_identity(DATA_ROOT)
            if identity: self.json_response(identity)
            else: self.send_error(404)
            return
        if parsed.path == "/api/community/config":
            from discovery.community_beta import configuration
            beta=configuration(DATA_ROOT)
            if beta['production']:
                self.json_response({'apiVersion':1,'development':False,'members':[],'beta':beta});return
            from discovery.community_security import fixture_enabled
            development = fixture_enabled()
            members = community_client().verifier.members.values() if development else []
            self.json_response({"apiVersion": 1, "development": development,
                "members": [{'id': m.id, 'username': m.username} for m in members]}); return
        if parsed.path.startswith("/api/community/"):
            operation = parsed.path.rsplit("/", 1)[-1]
            if operation not in ("status", "gallery",'detail'):
                self.json_response({"error": "Unknown community operation"}, 404); return
            payload = {'refresh':query.get('refresh',['0'])[0]=='1'}
            if operation=='gallery' and 'refreshContext' in query:payload['refreshContext']=query['refreshContext'][0]
            if operation=='gallery':
                payload['cursor']=query.get('cursor',[None])[0]
                try:payload['limit']=int(query.get('limit',['50'])[0])
                except ValueError:
                    self.json_response({'code':'INVALID_REQUEST','error':'Invalid page size'},400);return
            if operation=='detail':payload={'artwork':query.get('id',[None])[0]}
            self.community_response(operation, payload); return
        if parsed.path == "/community-fixture.svg":
            if os.environ.get("CAD_DEVELOPMENT") != "1" or os.environ.get("CAD_COMMUNITY_MODE") != "fixture" or os.environ.get('CAD_COMMUNITY_TESTING')!='1':
                self.send_error(404); return
            try:self.require_community_session()
            except CommunityError as error:
                self.json_response(error.response(),error.status);return
            # Synthetic, local artwork: no internet images, credentials or third-party assets.
            value = query.get("id", ["1001"])[0]
            hue = (int(value) * 37) % 360 if value.isascii() and value.isdigit() and len(value) < 16 else 200
            body = (f'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="800" viewBox="0 0 640 800">'
                    f'<rect width="640" height="800" fill="hsl({hue},45%,18%)"/>'
                    f'<circle cx="450" cy="230" r="100" fill="hsl({hue},65%,65%)"/>'
                    '<path d="M0 650L200 310L400 650L550 420L640 600V800H0Z" fill="#27383d"/>'
                    '<text x="35" y="45" fill="white" opacity=".55" font-size="18">Development fixture</text></svg>').encode()
            self.send_response(200); self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(body); return
        if parsed.path == "/api/auth-status":
            try:
                # Renew a token that is close to lapsing. Reading the stored token does not
                # refresh it, so an idle app would sit until the authorization expired and
                # the next action failed. The page polls this, so the session stays alive
                # while someone is reading rather than dying underneath them.
                from discovery.oauth import session_status, get_community_session
                with OAUTH_LOCK: job = dict(OAUTH_JOB)
                identity=session_status()
                if identity.get('connected') and not identity.get('needsReauthentication'):
                    REGISTRY.authenticated(identity,get_community_session)
                self.json_response({**identity, "oauthJob": job})
            except Exception as error:
                community_failure(DATA_ROOT,'auth_status',error)
                with OAUTH_LOCK: job = dict(OAUTH_JOB)
                self.json_response({"connected": False, "socialWrite": False,
                                    "userWrite": False,
                                    "collectionsRead": False, "collectionsWrite": False,
                                    "readOnly": True, "oauthJob": job})
            return
        if parsed.path == "/api/oauth/client":
            try:
                self.json_response(client_info())
            except Exception as error:
                self.internal_error("OAuth application", error)
            return
        if parsed.path == "/api/history/config":
            try:
                with HISTORY.connect() as db:
                    archives = bool(db.execute("SELECT 1 FROM days WHERE complete=1 LIMIT 1").fetchone())
            except Exception:
                archives = False
            self.json_response({"timezoneSource": "America/Chicago", "archiveCalendar": "America/Chicago", "latestCompleteDay": previous_local_day(), "storage": "Isolated Discovery 3.0 development directory",
                "hasArchives": archives, "version": APP_VERSION, "developmentDiagnostics": development_diagnostics_enabled()})
            return
        if parsed.path == "/api/settings":
            self.json_response({**SETTINGS.load(), "siteOrigin": SITE_ORIGIN,
                "ratings": ["Soft", "Mature", "X"],
                "browsingLevelOptions": [1, 2, 4, 8, 16],
                "lanAddresses": lan_addresses()})
            return
        if parsed.path == "/api/update/status":
            enabled = SETTINGS.load()["checkForUpdates"]
            if enabled and UPDATES.supported:
                UPDATES.start_check()
            self.json_response({**UPDATES.status(), "enabled": enabled,
                                "busyReason": update_busy_reason()})
            return
        if parsed.path == "/api/history/status":
            try:
                value = query.get("date", [previous_local_day()])[0]
                key = HISTORY.archive_key(value, query.get("segment", ["all"])[0])
                self.json_response(HISTORY.status(key))
            except ValueError as error:
                self.json_response({"error": str(error)}, 400)
            except Exception as error:
                self.internal_error("History status", error)
            return
        if parsed.path == "/api/history/blocks":
            try:
                value = query.get("date", [previous_local_day()])[0]
                blocks = {}
                for segment in ("morning", "evening", "all"):
                    state = HISTORY.status(HISTORY.archive_key(value, segment))
                    blocks[segment] = {"complete": bool(state.get("archiveComplete")),
                        "itemCount": int(state.get("itemCount") or 0),
                        "state": state.get("state"),
                        "contentRating": state.get("archiveContentRating")}
                self.json_response({"date": value, "blocks": blocks})
            except ValueError as error: self.json_response({"error": str(error)}, 400)
            except Exception as error: self.internal_error("Day blocks", error)
            return
        if parsed.path == "/api/history/models":
            try:
                value = query.get("date", [previous_local_day()])[0]
                key = HISTORY.archive_key(value, query.get("segment", ["all"])[0])
                self.json_response({"date": value, "models": GallerySelection(HISTORY, key, media=media_type(query.get("media", ["all"])[0]), hidden=hidden_preferences()[1]).day_models(key)})
            except ValueError as error: self.json_response({"error": str(error)}, 400)
            except Exception as error: self.internal_error("Day models", error)
            return
        if parsed.path == "/api/history/duplicates":
            try:
                value = query.get("date", [None])[0]
                key = (HISTORY.archive_key(value, query.get("segment", ["all"])[0])
                       if value else None)
                self.json_response(HISTORY.duplicate_report(key))
            except ValueError as error: self.json_response({"error": str(error)}, 400)
            except Exception as error: self.internal_error("Duplicate report", error)
            return
        if parsed.path == "/api/history/estimate":
            try:
                segment = query.get("segment", ["all"])[0]
                rating = query.get("contentRating", ["Soft"])[0]
                value = query.get("date", [None])[0]
                self.json_response(HISTORY.build_estimate(segment, rating, value))
            except ValueError as error: self.json_response({"error": str(error)}, 400)
            except Exception as error: self.internal_error("Build estimate", error)
            return
        if parsed.path == "/api/history/capture":
            try:
                self.json_response(CAPTURE.status())
            except Exception as error: self.internal_error("Capture status", error)
            return
        if parsed.path == "/api/timemachine":
            try:
                hidden_creators, hidden_images = hidden_preferences()
                settings = SETTINGS.load()
                # Decorated by the same function the daily gallery uses, so a Time
                # Machine card carries the follow state, avatar, follower counts and
                # reaction history a gallery card does.
                signals = gallery_signals()
                profiles, follows = creator_profiles(), signals["followed"]
                self.json_response({
                    "cards": attach_preview_tags([decorate_history_artist(card, profiles, follows, signals)
                              for card in TIME_MACHINE.cards(
                                  apply_tag_filters=settings['applyTagFilters'],
                                  hidden_creators=hidden_creators, hidden_images=hidden_images)]),
                    "status": TIME_MACHINE.status()})
            except Exception as error: self.internal_error("Time machine", error)
            return
        if parsed.path == "/api/timemachine/creators":
            try:
                self.json_response({"creators": TIME_MACHINE.search_creators(
                    query.get("q", [""])[0])})
            except Exception as error: self.internal_error("Time machine creator search", error)
            return
        if parsed.path == "/api/timemachine/creator":
            try:
                after = query.get("after", [None])[0]
                hidden_creators, hidden_images = hidden_preferences()
                settings = SETTINGS.load()
                history = TIME_MACHINE.creator_history(
                    query.get("username", [""])[0],
                    after=int(after) if after not in (None, "") else None,
                    limit=int(query.get("limit", ["40"])[0]),
                    refill=query.get("savedOnly", [""])[0] != "1",
                    apply_tag_filters=settings['applyTagFilters'],
                    hidden_creators=hidden_creators, hidden_images=hidden_images)
                signals = gallery_signals()
                profiles, follows = creator_profiles(), signals["followed"]
                # Each image rides in a one-image card entry so the gallery's own
                # decoration (avatar, follow state, reactions) applies unchanged.
                cards = [decorate_history_artist({"username": history["username"], "imageCount": 1,
                                                  "representativeIndex": 0, "representative": image},
                                                 profiles, follows, signals)
                         for image in history["images"]]
                self.json_response({**history, "cards": attach_preview_tags(cards)})
            except KeyError as error: self.json_response({"error": str(error.args[0])}, 404)
            except ValueError as error: self.json_response({"error": str(error)}, 400)
            except Exception as error: self.internal_error("Time machine creator history", error)
            return
        if parsed.path == "/api/timemachine/status":
            try:
                self.json_response(TIME_MACHINE.status())
            except Exception as error: self.internal_error("Time machine status", error)
            return
        if parsed.path == "/api/history/window":
            try:
                rating = query.get("contentRating", [None])[0]
                self.json_response(HISTORY.history_window(rating))
            except ValueError as error: self.json_response({"error": str(error)}, 400)
            except Exception as error: self.internal_error("History window", error)
            return
        if parsed.path == "/api/history/day":
            try:
                value = query.get("date", [previous_local_day()])[0]
                key = HISTORY.archive_key(value, query.get("segment", ["all"])[0])
                models = query.get("model", [])[:20]
                media = media_type(query.get("media", ["all"])[0])
                archive = GallerySelection(HISTORY, key, models, media, hidden_preferences()[1]) if models or media != "all" else HISTORY
                day = archive.day_summary(key)
                if not day.get("complete"):
                    self.json_response({"error": "That day is still loading"}, 409)
                    return
                # Report the count the gallery will actually show. Naming a total the user
                # can never reach by scrolling reads as a bug in the paging.
                hidden_creators, hidden_images = hidden_preferences()
                if hidden_creators or hidden_images:
                    present = {row["key"] for row in archive.day_artist_keys(key)}
                    showable = archive.creators_with_visible_images(key, hidden_images) if hidden_images else present
                    remaining = {name for name in present
                                 if name not in hidden_creators and name in showable}
                    day = {**day, "artistCount": len(remaining),
                           "hiddenCreators": len(present) - len(remaining)}
                self.json_response(day)
            except ValueError as error:
                self.json_response({"error": str(error)}, 400)
            except Exception as error:
                self.internal_error("History day summary", error)
            return
        if parsed.path == "/api/collections":
            try:
                if not auth_status().get("collectionsRead"):
                    self.json_response({"error": "Sign out and back in once to allow collection access."}, 403)
                    return
                self.json_response({"collections": SocialClient().writable_image_collections(connected_user_id())})
            except Exception as error:
                self.internal_error("Civitai collections", error)
            return
        if parsed.path == "/api/history/calendar":
            try:
                days=HISTORY.calendar_days()
                if os.environ.get('CAD_COMMUNITY_TESTING')!='1':
                    with HISTORY.connect() as db:
                        excluded={row[0].split('#')[0] for row in db.execute("SELECT DISTINCT b.block_key FROM block_images b JOIN images i ON i.id=b.image_id WHERE i.url LIKE '%/community-fixture.svg%'")}
                    days=[day for day in days if day['date'] not in excluded]
                self.json_response({"days": days})
            except Exception as error:
                self.internal_error("History calendar", error)
            return
        if parsed.path == "/api/history/artists":
            try:
                value = query.get("date", [previous_local_day()])[0]; key = HISTORY.archive_key(value, query.get("segment", ["all"])[0])
                offset = max(0, int(query.get("offset", [0])[0])); limit = min(100, max(1, int(query.get("limit", [50])[0])))
                view = query.get("view", ["discovery"])[0]
                profiles, follows = creator_profiles(), followed_usernames()
                try: pinned_username = auth_status().get("username")
                except Exception: pinned_username = None
                signals = gallery_signals()
                hidden_creators, hidden_images = hidden_preferences()
                models = [item for item in query.get("model", []) if item][:20]
                media = media_type(query.get("media", ["all"])[0])
                archive = GallerySelection(HISTORY, key, models, media, hidden_images) if models or media != "all" else HISTORY
                showable = archive.creators_with_visible_images(key, hidden_images) if archive is not HISTORY else (visible_creator_keys(key, hidden_images) if hidden_images else None)
                preference_rows = [row for row in archive.day_artist_keys(key)
                                   if row["key"] not in hidden_creators and
                                   (showable is None or row["key"] in showable)]
                if view == "emerging":
                    preference_rows = [row for row in preference_rows
                                       if row["key"] not in signals["followed"] and
                                       row["key"] != (pinned_username or "").casefold()]
                preferences = SETTINGS.load()
                preference_hidden_keys = ({row["key"] for row in preference_rows
                    if int(row.get("imageCount") or 0) >= preferences["highVolumeThreshold"]}
                    if preferences["hideHighVolumeCreators"] else set())
                session_token = query.get("session", [None])[0]
                seen = TASTE.seen_creator_keys(value)
                order, total = cached_day_view_order(key, view, pinned_username, signals,
                                                     hidden_images, session_token, seen,
                                                     hidden_creators, showable, archive)
                representatives = None
                if hidden_creators or hidden_images:
                    order = [key_name for key_name in (order or [row["key"] for row in
                        archive.day_artist_keys(key)])
                        if key_name not in hidden_creators
                        and (showable is None or key_name in showable)]
                    total = len(order)
                artists = [decorate_history_artist(item, profiles, follows, signals, seen)
                    for item in archive.artists_page(key, offset, limit, pinned_username, order,
                                                     representatives, hidden_images)]
                if view in {"foryou", "emerging"}:
                    # State why each card placed where it did, rather than presenting a
                    # personalised order the user cannot inspect.
                    page_image_ids = [(artist.get("representative") or {}).get("id")
                                      for artist in artists]
                    explanations = TASTE.explain_scores(page_image_ids)
                    recent_explanations = TASTE.explain_recent_scores(page_image_ids)
                    model_weights = TASTE.recent_model_weights()
                    page_models = HISTORY.image_model_versions(page_image_ids)
                    follower_counts = TASTE.follower_counts([
                        artist.get("username", "") for artist in artists])
                    for artist in artists:
                        image = (artist.get("representative") or {}).get("id")
                        artist["matchedTags"] = explanations.get(image, []) if image else []
                        artist["matchedRecentTags"] = recent_explanations.get(image, []) if image else []
                        model_strength = max((model_weights.get(value, 0.0)
                            for value in page_models.get(image, set())), default=0.0)
                        artist["recentModelMatch"] = round(model_strength * 100)
                        reasons = []
                        reacted_count = int(artist.get("reactedCount") or 0)
                        follower_count = follower_counts.get(artist.get("username", "").casefold())
                        if artist["matchedTags"]:
                            reasons.append("Matches your taste: " + ", ".join(artist["matchedTags"][:3]))
                        if artist["matchedRecentTags"] and model_strength >= RECENT_MODEL_MIN_SHARE:
                            artist["recommendationLabel"] = "Similar to your work"
                            reasons.insert(0, "Shared creative signals: " +
                                           ", ".join(artist["matchedRecentTags"][:3]))
                            reasons.append(f"Uses a model found in {round(model_strength * 100)}% "
                                           "of your uploaded work")
                            if artist.get("following") or reacted_count:
                                reasons.append("You follow this artist" if artist.get("following") else
                                               f"You reacted to {reacted_count} of their images")
                            else:
                                reasons.append("New to you")
                        elif artist.get("following") or reacted_count:
                            artist["recommendationLabel"] = "Familiar favorite"
                            reasons.append("You follow this artist" if artist.get("following") else
                                           f"You reacted to {reacted_count} of their images")
                        elif (artist["matchedTags"] or artist["matchedRecentTags"]) and \
                                follower_count is not None and follower_count < EMERGING_FOLLOWERS:
                            artist["recommendationLabel"] = "Emerging match"
                            if artist["matchedRecentTags"]:
                                reasons.insert(0, "Shared creative signals: " +
                                               ", ".join(artist["matchedRecentTags"][:3]))
                            reasons.append(f"Emerging creator · {follower_count:,} followers")
                            reasons.append("New to you")
                        elif artist["matchedTags"]:
                            artist["recommendationLabel"] = "New match"
                            reasons.append("New to you")
                        artist["recommendationReasons"] = reasons
                counted_creators = set(order) if order is not None else None
                self.json_response({"date": value, "offset": offset, "artists": attach_preview_tags(artists),
                    "view": view, "total": total,
                    "imageCount": sum(row["imageCount"] for row in archive.day_artist_keys(key)
                                      if counted_creators is None or row['key'] in counted_creators),
                    "preferenceHidden": len(preference_hidden_keys),
                    # Tells the client the list it is being handed has already had seen
                    # creators moved to the end, which makes a scroll offset saved
                    # against the previous order meaningless.
                    "seenCount": len(seen),
                    "hasMore": offset + len(artists) < total
                        if total is not None else len(artists) == limit})
            except ValueError as error: self.json_response({"error": str(error)}, 400)
            except Exception as error: self.internal_error("History artist page", error)
            return
        if parsed.path == '/api/follow-status':
            try:
                raw = query.get('userId', [])
                if not 1 <= len(raw) <= 100: raise ValueError('Provide 1 to 100 creator IDs')
                ids = list(dict.fromkeys(int(value) for value in raw))
                if any(value <= 0 for value in ids): raise ValueError('Invalid creator ID')
                self.json_response(creator_follow_status(ids))
            except CommunityError as error:self.json_response(error.response(), error.status)
            except ValueError as error:self.json_response({'error': str(error)}, 400)
            except Exception as error:self.internal_error('Follow status', error)
            return
        if parsed.path == "/api/creator-metadata":
            try:
                if not connected_or_false():
                    self.json_response({"error": "Connect Civitai to load creator profiles and follow state."}, 401)
                    return
                self.json_response({"creators": enrich_creator_metadata(
                    query.get("username", []), include_follow=query.get("follow", ["1"])[0] != "0")})
            except Exception as error:
                self.internal_error("Creator metadata", error)
            return
        if parsed.path == "/api/reaction-status":
            try:
                if not connected_or_false():
                    self.json_response({"error": "Connect Civitai to load your reaction history."}, 401)
                    return
                raw_ids = query.get("imageId", [])
                if not raw_ids or len(raw_ids) > 100:
                    raise ValueError("Request between 1 and 100 image IDs")
                image_ids = list(dict.fromkeys(int(value) for value in raw_ids))
                if any(not self.collected_image(image_id) for image_id in image_ids):
                    raise ValueError("Image is not in this history archive")
                values = SocialClient().batch_query_optional("image.get", [{"id": image_id} for image_id in image_ids])
                self.json_response({"images": {str(image_id): {"reactions": sorted(reaction_names(value)), "stats": live_reaction_stats(value)}
                    for image_id, value in zip(image_ids, values) if value is not None}})
            except ValueError as error:
                self.json_response({"error": str(error)}, 400)
            except Exception as error:
                self.internal_error("Reaction status", error)
            return
        if parsed.path == "/api/history/prepare":
            try:
                value = query.get("date", [previous_local_day()])[0]
                key = HISTORY.archive_key(value, query.get("segment", ["all"])[0])
                kind = query.get("kind", ["followers"])[0]
                if kind not in SWEEP_KINDS: raise ValueError("Unknown preparation")
                self.json_response({"date": value, **sweep_status(kind, key)})
            except ValueError as error: self.json_response({"error": str(error)}, 400)
            except Exception as error: self.internal_error("Gallery preparation", error)
            return
        if parsed.path == "/api/discovery/hidden":
            try:
                if query.get("refresh", [""])[0] == "1":
                    if not connected_or_false():
                        self.json_response({"error": "Sign in to read your Civitai content controls."}, 401)
                        return
                    TASTE.import_hidden_preferences()
                self.json_response(TASTE.hidden_summary())
            except Exception as error:
                self.internal_error("Content controls", error)
            return
        if parsed.path == "/api/discovery/summary":
            try:
                self.json_response({**TASTE.summary(), "sync": TASTE.status(),
                    "connected": connected_or_false()})
            except Exception as error:
                self.internal_error("Discovery summary", error)
            return
        if parsed.path == "/api/discovery/status":
            try:
                self.json_response(TASTE.status())
            except Exception as error:
                self.internal_error("Discovery status", error)
            return
        if parsed.path == "/api/history/artist":
            try:
                value = query.get("date", [previous_local_day()])[0]; username = query.get("username", [""])[0]
                key = HISTORY.archive_key(value, query.get("segment", ["all"])[0])
                models = [item for item in query.get("model", []) if item][:20]
                self.json_response({"date": value, "username": username,
                    "images": [personal_preview(image) for image in
                        GallerySelection(HISTORY, key, models, media_type(query.get("media", ["all"])[0]),
                                         hidden_preferences()[1]).artist_images(key, username)]})
            except ValueError as error: self.json_response({"error": str(error)}, 400)
            except Exception as error: self.internal_error("Artist image history", error)
            return
        if parsed.path == "/api/history/image":
            try:
                image_id = int(query.get("id", [0])[0])
                detail = (image_store(image_id) or HISTORY).detail(image_id)
                # Tags are already collected for the personalised ordering; showing them
                # here means the reason an image was ranked or hidden is inspectable.
                try:
                    tags = TASTE.image_tags(image_id)
                    # A newly built day may be opened before its background tag sweep
                    # reaches this card. The user's click takes priority: fetch just this
                    # image now and cache it for every later view.
                    if not tags["known"] and connected_or_false():
                        tags = TASTE.ensure_image_tags(SocialClient(), image_id)
                    detail = {**detail, **tags}
                except Exception:
                    detail = {**detail, "known": False, "tags": []}
                self.json_response(detail)
            except ValueError as error: self.json_response({"error": str(error)}, 400)
            except Exception as error: self.internal_error("Image details", error)
            return
        try:
            # Reject Windows UNC, drive/ADS and traversal syntax lexically. A
            # containment check after resolve() is too late to prevent SMB access.
            path = static_relative_path(parsed.path)
        except (ValueError, UnicodeError):
            self.send_error(403)
            return
        try:
            root = STATIC.resolve()
            target = (STATIC / path).resolve()
            if root not in target.parents:
                self.send_error(403)
                return
            if not target.is_file():
                self.send_error(404)
                return
            body = target.read_bytes()
        except OSError:
            self.send_error(503)
            return
        self.send_response(200)
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if mime.startswith("text/") or mime in {"application/javascript", "application/json"}: mime += "; charset=utf-8"
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' https://image.civitai.com https://*.civitai.com https://*.civitai.red data:; media-src https://image.civitai.com https://*.civitai.com; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        # Without this, a plain refresh can silently keep serving a stale cached copy of
        # app.js/index.html — no ETag or Last-Modified was sent either, so the browser had
        # nothing to revalidate against and no reason not to just reuse its cache.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def community_response(self, operation, payload):
        try:
            from discovery.community_beta import require_enabled
            require_enabled(DATA_ROOT)
            self.require_community_session()
            # Simulator identities stay local; normal LAN uses the host OAuth session.
            from discovery.community_security import test_identity
            if test_identity(DATA_ROOT) and self.client_address[0] not in ("127.0.0.1", "::1"):
                raise CommunityError("FORBIDDEN", 403)
            if os.environ.get("CAD_COMMUNITY_MODE") == "offline":
                raise CommunityError("UNAVAILABLE", 503)
            client = community_client()
            from discovery.community_security import fixture_enabled
            token = None
            if fixture_enabled():
                cookie = SimpleCookie(); cookie.load(self.headers.get('Cookie', ''))
                identity = cookie.get('cad_fixture_member')
                selected = identity.value if identity else '1'
                if selected not in {str(m.id) for m in client.verifier.members.values()}:
                    raise CommunityError('AUTH_EXPIRED', 401)
                token = f'fixture:{selected}'
            result = client.request(operation, payload, self.client_address[0], fixture_token=token)
            if operation == 'status':
                result = {**result, 'viewSession':COMMUNITY_VIEW_SESSION}
            if operation == 'gallery' and result.get('feedVersion')!=3:
                client.remember_presented(result['items'], self.community_viewer(token))
            if operation == 'detail':
                result = {**result, **community_detail_metadata(client, result)}
            if operation == 'recommend' and getattr(client, 'dev_lost_response', False):
                client.dev_lost_response = False
                raise CommunityError('UNAVAILABLE', 503)
            self.json_response(result)
        except CommunityError as error:
            self.json_response(error.response(), error.status)
        except Exception as error:self.community_unavailable(operation,error)

    def community_unavailable(self, operation, error):
        diagnostic=community_failure(DATA_ROOT,operation,error)
        self.json_response({**CommunityError("UNAVAILABLE",503).response(),'diagnosticId':diagnostic},503)

    def community_viewer(self, token):
        from discovery.community_security import test_identity
        # Callers have already required the nonce or real OAuth session.
        if test_identity(DATA_ROOT):
            return ('isolated', token)
        return (auth_status().get('id'), token)

    def community_artwork(self, image_id=None, username=None):
        if COMMUNITY is None:
            return None
        try:
            self.require_community_session()
            token = None
            if COMMUNITY.mode == 'fixture':
                cookie = SimpleCookie(); cookie.load(self.headers.get('Cookie', ''))
                member = cookie.get('cad_fixture_member')
                token = f'fixture:{member.value if member else "1"}'
                if token not in COMMUNITY.verifier.members:
                    return None
            viewer = self.community_viewer(token)
            if username is not None:
                return COMMUNITY.presented_creator(username, viewer, token)
            return COMMUNITY.presented_artwork(image_id, viewer, token)
        except CommunityError:
            return None

    def collected_image(self, image_id):
        return collected_image(image_id) or self.community_artwork(image_id) is not None

    def request_json(self) -> dict:
        from discovery.community_security import authorize_test
        authorize_test(self.headers, DATA_ROOT)
        origin = self.headers.get("Origin")
        if origin:
            parsed = urllib.parse.urlsplit(origin)
            expected = urllib.parse.urlsplit(f"http://{self.headers.get('Host') or ''}")
            if (parsed.scheme != "http" or not is_own_address(parsed.hostname)
                    or parsed.hostname != expected.hostname or parsed.port != expected.port
                    or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
                raise PermissionError("Request origin is not allowed")
        if (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() != "application/json":
            raise ValueError("Content-Type must be application/json")
        length = int(self.headers.get("Content-Length") or 0)
        if length < 2 or length > 8192:
            raise ValueError("Invalid request size")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def require_community_session(self):
        """Normal OAuth, or an explicitly challenged isolated automation process."""
        from discovery.community_security import test_identity,authorize_test
        if test_identity(DATA_ROOT):
            try:authorize_test(self.headers,DATA_ROOT)
            except PermissionError:raise CommunityError('AUTH_REQUIRED',401) from None
            return
        from discovery.oauth import get_access_token,status,OAuthReauthenticationRequired,OAuthTemporarilyUnavailable
        try:
            get_access_token()
            identity=status()
            if not identity.get('connected') or int(identity.get('id') or 0)<=0:raise ValueError()
        except OAuthReauthenticationRequired:raise CommunityError('AUTH_EXPIRED',401) from None
        except OAuthTemporarilyUnavailable:raise CommunityError('UNAVAILABLE',503) from None
        except Exception:raise CommunityError('AUTH_REQUIRED',401) from None

    def do_POST(self) -> None:  # noqa: N802
        from discovery.local_security import browser_request_allowed
        if not self.host_allowed() or not browser_request_allowed(self.headers):
            self.send_error(403); return
        parsed = urllib.parse.urlparse(self.path)
        try:
            body = self.request_json()
            if getattr(sys,'frozen',False) and parsed.path.startswith(('/__dev/','/api/community/development/')):
                self.send_error(404);return
            if parsed.path=='/api/community/beta':
                self.send_error(404);return
            if parsed.path=='/api/community/reconnect':
                if body:
                    self.json_response(CommunityError('INVALID_REQUEST').response(),400);return
                try:
                    from discovery.oauth import status
                    if not status().get('connected'):raise CommunityError('AUTH_REQUIRED',401)
                    from discovery.oauth import client_id,BUILTIN_CLIENT_ID,COMMUNITY_UNSUPPORTED_CLIENT
                    if client_id()!=BUILTIN_CLIENT_ID:
                        self.json_response({'code':'UNSUPPORTED_CLIENT','error':COMMUNITY_UNSUPPORTED_CLIENT},409);return
                except CommunityError:raise
                except Exception:raise CommunityError('AUTH_REQUIRED',401) from None
                start_oauth_login(community_only=True)
                self.json_response({'started':True},202);return
            if parsed.path.startswith('/api/community/'):
                try:self.require_community_session()
                except CommunityError:raise
                except Exception as error:
                    self.community_unavailable('status',error);return
            if parsed.path == '/api/community/development/member':
                from discovery.community_security import fixture_enabled
                if not fixture_enabled() or self.client_address[0] != '127.0.0.1':
                    self.json_response(CommunityError('FORBIDDEN', 403).response(), 403); return
                value = body.get('member')
                if type(value) is not int or f'fixture:{value}' not in community_client().verifier.members:
                    self.json_response(CommunityError('INVALID_REQUEST').response(), 400); return
                self.json_response({'apiVersion': 1, 'member': value, 'development': True}, headers={
                    'Set-Cookie': f'cad_fixture_member={value}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000'})
                return
            if parsed.path == '/api/community/development/control':
                from discovery.community_security import control, test_identity
                if not test_identity(DATA_ROOT) or self.client_address[0] != '127.0.0.1':
                    self.json_response(CommunityError('FORBIDDEN', 403).response(), 403); return
                try: self.json_response(control(community_client(), body))
                except CommunityError as error: self.json_response(error.response(), error.status)
                return
            if parsed.path.startswith("/api/community/"):
                operation = parsed.path.rsplit("/", 1)[-1]
                allowed = {'artwork','reason'} if operation == 'report' else {'artwork','idempotencyKey'} if operation == 'recommend' else {'artwork'}
                if operation not in ("verify", "recommend", "withdraw", "report") or 'artwork' not in body or not set(body) <= allowed:
                    self.json_response(CommunityError("INVALID_REQUEST").response(), 400); return
                self.community_response(operation, body); return
            if parsed.path == "/api/artwork/availability":
                self.json_response(ARTWORK_AVAILABILITY.check(body.get("sources")))
                return
            if parsed.path == "/api/diagnostics/browser":
                log_browser_events(DATA_ROOT, body.get("events"))
                self.json_response({"logged": True})
                return
            if parsed.path == "/api/oauth/login":
                start_oauth_login()
                with OAUTH_LOCK: state = OAUTH_JOB["state"]
                self.json_response({"state": state}, 202); return
            if parsed.path == "/api/oauth/client":
                value = str(body.get("clientId") or "").strip()
                if value and not re.fullmatch(r"[A-Za-z0-9._~-]{8,128}", value):
                    self.json_response({"error": "That does not look like a Civitai client ID."}, 400)
                    return
                info = set_client_id(value)
                with WRITE_LOCK:
                    if FOLLOW_CACHE.exists(): FOLLOW_CACHE.unlink()
                TASTE.stop_sync(); TASTE.reset()
                with OAUTH_LOCK: OAUTH_JOB.update({"state": "idle", "error": None})
                self.json_response(info)
                return
            if parsed.path == "/api/oauth/disconnect":
                oauth_disconnect()
                with WRITE_LOCK:
                    if FOLLOW_CACHE.exists(): FOLLOW_CACHE.unlink()
                # The taste analysis survives a sign-out. Deleting it here used to force a
                # full re-sync of every reaction on the next sign-in — expensive for
                # exactly the accounts this app is built for, ones with a lot of
                # reactions — to guard against a risk that was already handled better:
                # _require_account() wipes and rebuilds automatically the moment a
                # *different* account actually syncs, so the same person signing back in
                # just picks up where they left off, and nobody else's data is exposed by
                # keeping it (there is no signed-out view of the app to see it through).
                TASTE.stop_sync()
                with OAUTH_LOCK: OAUTH_JOB.update({"state": "idle", "error": None})
                self.json_response({"connected": False}); return
            if parsed.path == "/api/settings":
                content_change = ("browsingLevels" in body or "contentRating" in body or "applyTagFilters" in body or "applyAccountFilters" in body or "applyImageFilters" in body)
                gallery_change = any(name in body for name in (
                    "hideHighVolumeCreators", "highVolumeThreshold",
                    "emergingReactionMode", "emergingReactionLimit", "mediaType"))
                value = SETTINGS.transactional_update(
                                        effect=(lambda value: HISTORY.set_content_filter(value["browsingLevels"])) if content_change else None,
                                        apply_tag_filters_value=body.get("applyTagFilters"),
                                        apply_account_filters_value=body.get("applyAccountFilters"),
                                        apply_image_filters_value=body.get("applyImageFilters"),
                                        media_type_value=body.get("mediaType"),
                                        browsing_levels_value=body.get("browsingLevels"),
                                        content_rating_value=body.get("contentRating"),
                                        dim_seen_cards_value=body.get("dimSeenCards"),
                                        detail_video_sound_value=body.get("detailVideoSound"),
                                        check_for_updates_value=body.get("checkForUpdates"),
                                        hide_high_volume_creators_value=body.get("hideHighVolumeCreators"),
                                        high_volume_threshold_value=body.get("highVolumeThreshold"),
                                        emerging_reaction_mode_value=body.get("emergingReactionMode"),
                                        emerging_reaction_limit_value=body.get("emergingReactionLimit"),
                                        allow_lan_access_value=body.get("allowLanAccess"))
                if content_change or gallery_change:
                    with WRITE_LOCK:
                        ORDER_CACHE.clear()
                        # Visibility is cached separately for each exact level selection,
                        # so switching away and back can reuse it. Gallery preferences
                        # invalidate only local ordering; neither path redownloads a day.
                self.json_response({**value, "siteOrigin": SITE_ORIGIN})
                return
            if parsed.path == "/api/update/check":
                if not SETTINGS.load()["checkForUpdates"]:
                    self.json_response({"error": "Update checks are disabled in My Profile."}, 409)
                    return
                self.json_response({**UPDATES.start_check(force=True), "enabled": True}, 202)
                return
            if parsed.path == "/api/update/download":
                if not SETTINGS.load()["checkForUpdates"]:
                    self.json_response({"error": "Update checks are disabled in My Profile."}, 409)
                    return
                if not UPDATES.supported:
                    self.json_response({"error": "One-click installation is only available in packaged builds; update this source checkout through Git."}, 409)
                    return
                self.json_response({**UPDATES.start_download(), "enabled": True}, 202)
                return
            if parsed.path == "/api/update/install":
                if not UPDATES.supported:
                    self.json_response({"error": "One-click installation is only available in packaged builds; update this source checkout through Git."}, 409)
                    return
                reason = update_busy_reason()
                if reason:
                    self.json_response({"error": reason}, 409)
                    return
                command = UPDATES.helper_command()
                flags = 0
                if os.name == "nt":
                    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                subprocess.Popen(command, cwd=Path(command[0]).parent, close_fds=True,
                                 creationflags=flags)
                self.json_response({"installing": True, "version": UPDATES.status().get("release", {}).get("version")})
                request_app_shutdown(self.server)
                return
            if parsed.path == "/api/app/restart":
                # Reuse the same port so any tab already pointed at this app (including
                # this settings page) reconnects on its own once the new process is up,
                # instead of being stranded on a port that no longer answers.
                restart_args = ["--port", str(RUNNING_PORT), "--no-browser", "--replacing"]
                command = ([str(UPDATES.executable), *restart_args] if UPDATES.supported
                           else [str(UPDATES.executable), '-B', str(Path(__file__).resolve()), *restart_args])
                flags = 0
                if os.name == "nt":
                    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                subprocess.Popen(command, cwd=UPDATES.install_root, close_fds=True,
                                 creationflags=flags)
                self.json_response({"restarting": True})
                request_app_shutdown(self.server)
                return
            if parsed.path == "/api/update/result/acknowledge":
                UPDATES.clear_result()
                self.json_response({"acknowledged": True})
                return
            if parsed.path == "/api/history/start":
                self.json_response(HISTORY.start(*archive_build_request(body),body.get("contentRating")),202)
                return
            if parsed.path == "/api/history/capture":
                enabled = body.get("enabled")
                hours = body.get("intervalHours")
                # "keep" leaves the chosen time alone; an explicit null restores the
                # automatic slot, so the two are not the same request.
                minute = body["atMinute"] if "atMinute" in body else "keep"
                SETTINGS.update(auto_capture_value=enabled if isinstance(enabled, bool) else None,
                                auto_capture_hours_value=hours if hours is not None else None,
                                auto_capture_minute_value=minute,
                                capture_coverage_value=body.get("coverage"))
                if SETTINGS.load()["autoCapture"] and connected_or_false():
                    CAPTURE.start()
                    if body.get("runNow"):
                        CAPTURE.trigger()
                else:
                    CAPTURE.stop()
                self.json_response(CAPTURE.status())
                return
            if parsed.path == "/api/timemachine/prime":
                if not connected_or_false():
                    raise PermissionError("Connect Civitai to read the creators you follow")
                self.json_response(TIME_MACHINE.start_prime(SocialClient(API_LANE)), 202)
                return
            if parsed.path == "/api/timemachine/stop":
                TIME_MACHINE.stop_prime()
                self.json_response(TIME_MACHINE.status())
                return
            if parsed.path == "/api/timemachine/seen":
                names = body.get("usernames")
                if not isinstance(names, list) or len(names) > 500:
                    raise ValueError("usernames must be a list of at most 500 names")
                self.json_response({"advanced": TIME_MACHINE.advance(names)})
                return
            if parsed.path == "/api/timemachine/advance":
                name = str(body.get("username") or "").strip()
                position = body.get("position")
                if not name or not isinstance(position, int) or position < 0:
                    raise ValueError("username and a non-negative position are required")
                self.json_response({"advanced": TIME_MACHINE.advance_to(name, position)})
                return
            if parsed.path == "/api/timemachine/refill":
                name = str(body.get("username") or "").strip()
                if not name:
                    raise ValueError("username is required")
                self.json_response({"added": TIME_MACHINE.refill(name)})
                return
            if parsed.path == "/api/history/rebuild":
                self.json_response(HISTORY.rebuild(*archive_build_request(body)),202)
                return
            if parsed.path == "/api/history/cancel":
                value = str(body.get("date") or "")
                key = HISTORY.archive_key(value, str(body.get("segment") or "all")) if value else None
                HISTORY.cancel(key)
                self.json_response({"cancelled": True, "date": value or None})
                return
            # Seen-tracking never touches Civitai — it is bookkeeping about what this
            # app has shown, not a Civitai action — so like discovery analysis below it
            # stays above the social-write gate and works on a read-only connection.
            if parsed.path == "/api/history/seen":
                value = str(body.get("date") or "")
                try:
                    parse_day(value)
                except ValueError:
                    self.json_response({"error": "A valid date is required"}, 400)
                    return
                usernames = body.get("usernames")
                if not isinstance(usernames, list) or not usernames or len(usernames) > 500:
                    self.json_response({"error": "Provide 1 to 500 usernames"}, 400)
                    return
                keys = [name.casefold() for name in usernames if isinstance(name, str) and name]
                marked = TASTE.mark_seen(value, keys)
                self.json_response({"date": value, "marked": marked})
                return
            if parsed.path == "/api/content-controls/hide-artist":
                if not auth_status().get("userWrite"):
                    self.json_response({"error": "Sign out and back in once to allow Civitai Content Control changes."}, 403)
                    return
                username = str(body.get("username") or "").strip()
                if not username:
                    raise ValueError("Provide an artist username")
                client = SocialClient()
                profile = client.query("user.getCreator", {"username": username})
                if not isinstance(profile, dict) or not profile.get("id"):
                    raise ValueError("Civitai could not find that artist")
                user_id = int(profile["id"])
                if not (HISTORY.has_creator(username) or TASTE.has_creator(user_id)
                        or self.community_artwork(username=username)):
                    raise ValueError("Artist is not in this app's saved gallery")
                supplied_id = body.get("userId")
                if supplied_id is not None and int(supplied_id) != user_id:
                    raise ValueError("Artist identity changed; refresh the gallery and try again")
                already_hidden = username.casefold() in TASTE.hidden_creator_keys()
                if not already_hidden:
                    client.hide_user(user_id, str(profile.get("username") or username))
                imported = TASTE.import_hidden_preferences(client)
                with WRITE_LOCK:
                    VISIBLE_CACHE.update({"token": None, "keys": {}})
                    ORDER_CACHE.clear()
                if username.casefold() not in TASTE.hidden_creator_keys():
                    raise RuntimeError("Civitai accepted the change but has not returned it in Content Controls yet. Refresh and try again shortly.")
                self.json_response({"username": username, "userId": user_id, "hidden": True,
                                    "changed": not already_hidden, "contentControls": imported})
                return
            # Discovery analysis is read-only, so it stays above the social-write gate
            # below and remains available to read-only OAuth connections.
            if parsed.path == "/api/history/prepare":
                if not connected_or_false():
                    self.json_response({"error": "Connect Civitai to prepare this view."}, 401)
                    return
                kind = str(body.get("kind") or "followers")
                if kind not in SWEEP_KINDS:
                    self.json_response({"error": "Unknown preparation"}, 400)
                    return
                key = HISTORY.archive_key(str(body.get("date") or previous_local_day()),
                                          str(body.get("segment") or "all"))
                self.json_response(start_sweep(kind, key), 202)
                return
            if parsed.path == '/api/history/images':
                raw=body.get('imageIds')
                if not isinstance(raw,list) or not 1<=len(raw)<=50 or any(type(i) is not int or i<=0 for i in raw):raise ValueError('Provide 1 to 50 IDs')
                viewer=connected_user_id()
                if not viewer:raise CommunityError('AUTH_REQUIRED',401)
                if any(not collected_image(i) and not (COMMUNITY and COMMUNITY.listed(i)) for i in raw):raise CommunityError('FORBIDDEN',403)
                profile=community_content_profile(None)
                images=HISTORY.gallery_images(raw,SocialClient())
                hidden,excluded=hidden_preferences()
                allowed=[i for i in images if i['browsingLevel'] in profile.levels and i['username'].casefold() not in hidden and i['id'] not in excluded
                    and (not COMMUNITY or not profile.apply_account_filters or COMMUNITY.listed_creator(i['id']) not in profile.creators|profile.blocked_creators)]
                if viewer!=connected_user_id():raise CommunityError('AUTH_EXPIRED',401)
                if COMMUNITY:COMMUNITY.remember_presented([{'artwork':i} for i in allowed],self.community_viewer(None))
                self.json_response({'images':[personal_preview(i) for i in allowed],
                    'counts':{'requested':len(raw),'metadata':len(images),'contentControls':len(images)-len(allowed),'unavailable':len(raw)-len(images)}})
                return
            if parsed.path == "/api/history/tags":
                raw_ids = body.get("imageIds")
                if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 100:
                    self.json_response({"error": "Provide 1 to 100 image IDs"}, 400)
                    return
                image_ids = list(dict.fromkeys(int(value) for value in raw_ids))
                if any(not self.collected_image(image_id) for image_id in image_ids):
                    self.json_response({"error": "An image is not in this history archive"}, 400)
                    return
                tag_viewer=connected_user_id()
                if tag_viewer:community_content_profile(None)
                tags = TASTE.image_tags_many(image_ids)
                # Cached checks remain useful in read-only/offline test and recovery
                # states. A live lookup needs OAuth, but the ordinary signed-in gallery
                # always has it before reaching this endpoint.
                if connected_or_false() and any(not value["known"] for value in tags.values()):
                    tags = TASTE.ensure_image_tags_many(SocialClient(), image_ids)
                if tag_viewer!=connected_user_id():raise CommunityError('AUTH_EXPIRED',401)
                self.json_response({"images": {str(image_id): tags[image_id]
                                                for image_id in image_ids}})
                return
            if parsed.path == "/api/history/prepare/stop":
                for event in SWEEP_CANCEL.values(): event.set()
                self.json_response({"stopping": True})
                return
            if parsed.path == "/api/discovery/sync":
                if not auth_status().get("connected"):
                    self.json_response({"error": "Connect Civitai to analyse your reactions."}, 401)
                    return
                self.json_response(TASTE.start_sync(), 202)
                return
            if parsed.path == "/api/discovery/sync/stop":
                TASTE.stop_sync()
                self.json_response({"stopping": True})
                return
            if parsed.path == "/api/discovery/reset":
                TASTE.stop_sync()
                TASTE.reset()
                self.json_response({"reset": True})
                return
            if parsed.path == "/api/app/close":
                self.json_response({"closing": True})
                request_app_shutdown(self.server)
                return
            if parsed.path == "/api/collections/add":
                if not auth_status().get("collectionsWrite"):
                    self.json_response({"error": "Sign out and back in once to allow collection changes."}, 403)
                    return
                image_id = int(body.get("imageId"))
                collection_id = int(body.get("collectionId"))
                if not self.collected_image(image_id):
                    raise ValueError("Image is not in this app's saved gallery")
                client = SocialClient()
                collection = next((row for row in client.writable_image_collections(connected_user_id())
                                   if int(row["id"]) == collection_id), None)
                if collection is None:
                    raise ValueError("That collection is not available for images")
                client.add_image_to_collection(image_id, collection)
                self.json_response({"imageId": image_id, "collectionId": collection_id,
                                    "collectionName": collection["name"], "added": True})
                return
            try:
                can_write = bool(auth_status().get("socialWrite"))
            except Exception:
                can_write = False
            if not can_write:
                self.json_response({"error": "OAuth is read-only. No Civitai write was attempted; enable social actions explicitly to react or follow."}, 403)
                return
            if parsed.path == "/api/reaction":
                self.handle_reaction(body)
                return
            if parsed.path == "/api/follow":
                self.handle_follow(body)
                return
            self.json_response({"error": "Unknown action"}, 404)
        except CommunityError as error:
            self.json_response(error.response(),error.status)
        except PermissionError as error:
            self.json_response({"error": str(error)}, 403)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self.json_response({"error": str(error)}, 400)
        except Exception as error:
            self.internal_error("Local action", error)

    def handle_reaction(self, body: dict) -> None:
        image_id = int(body.get("imageId"))
        reaction = body.get("reaction")
        desired = body.get("active")
        if reaction not in REACTIONS or not isinstance(desired, bool):
            raise ValueError("Invalid reaction request")
        state = CACHE.load(); item = next((row for row in state.get("items", []) if int(row.get("id", -1)) == image_id), None)
        stores = image_stores(image_id)
        in_history = bool(stores)
        if item is None and not in_history and self.community_artwork(image_id) is None:
            raise ValueError("Image is not in this discovery feed")
        client = SocialClient()
        payload = client.query("image.get", {"id": image_id})
        current = reaction_names(payload)
        # Civitai's own counts, read a moment ago, rather than whichever stored copy was
        # collected first: those differ, and reacting used to make the card jump to the
        # older number.
        live = live_reaction_stats(payload)
        changed = (reaction in current) != desired
        if changed:
            client.mutate("reaction.toggle", {"entityId": image_id, "entityType": "image", "reaction": reaction})
            current.discard(reaction) if not desired else current.add(reaction)
            with WRITE_LOCK:
                fresh = CACHE.load()
                cached = next((row for row in fresh.get("items", []) if int(row.get("id", -1)) == image_id), None)
                if cached is not None:
                    stats = cached.setdefault("stats", {})
                    delta = 1 if desired else -1
                    key = REACTIONS[reaction]
                    stats[key] = max(0, int(stats.get(key, 0)) + delta)
                    stats["reactionCount"] = max(0, int(stats.get("reactionCount", 0)) + delta)
                    CACHE.save(fresh)
                    item = cached
        if in_history:
            stats = {**stores[0].stats(image_id), **live}
            if changed:
                delta = 1 if desired else -1; key = REACTIONS[reaction]
                stats[key] = max(0, int(stats.get(key, 0)) + delta); stats["reactionCount"] = max(0, int(stats.get("reactionCount", 0)) + delta)
            if changed or live:
                for store in stores:
                    store.update_stats(image_id, {**store.stats(image_id), **stats})
        else:
            stats = {**(item or {}).get("stats", {}), **live}
            if item is None and changed:
                delta = 1 if desired else -1; key = REACTIONS[reaction]
                stats[key] = max(0, int(stats.get(key, 0)) + delta)
                stats["reactionCount"] = max(0, int(stats.get("reactionCount", 0)) + delta)
        self.json_response({"imageId": image_id, "reactions": sorted(current), "stats": stats, "changed": changed})

    def handle_follow(self, body: dict) -> None:
        raw_user_id = body.get("userId"); username_hint = str(body.get("username") or "")
        user_id = int(raw_user_id) if raw_user_id is not None else 0
        desired = body.get("following")
        if not isinstance(desired, bool):
            raise ValueError("Invalid follow request")
        profile = next((value for value in creator_profiles().values() if int(value.get("id", -1)) == user_id), None)
        client = SocialClient()
        if profile is None and username_hint and (HISTORY.has_creator(username_hint)
                                                  or TASTE.has_creator(user_id)
                                                  or self.community_artwork(username=username_hint)):
            resolved = client.query("user.getCreator", {"username": username_hint})
            if isinstance(resolved, dict) and resolved.get("id"):
                profile = resolved; user_id = int(resolved["id"])
        if profile is None:
            raise ValueError("Artist is not in this discovery feed")
        current_ids = following_ids(client.query("user.getFollowingUsers", {}))
        changed = (user_id in current_ids) != desired
        if changed:
            result = client.mutate("user.toggleFollow", {"targetUserId": user_id})
            if isinstance(result, dict) and isinstance(result.get("following"), bool):
                desired = result["following"]
        username = str(profile.get("username") or "")
        with WRITE_LOCK:
            account_user_id = connected_user_id()
            names = {name.casefold(): name for name in followed_usernames(account_user_id)}
            if desired:
                names[username.casefold()] = username
            else:
                names.pop(username.casefold(), None)
            temporary = FOLLOW_CACHE.with_suffix(".tmp")
            temporary.write_text(json.dumps({"updatedAt": datetime.now(timezone.utc).isoformat(),
                "userId": account_user_id, "count": len(names),
                "usernames": sorted(names.values(), key=str.casefold)}, indent=2), encoding="utf-8")
            temporary.replace(FOLLOW_CACHE)
        TASTE.set_following(user_id, desired)
        with FOLLOW_STATUS_LOCK: FOLLOW_STATUS_CACHE.clear()
        self.json_response({"userId": user_id, "following": desired, "changed": changed})

    def log_message(self, format: str, *args: object) -> None:
        print(f"[discovery] {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=8907)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--updated-from", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--replacing", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    from discovery.community_security import disable_test_outbound
    disable_test_outbound(DATA_ROOT)
    bind_host = args.host or ("0.0.0.0" if SETTINGS.load()['allowLanAccess'] else "127.0.0.1")
    if bind_host not in ('127.0.0.1','0.0.0.0') or (bind_host=='0.0.0.0' and not SETTINGS.load()['allowLanAccess']) or args.port in (8906,33101,33201):
        raise ValueError("Network binding requires explicit LAN sharing; use a separate Discovery 3.0 port")
    if not claim_single_instance(args.no_browser, args.replacing):
        return
    if not SETTINGS.path.exists():SETTINGS.update()
    # The OAuth redirect is registered against one fixed port, so the app must never
    # occupy it: doing so leaves the callback listener unable to bind and Civitai's
    # redirect lands on this server, which answers 404 and strands the sign-in.
    server = bind_http_server(bind_host, args.port, args.replacing)
    if server.server_address[1] == CALLBACK_PORT:
        server.server_close()
        server = bind_http_server(bind_host, 0, False)
        if server.server_address[1] == CALLBACK_PORT:
            raise SystemExit(f"Port {CALLBACK_PORT} is reserved for signing in to Civitai")
    actual_port = server.server_address[1]
    global RUNNING_PORT
    RUNNING_PORT = actual_port
    addresses = lan_addresses() if bind_host == "0.0.0.0" else []
    url = f"http://{addresses[0]}:{actual_port}" if addresses else f"http://127.0.0.1:{actual_port}"
    save_instance_url(url)
    UPDATES.schedule_success_cleanup()
    # Only starts a worker when the preference is on; otherwise nothing runs.
    if SETTINGS.load()["autoCapture"] and connected_or_false():
        CAPTURE.start()
    print(f"Civitai artist discovery running at {url}")
    print("OAuth-backed follow and reaction controls are enabled when SocialWrite is approved.")
    if bind_host == "0.0.0.0":
        print(f"LAN access is ON: other devices on your network can reach this app at {url}. "
              "Anyone who can reach that address can use the app, including follow/react "
              "actions. Turn this off in Settings if you do not want that.")
    tray = None
    if not args.no_browser:
        try:
            tray = start_windows_tray(url, STATIC / "app.ico",
                                      lambda: request_app_shutdown(server))
        except Exception as error:
            log_internal_error("Windows tray startup", error)
    if not args.no_browser:
        webbrowser.open(url, new=1)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try: stop_windows_tray(tray)
        except Exception as error: log_internal_error("Windows tray shutdown", error)
        server.server_close()
        try: HISTORY.checkpoint()
        except Exception as error: log_internal_error("SQLite shutdown checkpoint", error)
        clear_instance_url(url)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--apply-update":
            apply_staged_update(Path(sys.argv[2]))
        else:
            main()
    except Exception:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        append_log(DATA_ROOT, traceback.format_exc())
        # The replacement helper reports through its receipt, not a blocking
        # startup dialog. Failed unattended installs must return a failing status.
        if len(sys.argv)==3 and sys.argv[1]=='--apply-update':raise SystemExit(1)
        if getattr(sys, "frozen", False) and os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f"Civitai Discovery 3.0 Beta could not start.\n\nSee:\n{DATA_ROOT / 'error.log'}", "Civitai Discovery 3.0 Beta", 0x10)
            raise SystemExit(1)
        else:
            raise
