"""Real loopback server + Playwright, all data synthetic and isolated."""

import test_safety  # Fail closed before any test network request.
import json
import re
import os
from pathlib import Path
import socket
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]

def run(mode):
    with tempfile.TemporaryDirectory(prefix='data-test-community-browser-',ignore_cleanup_errors=True) as temporary:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        nonce=secrets.token_hex(24)
        env={**os.environ,'CAD_TEST_INSTANCE':nonce,'CAD_COMMUNITY_TESTING':'1' if mode=='fixture' else '0','CIVITAI_HISTORY_DATA_DIR':temporary,'CAD_DEVELOPMENT':'1','CAD_COMMUNITY_MODE':mode}
        # Disable optional background work in this clean test universe.
        Path(temporary,'settings.json').write_text(json.dumps({'autoCapture':False,'checkForUpdates':False}))
        (test_safety.REPORTS).mkdir(exist_ok=True)
        log_path=test_safety.REPORTS/f'community-browser-{mode}.log'
        server_log=log_path.open('w',encoding='utf-8')
        process=subprocess.Popen([sys.executable,'server.py','--port',str(port),'--no-browser'],
                                 cwd=ROOT,env=env,stdout=server_log,stderr=subprocess.STDOUT)
        base=f'http://127.0.0.1:{port}'
        def request(path,body=None):
            req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),
                headers={'Content-Type':'application/json','X-CAD-Test-Instance':nonce})
            with urllib.request.urlopen(req,timeout=5) as response:return json.loads(response.read())
        try:
            for _ in range(80):
                if process.poll() is not None:
                    raise AssertionError('Server exited before readiness: '+log_path.read_text(encoding='utf-8',errors='replace'))
                try:request('/api/history/config');break
                except OSError as error:last_error=error;time.sleep(.1)
            else:raise AssertionError(f'Server readiness failed: {last_error}')
            assert request('/api/history/config')['version']==re.search(r'^APP_VERSION = "([^"]+)"',(ROOT/'server.py').read_text(encoding='utf-8'),re.M).group(1)
            assert request('/api/auth-status')['connected'] is False
            if mode=='offline':
                try:request('/api/community/status');raise AssertionError('offline accepted')
                except urllib.error.HTTPError as error:
                    assert error.code==401 and json.loads(error.read())['code']=='AUTH_REQUIRED'
                assert 'browsingLevels' in request('/api/settings')
                assert 'hasData' in request('/api/discovery/summary')
                print('PASS: offline community, personal configuration/profile endpoints available')
                return
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1280,'height':900},extra_http_headers={'X-CAD-Test-Instance':nonce})
                errors=[]; page.on('pageerror',lambda error:errors.append(str(error)))
                page.route('**/api/auth-status',lambda route:route.fulfill(content_type='application/json',body=json.dumps({'connected':True,'id':42,'username':'MockAccount'})))
                page.route('**/api/discovery/summary',lambda route:route.fulfill(content_type='application/json',body=json.dumps({'hasData':False,'lastSyncAt':'2099-01-01T00:00:00Z'})))
                page.goto(base,wait_until='networkidle')
                page.locator('#tabCommunity').click()
                page.locator('#communityQuota').filter(has_text='3 left').wait_for()
                assert page.locator('#communityMode').text_content().startswith('DEVELOPMENT SIMULATOR')
                def open_modal(value=''):
                    page.locator('#communityRecommend').click()
                    page.locator('#communityLink').fill(value)
                    assert page.locator('dialog[open]').count()==1
                def preview(value):
                    open_modal(value);page.locator('#communityLoad').click()
                def submit(value):
                    preview(value);page.locator('#communitySubmit').click()
                # Keyboard opening, native focus and Escape restoration.
                page.locator('#communityRecommend').focus();page.keyboard.press('Enter')
                page.wait_for_function("()=>document.querySelector('#communityLink')===document.activeElement")
                page.keyboard.press('Escape');assert page.locator('#communityRecommend').evaluate('(e)=>e===document.activeElement')
                held=[];requests=[]
                page.on('request',lambda req:requests.append(req) if req.url.endswith('/api/community/recommend') else None)
                page.route('**/api/community/recommend',lambda route:held.append(route),times=1)
                submit('https://civitai.com/images/1001')
                page.wait_for_function("()=>document.querySelector('#communitySubmit').disabled")
                assert page.locator('#communityLink').evaluate('(e)=>e.readOnly')
                assert 'Submitting' in page.locator('#communitySubmit').inner_text()
                page.locator('#communityForm').evaluate('(form)=>{form.requestSubmit();form.requestSubmit();}')
                assert len(held)==1
                held[0].continue_()
                page.locator('#communityGrid .creator-card').wait_for()
                assert len(requests)==1 and request('/api/community/status')['quota']['used']==1
                assert '2 left' in page.locator('#communityQuota').inner_text()
                assert not page.locator('dialog[open]').count()
                # Card entry rechecks admission, then prepopulates the same modal without a submission.
                page.locator('#communityGrid .more-menu').click()
                page.get_by_role('menuitem',name='Recommend to Community').click()
                page.locator('#communityDialogTitle').wait_for(state='visible')
                assert page.locator('#communityLink').input_value()=='1001' and len(requests)==1
                page.locator('#communityLoad').click()
                page.locator('#communityDialogMessage').filter(has_text='already been recommended').wait_for()
                assert page.locator('#communityLink').input_value()=='1001'
                page.locator('#communityCancel').click()
                for value,expected in [('1099',"can't recommend your own"),('999999','Artwork unavailable'),
                                       ('https://evil.test/images/1001','Enter a public Civitai artwork URL')]:
                    preview(value);page.locator('#communityDialogMessage').filter(has_text=expected).wait_for()
                    assert page.locator('#communityLink').input_value()==value
                    assert request('/api/community/status')['quota']['used']==1
                    page.locator('#communityCancel').click()
                preview('1003')
                page.locator('#communityDialogMessage').filter(has_text='outside your current content controls').wait_for()
                page.locator('#communityCancel').click()
                # Server commits but loses its reply: same key retries to success once.
                request('/api/community/development/control',{'action':'loseConfirmationResponse'})
                submit('1006')
                page.locator('#communityDialogMessage').filter(has_text='temporarily unavailable').wait_for()
                assert request('/api/community/status')['quota']['used']==2
                assert page.locator('#communityLink').input_value()=='1006'
                first=json.loads(requests[-1].post_data)['idempotencyKey']
                page.locator('#communitySubmit').click()
                try:page.locator('#communityDialogTitle').wait_for(state='hidden')
                except Exception:
                    raise AssertionError({'message':page.locator('#communityDialogMessage').inner_text(),
                        'errors':errors,'requests':[req.post_data for req in requests]})
                assert json.loads(requests[-1].post_data)['idempotencyKey']==first
                assert request('/api/community/status')['quota']['used']==2
                submit('1011');page.locator('#communityDialogTitle').wait_for(state='hidden')
                preview('1016');page.locator('#communityDialogMessage').filter(has_text='all three recommendations').wait_for()
                assert request('/api/community/status')['quota']['used']==3
                reports=test_safety.REPORTS;reports.mkdir(exist_ok=True)
                page.screenshot(path=str(reports/'submission-synthetic-desktop.png'))
                page.set_viewport_size({'width':390,'height':844})
                page.screenshot(path=str(reports/'submission-synthetic-mobile.png'))
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth + 1')
                assert page.locator('dialog[open]').evaluate('(e)=>e.getBoundingClientRect().right<=innerWidth && e.getBoundingClientRect().height<600')
                page.locator('#communityCancel').click()
                # The shared setting updates through Community's browsing controls.
                page.locator('#filterToggle').click()
                page.locator('#contentMenu [data-level="1"]').click()
                page.locator('#communityMessage').filter(has_text='No recommendations match').wait_for()
                assert page.locator('#communityGrid img').count()==0
                assert 'thumbnailUrl' not in json.dumps(request('/api/community/gallery'))
                page.set_viewport_size({'width':390,'height':844})
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth + 1'), page.evaluate('Array.from(document.querySelectorAll("body *")).filter(e=>e.getBoundingClientRect().right>innerWidth+1).map(e=>[e.tagName,e.id,e.className,Math.round(e.getBoundingClientRect().width)]).slice(0,20)')
                assert not errors,errors
                browser.close()
            print('PASS: single modal, loading/double click/replay/keyboard, card action, duplicates/self/fake/link/quota errors, privacy, mobile')
        finally:
            process.terminate();process.wait(timeout=10)
            server_log.close()

if __name__=='__main__':run('fixture');run('offline')
