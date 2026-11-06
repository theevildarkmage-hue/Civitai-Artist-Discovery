"""Deterministic browser-media lifecycle tests use the production module in Node."""
import test_safety  # Refuse live application endpoints and isolate all test data.

from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
subprocess.run(["node", str(root / "tests" / "fixtures" / "video_lifecycle.mjs")], check=True, cwd=root)

sys.path.insert(0, str(root))
from discovery.diagnostics import log_browser_events

with tempfile.TemporaryDirectory(prefix="cad3-video-log-") as temporary:
    log_browser_events(Path(temporary), [{"event": event, "imageId": 42,
        "reason": "playback-stalled", "surface": "gallery",
        "url": "https://image.civitai.com/test.mp4?token=private#secret",
        "authorization": "private"} for event in ("video_retry", "video_failed")])
    logged = (Path(temporary) / "error.log").read_text(encoding="utf-8")
    assert "video_retry" in logged and "video_failed" in logged
    assert "private" not in logged and "secret" not in logged
