"""Transient tag/reaction failures stay errors; only safe reads may be replayed."""
import test_safety  # Refuse live application endpoints and isolate all test data.

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from discovery import social
from discovery.civitai import RequestLane
from discovery.retry import retry_after_seconds

now = datetime(2026, 10, 15, tzinfo=timezone.utc)
assert retry_after_seconds("12", now) == 12
assert retry_after_seconds(format_datetime(now + timedelta(seconds=75)), now) == 75
assert retry_after_seconds(format_datetime(now - timedelta(seconds=75)), now) == 0
assert retry_after_seconds("nan", now) is None
assert retry_after_seconds("bad", now) is None

client = social.SocialClient()
with (
    patch.object(social, "get_access_token", return_value="fake"),
    patch.object(social, "refresh_access_token", return_value="fresh") as refresh,
    patch.object(social.time, "sleep"),
    patch.object(
        client,
        "_send",
        side_effect=[
            social.CivitaiHTTPError(401, "Expired"),
            social.CivitaiHTTPError(503, "Temporary"),
            {"ok": True},
        ],
    ) as send,
):
    assert client._request(urllib.request.Request(social.BASE_URL)) == {"ok": True}
    assert send.call_count == 3 and refresh.call_count == 1

client = social.SocialClient(lane=RequestLane())
with (
    patch.object(social, "get_access_token", return_value="fake"),
    patch.object(client, "_send", side_effect=TimeoutError("timeout")) as send,
):
    try:
        client._request(urllib.request.Request(social.BASE_URL))
        raise AssertionError("background timeout hidden")
    except TimeoutError:
        pass
    assert send.call_count == 1, "background and server retry budgets multiplied"

for failure in [
    social.CivitaiHTTPError(401, "Denied"),
    social.CivitaiHTTPError(503, "Unavailable"),
    TimeoutError("timeout"),
]:
    client = social.SocialClient()
    request = urllib.request.Request(social.BASE_URL, data=b"{}", method="POST")
    with (
        patch.object(social, "get_access_token", return_value="fake"),
        patch.object(social, "refresh_access_token") as refresh,
        patch.object(client, "_send", side_effect=failure) as send,
    ):
        try:
            client._request(request)
            raise AssertionError("mutation should fail")
        except (social.CivitaiHTTPError, TimeoutError):
            pass
        assert send.call_count == 1 and refresh.call_count == 0

for failure in [
    TimeoutError("timeout"),
    urllib.error.URLError("offline"),
    social.CivitaiHTTPError(503, "Unavailable", "2"),
    social.CivitaiHTTPError(
        503,
        "Unavailable",
        format_datetime(datetime.now(timezone.utc) + timedelta(seconds=10)),
    ),
]:
    client = social.SocialClient()
    with (
        patch.object(social, "get_access_token", return_value="fake"),
        patch.object(social.time, "sleep") as sleep,
        patch.object(
            client, "_send", side_effect=[failure, failure, {"ok": True}]
        ) as send,
    ):
        assert client._request(urllib.request.Request(social.BASE_URL)) == {"ok": True}
        assert send.call_count == 3 and sleep.call_count == 2
        if isinstance(failure, social.CivitaiHTTPError):
            assert sleep.call_args[0][0] >= 2
            if failure.retry_after != "2":
                assert sleep.call_args[0][0] >= 8
    with (
        patch.object(social, "get_access_token", return_value="fake"),
        patch.object(social.time, "sleep"),
        patch.object(client, "_send", side_effect=failure) as send,
    ):
        try:
            client._request(urllib.request.Request(social.BASE_URL))
            raise AssertionError("exhaustion hidden")
        except (social.CivitaiHTTPError, TimeoutError, urllib.error.URLError):
            pass
        assert send.call_count == 3

with tempfile.TemporaryDirectory(
    prefix="cad3-retry-correctness-", ignore_cleanup_errors=True
) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    from discovery.taste import TasteStore

    store = TasteStore(Path(temporary) / "discovery")
    client = social.SocialClient()
    client._request = lambda request: [
        {"error": {"json": {"message": "Temporary", "data": {"httpStatus": 503}}}}
    ]
    try:
        store.ensure_image_tags_many(client, [1])
        raise AssertionError("batch error hidden")
    except social.CivitaiHTTPError:
        pass
    assert store.image_tags(1) == {"known": False, "tags": []}
    client._request = lambda request: [{"result": {"data": {"json": []}}}]
    assert store.ensure_image_tags_many(client, [1])[1] == {"known": True, "tags": []}
    client._request = lambda request: [
        {"error": {"json": {"message": "Gone", "data": {"httpStatus": 404}}}}
    ]
    assert store.ensure_image_tags_many(client, [2])[2]["known"] is False
    import server

    handler = server.Handler.__new__(server.Handler)
    handler.path = "/api/reaction-status?imageId=1"
    handler.host_allowed = lambda: True
    responses = []
    handler.json_response = lambda value, status=200: responses.append((status, value))
    with (
        patch.object(server, "connected_or_false", return_value=True),
        patch.object(server, "collected_image", return_value=True),
        patch.object(server, "SocialClient") as factory,
    ):
        factory.return_value.batch_query_optional.side_effect = social.CivitaiHTTPError(
            503, "Temporary"
        )
        handler.do_GET()
    assert responses[-1][0] == 503 and "error" in responses[-1][1], responses
    handler.internal_error(
        "Image detail", server.RetryBudgetExhausted("network_retry", 3)
    )
    assert (
        responses[-1][0] == 503
        and "temporarily unavailable" in responses[-1][1]["error"]
    )

print(
    {
        "transientTagFailuresNotCached": True,
        "genuineEmptyTagsCached": True,
        "reactionErrorsPreserved": True,
        "safeReadRecovery": True,
        "boundedExhaustion": True,
        "mutationsNeverReplayed": True,
        "retryAfterDate": True,
    }
)
