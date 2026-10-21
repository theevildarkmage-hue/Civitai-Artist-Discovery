"""Real server restart + browser, fake DPAPI identity and ten persistent members."""
import test_safety
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
from playwright.sync_api import sync_playwright
sys.path.insert(0,str(test_safety.ROOT))
from discovery import oauth
from discovery.community import FixtureVerifier,LocalCommunityBackend
from discovery.taste import TasteStore
from test_safety import IsolatedEndpoint


with tempfile.TemporaryDirectory(prefix='data-test-persistent-browser-',ignore_cleanup_errors=True) as temporary:
    root=Path(temporary);instance=secrets.token_hex(24)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env={**os.environ,'CIVITAI_HISTORY_DATA_DIR':temporary,'CAD_DEVELOPMENT':'1',
        'CAD_COMMUNITY_MODE':'fixture','CAD_COMMUNITY_TESTING':'1','CAD_TEST_INSTANCE':instance}
    env.pop('CAD_COMMUNITY_SAMPLE',None)
    root.joinpath('settings.json').write_text(json.dumps({'browsingLevels':[1,2,4,8,16],
        'autoCapture':False,'checkForUpdates':False}))
    root.joinpath('oauth_client.json').write_text(json.dumps({'clientId':'synthetic-browser-client'}))
    with patch.object(oauth,'TOKEN_PATH',root/'oauth_tokens.dpapi'):
        oauth._save({'access_token':'synthetic-access','refresh_token':'synthetic-refresh',
            'client_id':'synthetic-browser-client','expires_at':int(time.time())+3600,
            'identity':{'id':42,'username':'MockAccount'},'scope':0})
    store=TasteStore(root/'discovery');now=datetime.now(timezone.utc).isoformat()
    with store.connect() as db:
        store._set_state(db,'account_id',42);store._set_state(db,'last_sync_at',now)
        db.execute('INSERT INTO reacted_images(image_id,creator_id,creator_username,first_observed_at,last_observed_at) VALUES(1,99,?,?,?)',('MockArtist',now,now))
        db.execute("INSERT INTO reacted_reactions VALUES(1,'Like')")
        db.execute('INSERT INTO followed_creators VALUES(99)')
    verifier=FixtureVerifier(development=True)
    backend=LocalCommunityBackend(root/'community'/'fixtures.sqlite3',verifier,
        clock=lambda:datetime(2026,10,14,12,tzinfo=timezone.utc))
    # Two works per artist: 30 distinct works, all ten histories and quotas.
    images=[1001+artist*5+offset for artist in range(12) for offset in range(3)][:30]
    for index,image in enumerate(images):backend.request('recommend',f'fixture:{index//3+1}',{'artwork':image})
    endpoint=IsolatedEndpoint(f'http://127.0.0.1:{port}',instance,root)
    process=None
    def start():
        global process
        process=subprocess.Popen([sys.executable,str(test_safety.ROOT/'server.py'),'--port',str(port),'--no-browser'],
            cwd=test_safety.ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:endpoint.verify();return
            except OSError:time.sleep(.1)
        raise AssertionError('Server readiness')
    def stop():
        if process and process.poll() is None:process.terminate();process.wait(timeout=10)
    try:
        start()
        baseline=endpoint.request('/api/community/development/control',{'action':'snapshot'})
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            context=browser.new_context(viewport={'width':1440,'height':1000},extra_http_headers={'X-CAD-Test-Instance':instance})
            page=context.new_page();errors=[];requests=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:requests.append(request.url))
            page.goto(endpoint.base,wait_until='networkidle')
            page.locator('#discoveryBody:not(.hidden)').wait_for()
            assert '1' in page.locator('#summaryRow').inner_text()
            assert 'MockAccount' in page.locator('#accountStatus').inner_text()
            page.locator('#tabCommunity').click()
            page.locator('#communityGrid .creator-card').first.wait_for()
            assert not page.locator('#communityForm').is_visible()
            page.locator('#communityRecommend').click();assert page.locator('#communityLink').is_visible()
            page.locator('#communityCancel').click()
            page.locator('#tabDiscovery').click()
            page.locator('#communityDiagnostics summary').click()
            for member in range(1,11):
                page.locator('#tabDiscovery').click()
                with page.expect_response(lambda response:'/api/community/development/member' in response.url):
                    page.locator('#communityMember').select_option(str(member))
                page.locator('#tabCommunity').click()
                page.locator('#communityQuota').filter(has_text='0 left').wait_for()
                page.locator('#communityGrid .creator-card').first.wait_for()
            assert not any('/api/discovery/sync' in url or '/api/history/build' in url for url in requests)
            endpoint.request('/api/community/development/control',{'action':'offline','enabled':True})
            page.locator('#communityRefresh').click()
            page.locator('#communityGrid .creator-card').first.wait_for()
            page.locator('#tabDiscovery').click()
            page.locator('#communityMode').filter(has_text='Cached results').wait_for()
            page.locator('#tabDiscovery').click();assert page.locator('#discoveryBody').is_visible()
            stop();start()
            page.reload(wait_until='networkidle')
            page.locator('#discoveryBody:not(.hidden)').wait_for()
            page.locator('#tabCommunity').click();page.locator('#communityQuota').filter(has_text='0 left').wait_for()
            assert page.locator('#communityMember').input_value()=='10'
            after=endpoint.request('/api/community/development/control',{'action':'snapshot'})
            assert baseline==after
            for member in range(1,11):assert endpoint.request('/api/community/status',member=member)['quota']['used']==3
            assert endpoint.request('/api/auth-status')['username']=='MockAccount'
            assert endpoint.request('/api/discovery/summary')['reactedImages']==1
            reports=test_safety.REPORTS;reports.mkdir(exist_ok=True)
            page.screenshot(path=str(reports/'community-persistent-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(reports/'community-persistent-mobile.png'),full_page=True)
            assert not errors,errors
            assert not any(url.startswith('https:') for url in requests)
            browser.close()
        print('PASS: DPAPI server restart, immediate cached profile, no startup sync/day download, compact form, outage browsing, ten member histories/quotas, persistent member cookie, desktop/mobile')
    finally:stop()
