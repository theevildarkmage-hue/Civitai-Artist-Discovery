"""Civitai batch limits adapt without losing results or hiding sweep failures."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.parse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEST_DATA = tempfile.TemporaryDirectory(prefix="adaptive-batches-", ignore_cleanup_errors=True)
os.environ["CIVITAI_HISTORY_DATA_DIR"] = TEST_DATA.name

from discovery.social import CivitaiHTTPError, SocialClient


class LimitedClient(SocialClient):
    """Pretend Civitai currently accepts no more than three calls per batch."""

    def __init__(self):
        super().__init__()
        self.batch_sizes: list[int] = []

    def _request(self, request):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        inputs = json.loads(query["input"][0])
        self.batch_sizes.append(len(inputs))
        if len(inputs) > 3:
            raise CivitaiHTTPError(
                400, "Civitai returned HTTP 400: Batch call exceeds maximum size")
        response = []
        for item in inputs.values():
            value = item["json"]["value"]
            if value == 4:
                response.append({"error": {"json": {"message": "Unavailable"}}})
            else:
                response.append({"result": {"data": {"json": value}}})
        return response


client = LimitedClient()
strict = client.batch_query("test.echo", [{"value": value} for value in range(4)])
assert strict == [0, 1, 2, 3], strict
assert any(size > 3 for size in client.batch_sizes), client.batch_sizes
assert max(client.batch_sizes[-2:]) <= 2, client.batch_sizes

# Optional batches retain unavailable rows and preserve the input order across splits.
before = len(client.batch_sizes)
optional = client.batch_query_optional(
    "test.optional", [{"value": value} for value in range(8)])
assert optional == [0, 1, 2, 3, None, 5, 6, 7], optional
assert any(size > 3 for size in client.batch_sizes[before:]), client.batch_sizes
before = len(client.batch_sizes)
assert client.batch_query_optional(
    "test.optional", [{"value": value} for value in range(6)]) == [0, 1, 2, 3, None, 5]
assert max(client.batch_sizes[before:]) <= 2, client.batch_sizes


class OtherFailureClient(SocialClient):
    def _request(self, request):
        raise CivitaiHTTPError(400, "Civitai returned HTTP 400: Invalid username")


try:
    OtherFailureClient().batch_query("test.echo", [{"value": 1}, {"value": 2}])
    raise AssertionError("A non-size HTTP error was incorrectly split or swallowed")
except CivitaiHTTPError as error:
    assert "Invalid username" in str(error)


# A background sweep must retain its partial count and expose its error to the browser.
import server


class FakeHistory:
    def day_artist_keys(self, key):
        return [{"username": f"Creator{index}", "representativeId": index}
                for index in range(5)]


class FakeTaste:
    def follower_coverage(self, names):
        return 2, len(names)

    def sweep_followers(self, client, targets, cancel, progress):
        progress(1, len(targets))
        raise RuntimeError("deliberate follower failure")


real_history, real_taste = server.HISTORY, server.TASTE
server.HISTORY, server.TASTE = FakeHistory(), FakeTaste()
try:
    server.SWEEP_JOBS["followers"].update({
        "running": True, "done": 1, "total": 5, "day": "test-day", "error": None})
    server.run_sweep("followers", "test-day", [f"Creator{i}" for i in range(5)], 1, 5)
    job = server.SWEEP_JOBS["followers"]
    assert job["running"] is False
    assert job["done"] == 2, job
    assert "deliberate follower failure" in job["error"], job

    class SuccessfulTaste(FakeTaste):
        def sweep_followers(self, client, targets, cancel, progress):
            progress(3, len(targets))
            return 3

    server.TASTE = SuccessfulTaste()
    server.SWEEP_JOBS["followers"].update({
        "running": True, "done": 2, "total": 5, "day": "test-day",
        "error": None, "attemptedAll": False})
    server.run_sweep("followers", "test-day", [f"Creator{i}" for i in range(5)], 2, 5)
    status = server.sweep_status("followers", "test-day")
    assert status["complete"] is True, status
    assert status["known"] == 2, status
    assert status["job"]["attemptedAll"] is True, status
finally:
    server.HISTORY, server.TASTE = real_history, real_taste


print({"adaptiveLimit": client._batch_limits, "optionalOrderPreserved": True,
       "sweepFailureVisible": True, "unavailableCountsDoNotLoop": True})
TEST_DATA.cleanup()
