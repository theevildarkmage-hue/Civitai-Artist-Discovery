"""Status confirmation is bounded, HEAD-only, conservative, and CDN-restricted."""
import test_safety  # Refuse live application endpoints and isolate all test data.

from pathlib import Path
import sys
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from discovery.availability import ArtworkAvailability, NoRedirect

URL = 'https://image.civitai.com/example/original=true/test.jpeg'


class Response:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass


for statuses, expected in [([404, 410], True), ([404, 200], False),
                            ([404, 429], False), ([503, 503], False),
                            ([401, 403], False), ([TimeoutError(), 404], False),
                            ([302, 404], False), ([405, 404], False)]:
    checker = ArtworkAvailability()
    calls = []

    def head(request, timeout):
        calls.append(request)
        assert request.method == 'HEAD' and timeout == 3
        value = statuses[len(calls) - 1]
        if isinstance(value, Exception):
            raise value
        if value >= 300:
            raise urllib.error.HTTPError(request.full_url, value, '', {}, None)
        return Response(value)

    with patch('urllib.request.build_opener') as factory:
        factory.return_value.open.side_effect = head
        result = checker.check([URL, URL.replace('original=true', 'width=768')])
        assert result['unavailable'] is expected, result
        assert checker.check([URL, URL.replace('original=true', 'width=768')]) == result
        assert len(calls) == 2
        assert isinstance(factory.call_args.args[0], NoRedirect)

checker = ArtworkAvailability()
for invalid in ['http://image.civitai.com/a.jpeg', 'https://localhost/a.jpeg',
                'https://image.civitai.com.evil/a.jpeg', 'https://image.civitai.com:443/a.jpeg',
                URL + '?token=x', URL + '#x', URL.replace('.jpeg', '.mp4')]:
    try:
        checker.check([invalid])
        raise AssertionError(invalid)
    except ValueError:
        pass
assert NoRedirect().redirect_request(None, None, 302, '', {}, URL) is None

# Compose availability classification with 3.0's protected local preview cache.
import server
item={'id':42,'type':'image','url':URL,'thumbnailUrl':URL.replace('original=true','width=512')}
with patch.object(server,'auth_status',side_effect=RuntimeError('No cached session')):
    assert server.personal_preview(item)==item  # Missing auth is not a 500 on cached reads.
with patch.object(server,'auth_status',return_value={'id':123}):
    cached=server.personal_preview(item)
    assert cached['thumbnailUrl']=='/api/history/thumbnail/42'
    assert cached['availabilitySources']==[item['thumbnailUrl'],item['url']]
    assert item['url']==URL  # Never mutate authoritative archive metadata.
checker.recent.extend([__import__('time').monotonic()] * 64)
with patch('urllib.request.build_opener') as opener:
    assert checker.check([URL])['unavailable'] is False
    opener.assert_not_called()
print({'statusPolicy': True, 'headOnly': True, 'bounded': True, 'noRedirects': True})
