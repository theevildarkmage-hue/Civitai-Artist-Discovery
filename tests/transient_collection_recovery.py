"""A checkpointed history build survives an outage longer than one retry cycle."""

import json
from pathlib import Path
import sys
import tempfile
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import discovery.history as history
from discovery.history import (CHECKPOINTED_RETRY_ATTEMPTS, CollectionCancelled,
                               HistoryArchive, RATE_LIMIT_RETRIES)


class Event:
    def __init__(self, cancel=False):
        self.cancel = cancel

    def is_set(self):
        return False

    def wait(self, _seconds):
        return self.cancel


class Response:
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps({"items": [], "metadata": {"nextCursor": None}}).encode()


with tempfile.TemporaryDirectory(prefix="civitai-outage-recovery-") as temporary:
    archive = HistoryArchive(Path(temporary))
    archive.api_pacer.minimum = archive.api_pacer.interval = 0
    real_urlopen = history.urllib.request.urlopen
    real_uniform = history.random.uniform
    real_sleep = history.time.sleep
    calls = 0

    def flaky(*_args, **_kwargs):
        global calls
        calls += 1
        if calls <= RATE_LIMIT_RETRIES:
            raise urllib.error.URLError("temporary outage")
        return Response()

    history.urllib.request.urlopen = flaky
    history.random.uniform = lambda *_: 0
    history.time.sleep = lambda *_: None
    try:
        delays = []
        payload, _ = archive._request({"limit": 1},
            on_delay=lambda reason, wait, attempt, attempts:
                delays.append((reason, wait, attempt, attempts)), cancel_event=Event())
        assert payload["items"] == []
        assert calls == RATE_LIMIT_RETRIES + 1, calls
        assert delays[-1][2:] == (RATE_LIMIT_RETRIES, CHECKPOINTED_RETRY_ATTEMPTS), delays[-1]

        calls = 0
        try:
            archive._request({"limit": 1}, cancel_event=Event(cancel=True))
            raise AssertionError("cancel did not interrupt the retry wait")
        except CollectionCancelled:
            pass

        calls = 0
        try:
            archive._request({"limit": 1})
            raise AssertionError("a one-off request retried forever")
        except RuntimeError as error:
            assert "8 attempts" in str(error), error
        assert calls == RATE_LIMIT_RETRIES, calls

        calls = 0
        def outage(*_args, **_kwargs):
            global calls
            calls += 1
            raise urllib.error.URLError("persistent outage")
        history.urllib.request.urlopen = outage
        try:
            archive._request({"limit": 1}, cancel_event=Event())
            raise AssertionError("a checkpointed request retried forever")
        except RuntimeError as error:
            assert "16 attempts" in str(error), error
        assert calls == CHECKPOINTED_RETRY_ATTEMPTS, calls
    finally:
        history.urllib.request.urlopen = real_urlopen
        history.random.uniform = real_uniform
        history.time.sleep = real_sleep

print({"historySurvivesRetryCycle": True, "cancelInterruptsWait": True,
       "oneOffRequestsRemainBounded": True, "checkpointedRequestsRemainBounded": True})
