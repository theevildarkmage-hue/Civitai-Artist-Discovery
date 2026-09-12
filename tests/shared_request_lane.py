from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from discovery import social
from discovery.civitai import API_LANE, RequestLane
from discovery.history import HistoryArchive
from discovery.social import CivitaiHTTPError, SocialClient


# Day collection and the background sweeps reach different Civitai endpoints but are the
# same account talking to the same service. Pacing them separately let a Time Machine
# prime run alongside a day collection at twice the intended rate.
with tempfile.TemporaryDirectory() as temporary:
    archive = HistoryArchive(Path(temporary) / "history")
    assert archive.api_lane is API_LANE
    assert archive.api_pacer is API_LANE.pacer
    assert archive.api_lock is API_LANE.lock

sent: list[float] = []
record_lock = threading.Lock()


def fake_send(request):
    with record_lock:
        sent.append(time.monotonic())
    time.sleep(0.05)
    return {"result": {"data": {"json": {}}}}


social.SocialClient._send = staticmethod(fake_send)
social.get_access_token = lambda: "test-token"

# Background work queues in the lane: concurrent jobs are serialized and spaced by the
# shared pacer rather than each believing it is the only one running.
lane = RequestLane()
lane.pacer.interval = 0.2
workers = [threading.Thread(target=lambda: SocialClient(lane).query("image.get", {"id": 1}))
           for _ in range(4)]
for worker in workers:
    worker.start()
for worker in workers:
    worker.join()
gaps = [round(second - first, 3) for first, second in zip(sorted(sent), sorted(sent)[1:])]
assert len(sent) == 4, sent
assert all(gap >= lane.pacer.interval - 0.01 for gap in gaps), gaps

# An interactive action is one request a reader is waiting on. It must never queue behind
# a collection holding the lane through a rate-limit backoff, which can last minutes.
busy = RequestLane()
busy.pacer.interval = 5.0
holding = threading.Event()
finished = threading.Event()


def hold_lane() -> None:
    with busy.lock:
        busy.last_request = time.monotonic()
        holding.set()
        finished.wait(10)


threading.Thread(target=hold_lane, daemon=True).start()
assert holding.wait(5)
started = time.monotonic()
SocialClient().query("image.get", {"id": 2})
interactive_seconds = time.monotonic() - started
finished.set()
assert interactive_seconds < 0.5, interactive_seconds

# A limit hit on the tRPC side slows the shared pacer, so the collection engine inherits
# what the sweep just learned instead of rediscovering it against the same service.
feedback = RequestLane()
before = feedback.pacer.interval
social.SocialClient._send = staticmethod(
    lambda request: (_ for _ in ()).throw(CivitaiHTTPError(429, "rate limited")))
try:
    SocialClient(feedback).query("image.get", {"id": 3})
except CivitaiHTTPError:
    pass
assert feedback.pacer.interval > before, (before, feedback.pacer.interval)

print({"collectionSharesLane": True, "backgroundWorkSerialized": True,
       "interactiveNeverQueues": round(interactive_seconds, 3),
       "tRPCLimitSlowsSharedPacer": feedback.pacer.interval})
