"""Restart, expiry and outage contracts; credentials and upstream responses are fake."""
import test_safety
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from discovery.cache import MetadataCache,ArtworkCache,ThumbnailCache,thumbnail_source
from discovery.community import Artwork,ContentProfile,CommunityClient,CommunityError
from discovery import oauth


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='cad3-persistence-')
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)

    def test_persistent_metadata_freshness_and_failed_background_refresh(self):
        now=[100.0];path=self.root/'cache.sqlite3'
        cache=MetadataCache(path,clock=lambda:now[0]);cache.put('image',{'id':1})
        restarted=MetadataCache(path,clock=lambda:now[0])
        self.assertFalse(restarted.get('image')['stale'])
        now[0]+=86401;self.assertTrue(restarted.get('image')['stale'])
        started=threading.Event();release=threading.Event()
        def unavailable():started.set();release.wait(2);raise CommunityError('UNAVAILABLE',503)
        before=time.monotonic();restarted.refresh('image',unavailable,background=True)
        self.assertLess(time.monotonic()-before,.2);started.wait(1)
        self.assertFalse(restarted.refresh('image',unavailable,background=True))
        self.assertEqual(restarted.get('image')['value'],{'id':1})
        release.set()
        for _ in range(100):
            if not restarted.get('image')['refreshing']:break
            time.sleep(.01)
        self.assertEqual(restarted.get('image')['error'],'UNAVAILABLE')
        self.assertEqual(restarted.get('image')['value'],{'id':1})

    def test_browse_avoids_download_but_submission_still_verifies(self):
        art=Artwork(1,99,'Artist',1,'https://image.civitai.com/x/y/width=512/image.jpg')
        class Verifier:
            calls=0
            def artwork(self,image):self.calls+=1;return art
        verifier=Verifier();store=MetadataCache(self.root/'metadata.sqlite3')
        cache=ArtworkCache(store,verifier,'local')
        cache.browse(1);cache.browse(1);self.assertEqual(verifier.calls,1)
        class Backend:
            def request(self,*args):return {'imageId':1,'quota':{'used':0,'remaining':3}}
        client=CommunityClient(Backend(),verifier,lambda:'fake',lambda token:ContentProfile())
        client.artwork_cache=cache
        client.request('verify',{'artwork':1});self.assertEqual(verifier.calls,2)
        denied=ContentProfile(images=frozenset((1,)))
        with self.assertRaises(CommunityError):client.browse_artwork(1,denied)
        from dataclasses import replace
        self.assertFalse(ContentProfile(tags=frozenset(('hidden',))).allows(replace(art,tags_known=False)))

    def test_thumbnail_restart_and_resizing(self):
        url='https://image.civitai.com/x/y/original=true/image.jpeg';calls=[]
        def fetch(source):calls.append(source);return b'fake-jpeg','image/jpeg'
        first=ThumbnailCache(self.root/'thumbs',fetch=fetch)
        self.assertEqual(first.get(url)[0],b'fake-jpeg')
        second=ThumbnailCache(self.root/'thumbs',fetch=lambda _:self.fail('redownloaded'))
        self.assertEqual(second.get(url)[0],b'fake-jpeg')
        self.assertIn('/width=512/',calls[0])
        for bad in ('https://evil.test/x','https://image.civitai.com/x/no-transform.jpg','http://image.civitai.com/x'):
            with self.assertRaises(ValueError):thumbnail_source(bad)

    def test_legacy_cdn_redirect_rejects_large_original_before_body(self):
        import io,struct,zlib,urllib.error
        from email.message import Message
        from unittest.mock import Mock
        def chunk(kind,value):return struct.pack('!I',len(value))+kind+value+struct.pack('!I',zlib.crc32(kind+value))
        header=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',4096,4096,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0'))
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=header;response.headers=Message();response.headers['Content-Type']='image/png'
        headers=Message();headers['Location']='https://blobs-b2.civitai.com/file/blobs-managed-public/small.png'
        redirect=urllib.error.HTTPError('https://image.civitai.com/x',301,'Moved',headers,io.BytesIO())
        opener=Mock();opener.open.side_effect=[redirect,response]
        with patch('urllib.request.build_opener',return_value=opener):
            with self.assertRaisesRegex(ValueError,'not thumbnail-sized'):ThumbnailCache._fetch('https://image.civitai.com/x')
        response.read.assert_called_once_with(65536)

    def test_profile_resume_uses_persisted_cursor_and_preserves_prior_pages(self):
        from discovery import taste
        root=self.root/'discovery';store=taste.TasteStore(root)
        def image(identity):return {'id':identity,'user':{'id':99,'username':'Artist'},'nsfwLevel':1,
            'reactions':[{'userId':42,'reaction':'Like'}],'tags':[{'id':11,'name':'landscape'}]}
        store._store_page([image(1)],7,42)
        with store.connect() as db:
            store._set_state(db,'account_id',42)
            store._set_state(db,'sync_checkpoint',json.dumps({'syncId':7,'cursor':'page-two','pages':1,'total':1,'savedAt':time.time()}))
        restarted=taste.TasteStore(root);pages=[]
        def page(client,**kwargs):pages.append(kwargs['cursor']);return {'items':[image(2)],'nextCursor':None}
        with ExitStack() as stack:
            stack.enter_context(patch.object(taste,'auth_status',return_value={'id':42}))
            stack.enter_context(patch.object(restarted,'_page',side_effect=page))
            for name in ('_pause','_sync_following','_sync_baseline','_sync_followers','_fetch_reacted_tags'):
                stack.enter_context(patch.object(restarted,name))
            restarted._run_sync()
        self.assertEqual(pages,['page-two'])
        with restarted.connect() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT image_id FROM reacted_images ORDER BY image_id')],[1,2])
            self.assertIsNone(restarted._state(db,'sync_checkpoint'))
            self.assertTrue(restarted._state(db,'last_sync_at'))

    def tokens(self):return {'access_token':'synthetic-access','refresh_token':'synthetic-refresh',
        'client_id':'fake-client','expires_at':int(time.time())-10,'scope':oauth.SOCIAL_WRITE,
        'identity':{'id':42,'username':'MockAccount'}}

    def test_refresh_preserves_grants_and_rotating_refresh_token(self):
        tokens=self.tokens()
        with patch.object(oauth,'_load',lambda:dict(tokens)),patch.object(oauth,'_save',lambda value:tokens.update(value)),\
             patch.object(oauth,'client_id',lambda:'fake-client'),patch.object(oauth,'_post',return_value={'access_token':'renewed','expires_in':3600}) as post:
            self.assertEqual(oauth.get_access_token(),'renewed')
            self.assertEqual(tokens['refresh_token'],'synthetic-refresh')
            self.assertTrue(oauth.status()['socialWrite'])
            self.assertEqual(oauth.get_access_token(),'renewed');self.assertEqual(post.call_count,1)

    def test_revocation_persists_but_transient_failure_preserves_session(self):
        tokens=self.tokens()
        with patch.object(oauth,'_load',lambda:dict(tokens)),patch.object(oauth,'_save',lambda value:tokens.update(value)),\
             patch.object(oauth,'client_id',lambda:'fake-client'):
            with patch.object(oauth,'_post',side_effect=oauth.OAuthTemporarilyUnavailable('offline')):
                with self.assertRaises(oauth.OAuthTemporarilyUnavailable):oauth.get_access_token()
            self.assertNotIn('reauth_required',tokens)
            with patch.object(oauth,'_post',side_effect=oauth.OAuthReauthenticationRequired('revoked')):
                with self.assertRaises(oauth.OAuthReauthenticationRequired):oauth.get_access_token()
            self.assertTrue(tokens['reauth_required'])
            state=oauth.session_status();self.assertTrue(state['cachedSession'])
            self.assertTrue(state['needsReauthentication']);self.assertFalse(state['socialWrite'])

    def test_cached_identity_returns_before_slow_refresh_finishes(self):
        tokens=self.tokens();started=threading.Event();release=threading.Event()
        def offline(*args):started.set();release.wait(2);raise oauth.OAuthTemporarilyUnavailable('offline')
        with patch.object(oauth,'_load',lambda:dict(tokens)),patch.object(oauth,'_save',lambda value:tokens.update(value)),\
             patch.object(oauth,'client_id',lambda:'fake-client'),patch.object(oauth,'_post',side_effect=offline):
            with oauth._SESSION_LOCK:oauth._SESSION_STATE.update(running=False,attemptedAt=0,error=None)
            try:
                before=time.monotonic();state=oauth.session_status()
                self.assertLess(time.monotonic()-before,.2)
                self.assertTrue(started.wait(1));self.assertEqual(state['username'],'MockAccount')
                self.assertTrue(state['cachedSession']);self.assertFalse(state['socialWrite'])
            finally:
                release.set()
                for _ in range(100):
                    with oauth._SESSION_LOCK:running=oauth._SESSION_STATE['running']
                    if not running:break
                    time.sleep(.01)

    @unittest.skipUnless(sys.platform=='win32','Windows DPAPI contract')
    def test_dpapi_survives_new_python_process_without_plaintext(self):
        path=self.root/'oauth_tokens.dpapi';tokens=self.tokens()
        with patch.object(oauth,'TOKEN_PATH',path):oauth._save(tokens)
        self.assertNotIn(b'synthetic-access',path.read_bytes())
        program="from pathlib import Path;from discovery import oauth;oauth.TOKEN_PATH=Path(__import__('sys').argv[1]);assert oauth._load()['identity']['id']==42;print('protected identity restored')"
        result=subprocess.run([sys.executable,'-c',program,str(path)],capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'protected identity restored')


if __name__=='__main__':unittest.main()
