"""A rejected OAuth token is refreshed once without hiding persistent failures."""
import test_safety  # Refuse live application endpoints and isolate all test data.

from pathlib import Path
import sys
from unittest.mock import patch
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery import social
from discovery.civitai import RequestLane


for lane in (None, RequestLane()):
    client = social.SocialClient(lane=lane)
    headers = []

    def send(request):
        headers.append(request.get_header("Authorization"))
        if len(headers) == 1:
            raise social.CivitaiHTTPError(401, "Unauthorized")
        return {"ok": True}

    with patch.object(social.time, "sleep"), patch.object(social, "get_access_token", return_value="rejected"), \
         patch.object(social, "refresh_access_token", return_value="fresh") as refresh, \
         patch.object(client, "_send", side_effect=send):
        assert client._request(urllib.request.Request(social.BASE_URL)) == {"ok": True}
        assert headers == ["Bearer rejected", "Bearer fresh"]
        refresh.assert_called_once_with("rejected")

for status in (401, 403, 429, 500):
    client = social.SocialClient()
    error = social.CivitaiHTTPError(status, "Still unavailable")
    with patch.object(social.time, "sleep"), patch.object(social, "get_access_token", return_value="rejected"), \
         patch.object(social, "refresh_access_token", return_value="fresh") as refresh, \
         patch.object(client, "_send", side_effect=error) as send:
        try:
            client._request(urllib.request.Request(social.BASE_URL))
            raise AssertionError("Persistent failures must propagate")
        except social.CivitaiHTTPError as raised:
            assert raised is error
        assert send.call_count == (2 if status == 401 else 3 if status in (429, 500) else 1)
        assert refresh.call_count == (1 if status == 401 else 0)

print("OAuth request recovery: one retry; other HTTP failures preserved")
