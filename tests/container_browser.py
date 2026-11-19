"""Real Chromium and local HTTP; synthetic OAuth token exchange, no provider access."""
import test_safety
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright

with tempfile.TemporaryDirectory(prefix='cad3-container-browser-') as temporary:
    root = Path(temporary)
    os.environ.update(CAD_CONTAINER='1', CIVITAI_HISTORY_DATA_DIR=str(root/'data'),
                      CAD_CREDENTIAL_KEY_FILE=str(root/'key'))
    (root/'key').write_bytes(Fernet.generate_key())
    import server
    from discovery import oauth, container_runtime
    server.SETTINGS.update(auto_capture_value=False)
    listener=server.ThreadingHTTPServer(('127.0.0.1',8765),server.Handler)
    worker=threading.Thread(target=listener.serve_forever,daemon=True);worker.start()
    result=[]
    def authorize():
        try: result.append(oauth._authorize_tokens(oauth.READ_SCOPE, timeout=30))
        except Exception as error: result.append(error)
    server.OAUTH_JOB.update(state='loading',error=None)
    with patch.object(oauth,'_post',return_value={'access_token':'synthetic-browser-token','expires_in':3600}) as exchange:
        authorization=threading.Thread(target=authorize);authorization.start()
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True)
                try:
                    page=browser.new_page(viewport={'width':1440,'height':1000})
                    page.goto('http://localhost:8765',wait_until='domcontentloaded')
                    link=page.locator('#containerAuthorization')
                    link.wait_for(state='visible')
                    assert link.inner_text()=='Continue to Civitai sign-in'
                    url=link.get_attribute('href');params=parse_qs(urlsplit(url).query)
                    assert params['redirect_uri']==['http://localhost:8765/oauth/callback']
                    assert params['code_challenge_method']==['S256']
                    assert 'code_verifier' not in params
                    assert 'synthetic-browser-token' not in page.content()
                    page.screenshot(path=str(test_safety.REPORTS/'container-sign-in.png'),full_page=True)
                    callback=browser.new_page()
                    response=callback.goto('http://localhost:8765/oauth/callback?state='+params['state'][0]+'&code=synthetic-code')
                    assert response.status==200
                    authorization.join(timeout=5)
                    assert not authorization.is_alive()
                    assert isinstance(result[0],dict),result
                    assert result[0]['access_token']=='synthetic-browser-token'
                    assert exchange.call_args.args[1]['code']=='synthetic-code'
                    assert 'code_verifier' in exchange.call_args.args[1]
                    assert callback.goto('http://localhost:8765/oauth/callback?state='+params['state'][0]+'&code=synthetic-code').status==400
                    server.OAUTH_JOB.update(state='complete')
                    page.evaluate('window.refreshAuth()')
                    assert link.is_hidden()
                finally: browser.close()
        finally:
            listener.shutdown();listener.server_close();worker.join(timeout=5)
            authorization.join(timeout=35)
print('PASS: real Chromium sign-in link, PKCE parameters, single-port callback, replay rejection, hidden tokens; synthetic exchange')
