"""Exercise a built executable's replacement-helper mode in a disposable install."""

import argparse
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("package", type=Path)
parser.add_argument("--browser", action="store_true",
                    help="Render all three pages with offline synthetic data (requires Playwright)")
args = parser.parse_args()

package = args.package.resolve()
executable_name = "CivitaiArtistDiscovery.exe"
if not (package / executable_name).is_file():
    raise SystemExit(f"built executable not found in {package}")

with tempfile.TemporaryDirectory(prefix="civitai-packaged-update-") as temporary:
    base = Path(temporary)
    install = base / "CivitaiArtistDiscovery"
    shutil.copytree(package, install)
    data = install / "data"
    stage = data / "update" / "staged" / "test-version" / "CivitaiArtistDiscovery"
    shutil.copytree(package, stage)
    personal = data / "personal.sqlite3"
    personal.write_bytes(b"portable-user-data")
    unrelated = install / "my-notes.txt"
    unrelated.write_text("keep me", encoding="utf-8")
    result = data / "update" / "result.json"
    config = data / "update" / "apply.json"
    config.write_text(json.dumps({
        "version": "test-version", "parentPid": 0,
        "installRoot": str(install), "stagedRoot": str(stage),
        "dataRoot": str(data), "executableName": executable_name,
        "backupRoot": str(data / "update" / "backup" / "old-version"),
        "resultPath": str(result), "relaunch": False,
    }), encoding="utf-8")

    process = subprocess.Popen([str(stage / executable_name), "--apply-update", str(config)],
                               cwd=stage)
    deadline = time.monotonic() + 90
    while not result.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(.1)
    process.wait(timeout=max(1, deadline - time.monotonic()))
    if process.returncode:
        error_log = stage / "data" / "error.log"
        raise AssertionError(error_log.read_text(encoding="utf-8") if error_log.exists()
                             else f"helper exited with {process.returncode}")
    receipt = json.loads(result.read_text(encoding="utf-8"))
    assert receipt["state"] == "installed" and receipt["rolledBack"] is False
    assert personal.read_bytes() == b"portable-user-data"
    assert unrelated.read_text(encoding="utf-8") == "keep me"
    assert (install / executable_name).is_file()
    assert (install / "_internal").is_dir()
    assert not (data / "data").exists()

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    application = subprocess.Popen([str(install / executable_name), "--no-browser",
                                    "--port", str(port)], cwd=install)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/update/status",
                                            timeout=2) as response:
                    update_status = json.load(response)
                break
            except Exception:
                if time.monotonic() >= deadline or application.poll() is not None:
                    raise
                time.sleep(.1)
        assert update_status["supported"] is True
        # Nested native modules/styles must survive packaging and replacement, not
        # merely work from the source checkout. Check every bundled shared asset.
        static = install / "_internal" / "static"
        nested_assets = sorted((static / "ui").glob("*.js")) + sorted(
            (static / "styles").glob("*.css"))
        assert nested_assets, "shared UI assets missing from package"
        source_static = Path(__file__).resolve().parents[1] / "static"
        expected_assets = {asset.relative_to(source_static).as_posix()
                           for folder, pattern in [("ui", "*.js"), ("styles", "*.css")]
                           for asset in (source_static / folder).glob(pattern)}
        assert {asset.relative_to(static).as_posix() for asset in nested_assets} == expected_assets, (
            "packaged shared assets differ from this checkout; rebuild the package")
        for asset in nested_assets:
            route = asset.relative_to(static).as_posix()
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/{route}",
                                        timeout=3) as response:
                assert response.read() == asset.read_bytes(), route
                content_type = response.headers.get_content_type()
                assert content_type in ({"text/javascript", "application/javascript"}
                                        if asset.suffix == ".js" else {"text/css"}), (
                                            route, content_type)
        browser_views = 0
        if args.browser:
            # Only assets come from the executable: account/API and artwork routes
            # use the same offline fixture as source visual tests, never user data.
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
            from v2_visual_baseline import install_fixture
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                for width, height in [(390, 844), (1366, 768), (1920, 1080)]:
                    page = browser.new_page(viewport={"width": width, "height": height})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    base_url = f"http://127.0.0.1:{port}"
                    install_fixture(page, base_url, cached_tags=True)
                    page.goto(base_url, wait_until="networkidle")
                    page.wait_for_selector(".creator-card .image-button img[src]")
                    for tab, section in [("tabGallery", "gallery"),
                                         ("tabTimeMachine", "timeMachine"),
                                         ("tabDiscovery", "discovery")]:
                        page.locator(f"#{tab}").click()
                        page.locator(f"#{section}").wait_for()
                        if section != "discovery":
                            page.locator(f"#{section} .creator-card img[src]").first.wait_for()
                        assert not page.evaluate(
                            "document.documentElement.scrollWidth > innerWidth + 1"), (section, width)
                        browser_views += 1
                    assert not errors, errors
                    page.close()
                browser.close()
        close = urllib.request.Request(f"http://127.0.0.1:{port}/api/app/close",
                                       data=b"{}", method="POST",
                                       headers={"Content-Type": "application/json"})
        urllib.request.urlopen(close, timeout=3).close()
        application.wait(timeout=15)
    finally:
        if application.poll() is None:
            application.terminate()
            application.wait(timeout=10)
    print({"packagedHelperRan": True, "portableDataPreserved": True,
           "unrelatedFilePreserved": True, "packageReplaced": True,
           "unexpectedRelaunch": False, "packagedServerStarted": True,
           "packagedUpdaterEnabled": True, "sharedAssetsVerified": len(nested_assets),
           "offlineBrowserViews": browser_views})
