"""Authenticated Civitai social actions; OAuth tokens never leave this process."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


from .flatjson import unwrap_result
from .oauth import get_access_token, status


BASE_URL = "https://civitai.red"
SOCIAL_WRITE = 1 << 19
REACTION_NAMES = ("Like", "Heart", "Laugh", "Cry")


class CivitaiHTTPError(RuntimeError):
    """Carries the HTTP status so callers can back off on 429 without parsing text."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class SocialClient:
    def public_model_version(self, model_version_id: int) -> dict:
        """Resolve a public model-version id without sending the OAuth token."""
        request = urllib.request.Request(
            f"{BASE_URL}/api/v1/model-versions/{int(model_version_id)}",
            headers={"Accept": "application/json",
                     "User-Agent": "CivitaiArtistDiscovery/1.0 (local artist discovery app)"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                value = json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:600]
            raise CivitaiHTTPError(
                error.code, f"Civitai returned HTTP {error.code}: {detail}") from error
        if not isinstance(value, dict):
            raise RuntimeError("Civitai returned an unexpected model-version response")
        return value

    def _request(self, request: urllib.request.Request) -> object:
        request.add_header("Authorization", f"Bearer {get_access_token()}")
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", "CivitaiArtistDiscovery/1.0 (local Windows artist discovery app; user-requested reads)")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:600]
            raise CivitaiHTTPError(error.code, f"Civitai returned HTTP {error.code}: {detail}") from error

    def query(self, procedure: str, payload: dict) -> object:
        encoded = urllib.parse.quote(json.dumps({"json": payload}, separators=(",", ":")))
        response = self._request(urllib.request.Request(f"{BASE_URL}/api/trpc/{procedure}?input={encoded}"))
        if "error" in response:
            raise RuntimeError(response["error"].get("json", {}).get("message", "Civitai query failed"))
        return unwrap_result(response["result"])

    def batch_query(self, procedure: str, payloads: list[dict]) -> list[object]:
        if not payloads:
            return []
        if len(payloads) > 100:
            raise ValueError("Civitai query batches are limited to 100 items")
        procedures = ",".join(procedure for _ in payloads)
        inputs = {str(index): {"json": payload} for index, payload in enumerate(payloads)}
        encoded = urllib.parse.quote(json.dumps(inputs, separators=(",", ":")))
        response = self._request(urllib.request.Request(f"{BASE_URL}/api/trpc/{procedures}?batch=1&input={encoded}"))
        if not isinstance(response, list) or len(response) != len(payloads):
            raise RuntimeError("Civitai returned an incomplete creator batch")
        results = []
        for item in response:
            if not isinstance(item, dict) or "error" in item:
                message = item.get("error", {}).get("json", {}).get("message") if isinstance(item, dict) else None
                raise RuntimeError(message or "Civitai batch query failed")
            results.append(unwrap_result(item["result"]))
        return results

    def batch_query_optional(self, procedure: str, payloads: list[dict]) -> list[object | None]:
        """Run a read batch while preserving successful rows if one item is unavailable."""
        if not payloads:
            return []
        if len(payloads) > 100:
            raise ValueError("Civitai query batches are limited to 100 items")
        procedures = ",".join(procedure for _ in payloads)
        inputs = {str(index): {"json": payload} for index, payload in enumerate(payloads)}
        encoded = urllib.parse.quote(json.dumps(inputs, separators=(",", ":")))
        response = self._request(urllib.request.Request(f"{BASE_URL}/api/trpc/{procedures}?batch=1&input={encoded}"))
        if not isinstance(response, list) or len(response) != len(payloads):
            raise RuntimeError("Civitai returned an incomplete query batch")
        return [None if not isinstance(item, dict) or "error" in item else unwrap_result(item["result"])
            for item in response]

    def mutate(self, procedure: str, payload: dict) -> object:
        body = json.dumps({"json": payload}, separators=(",", ":")).encode()
        request = urllib.request.Request(
            f"{BASE_URL}/api/trpc/{procedure}", data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        response = self._request(request)
        if "error" in response:
            raise RuntimeError(response["error"].get("json", {}).get("message", "Civitai mutation failed"))
        return unwrap_result(response["result"])

    def images_page(self, *, cursor: object = None, limit: int = 100,
                    reactions: list[str] | None = None, with_tags: bool = True,
                    tags: list[int] | None = None, period: str = "AllTime") -> dict:
        """One page of `image.getInfinite`.

        Passing `reactions` without a username asks Civitai for images the *connected
        account reacted to*, which is what the profile page's My Reactions tab shows.
        Supplying a username instead would filter by uploader and is not what we want.
        """
        payload: dict = {"limit": max(1, min(200, limit)), "period": period, "sort": "Newest"}
        if reactions:
            payload["reactions"] = list(reactions)
        if tags:
            # Tag ids, never names: Civitai rejects names, and an id from a different
            # namespace silently returns unrelated images.
            payload["tags"] = [int(value) for value in tags]
        if with_tags:
            payload["include"] = ["tags"]
        if cursor is not None:
            payload["cursor"] = cursor
        page = self.query("image.getInfinite", payload)
        if not isinstance(page, dict):
            raise RuntimeError("Civitai returned an unexpected image page")
        return {"items": page.get("items") or [], "nextCursor": page.get("nextCursor")}

    def creator_images_page(self, username: str, cursor: object = None,
                            limit: int = 100) -> dict:
        """One newest-first page of a creator's public images, including tags."""
        clean = str(username or "").strip()
        if not clean:
            raise ValueError("Provide a creator username")
        payload = {"limit": max(1, min(200, int(limit))), "period": "AllTime",
                   "sort": "Newest", "username": clean, "include": ["tags"]}
        if cursor is not None:
            payload["cursor"] = cursor
        page = self.query("image.getInfinite", payload)
        if not isinstance(page, dict):
            raise RuntimeError("Civitai returned an unexpected creator image page")
        return {"items": page.get("items") or [], "nextCursor": page.get("nextCursor")}


def auth_status() -> dict:
    value = status()
    raw_scope = value.get("scope") or 0
    if isinstance(raw_scope, list):
        raw_scope = raw_scope[0] if raw_scope else 0
    try:
        scope = int(raw_scope)
    except (TypeError, ValueError):
        scope = 0
    # Follows and reactions are part of signing in, so Civitai's grant is the only
    # gate. If it withheld write access, the app must not attempt one.
    granted = (scope & SOCIAL_WRITE) == SOCIAL_WRITE
    return {**value, "scopeGrantsWrite": granted, "socialWrite": granted}


def reaction_names(image: object) -> set[str]:
    if not isinstance(image, dict):
        return set()
    result = set()
    for value in image.get("reactions") or []:
        name = value.get("reaction") if isinstance(value, dict) else value
        if name in {"Like", "Heart", "Laugh", "Cry"}:
            result.add(name)
    return result


def following_ids(value: object) -> set[int]:
    if isinstance(value, dict):
        value = value.get("items") or value.get("users") or value.get("following") or []
    result = set()
    for item in value if isinstance(value, list) else []:
        raw = item.get("id") if isinstance(item, dict) else item
        try:
            result.add(int(raw))
        except (TypeError, ValueError):
            pass
    return result
