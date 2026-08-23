"""Profile analysis uses the gallery collector's safe cadence and honors 429 waits."""

from io import BytesIO
from pathlib import Path
import sys
import urllib.error


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from discovery import social, taste  # noqa: E402


assert taste.MIN_PAUSE >= 5.0
assert taste.MAX_PAUSE >= taste.MIN_PAUSE
assert taste._retry_wait(social.CivitaiHTTPError(429, "limited", "71"), 1) == 71
assert taste._retry_wait(social.CivitaiHTTPError(429, "limited"), 1) >= 30
assert taste._retry_wait(social.CivitaiHTTPError(500, "service"), 2) == 8

original_urlopen = social.urllib.request.urlopen


def limited(request, timeout=60):
    raise urllib.error.HTTPError(
        request.full_url, 429, "Too Many Requests", {"Retry-After": "83"},
        BytesIO(b'{"message":"slow down"}'))


social.urllib.request.urlopen = limited
try:
    try:
        social.SocialClient().public_model_version(1)
        raise AssertionError("Expected the simulated rate limit")
    except social.CivitaiHTTPError as error:
        assert error.status == 429
        assert error.retry_after == "83"
finally:
    social.urllib.request.urlopen = original_urlopen


print({"fiveSecondCadence": True, "retryAfterHonored": True,
       "fallbackBackoff": True, "headersPreserved": True})
