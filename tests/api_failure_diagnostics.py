"""Failed public API responses leave bounded, useful, credential-free diagnostics."""

from email.message import Message
import json
from pathlib import Path
import sys
import tempfile
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import discovery.history as history
from discovery.history import HistoryArchive


class Event:
    def is_set(self):
        return False

    def wait(self, _seconds):
        return False


with tempfile.TemporaryDirectory(prefix="civitai-api-diagnostics-") as temporary:
    archive = HistoryArchive(Path(temporary) / "history")
    archive.api_pacer.minimum = archive.api_pacer.maximum = archive.api_pacer.interval = 0
    real_urlopen = history.urllib.request.urlopen
    real_uniform = history.random.uniform
    calls = 0

    def unavailable(request, timeout=60):
        global calls
        calls += 1
        headers = Message()
        headers["Content-Type"] = "application/json"
        headers["Retry-After"] = "1"
        headers["CF-Ray"] = "diagnostic-ray"
        body = ('{"error":"upstream query timed out","access_token":"must-not-survive"}'
                ).encode()
        raise urllib.error.HTTPError(request.full_url, 503, "Service Unavailable",
                                    headers, __import__("io").BytesIO(body))

    history.urllib.request.urlopen = unavailable
    history.random.uniform = lambda *_: 0
    try:
        try:
            archive._request({"limit": 200, "sort": "Newest", "period": "AllTime",
                              "browsingLevel": 16, "withMeta": "false",
                              "cursor": "saved-public-cursor"}, cancel_event=Event())
            raise AssertionError("persistent service failure did not stop")
        except history.RetryBudgetExhausted as error:
            assert error.last_failure == "HTTP 503 Service Unavailable", error.last_failure

        lines = archive.api_failure_log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == history.CHECKPOINTED_RETRY_ATTEMPTS, len(lines)
        record = json.loads(lines[-1])
        assert record["status"] == 503 and record["kind"] == "http", record
        assert record["headers"]["Retry-After"] == "1", record
        assert record["headers"]["CF-Ray"] == "diagnostic-ray", record
        assert record["request"]["browsingLevel"] == 16, record
        assert "upstream query timed out" in record["bodyExcerpt"], record
        assert "must-not-survive" not in record["bodyExcerpt"], record
        assert "[redacted]" in record["bodyExcerpt"], record
    finally:
        history.urllib.request.urlopen = real_urlopen
        history.random.uniform = real_uniform

print({"httpStatusRecorded": True, "safeHeadersRecorded": True,
       "bodyExcerptRecorded": True, "credentialsRedacted": True,
       "requestContextRecorded": True, "retryLimitStillApplied": True})
