"""Capture article/release screenshots from a real signed-in gallery.

Unlike tests/v2_visual_baseline.py, this drives the real server against the real
portable data folder, so the shots show actual creators and artwork rather than
fixture placeholders. Close the running app first: the app is single-instance and
this starts its own server on a fixed port.

    python scripts/capture_release_screenshots.py --date 2026-09-06

Shots land in release-assets/<version>/.
"""
import argparse
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PORT = 8912
BASE = f"http://127.0.0.1:{PORT}"
WIDTH, HEIGHT = 1920, 1080


def version() -> str:
    text = (ROOT / "server.py").read_text(encoding="utf-8")
    return re.search(r'APP_VERSION = "([^"]+)"', text).group(1)


def wait_for_server(timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{BASE}/", timeout=2).read()
            return
        except Exception:
            time.sleep(0.5)
    raise SystemExit("server did not start; is the app still running?")


def settle(page, ms: int = 2500) -> None:
    """Let lazy-loaded artwork arrive before the shutter."""
    page.wait_for_timeout(ms)
    page.evaluate("window.scrollTo(0, 400)")
    page.wait_for_timeout(1200)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(1200)


def shot(page, out: Path, name: str) -> None:
    path = out / f"{name}.png"
    page.screenshot(path=str(path))
    print(f"  captured {name}.png")


def click_text(page, text: str, timeout: int = 8000) -> bool:
    try:
        page.get_by_role("button", name=re.compile(text, re.I)).first.click(timeout=timeout)
        return True
    except Exception:
        print(f"  ! could not click {text!r}; skipping that shot")
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="a fully collected day, e.g. 2026-09-06")
    parser.add_argument("--keep-open", action="store_true",
                        help="leave the browser open at the end for manual shots")
    args = parser.parse_args()

    out = ROOT / "release-assets" / f"v{version()}"
    out.mkdir(parents=True, exist_ok=True)

    server = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py"), "--port", str(PORT),
         "--host", "127.0.0.1", "--no-browser"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        wait_for_server()
        with sync_playwright() as play:
            browser = play.chromium.launch()
            page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT},
                                    device_scale_factor=1)
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))

            print(f"opening {args.date}")
            page.goto(f"{BASE}/?date={args.date}", wait_until="networkidle")
            page.wait_for_selector("#gallery .creator-card, #gallery article", timeout=180_000)
            settle(page, 4000)
            shot(page, out, "gallery")

            if click_text(page, r"^Filters"):
                page.wait_for_timeout(900)
                shot(page, out, "filters")
                page.keyboard.press("Escape")
                page.wait_for_timeout(600)

            try:
                page.locator('button[aria-controls="calendarPanel"]').click(timeout=8000)
                page.wait_for_selector("#calendarPanel .calendar-grid", timeout=8000)
                page.wait_for_timeout(900)
                shot(page, out, "calendar")
                page.keyboard.press("Escape")
                page.wait_for_timeout(600)
            except Exception:
                print("  ! could not open the calendar; skipping that shot")

            try:
                page.locator("header button, .view-tabs ~ *").filter(
                    has_text=re.compile(r"^$")).first.click(timeout=4000)
            except Exception:
                pass
            try:
                page.get_by_role("button", name=re.compile("settings|preferences", re.I)
                                 ).first.click(timeout=6000)
                page.wait_for_timeout(900)
                shot(page, out, "settings")
                page.keyboard.press("Escape")
                page.wait_for_timeout(600)
            except Exception:
                print("  ! could not open Settings; skipping that shot")

            page.locator("#tabTimeMachine").click()
            settle(page, 3500)
            shot(page, out, "time-machine")

            page.locator("#tabDiscovery").click()
            settle(page, 3000)
            shot(page, out, "profile")

            if args.keep_open:
                print("\nbrowser left open — press Enter when done")
                input()
            browser.close()
            print(f"\npage errors: {errors if errors else 'none'}")
            print(f"screenshots in {out}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
