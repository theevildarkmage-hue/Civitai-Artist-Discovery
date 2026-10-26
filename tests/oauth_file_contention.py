"""Synthetic credentials only: Windows sharing failures after provider rotation."""
import test_safety
import ctypes,json,os,sys,tempfile,threading,time,unittest,subprocess
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from discovery import oauth

@unittest.skipUnless(os.name=='nt','Real Windows DPAPI and sharing semantics')
class CredentialContention(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='cad3-oauth-race-');self.root=Path(self.temp.name)
        self.patches=[patch.object(oauth,'APP_DATA',self.root),patch.object(oauth,'TOKEN_PATH',self.root/'oauth_tokens.dpapi'),patch.object(oauth,'LEGACY_TOKEN_PATH',self.root/'oauth_tokens.dpapi'),patch.object(oauth,'client_id',return_value='synthetic-client')]
        for p in self.patches:p.start()
        self.tokens={'access_token':'synthetic-old-access','refresh_token':'synthetic-old-refresh','client_id':'synthetic-client','expires_at':int(time.time())-10,'identity':{'id':123,'username':'Synthetic'}}
        oauth._save(self.tokens)
    def tearDown(self):
        if hasattr(oauth,'_ROTATED_PENDING'):oauth._ROTATED_PENDING=None
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def test_external_reader_releases_after_rotation(self):
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p];api.CreateFileW.restype=ctypes.c_void_p
        api.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=api.CreateFileW(str(oauth.TOKEN_PATH),0x80000000,3,None,3,0x80,None)
        self.assertNotEqual(handle,ctypes.c_void_p(-1).value)
        released=threading.Event()
        def release():time.sleep(.12);api.CloseHandle(handle);released.set()
        thread=threading.Thread(target=release);thread.start()
        try:
            with patch.object(oauth,'_post',return_value={'access_token':'synthetic-new-access','refresh_token':'synthetic-new-refresh','expires_in':3600}) as post:
                self.assertEqual(oauth.get_access_token(),'synthetic-new-access')
                self.assertEqual(post.call_count,1)
            self.assertEqual(oauth._load()['refresh_token'],'synthetic-new-refresh')
        finally:thread.join();self.assertTrue(released.is_set())
    def test_concurrent_protected_readers_and_writes(self):
        errors=[];done=threading.Event()
        def reader():
            while not done.is_set():
                try:self.assertIn('access_token',oauth._load())
                except Exception as e:errors.append(type(e).__name__)
        threads=[threading.Thread(target=reader) for _ in range(4)]
        for t in threads:t.start()
        try:
            for i in range(30):oauth._save({**self.tokens,'access_token':'synthetic-generation-'+str(i)})
        finally:
            done.set()
            for t in threads:t.join(timeout=3)
        self.assertFalse(errors);self.assertFalse(any(t.is_alive() for t in threads))
    def test_transient_permission_retries_one_remote_redemption(self):
        replace=os.replace;calls=[]
        def flaky(*args):
            calls.append(1)
            if len(calls)<3:raise PermissionError('synthetic sharing denial')
            return replace(*args)
        with patch('discovery.atomic_file.os.replace',side_effect=flaky),patch.object(oauth,'_post',return_value={'access_token':'synthetic-new-access','refresh_token':'synthetic-new-refresh','expires_in':3600}) as post:
            self.assertEqual(oauth.get_access_token(),'synthetic-new-access')
            self.assertEqual(post.call_count,1)
        self.assertEqual(len(calls),3)
    def test_permanent_save_keeps_rotation_in_memory_without_redeeming_again(self):
        disk=oauth.TOKEN_PATH.read_bytes()
        with patch('discovery.atomic_file.os.replace',side_effect=PermissionError('synthetic permanent denial')),patch.object(oauth,'_post',return_value={'access_token':'synthetic-new-access','refresh_token':'synthetic-new-refresh','expires_in':3600}) as post:
            with self.assertRaises(oauth.OAuthTemporarilyUnavailable):oauth.get_access_token()
            self.assertEqual(oauth.TOKEN_PATH.read_bytes(),disk)
            self.assertEqual(oauth.get_access_token(),'synthetic-new-access')
            self.assertEqual(post.call_count,1)
            oauth._ROTATED_PENDING['tokens']['expires_at']=0
            with self.assertRaises(oauth.OAuthTemporarilyUnavailable):oauth.get_access_token()
            self.assertEqual(post.call_count,1)
        oauth._ROTATED_PENDING['retryAt']=0
        self.assertEqual(oauth._load()['refresh_token'],'synthetic-new-refresh')
        self.assertIsNone(oauth._ROTATED_PENDING)
        self.assertFalse(list(self.root.glob('*.tmp')))
    def test_disconnect_waits_for_rotation_and_does_not_resurrect(self):
        entered=threading.Event();release=threading.Event();errors=[]
        def post(*args):
            entered.set();self.assertTrue(release.wait(3))
            return {'access_token':'synthetic-new-access','refresh_token':'synthetic-new-refresh','expires_in':3600}
        def rotate():
            try:oauth.get_access_token()
            except Exception as error:errors.append(type(error).__name__)
        with patch.object(oauth,'_post',side_effect=post),patch.object(oauth,'_revoke',return_value=True):
            thread=threading.Thread(target=rotate);thread.start();self.assertTrue(entered.wait(3))
            closing=threading.Thread(target=oauth.disconnect);closing.start();release.set()
            thread.join(3);closing.join(3)
        self.assertFalse(errors);self.assertFalse(closing.is_alive());self.assertFalse(oauth.TOKEN_PATH.exists())
        with self.assertRaises(RuntimeError):oauth._load()
    def test_community_renewal_while_real_protected_reads_continue(self):
        now=int(time.time());self.tokens['expires_at']=now+3600
        self.tokens['discovery_session']={'session':'sdc_'+'a'*43,'expires_at':now-1,'member_id':123,
             'renewal':{'credential':'sdr_'+'b'*43,'expires_at':now+86400,'absolute_expires_at':now+864000}}
        oauth._save(self.tokens);done=threading.Event();errors=[]
        def reader():
            while not done.is_set():
                try:self.assertEqual(oauth._load()['identity']['id'],123)
                except Exception as error:errors.append(type(error).__name__)
        thread=threading.Thread(target=reader);thread.start()
        fresh={**self.tokens['discovery_session'],'session':'sdc_'+'c'*43,'expires_at':now+43200}
        try:
            with patch('discovery.cloud_identity.renew',return_value=fresh):self.assertEqual(oauth.get_community_session(),fresh['session'])
        finally:done.set();thread.join(3)
        self.assertFalse(errors);self.assertEqual(oauth._load()['refresh_token'],self.tokens['refresh_token'])
    def test_child_restart_and_separate_store_preservation(self):
        stable=self.root/'separate-stable-store.dpapi';stable.write_bytes(b'synthetic-stable-marker')
        oauth._save({**self.tokens,'expires_at':int(time.time())+3600})
        env={**os.environ,'CIVITAI_HISTORY_DATA_DIR':str(self.root)}
        result=subprocess.run([sys.executable,'-c',"from discovery import oauth; assert oauth._load()['refresh_token']=='synthetic-old-refresh'; print('protected restart passed')"],env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(stable.read_bytes(),b'synthetic-stable-marker')

if __name__=='__main__':unittest.main()
