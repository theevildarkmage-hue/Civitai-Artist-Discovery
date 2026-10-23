"""Exercise a built executable's replacement-helper mode in a disposable install."""

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
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
executable_name = "CivitaiDiscoveryBeta.exe"
if not (package / executable_name).is_file():
    raise SystemExit(f"built executable not found in {package}")
for notice in ['LICENSE.txt', 'THIRD_PARTY_NOTICES.md', 'licenses/Python-LICENSE.txt',
               'licenses/PyInstaller-COPYING.txt', 'licenses/Pillow-LICENSE.txt',
               'licenses/pystray-COPYING.txt', 'licenses/pystray-COPYING.LGPL.txt']:
    if not (package / notice).is_file():
        raise SystemExit(f"packaged license missing: {notice}")

with tempfile.TemporaryDirectory(prefix="civitai-packaged-update-") as temporary:
    base = Path(temporary)
    install = base / "CivitaiDiscoveryBeta"
    shutil.copytree(package, install)
    data = install / "data"
    stage = data / "update" / "staged" / "test-version" / "CivitaiDiscoveryBeta"
    shutil.copytree(package, stage)
    personal = data / "personal.sqlite3"
    personal.write_bytes(b"portable-user-data")
    preferences=data/'settings.json'
    preferences.write_text(json.dumps({'autoCapture':False,'checkForUpdates':False,'browsingLevels':[1,2],'userPreferenceMarker':'synthetic-preserved'}))
    original_preferences=preferences.read_bytes()
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
    assert preferences.read_bytes()==original_preferences
    assert unrelated.read_text(encoding="utf-8") == "keep me"
    assert (install / executable_name).is_file()
    assert (install / "_internal").is_dir()
    assert not (data / "data").exists()

    locked_rollback=False
    if os.name=='nt':
        # Real Windows sharing violation against the owned disposable install.
        # Permit reads/writes but deny deletion/rename while the helper runs.
        failure_stage=data/'update'/'staged'/'locked-version'/'CivitaiDiscoveryBeta'
        shutil.copytree(package,failure_stage)
        failure_config=data/'update'/'locked-apply.json'
        settings=json.loads(config.read_text());settings.update(version='locked-version',stagedRoot=str(failure_stage),backupRoot=str(data/'update'/'backup'/'before-locked'))
        failure_config.write_text(json.dumps(settings))
        before=hashlib.sha256((install/executable_name).read_bytes()).hexdigest()
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        kernel.CreateFileW.restype=wintypes.HANDLE
        kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
        handle=kernel.CreateFileW(str(install/executable_name),0x80000000,3,None,3,0x80,None)
        assert handle!=ctypes.c_void_p(-1).value,'Could not establish owned executable lock'
        try:
            failed=subprocess.run([str(failure_stage/executable_name),'--apply-update',str(failure_config)],cwd=failure_stage,timeout=90)
            assert failed.returncode!=0,'Locked replacement unexpectedly succeeded'
        finally:kernel.CloseHandle(handle)
        failure_receipt=json.loads(result.read_text())
        assert failure_receipt['state']=='failed' and failure_receipt['rolledBack'] is True and not failure_receipt.get('recoveryRequired')
        assert hashlib.sha256((install/executable_name).read_bytes()).hexdigest()==before
        assert personal.read_bytes()==b'portable-user-data' and unrelated.read_text()=='keep me'
        assert preferences.read_bytes()==original_preferences
        locked_rollback=True

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
            import test_safety
            # This exact process was launched above against a newly copied
            # disposable install. Register it before the fixture installs its
            # fail-closed urllib guard; never register an existing listener.
            assert application.poll() is None
            test_safety.ENDPOINTS[port]=application
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
           "realWindowsLockedRollback":locked_rollback,
           "offlineBrowserViews": browser_views})
