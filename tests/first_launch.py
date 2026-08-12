"""Signing in is the way in: there is no browsing without an account."""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = 8893


def serve(temporary):
    env = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    process = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(PORT),
                                "--no-browser"], cwd=ROOT, env=env)
    deadline = time.monotonic() + 25
    while True:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/history/config", timeout=1).read()
            return process
        except Exception:
            if time.monotonic() > deadline:
                process.terminate(); raise
            time.sleep(.2)


with tempfile.TemporaryDirectory(prefix="civitai-first-launch-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    from discovery.history import HistoryArchive

    process = serve(temporary)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)

            # Signed out, the app shows one door and no gallery chrome behind it.
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"connected":false,"socialWrite":false}'))
            page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
            page.wait_for_selector("#welcome:not(.hidden)", timeout=15000)
            assert page.is_visible("#welcomeConnect"), "no way to sign in"
            # The old escape hatch is gone: browsing signed out is not a state any more.
            assert page.evaluate("!document.getElementById('welcomeSkip')"), "skip path still present"
            assert page.is_hidden("#gallery"), "gallery shown to a signed-out visitor"
            assert page.is_hidden("#daySegment"), "gallery controls shown before sign-in"
            assert page.is_hidden("#olderDay") and page.is_hidden("#rebuildDay")
            assert page.is_hidden("#startLoading"), "a signed-out visitor was offered a build"
            # The reason for signing in is stated, not just the demand.
            assert "react" in page.text_content("#welcomeBody").lower()

            # One sign-in, not a connect step plus a separate permission step.
            assert page.evaluate("!document.getElementById('enableSocial')"), "second permission step remains"
            assert "Sign in" in page.text_content("#connect")

            # This build bundles an application, so nothing about OAuth setup is surfaced.
            page.wait_for_timeout(900)
            assert not page.is_disabled("#welcomeConnect"), "bundled application did not enable sign-in"
            assert page.is_hidden("#ownAppToggle"), "onboarding offered OAuth configuration"
            assert page.is_hidden("#ownAppPanel") and page.is_hidden("#ownAppInput")
            page.screenshot(path=str(ROOT / "reports" / "discovery-dashboard" / "first-launch.png"))
            assert not errors, errors
            page.close()
            browser.close()
    finally:
        process.terminate(); process.wait(timeout=15)

    # Having archives is no longer a way around signing in: the gallery is ordered by the
    # account's own reactions, so there is nothing to show without one.
    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    history = HistoryArchive(Path(temporary) / "history")
    pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='8'%3E%3C/svg%3E"
    history._upsert_normalized([{"id": 7001, "postId": 7001, "username": "ReturningArtist",
        "createdAt": f"{day}T13:00:00Z", "url": pixel, "width": 8, "height": 8,
        "type": "image", "nsfwLevel": "None", "stats": {"reactionCount": 1}}], forced_date=day)
    with history.connect() as db:
        db.execute("INSERT INTO days(day,complete,updated_at) VALUES(?,1,?)",
                   (day, datetime.now().isoformat()))
    history.build_artist_index(day)

    process = serve(temporary)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"connected":false,"socialWrite":false}'))
            page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
            page.wait_for_selector("#welcome:not(.hidden)", timeout=25000)
            assert page.is_hidden("#gallery"), "archives were shown without signing in"
            browser.close()
    finally:
        process.terminate(); process.wait(timeout=15)

# The bundled application is one constant. Blanking it must flip the app into requiring
# each user to register their own, with no other change.
import discovery.oauth as oauth_module

original = oauth_module.BUILTIN_CLIENT_ID
original_path = oauth_module.CLIENT_PATH
assert original, "this build is expected to bundle an application"
switch_dir = tempfile.mkdtemp(prefix="civitai-byo-")
try:
    oauth_module.BUILTIN_CLIENT_ID = ""
    oauth_module.CLIENT_PATH = Path(switch_dir) / "oauth_client.json"
    os.environ.pop("CIVITAI_OAUTH_CLIENT_ID", None)
    info = oauth_module.client_info()
    assert info["hasBuiltin"] is False and info["configured"] is False, info
    assert oauth_module.client_id() == ""
    try:
        oauth_module.login(timeout=1)
        raise AssertionError("login proceeded with no application configured")
    except oauth_module.OAuthSetupError as error:
        assert "application is set up" in str(error), error

    # A user-supplied id still works in that mode.
    oauth_module.CLIENT_PATH.write_text(json.dumps({"clientId": "abc-123-def"}), encoding="utf-8")
    assert oauth_module.client_id() == "abc-123-def"
    assert oauth_module.client_info()["configured"] is True
finally:
    oauth_module.BUILTIN_CLIENT_ID = original
    oauth_module.CLIENT_PATH = original_path
    __import__("shutil").rmtree(switch_dir, ignore_errors=True)

print({"signInRequired": True, "noSkipPath": True, "noSecondPermissionStep": True,
       "chromeHiddenUntilSignedIn": True, "archivesDoNotBypassSignIn": True,
       "bundledByDefault": True, "byoSwitchFlips": True, "noOAuthSetupInOnboarding": True})
