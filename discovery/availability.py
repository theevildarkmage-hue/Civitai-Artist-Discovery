"""Bounded, on-demand CDN status checks. Never reads media bodies or archive data."""

from collections import OrderedDict, deque
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ArtworkAvailability:
    def __init__(self):
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(2)
        self.recent = deque()
        self.cache = OrderedDict()

    @staticmethod
    def valid_url(value):
        if not isinstance(value, str) or len(value) > 1024:
            return False
        try:
            url = urllib.parse.urlsplit(value)
            return (url.scheme == 'https' and url.netloc == 'image.civitai.com'
                    and not url.query and not url.fragment
                    and url.path.lower().endswith(('.jpeg', '.jpg', '.png', '.webp', '.avif', '.gif')))
        except ValueError:
            return False

    def check(self, sources):
        if (not isinstance(sources, list) or not 1 <= len(sources) <= 2
                or not all(self.valid_url(url) for url in sources)):
            raise ValueError('Provide one or two Civitai image CDN URLs')
        sources = tuple(dict.fromkeys(sources))
        now = time.monotonic()
        with self.lock:
            cached = self.cache.get(sources)
            if cached and now - cached[0] < 60:
                return cached[1]
            while self.recent and now - self.recent[0] > 60:
                self.recent.popleft()
            if len(self.recent) >= 64 or not self.slots.acquire(blocking=False):
                return {'unavailable': False, 'statuses': [], 'retryAfter': 60}
            self.recent.append(now)
        try:
            opener = urllib.request.build_opener(NoRedirect())
            statuses = []
            for url in sources:
                request = urllib.request.Request(url, method='HEAD', headers={
                    'User-Agent': 'CivitaiArtistDiscovery/2.2', 'Accept': 'image/*'})
                try:
                    with opener.open(request, timeout=3) as response:
                        statuses.append(response.status)
                except urllib.error.HTTPError as error:
                    statuses.append(error.code)
                    error.close()
                except (OSError, ValueError):
                    statuses.append(None)
            result = {'unavailable': all(status in (404, 410) for status in statuses),
                      'statuses': statuses, 'retryAfter': 60}
            with self.lock:
                self.cache[sources] = (time.monotonic(), result)
                self.cache.move_to_end(sources)
                while len(self.cache) > 512:
                    self.cache.popitem(last=False)
            return result
        finally:
            self.slots.release()
