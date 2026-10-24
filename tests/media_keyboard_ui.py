"""Media preferences/chips/reset and card-focused keys work through the real API."""
import test_safety  # Refuse live application endpoints and isolate all test data.

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(
    prefix="cad3-media-keyboard-", ignore_cleanup_errors=True
) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    from discovery.history import HistoryArchive

    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='8'%3E%3Crect width='8' height='8'/%3E%3C/svg%3E"
    archive = HistoryArchive(Path(temporary) / "history")
    rows = [
        dict(
            id=i,
            username=name,
            type=kind,
            baseModel="Foo",
            browsingLevel=1,
            createdAt=day + "T13:00:00Z",
            url=(f'https://image.civitai.com/keyboard-fixture/{i}/original=true/{i}.mp4'
                 if kind == 'video' else pixel),
            width=8,
            height=8,
            stats={"reactionCount": score},
        )
        for i, name, kind, score in [
            (1, "Mixed", "image", 100),
            (2, "Mixed", "video", 20),
            (3, "Mixed", "video", 10),
            (4, "ImageOnly", "image", 5),
        ]
    ]
    archive._upsert_normalized(rows, forced_date=day)
    with archive.connect() as db:
        db.execute("INSERT INTO days(day,complete) VALUES(?,1)", (day,))
    archive.build_artist_index(day)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py"), "--port", str(port), "--no-browser"],
        cwd=ROOT,
        env={**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen(base + "/api/history/config", timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(
                viewport={"width": 1366, "height": 900},
                reduced_motion="reduce",
                bypass_csp=True,
            )
            page.add_init_script("""HTMLMediaElement.prototype.play=function(){return Promise.resolve()};
                HTMLMediaElement.prototype.pause=function(){};""")
            # Valid media avoids a native decode error racing the two Space presses.
            # Playback policy itself is exercised with real players in the video suite.
            def media_fixture(route):
                if route.request.resource_type == 'media':
                    route.fulfill(content_type='video/mp4',
                        headers={'Accept-Ranges': 'bytes'},
                        body=(ROOT / 'tests/fixtures/recovery.mp4').read_bytes())
                else:
                    route.fulfill(content_type='image/svg+xml', body=
                        '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8"/></svg>')
            page.route('https://image.civitai.com/keyboard-fixture/**', media_fixture)
            page.route(
                "**/api/auth-status",
                lambda route: route.fulfill(
                    content_type="application/json",
                    body='{"connected":true,"username":"fixture","socialWrite":false}',
                ),
            )
            page.route(
                "**/api/discovery/summary",
                lambda route: route.fulfill(
                    content_type="application/json", body='{"hasData":true}'
                ),
            )
            page.add_init_script("if(!sessionStorage.getItem('civitai-active-view'))sessionStorage.setItem('civitai-active-view','gallery')")
            page.goto(base, wait_until="domcontentloaded")
            page.locator(".creator-card").first.wait_for()
            page.locator("#filterToggle").click()
            page.locator("#mediaType").select_option("video")
            page.wait_for_function(
                "document.querySelectorAll('.creator-card').length===1 && document.querySelector('.image-position').textContent.startsWith('2 of 2 images')"
            )
            assert page.locator('[aria-label="Remove media filter"]').count() == 1
            page.get_by_role("button", name="Close filters", exact=True).click()
            card = page.locator(".creator-card")
            page.wait_for_function(
                "document.querySelector('.creator-card').getAttribute('aria-busy')==='false'"
            )
            card.focus()
            card.press("ArrowRight")
            page.wait_for_function(
                "document.querySelector('.image-position').textContent.startsWith('1 of 2 images')"
            )
            card.press("ArrowLeft")
            page.wait_for_function(
                "document.querySelector('.image-position').textContent.startsWith('2 of 2 images')"
            )
            page.wait_for_function(
                "document.querySelector('.creator-card').getAttribute('aria-busy')==='false'"
            )
            card.press("Space")
            assert (
                page.locator(".video-toggle").get_attribute("aria-label")
                == "Pause video"
            )
            card.press("Space")
            assert (
                page.locator(".video-toggle").get_attribute("aria-label")
                == "Play video"
            )
            page.evaluate(
                "document.querySelector('.creator-card').appendChild(document.createElement('input')).id='cardInput'"
            )
            page.locator("#cardInput").focus()
            page.locator("#cardInput").press("ArrowRight")
            assert (
                page.locator(".image-position").inner_text().startswith("2 of 2 images")
            )
            for target in ["video", "[contenteditable]"]:
                page.evaluate("""() => {
                    if (!document.querySelector('video')) {
                        const native=document.createElement('video');native.controls=true;
                        document.querySelector('.creator-card').append(native);
                    }
                    if (!document.querySelector('[contenteditable]')) {
                        const edit=document.createElement('div');edit.setAttribute('contenteditable','');
                        edit.textContent='Editable';document.querySelector('.creator-card').append(edit);
                    }
                }""")
                page.locator(target).first.focus()
                page.locator(target).first.press("ArrowRight")
                assert (
                    page.locator(".image-position")
                    .inner_text()
                    .startswith("2 of 2 images")
                )
            page.locator("#filterToggle").focus()
            page.keyboard.press("ArrowRight")
            assert (
                page.locator(".image-position").inner_text().startswith("2 of 2 images")
            )
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function(
                "document.querySelectorAll('.creator-card').length===1 && document.querySelector('.image-position').textContent.startsWith('2 of 2 images')"
            )
            assert (
                json.load(urllib.request.urlopen(base + "/api/settings"))["mediaType"]
                == "video"
            )
            page.locator("#filterToggle").click()
            assert page.locator("#mediaType").input_value() == "video"
            page.locator('#modelMenu input[value="Foo"]').check()
            page.wait_for_function("galleryFilterState().models.includes('Foo')")
            page.locator(".filter-reset").click()
            page.wait_for_function(
                "galleryFilterState().media==='all' && galleryFilterState().models.length===0"
            )
            assert (
                json.load(urllib.request.urlopen(base + "/api/settings"))["mediaType"]
                == "all"
            )
            # Deadline and abort handling do not replay requests, including writes.
            result = page.evaluate("""async () => {
                const {api}=await import('/ui/api.js'); const original=window.fetch; let calls=0;
                window.fetch=(url, options)=>String(url).startsWith('/fake-') ?
                    (calls++,new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))))) : original(url,options);
                let timeout=false, aborted=false;
                try {
                    try {await api('/fake-write',{method:'POST',body:'{}',timeoutMs:20})} catch(error){timeout=error.message.includes('timed out')}
                    const controller=new AbortController(); const task=api('/fake-read',{signal:controller.signal});controller.abort();
                    try {await task} catch(error){aborted=error.name==='AbortError'}
                } finally {window.fetch=original}
                return {calls,timeout,aborted};
            }""")
            assert result == {"calls": 2, "timeout": True, "aborted": True}, result
            # Replacing Daily gallery must release its players without disposing Time Machine's retained cards.
            cleanup = page.evaluate("""() => {
                let dailyReleased=0, retainedReleased=0;
                const daily=document.createElement('article');daily.className='creator-card';
                daily.releaseVideo=()=>dailyReleased++;
                document.querySelector('#gallery').append(daily);
                const retained=document.createElement('article');retained.className='creator-card';
                retained.releaseVideo=()=>retainedReleased++;
                document.querySelector('#timeMachineGrid').append(retained);
                clearGallery();
                return {dailyReleased,retainedReleased,retained:retained.isConnected};
            }""")
            assert cleanup == {"dailyReleased": 1, "retainedReleased": 0, "retained": True}, cleanup
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=15)

print(
    {
        "mediaPersistsAndResets": True,
        "filterChip": True,
        "combinedModelMedia": True,
        "focusedCarouselKeys": True,
        "focusedVideoSpace": True,
        "inputsPreserved": True,
        "browserTimeoutAndAbort": True,
        "browserDoesNotReplayWrites": True,
    }
)
