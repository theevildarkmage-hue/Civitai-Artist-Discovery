"""Authentication boundary and metadata restoration regression. All credentials fake."""
import test_safety
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
from playwright.sync_api import sync_playwright
ROOT=test_safety.ROOT
sys.path.insert(0,str(ROOT))
from discovery import oauth
from discovery.community import build_community,ContentProfile


class AuthenticationBoundary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='cad3-auth-boundary-',ignore_cleanup_errors=True)
        cls.root=Path(cls.temp.name)
        cls.root.joinpath('settings.json').write_text(json.dumps({'autoCapture':False,'checkForUpdates':False}))
        cls.root.joinpath('oauth_client.json').write_text(json.dumps({'clientId':'synthetic-boundary-client'}))
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));cls.port=sock.getsockname()[1]
        cls.base=f'http://127.0.0.1:{cls.port}'
        env={**os.environ,'CIVITAI_HISTORY_DATA_DIR':str(cls.root),'CAD_DEVELOPMENT':'1','CAD_COMMUNITY_MODE':'fixture','CAD_COMMUNITY_TESTING':'0'}
        env.pop('CAD_COMMUNITY_SAMPLE',None)
        bootstrap="import socket,runpy,sys\ndef refuse(*args,**kwargs):\n raise PermissionError('No outbound network in auth tests')\nsocket.create_connection=refuse\nsys.argv=sys.argv[1:]\nrunpy.run_path(sys.argv[0],run_name='__main__')"
        cls.process=subprocess.Popen([sys.executable,'-c',bootstrap,str(ROOT/'server.py'),'--port',str(cls.port),'--no-browser'],
            cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:cls.request('/api/history/config');return
            except OSError:time.sleep(.1)
        raise AssertionError('Readiness')

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate();cls.process.wait(timeout=10);cls.temp.cleanup()

    @classmethod
    def request(cls,path,body=None,cookie=None,extra_headers=None):
        headers={'Content-Type':'application/json'}
        headers.update(extra_headers or {})
        if cookie:headers['Cookie']=cookie
        req=urllib.request.Request(cls.base+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req,timeout=3) as response:return response.status,json.load(response),dict(response.headers)
        except urllib.error.HTTPError as error:
            with error:return error.code,json.load(error),{}

    def setUp(self):
        token=self.root/'oauth_tokens.dpapi'
        if token.exists():token.unlink()  # Newly created synthetic credential only.

    def authorize(self,*,revoked=False):
        from discovery.cache import MetadataCache
        MetadataCache(self.root/'cache'/'community-metadata.sqlite3').put('controls:42',{
            'hiddenUsers':[],'hiddenTags':[],'hiddenImages':[],'blockedUsers':[],'blockedByUsers':[]})
        with patch.object(oauth,'TOKEN_PATH',self.root/'oauth_tokens.dpapi'):
            oauth._save({'client_id':'synthetic-boundary-client','access_token':'synthetic-access',
                'refresh_token':'synthetic-refresh','expires_at':int(time.time())+3600,
                'identity':{'id':42,'username':'MockAccount'},'scope':0,'reauth_required':revoked})

    def test_anonymous_routes_and_forged_member_are_rejected(self):
        for path in ('status','gallery','config','detail?id=1001','thumbnail/1001'):
            status,body,_=self.request('/api/community/'+path,cookie='cad_fixture_member=2')
            self.assertEqual(status,401,path);self.assertEqual(body['code'],'AUTH_REQUIRED')
        for path,body in [('recommend',{'artwork':1001}),('development/member',{'member':2})]:
            self.assertEqual(self.request('/api/community/'+path,body)[0],401)

    def test_valid_protected_session_and_member_switch(self):
        self.authorize()
        self.assertEqual(self.request('/api/community/status')[0],200)
        status,_,headers=self.request('/api/community/development/member',{'member':2})
        self.assertEqual(status,200);self.assertIn('HttpOnly',headers['Set-Cookie'])
        self.assertEqual(self.request('/api/community/status',cookie='cad_fixture_member=2')[1]['member']['id'],2)
        self.assertEqual(self.request('/api/auth-status')[1]['username'],'MockAccount')

    def test_identity_spoofing_does_not_change_authenticated_or_simulated_identity(self):
        self.authorize()
        for identity in ('member','memberId','creatorId','username','token'):
            self.assertEqual(self.request('/api/community/recommend',{'artwork':123,identity:99})[0],400)
        result=self.request('/api/community/status?memberId=99&username=Spoofed',extra_headers={
            'X-Member-Id':'99','X-Civitai-User-Id':'99','Authorization':'Bearer fixture:9'})
        self.assertEqual(result[1]['member']['id'],1)
        self.assertEqual(self.request('/api/auth-status')[1]['id'],42)
        self.assertEqual(self.request('/api/community/development/control',{'action':'reset'})[0],403)

    def test_missing_shared_controls_fail_closed_without_relaxing_policy(self):
        self.authorize()
        from discovery.cache import MetadataCache
        with MetadataCache(self.root/'cache'/'community-metadata.sqlite3').connect() as db:
            db.execute("DELETE FROM metadata_cache WHERE key='controls:42'")
        self.assertEqual(self.request('/api/community/gallery')[0],503)

    def test_revoked_session_cannot_read_cached_community(self):
        self.authorize();self.assertEqual(self.request('/api/community/status')[0],200)
        self.authorize(revoked=True)
        self.assertEqual(self.request('/api/community/status')[0],401)
        self.assertEqual(self.request('/api/community/gallery')[0],401)

    def test_expired_grant_without_refresh_is_rejected(self):
        with patch.object(oauth,'TOKEN_PATH',self.root/'oauth_tokens.dpapi'):
            oauth._save({'client_id':'synthetic-boundary-client','access_token':'expired-synthetic',
                'expires_at':0,'identity':{'id':42,'username':'MockAccount'},'scope':0})
        self.assertEqual(self.request('/api/community/gallery')[0],401)
        self.assertTrue((self.root/'oauth_tokens.dpapi').exists())

    def test_direct_url_has_no_bypass(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True);page=browser.new_page()
            page.goto(self.base+'/#community',wait_until='networkidle')
            self.assertTrue(page.locator('#welcome').is_visible())
            self.assertEqual(page.locator('#communityDemo').count(),0)
            page.evaluate("window.showView('community')")
            self.assertFalse(page.locator('#community').is_visible())
            self.assertEqual(page.locator('#communityGrid .creator-card').count(),0)
            browser.close()

    def test_personal_thumbnail_cache_rechecks_global_controls(self):
        import io
        from PIL import Image
        from discovery.history import HistoryArchive
        from discovery.cache import ThumbnailCache
        self.authorize();image_id=321
        archive=HistoryArchive(self.root/'history')
        url='https://image.civitai.com/x/y/width=512/cache-test.png'
        archive._upsert_normalized([{'id':image_id,'username':'MetadataTest','url':url,
            'createdAt':'2026-10-13T12:00:00Z','type':'image','browsingLevel':1,'width':32,'height':32}],forced_date='2026-10-13')
        encoded=io.BytesIO();Image.new('RGB',(32,32),(20,30,40)).save(encoded,format='PNG')
        ThumbnailCache(self.root/'cache'/'artwork-thumbnails',fetch=lambda _:(encoded.getvalue(),'image/png')).get(url)
        with urllib.request.urlopen(self.base+f'/api/history/thumbnail/{image_id}',timeout=3) as response:
            self.assertEqual(response.status,200);self.assertEqual(response.read(),encoded.getvalue())
        self.request('/api/settings',{'browsingLevels':[2]})
        try:self.assertEqual(self.request(f'/api/history/thumbnail/{image_id}')[0],403)
        finally:self.request('/api/settings',{'browsingLevels':[1,2]})

    def test_fixture_archive_is_preserved_but_not_browsable(self):
        from discovery.history import HistoryArchive
        archive=HistoryArchive(self.root/'history');day='2026-10-12'
        archive._upsert_normalized([{'id':9000,'username':'PersonalFixture1','type':'image','browsingLevel':1,
            'url':self.base+'/community-fixture.svg?id=9000','createdAt':day+'T12:00:00Z'}],forced_date=day)
        with archive.connect() as db:db.execute('INSERT INTO days(day,complete) VALUES(?,1)',(day,))
        status,value,_=self.request('/api/history/status?date='+day)
        self.assertEqual(status,200);self.assertTrue(value['fixtureArchiveExcluded']);self.assertFalse(value['complete'])
        self.assertEqual(self.request('/api/history/artists?date='+day)[0],409)
        self.assertNotIn(day,[entry['date'] for entry in self.request('/api/history/calendar')[1]['days']])
        with archive.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM images WHERE id=9000').fetchone()[0],1)


class MetadataIsolation(unittest.TestCase):
    def test_cdn_original_is_bounded_downscaled_and_cached(self):
        import io
        from unittest.mock import Mock
        from PIL import Image
        from discovery.cache import ThumbnailCache
        encoded=io.BytesIO();Image.new('RGB',(1600,900),(30,60,90)).save(encoded,format='PNG')
        incoming=io.BytesIO(encoded.getvalue())
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.side_effect=incoming.read;response.headers.get_content_type.return_value='image/png'
        opener=Mock();opener.open.return_value=response
        with patch('urllib.request.build_opener',return_value=opener):
            body,kind=ThumbnailCache._fetch('https://image.civitai.com/x/y/width=512/a.png',allow_downscale=True)
        self.assertEqual(kind,'image/webp')
        with Image.open(io.BytesIO(body)) as decoded:self.assertEqual(decoded.size,(512,288))
        self.assertLess(len(body),len(encoded.getvalue()))

    def test_packaged_production_cannot_enable_fixture_identity(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'CAD_DEVELOPMENT':'1','CAD_COMMUNITY_MODE':'fixture'}),patch.object(sys,'frozen',True,create=True):
            with self.assertRaises(RuntimeError):build_community(Path(root),lambda _:ContentProfile(),lambda:'fake')

    def test_real_restore_is_additive_and_requires_matching_profile(self):
        spec=importlib.util.spec_from_file_location('restore_metadata',ROOT/'scripts/restore-real-metadata.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        from discovery.history import HistoryArchive
        from discovery.taste import TasteStore
        with tempfile.TemporaryDirectory(prefix='data-test-restore-',ignore_cleanup_errors=True) as folder:
            module.ROOT=Path(folder)  # The disposable fixture is this test's workspace.
            base=Path(folder);source=base/'source';target=base/'target';day='2026-10-13'
            original=HistoryArchive(source/'history','X',(1,2));taste=TasteStore(source/'discovery');other=TasteStore(target/'discovery')
            for store in (taste,other):
                with store.connect() as db:store._set_state(db,'account_id',42)
            image={'id':123,'username':'RealMetadataTest','createdAt':day+'T13:00:00Z','url':'https://image.civitai.com/x/y/width=512/a.jpg','width':512,'height':512,'type':'image','browsingLevel':1}
            original._upsert_normalized([image],forced_date=day)
            with taste.connect() as db:
                db.execute('INSERT INTO archive_image_seen VALUES(?,?)',(123,'2026-10-13T12:00:00Z'))
                db.execute('INSERT INTO archive_image_tags VALUES(?,?)',(123,'original complete snapshot'))
            with original.connect() as db:db.execute('INSERT INTO days(day,complete) VALUES(?,1)',(day,))
            original.build_artist_index(day)
            result=module.restore(source,target,day)
            self.assertEqual(result['realImages'],1);self.assertTrue(result['sourceFilesUnchanged'])
            self.assertEqual(result['tagSnapshots'],1)
            with taste.connect() as db:db.execute('INSERT INTO archive_image_tags VALUES(?,?)',(123,'later source change'))
            repeated=module.restore(source,target,day)
            self.assertTrue(repeated['alreadyImported']);self.assertEqual(repeated['imagesAdded'],0)
            self.assertEqual(repeated['tagSnapshots'],0)
            with other.connect() as db:self.assertEqual(other._state(db,'account_id'),'42')
            with other.connect() as db:
                self.assertEqual([r[0] for r in db.execute('SELECT tag_name FROM archive_image_tags')],['original complete snapshot'])
                other._set_state(db,'account_id',43)
            with self.assertRaisesRegex(ValueError,'account must match'):module.restore(source,target,day)
            with other.connect() as db:
                other._set_state(db,'account_id',42)
                db.execute('DELETE FROM archive_image_seen WHERE image_id=123')
                db.execute('INSERT INTO archive_image_tags VALUES(?,?)',(123,'conflicting partial tag'))
            with self.assertRaisesRegex(ValueError,'Conflicting incomplete'):module.restore(source,target,day)
            with other.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM archive_image_seen').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT count(*) FROM archive_image_tags').fetchone()[0],2)
            with sqlite3.connect(target/'history/history.sqlite3') as db:self.assertEqual(db.execute('SELECT username FROM images').fetchone()[0],'RealMetadataTest')


if __name__=='__main__':unittest.main()
