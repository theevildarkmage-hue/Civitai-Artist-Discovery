"""An out-of-reach date fails before collecting, and says how far back Civitai reaches."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.history import FEED_FLOOR_PROBE_OFFSET, HistoryArchive


DAY = "2026-07-31"
START = datetime(2026, 7, 31, 5, tzinfo=timezone.utc)
END = datetime(2026, 7, 31, 17, tzinfo=timezone.utc)
# Every level can reach a week back except PG-13, which stops two days after the block.
FLOORS = {1: START - timedelta(days=7), 2: START + timedelta(days=2),
          4: START - timedelta(days=7), 8: START - timedelta(days=7),
          16: START - timedelta(days=7)}


def wait(archive: HistoryArchive, key: str) -> dict:
    deadline = time.monotonic() + 15
    while archive.status(key)["state"] == "loading" and time.monotonic() < deadline:
        time.sleep(.01)
    return archive.status(key)


with tempfile.TemporaryDirectory(prefix="civitai-window-") as temporary:
    archive = HistoryArchive(Path(temporary) / "history")
    calls: list[dict] = []

    def request(params, on_delay=None, cancel_event=None, on_timing=None,
                on_transfer=None, minimum_interval=None):
        calls.append(dict(params))
        level = int(params["browsingLevel"])
        if str(params.get("cursor", "")).startswith(f"{FEED_FLOOR_PROBE_OFFSET}|"):
            stamp = FLOORS[level].isoformat().replace("+00:00", "Z")
            return {"items": [{"id": level, "createdAt": stamp}]}, 200
        raise AssertionError(f"collection must not start: {params}")

    archive._request = request
    key = f"{DAY}#morning"
    archive.start(DAY, START.isoformat(), END.isoformat(), "America/Chicago", "morning", "Soft")
    failed = wait(archive, key)

    assert failed["state"] == "error", failed
    assert failed["errorKind"] == "history_window", failed
    # It must name the reachable boundary rather than only saying "choose a newer day".
    assert "August 2, 2026" in failed["error"], failed["error"]
    assert "already in your archive" in failed["error"], failed["error"]
    # One probe per required level, and nothing else: no seek, no listing pages.
    probes = [c for c in calls if str(c.get("cursor", "")).startswith(f"{FEED_FLOOR_PROBE_OFFSET}|")]
    assert len(calls) == len(probes) <= 2, calls
    assert all(int(c["limit"]) == 1 for c in probes), probes

    # The window report names the binding level and the oldest buildable local day.
    window = archive.history_window("Soft")
    named = f"{datetime.fromisoformat(window['oldestBuildableDay']):%B}"
    assert f"{named} {int(window['oldestBuildableDay'][8:])}," in failed["error"], (window, failed["error"])
    assert window["measured"] and window["bindingLevel"] == 2, window
    assert window["floor"].startswith("2026-08-02"), window
    assert window["perLevel"]["1"].startswith("2026-07-24"), window
    # Probes are cached, so repeating the report costs no further requests.
    before = len(calls)
    archive.history_window("Soft")
    assert len(calls) == before, calls

print({"preflightBlocked": True, "requestsSpent": len(calls),
       "namesReachableDate": True, "cached": True})
