"""Local artist-first Civitai discovery feed."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import mimetypes
import os
import re
import subprocess
from pathlib import Path
from statistics import mean
import threading
import sys
import urllib.parse
import webbrowser
import traceback

from discovery.civitai import CandidateCache
from discovery.history import HistoryArchive, parse_day, previous_local_day
from discovery.oauth import (CALLBACK_PORT, OAuthSetupError, client_info,
                             disconnect as oauth_disconnect, login as oauth_login,
                             set_client_id)
from discovery.paths import application_root, data_root
from discovery.settings import AppSettings
from discovery.site import SITE_ORIGIN, profile_url
from discovery.social import (CivitaiHTTPError, SocialClient, auth_status,
                              following_ids, reaction_names)
from discovery.taste import (EMERGING_FOLLOWERS, GALLERY_HEART_MIN, TasteStore,
                             WORTH_FOLLOWING_MIN)
from discovery.tray import start_windows_tray, stop_windows_tray
from discovery.updater import UpdateManager, apply_staged_update


ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
STATIC = ROOT / "static"
APP_NAME = "Civitai Artist Discovery"
APP_VERSION = "0.3.2-beta.1"
DATA_ROOT = data_root()
DATA_ROOT.mkdir(parents=True, exist_ok=True)
CACHE = CandidateCache(DATA_ROOT / "cache" / "candidates.json")
FOLLOW_CACHE = DATA_ROOT / "following.json"
CREATOR_PROFILES = DATA_ROOT / "cache" / "creator_profiles.json"
SETTINGS = AppSettings(DATA_ROOT / "settings.json")
INITIAL_SETTINGS = SETTINGS.load()
HISTORY = HistoryArchive(DATA_ROOT / "history", INITIAL_SETTINGS["contentRating"],
                         INITIAL_SETTINGS["browsingLevels"])
TASTE = TasteStore(DATA_ROOT / "discovery")
UPDATES = UpdateManager(DATA_ROOT, application_root(), APP_VERSION)
WRITE_LOCK = threading.Lock()
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


def creator_profiles() -> dict:
    if not CREATOR_PROFILES.exists():
        return {}
    try:
        value = json.loads(CREATOR_PROFILES.read_text(encoding="utf-8"))
        return value.get("byUsername", {}) if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


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


def followed_usernames(user_id: int | None = None) -> set[str]:
    expected_user_id = user_id if user_id is not None else connected_user_id()
    if expected_user_id is None:
        return set()
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
    """(creator username keys, image ids) the account has chosen not to see."""
    try:
        return TASTE.hidden_creator_keys(), TASTE.hidden_image_ids()
    except Exception:
        # Content Controls are a filter, not the feature. If the mirror cannot be read,
        # show the gallery rather than failing it.
        return set(), set()


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
        client = SocialClient()
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
                   seen: set | None = None) -> tuple[list[str] | None, int | None]:
    """Order (and for 'new', filter) a whole day's creators by personal signal.

    Returns None to mean "use the archive's own order", which keeps the default view on
    its existing SQL path.
    """
    if view not in GALLERY_VIEWS or view == "discovery":
        return None, None
    followed, reacted = signals["followed"], signals["reacted"]
    rows = HISTORY.day_artist_keys(key)
    hidden_creators = set(hidden_creators or ())
    if hidden_creators or eligible_creators is not None:
        rows = [row for row in rows if row["key"] not in hidden_creators and
                (eligible_creators is None or row["key"] in eligible_creators)]
    pinned = (pinned_username or "").casefold()
    if view == "foryou":
        # Score whichever image will actually be the card's cover, not the raw archive
        # pick: a hidden cover can carry the score while a different image renders, and
        # scoring the wrong one produces a top-ranked card with no visible explanation.
        effective = HISTORY.effective_representative_ids(key, hidden_images)
        image_ids = [effective[row["key"]] for row in rows if row["key"] in effective]
        if hasattr(TASTE, "score_image_components"):
            components = TASTE.score_image_components(image_ids)
        else:
            components = {image_id: {"reaction": score, "recent": 0.0}
                          for image_id, score in TASTE.score_images(image_ids).items()}
        model_weights = TASTE.recent_model_weights() if hasattr(TASTE, "recent_model_weights") else {}
        image_models = HISTORY.image_model_versions(image_ids)
        counts = TASTE.follower_counts([row["username"] for row in rows])
        quality = HISTORY.creator_quality_scores(key, hidden_images)
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
        # Emerging exists to surface creators the user has not found yet, so anyone they
        # already follow is removed rather than merely ranked lower.
        rows = [row for row in rows if row["key"] not in followed and row["key"] != pinned]
        if not rows:
            return [], 0
        counts = TASTE.follower_counts([row["username"] for row in rows])
        # Only a creator with a known count can be called emerging. Unknown counts sort
        # after the rest rather than being presented as small accounts.
        # Within the emerging tier, order by the day's own engagement rank, not by
        # ascending follower count. Ascending count leads with one-follower throwaway
        # accounts; 77% of a measured day sits under the threshold, so the threshold
        # selects a tier and engagement decides who leads it.
        def rank(row):
            value = counts.get(row["key"])
            if value is None:
                return (2, row["rank"])
            return (0 if value < EMERGING_FOLLOWERS else 1, row["rank"])
        ordered = sorted(rows, key=rank)
        return [row["key"] for row in ordered], len(ordered)
    if not followed and not reacted:
        return None, None
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
ORDER_CACHE: dict = {}


def _seen_last(key: str, order: list[str] | None, total: int | None,
               seen: set) -> tuple[list[str] | None, int | None]:
    """Push already-seen creators to the end without disturbing anyone else's order.

    Applies after whatever the view produced, including a view that returned None to
    mean "the archive's own order needs no override" — seen-tracking is independent of
    which view is active. sorted() is stable, so "not yet seen" and "seen" each keep
    exactly the relative order the view gave them.
    """
    if not seen:
        return order, total
    base = order if order is not None else [row["key"] for row in HISTORY.day_artist_keys(key)]
    ordered = sorted(base, key=lambda item: item in seen)
    return ordered, len(ordered)


def cached_day_view_order(key: str, view: str, pinned_username: str | None, signals: dict,
                          hidden_images: set | None, session_token: str | None, seen: set,
                          hidden_creators: set | None = None,
                          eligible_creators: set | None = None
                          ) -> tuple[list[str] | None, int | None]:
    if not session_token:
        order, total = day_view_order(key, view, pinned_username, signals, hidden_images,
                                      hidden_creators, eligible_creators, seen)
        return ((order, total) if view == "foryou" and order is not None
                else _seen_last(key, order, total, seen))
    cache_key = (key, view, pinned_username)
    with WRITE_LOCK:
        cached = ORDER_CACHE.get(cache_key)
        if cached is not None and cached[0] == session_token:
            return cached[1], cached[2]
    order, total = day_view_order(key, view, pinned_username, signals, hidden_images,
                                  hidden_creators, eligible_creators, seen)
    if view != "foryou" or order is None:
        order, total = _seen_last(key, order, total, seen)
    with WRITE_LOCK:
        ORDER_CACHE[cache_key] = (session_token, order, total)
    return order, total


def _follower_fields(profile: object) -> dict:
    """Follower count and emerging flag from a cached user.getCreator profile."""
    stats = (profile or {}).get("stats") if isinstance(profile, dict) else None
    value = (stats or {}).get("followerCountAllTime")
    count = int(value) if isinstance(value, (int, float)) else None
    return {"followers": count, "emerging": count is not None and count < EMERGING_FOLLOWERS}


def enrich_creator_metadata(usernames: list[str]) -> dict[str, dict]:
    """Resolve visible creators and cache their avatars and follow state."""
    clean = list(dict.fromkeys(name.strip() for name in usernames if name.strip()))[:100]
    profiles = creator_profiles()
    missing = [name for name in clean if name.casefold() not in profiles]
    client = SocialClient()
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
        return {
            "username": name,
            "avatarUrl": profile_avatar(profile),
            "following": bool(
                (profile or {}).get("id") is not None
                and int(profile["id"]) in followed_ids
            ),
            "userId": (profile or {}).get("id"),
            # The all-day sweep stores counts in SQLite. The profile cache carries
            # avatars and ids, but is populated only for cards the user has loaded.
            **fields,
        }
    return {name.casefold(): result_for(name) for name in clean}


def start_oauth_login() -> None:
    with OAUTH_LOCK:
        if OAUTH_JOB["state"] == "loading": return
        OAUTH_JOB.update({"state": "loading", "error": None})
    def run():
        try:
            oauth_login()
            # Mirror the account's Content Controls straight away, so the first gallery
            # after signing in already respects them. A failure here must not fail the
            # sign-in: the filter is a refinement, not the point of connecting.
            try:
                TASTE.import_hidden_preferences()
            except Exception as error:
                log_internal_error("Content controls import", error)
            with OAUTH_LOCK: OAUTH_JOB["state"] = "complete"
        except Exception as error:
            log_internal_error("OAuth authorization", error)
            # Most failures stay generic on purpose, but setup problems are the user's to
            # fix and unfixable by retrying, so those say what is actually wrong.
            message = str(error) if isinstance(error, OAuthSetupError) else \
                "Civitai authorization failed. Please try again."
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
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    message = f"\n[{datetime.now(timezone.utc).isoformat()}] {context}: {type(error).__name__}\n{traceback.format_exc()}"
    try:
        with (DATA_ROOT / "error.log").open("a", encoding="utf-8") as output: output.write(message)
    except OSError:
        pass


def claim_single_instance(no_browser: bool) -> bool:
    """Return False after directing a second Windows launch to the first one."""
    global INSTANCE_MUTEX
    if os.name != "nt":
        return True
    import ctypes
    identity = hashlib.sha256(str(DATA_ROOT.resolve()).casefold().encode()).hexdigest()[:20]
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, f"Local\\CivitaiArtistDiscovery-{identity}")
    if not handle:
        raise ctypes.WinError()
    if ctypes.windll.kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
        INSTANCE_MUTEX = handle
        return True
    ctypes.windll.kernel32.CloseHandle(handle)
    for _ in range(30):
        try:
            existing = json.loads(INSTANCE_FILE.read_text(encoding="utf-8"))
            url = str(existing.get("url") or "")
            if url.startswith("http://127.0.0.1:") or url.startswith("http://localhost:") or url.startswith("http://[::1]:"):
                if not no_browser: webbrowser.open(url, new=1)
                return False
        except (OSError, json.JSONDecodeError):
            pass
        threading.Event().wait(.1)
    return False


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


def request_app_shutdown(server: ThreadingHTTPServer) -> None:
    """Stop background work and ask the local HTTP server to exit."""
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
            return bool(hostname) and hostname.lower().rstrip(".") in {"127.0.0.1", "localhost", "::1"}
        except ValueError:
            return False

    def internal_error(self, context: str, error: BaseException) -> None:
        log_internal_error(context, error)
        self.json_response({"error": "The app encountered an internal error. Details were saved to the local error log."}, 500)

    def json_response(self, value: object, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Browsers routinely cancel an obsolete fetch when the user changes day or
            # view. That is not an application failure and must not create an error-log
            # entry or trigger a futile second response on the closed socket.
            pass

    def do_GET(self) -> None:  # noqa: N802
        if not self.host_allowed():
            self.send_error(403); return
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/api/artists":
            try:
                limit = min(1000, max(1, int(query.get("limit", [500])[0])))
                target = min(3000, max(limit, int(query.get("target", [1000])[0])))
                refresh = query.get("refresh", ["false"])[0].lower() == "true"
                nonce = query.get("nonce", ["0"])[0]
                with WRITE_LOCK: state = CACHE.collect(target=target, refresh=refresh)
                items = state.get("items", [])
                artists = artist_gallery(items, limit, nonce)
                try: read_only = not bool(auth_status().get("socialWrite"))
                except Exception: read_only = True
                self.json_response({
                    "artists": artists,
                    "imageCount": len(items),
                    "artistCount": len({item["username"].casefold() for item in items}),
                    "updatedAt": state.get("updatedAt"),
                    "lastCollect": state.get("lastCollect"),
                    "oauthProfileCache": CREATOR_PROFILES.exists(),
                    "readOnly": read_only,
                })
            except Exception as error:
                self.internal_error("Legacy artist feed", error)
            return
        if parsed.path == "/api/auth-status":
            try:
                # Renew a token that is close to lapsing. Reading the stored token does not
                # refresh it, so an idle app would sit until the authorization expired and
                # the next action failed. The page polls this, so the session stays alive
                # while someone is reading rather than dying underneath them.
                try: renew_session_if_stale()
                except Exception: pass
                with OAUTH_LOCK: job = dict(OAUTH_JOB)
                self.json_response({**auth_status(), "oauthJob": job})
            except Exception:
                with OAUTH_LOCK: job = dict(OAUTH_JOB)
                self.json_response({"connected": False, "socialWrite": False, "readOnly": True, "oauthJob": job})
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
            self.json_response({"timezoneSource": "browser", "storage": "Windows Local AppData",
                "hasArchives": archives, "version": APP_VERSION})
            return
        if parsed.path == "/api/settings":
            self.json_response({**SETTINGS.load(), "siteOrigin": SITE_ORIGIN,
                "ratings": ["Soft", "Mature", "X"],
                "browsingLevelOptions": [1, 2, 4, 8, 16]})
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
                self.json_response({"date": value, "models": HISTORY.day_models(key)})
            except ValueError as error: self.json_response({"error": str(error)}, 400)
            except Exception as error: self.internal_error("Day models", error)
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
        if parsed.path == "/api/history/day":
            try:
                value = query.get("date", [previous_local_day()])[0]
                key = HISTORY.archive_key(value, query.get("segment", ["all"])[0])
                day = HISTORY.day_summary(key)
                if not day.get("complete"):
                    self.json_response({"error": "That day is still loading"}, 409)
                    return
                # Report the count the gallery will actually show. Naming a total the user
                # can never reach by scrolling reads as a bug in the paging.
                hidden_creators, hidden_images = hidden_preferences()
                if hidden_creators or hidden_images:
                    present = {row["key"] for row in HISTORY.day_artist_keys(key)}
                    showable = visible_creator_keys(key, hidden_images) if hidden_images else present
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
                showable = visible_creator_keys(key, hidden_images) if hidden_images else None
                session_token = query.get("session", [None])[0]
                seen = TASTE.seen_creator_keys(value)
                order, total = cached_day_view_order(key, view, pinned_username, signals,
                                                     hidden_images, session_token, seen,
                                                     hidden_creators, showable)
                models = [value for value in query.get("model", []) if value][:20]
                representatives = None
                if models:
                    picks = HISTORY.creators_using_models(key, models)
                    representatives = picks
                    allowed = set(picks)
                    order = [key_name for key_name in (order or [row["key"] for row in
                        HISTORY.day_artist_keys(key)]) if key_name in allowed]
                    total = len(order)
                if hidden_creators or hidden_images:
                    order = [key_name for key_name in (order or [row["key"] for row in
                        HISTORY.day_artist_keys(key)])
                        if key_name not in hidden_creators
                        and (showable is None or key_name in showable)]
                    total = len(order)
                artists = [decorate_history_artist(item, profiles, follows, signals, seen)
                    for item in HISTORY.artists_page(key, offset, limit, pinned_username, order,
                                                     representatives, hidden_images)]
                if view == "foryou":
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
                self.json_response({"date": value, "offset": offset, "artists": artists,
                    "view": view, "total": total, "hasMore": offset + len(artists) < total
                        if total is not None else len(artists) == limit})
            except ValueError as error: self.json_response({"error": str(error)}, 400)
            except Exception as error: self.internal_error("History artist page", error)
            return
        if parsed.path == "/api/creator-metadata":
            try:
                if not connected_or_false():
                    self.json_response({"error": "Connect Civitai to load creator profiles and follow state."}, 401)
                    return
                self.json_response({"creators": enrich_creator_metadata(query.get("username", []))})
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
                if any(not HISTORY.has_image(image_id) for image_id in image_ids):
                    raise ValueError("Image is not in this history archive")
                try:
                    values = SocialClient().batch_query_optional("image.get", [{"id": image_id} for image_id in image_ids])
                except CivitaiHTTPError:
                    # Civitai fails the whole batch when any image has been deleted since
                    # it was archived. Reaction state is decoration, so a batch containing
                    # a removed image yields nothing rather than an error on the page.
                    values = [None] * len(image_ids)
                self.json_response({"images": {str(image_id): {"reactions": sorted(reaction_names(value))}
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
                    "images": HISTORY.artist_images(key, username, models,
                                                    hidden_preferences()[1])})
            except ValueError as error: self.json_response({"error": str(error)}, 400)
            except Exception as error: self.internal_error("Artist image history", error)
            return
        if parsed.path == "/api/history/image":
            try:
                image_id = int(query.get("id", [0])[0])
                detail = HISTORY.detail(image_id)
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
        path = "index.html" if parsed.path == "/" else urllib.parse.unquote(parsed.path).lstrip("/")
        target = (STATIC / path).resolve()
        if STATIC.resolve() not in target.parents and target != STATIC.resolve():
            self.send_error(403)
            return
        if not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if mime.startswith("text/") or mime in {"application/javascript", "application/json"}: mime += "; charset=utf-8"
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' https://image.civitai.com https://*.civitai.com https://*.civitai.red data:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        # Without this, a plain refresh can silently keep serving a stale cached copy of
        # app.js/index.html — no ETag or Last-Modified was sent either, so the browser had
        # nothing to revalidate against and no reason not to just reuse its cache.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def request_json(self) -> dict:
        origin = self.headers.get("Origin")
        if origin:
            parsed = urllib.parse.urlparse(origin)
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise PermissionError("Request origin is not allowed")
        if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            raise ValueError("Content-Type must be application/json")
        length = int(self.headers.get("Content-Length") or 0)
        if length < 2 or length > 8192:
            raise ValueError("Invalid request size")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_POST(self) -> None:  # noqa: N802
        if not self.host_allowed():
            self.send_error(403); return
        parsed = urllib.parse.urlparse(self.path)
        try:
            body = self.request_json()
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
                previous = SETTINGS.load()
                content_change = ("browsingLevels" in body or "contentRating" in body)
                value = SETTINGS.update(browsing_levels_value=body.get("browsingLevels"),
                                        content_rating_value=body.get("contentRating"),
                                        dim_seen_cards_value=body.get("dimSeenCards"),
                                        check_for_updates_value=body.get("checkForUpdates"))
                if content_change:
                    try:
                        HISTORY.set_content_filter(value["browsingLevels"])
                    except Exception:
                        SETTINGS.update(browsing_levels_value=previous["browsingLevels"],
                                        dim_seen_cards_value=previous["dimSeenCards"],
                                        check_for_updates_value=previous["checkForUpdates"])
                        raise
                    with WRITE_LOCK:
                        ORDER_CACHE.clear()
                        # Visibility is cached separately for each exact level selection,
                        # so switching away and back can reuse it. The account-data token
                        # still invalidates every selection when Civitai controls change.
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
                self.json_response({**UPDATES.start_download(), "enabled": True}, 202)
                return
            if parsed.path == "/api/update/install":
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
            if parsed.path == "/api/update/result/acknowledge":
                UPDATES.clear_result()
                self.json_response({"acknowledged": True})
                return
            if parsed.path == "/api/history/start":
                value = str(body.get("date") or previous_local_day())
                self.json_response(HISTORY.start(value, str(body.get("startUtc") or ""),
                    str(body.get("endUtc") or ""), str(body.get("timezone") or "Local"),
                    str(body.get("segment") or "all"), body.get("contentRating")), 202)
                return
            if parsed.path == "/api/history/rebuild":
                value = str(body.get("date") or previous_local_day())
                self.json_response(HISTORY.rebuild(value, str(body.get("startUtc") or ""), str(body.get("endUtc") or ""), str(body.get("timezone") or "Local"), str(body.get("segment") or "all")), 202)
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
            if parsed.path == "/api/history/tags":
                raw_ids = body.get("imageIds")
                if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 100:
                    self.json_response({"error": "Provide 1 to 100 image IDs"}, 400)
                    return
                image_ids = list(dict.fromkeys(int(value) for value in raw_ids))
                if any(not HISTORY.has_image(image_id) for image_id in image_ids):
                    self.json_response({"error": "An image is not in this history archive"}, 400)
                    return
                tags = {image_id: TASTE.image_tags(image_id) for image_id in image_ids}
                # Cached checks remain useful in read-only/offline test and recovery
                # states. A live lookup needs OAuth, but the ordinary signed-in gallery
                # always has it before reaching this endpoint.
                if connected_or_false() and any(not value["known"] for value in tags.values()):
                    tags = TASTE.ensure_image_tags_many(SocialClient(), image_ids)
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
        in_history = HISTORY.has_image(image_id)
        if item is None and not in_history:
            raise ValueError("Image is not in this discovery feed")
        client = SocialClient()
        current = reaction_names(client.query("image.get", {"id": image_id}))
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
            stats = HISTORY.stats(image_id)
            if changed:
                delta = 1 if desired else -1; key = REACTIONS[reaction]
                stats[key] = max(0, int(stats.get(key, 0)) + delta); stats["reactionCount"] = max(0, int(stats.get("reactionCount", 0)) + delta)
                HISTORY.update_stats(image_id, stats)
        else: stats = item.get("stats", {})
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
                                                  or TASTE.has_creator(user_id)):
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
        self.json_response({"userId": user_id, "following": desired, "changed": changed})

    def log_message(self, format: str, *args: object) -> None:
        print(f"[discovery] {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--updated-from", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("For your privacy, this app can only listen on the local computer")
    if not claim_single_instance(args.no_browser):
        return
    # The OAuth redirect is registered against one fixed port, so the app must never
    # occupy it: doing so leaves the callback listener unable to bind and Civitai's
    # redirect lands on this server, which answers 404 and strands the sign-in.
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    if server.server_address[1] == CALLBACK_PORT:
        server.server_close()
        server = ThreadingHTTPServer((args.host, 0), Handler)
        if server.server_address[1] == CALLBACK_PORT:
            raise SystemExit(f"Port {CALLBACK_PORT} is reserved for signing in to Civitai")
    actual_port = server.server_address[1]
    display_host = f"[{args.host}]" if ":" in args.host else args.host
    url = f"http://{display_host}:{actual_port}"
    save_instance_url(url)
    UPDATES.schedule_success_cleanup()
    print(f"Civitai artist discovery running at {url}")
    print("OAuth-backed follow and reaction controls are enabled when SocialWrite is approved.")
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
        (DATA_ROOT / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        if getattr(sys, "frozen", False) and os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f"Civitai Artist Discovery could not start.\n\nSee:\n{DATA_ROOT / 'error.log'}", "Civitai Artist Discovery", 0x10)
        else:
            raise
