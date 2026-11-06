"""Source release selection and isolation: real child imports/HTTP, no user credentials."""
import test_safety
import json,os,socket,subprocess,sys,tempfile,time,unittest,urllib.request,urllib.error
from pathlib import Path

ROOT=test_safety.ROOT

def probe(code,stable=True,environment=None):
    env={**os.environ,**(environment or {})}
    result=subprocess.run([sys.executable,'-c',code,*(['--stable'] if stable else [])],cwd=ROOT,env=env,text=True,capture_output=True,timeout=30)
    if result.returncode:raise AssertionError(result.stderr)
    return json.loads(result.stdout)

class StableSource(unittest.TestCase):
    def test_source_notices_explain_selection_and_exclude_packages(self):
        from discovery.source_notice import render
        self.assertEqual(render(frozen=True,environment='production'),'')
        self.assertEqual(render(frozen=True,environment='development'),'')
        development=render(frozen=False,environment='development')
        self.assertIn('Public Community needs the stable launch',development)
        self.assertIn('server.py --stable',development)
        self.assertIn('Your existing data will be kept',development)
        stable=render(frozen=False,environment='production')
        self.assertIn('Public Community is selected',stable)
        self.assertNotIn('needs the stable launch',stable)

    def test_production_clients_share_fixed_endpoint_despite_environment(self):
        result=probe('''import json
from discovery import build_config as b,cloud_identity,registry,community_beta,community,community_security,dev_diagnostics
from discovery.paths import data_root
client=community.build_community(data_root(),lambda *a:None,lambda:None)
print(json.dumps({'config':b.CONFIG,'endpoints':[cloud_identity.ENDPOINT,registry.ENDPOINT,community_beta.BETA_ENDPOINT,client.backend.url], 'mode':client.mode,'policy':community_beta.production(),'fixture':community_security.fixture_enabled(),'identity':community_security.test_identity(data_root()),'diagnostics':dev_diagnostics.enabled()}))''',environment={'CAD_DEVELOPMENT':'1','CAD_COMMUNITY_MODE':'local','CAD_COMMUNITY_URL':'https://attacker.invalid','SUPABASE_URL':'https://attacker.invalid'})
        self.assertEqual(result['config']['environment'],'production');self.assertEqual(result['config']['version'],'3.0.0')
        self.assertEqual(set(result['endpoints']),{'https://masrxykkbqngtbeeqpbq.supabase.co/functions/v1/community-v1'})
        self.assertEqual(result['mode'],'supabase');self.assertTrue(result['policy']);self.assertFalse(result['fixture']);self.assertIsNone(result['identity'])
        self.assertFalse(result['diagnostics'])

    def test_production_profile_ignores_development_selection(self):
        with tempfile.TemporaryDirectory(prefix='cad3-source-path-') as folder:
            root=Path(folder);(root/'.data-directory-3.0.json').write_text('{"directory":"data-test-community"}')
            code='''import os,json
from pathlib import Path
from discovery import paths
os.environ.pop('CIVITAI_HISTORY_DATA_DIR',None)
paths.application_root=lambda:Path(os.environ['CAD_TEST_SOURCE_PATH'])
print(json.dumps({'data':str(paths.data_root())}))'''
            self.assertEqual(Path(probe(code,environment={'CAD_TEST_SOURCE_PATH':folder})['data']),root/'data-production-3.0')
            self.assertEqual(Path(probe(code,False,{'CAD_TEST_SOURCE_PATH':folder})['data']),root/'data-test-community')

    def test_default_source_remains_development(self):
        result=probe('from discovery.build_config import CONFIG;import json;print(json.dumps(CONFIG))',False)
        self.assertEqual(result['environment'],'development');self.assertEqual(result['version'],'3.0.0-beta.14')

    def test_stable_rejects_fixture_and_development_controls(self):
        result=probe('''import json
from discovery import community,community_security
from discovery.paths import data_root
checks=[]
try:community.build_community(data_root(),lambda *a:None,lambda:None)
except RuntimeError:checks.append('fixture rejected')
try:community_security.control(None,{})
except PermissionError:checks.append('controls rejected')
print(json.dumps(checks))''',environment={'CAD_DEVELOPMENT':'1','CAD_COMMUNITY_MODE':'fixture','CAD_COMMUNITY_TESTING':'1'})
        self.assertEqual(result,['fixture rejected','controls rejected'])

    def test_frozen_configuration_wins_over_source_flag(self):
        result=probe('''import sys,json,tempfile
from pathlib import Path
from discovery import build_config as b
with tempfile.TemporaryDirectory() as folder:
 p=Path(folder);(p/'build-configuration.json').write_text(json.dumps(b.DEFAULT))
 b.__file__=str(p/'build_config.py');sys.frozen=True
 print(json.dumps(b.load()))''')
        self.assertEqual(result['environment'],'development')

    def test_signed_out_stable_server_real_http(self):
        with tempfile.TemporaryDirectory(prefix='cad3-stable-http-') as folder:
            with socket.socket() as reserve:reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
            env={**os.environ,'CIVITAI_HISTORY_DATA_DIR':folder}
            for key in ('CAD_COMMUNITY_MODE','CAD_COMMUNITY_URL','CAD_COMMUNITY_TESTING','CAD_DEVELOPMENT'):env.pop(key,None)
            log=Path(folder)/'server-output.txt'
            with log.open('w') as stream:
                process=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--stable','--port',str(port),'--no-browser'],cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT)
            base=f'http://127.0.0.1:{port}'
            try:
                deadline=time.monotonic()+30
                while True:
                    try:
                        with urllib.request.urlopen(base+'/api/history/config',timeout=1) as response:config=json.load(response)
                        break
                    except (OSError,PermissionError):
                        if time.monotonic()>deadline or process.poll() is not None:self.fail(log.read_text())
                        time.sleep(.1)
                self.assertEqual(config['version'],'3.0.0');self.assertEqual(config['environment'],'production');self.assertEqual(config['channel'],'stable')
                with urllib.request.urlopen(base+'/api/community/beta',timeout=5) as response:self.assertTrue(json.load(response)['serverPolicy'])
                with urllib.request.urlopen(base+'/api/auth-status',timeout=5) as response:self.assertFalse(json.load(response)['connected'])
                with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(base+'/api/community/gallery',timeout=5)
                self.assertEqual(caught.exception.code,401)
                caught.exception.close()
                self.assertFalse((Path(folder)/'community/local.sqlite3').exists())
                from playwright.sync_api import sync_playwright
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(headless=True)
                    try:
                        page=browser.new_page()
                        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                        page.goto(base,wait_until='domcontentloaded')
                        page.locator('#appVersion').filter(has_text='v3.0.0').wait_for()
                        page.locator('#welcomeConnect').wait_for(state='visible')
                        self.assertEqual(page.locator('#sourceLaunchTitle').inner_text(),'Public Community is selected')
                        page.locator('#sourceLaunchNotice').screenshot(path=str(test_safety.REPORTS/'source-stable-message.png'))
                        page.screenshot(path=str(test_safety.REPORTS/'source-stable-screen.png'),full_page=True)
                        for width in (390,1440):
                            page.set_viewport_size({'width':width,'height':1000})
                            self.assertTrue(page.locator('#sourceLaunchNotice').is_visible())
                            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width)
                        self.assertEqual(page.locator('#appVersion').inner_text(),'v3.0.0')
                        self.assertNotIn('Beta',page.locator('h1').inner_text())
                        self.assertEqual(errors,[])
                    finally:browser.close()
            finally:
                process.terminate();process.wait(timeout=10)

    def test_development_message_real_browser(self):
        with tempfile.TemporaryDirectory(prefix='cad3-source-message-') as folder:
            with socket.socket() as reserve:reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
            env={**os.environ,'CIVITAI_HISTORY_DATA_DIR':folder}
            for key in ('CAD_COMMUNITY_MODE','CAD_COMMUNITY_URL','CAD_COMMUNITY_TESTING','CAD_DEVELOPMENT'):env.pop(key,None)
            with (Path(folder)/'server-output.txt').open('w') as log:
                process=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port',str(port),'--no-browser'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            try:
                base=f'http://127.0.0.1:{port}';deadline=time.monotonic()+30
                while True:
                    try:
                        with urllib.request.urlopen(base+'/api/history/config',timeout=1) as response:config=json.load(response)
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic()>deadline:self.fail('Isolated source server did not start')
                        time.sleep(.1)
                self.assertEqual(config['environment'],'development')
                from playwright.sync_api import sync_playwright
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(headless=True)
                    try:
                        page=browser.new_page(viewport={'width':1440,'height':1000})
                        page.goto(base,wait_until='domcontentloaded')
                        page.locator('#welcomeConnect').wait_for(state='visible')
                        self.assertEqual(page.locator('#sourceLaunchTitle').inner_text(),'Public Community needs the stable launch')
                        self.assertIn('server.py --stable',page.locator('#sourceLaunchNotice').inner_text())
                        page.locator('#sourceLaunchNotice').screenshot(path=str(test_safety.REPORTS/'source-development-message.png'))
                        page.screenshot(path=str(test_safety.REPORTS/'source-development-screen.png'),full_page=True)
                        page.set_viewport_size({'width':390,'height':844})
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
                    finally:browser.close()
            finally:process.terminate();process.wait(timeout=10)

if __name__=='__main__':unittest.main()
