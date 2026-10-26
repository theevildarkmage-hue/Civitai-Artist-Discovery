"""Synthetic provider/identity proofs; actual protected persistence and reconnect."""
import test_safety
import copy,json,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from discovery import oauth,cloud_identity
from discovery.community import CommunityError

class EnrollmentRecovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.patches=[patch.object(oauth,'APP_DATA',self.root),patch.object(oauth,'TOKEN_PATH',self.root/'oauth_tokens.dpapi'),patch.object(oauth,'LEGACY_TOKEN_PATH',self.root/'oauth_tokens.dpapi'),patch.object(oauth,'client_id',return_value=oauth.BUILTIN_CLIENT_ID)]
        for p in self.patches:p.start()
        self.primary={'access_token':'synthetic-primary-access','refresh_token':'synthetic-primary-refresh','client_id':oauth.BUILTIN_CLIENT_ID,'expires_at':int(time.time())+3600,'identity':{'id':17,'username':'Synthetic'},'scope':1}
        self.pending={'nonce':'n'*43,'challenge':'synthetic-envelope','expiresAt':int(time.time())+300}
        self.session={'session':'sdc_'+'s'*43,'expires_at':int(time.time())+43200,'member_id':17,'renewal':{'credential':'sdr_'+'r'*43,'expires_at':int(time.time())+86400,'absolute_expires_at':int(time.time())+864000}}
        from discovery.device_identity import generate
        self.session['renewal']['device']=generate()
        oauth._save(self.primary)
    def tearDown(self):
        oauth._ROTATED_PENDING=None
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def issued(self):return {**self.primary,'access_token':'synthetic-fresh-access','refresh_token':'synthetic-fresh-refresh','id_token':'synthetic.identity.proof'}
    def test_login_outage_is_visible_and_daily_survives(self):
        with patch.object(cloud_identity,'challenge',side_effect=OSError('synthetic-secret')),patch.object(oauth,'_authorize_tokens',return_value=self.issued()),patch.object(oauth,'_userinfo',return_value={'id':17,'username':'Synthetic'}):
            oauth.login()
        stored=oauth._load();self.assertNotIn('id_token',stored)
        self.assertEqual(oauth.get_access_token(),'synthetic-fresh-access')
        self.assertEqual(oauth.session_status()['communityAuthorization']['state'],'temporarily_unavailable')
        self.assertNotIn('synthetic-secret',json.dumps(stored))
    def test_targeted_reconnect_recovers_same_account(self):
        with patch.object(cloud_identity,'challenge',return_value=self.pending),patch.object(oauth,'_authorize_tokens',return_value=self.issued()) as authorize,patch.object(oauth,'_userinfo',return_value={'id':17,'username':'Synthetic'}),patch.object(cloud_identity,'exchange',return_value=self.session):
            oauth.reconnect_community()
        self.assertEqual(authorize.call_args.kwargs['nonce'],'n'*43)
        self.assertEqual(oauth.get_community_session(),self.session['session'])
        self.assertTrue(oauth.session_status()['communityEnrolled'])
        self.assertNotIn('id_token',oauth._load())
    def test_challenge_outage_does_not_launch_browser_or_replace_daily(self):
        with patch.object(cloud_identity,'challenge',side_effect=OSError('synthetic-secret')),patch.object(oauth,'_authorize_tokens') as authorize:
            with self.assertRaises(oauth.OAuthTemporarilyUnavailable):oauth.reconnect_community()
        authorize.assert_not_called()
        self.assertEqual(oauth.get_access_token(),self.primary['access_token'])
    def test_mismatched_identity_is_rejected_without_overwrite(self):
        before=oauth.TOKEN_PATH.read_bytes()
        with patch.object(cloud_identity,'challenge',return_value=self.pending),patch.object(oauth,'_authorize_tokens',return_value=self.issued()),patch.object(oauth,'_userinfo',return_value={'id':99}),patch.object(oauth,'_revoke',return_value=True) as revoke:
            with self.assertRaises(oauth.OAuthSetupError):oauth.reconnect_community()
        self.assertEqual(oauth.TOKEN_PATH.read_bytes(),before);self.assertEqual(revoke.call_count,1)
    def test_failed_exchange_keeps_verified_new_daily_grant(self):
        with patch.object(cloud_identity,'challenge',return_value=self.pending),patch.object(oauth,'_authorize_tokens',return_value=self.issued()),patch.object(oauth,'_userinfo',return_value={'id':17}),patch.object(cloud_identity,'exchange',side_effect=CommunityError('UNAVAILABLE',503)):
            with self.assertRaises(oauth.OAuthTemporarilyUnavailable):oauth.reconnect_community()
        self.assertEqual(oauth.get_access_token(),'synthetic-fresh-access')
        self.assertFalse(oauth._load().get('reauth_required',False))
    def test_proof_expiry_missing_assertion_and_custom_client_are_specific(self):
        with self.assertRaises(cloud_identity.EnrollmentError) as error:cloud_identity.exchange(None,self.pending,17)
        self.assertEqual(error.exception.reason,'missing_identity_proof')
        with self.assertRaises(cloud_identity.EnrollmentError) as error:cloud_identity.exchange('a.b.c',{**self.pending,'expiresAt':0},17)
        self.assertEqual(error.exception.reason,'clock_or_expired_challenge')
        with patch.object(oauth,'client_id',return_value='synthetic-custom'):
            oauth._save({**self.primary,'client_id':'synthetic-custom'})
            with patch.object(oauth,'_authorize_tokens') as authorize:
                with self.assertRaises(oauth.OAuthSetupError):oauth.reconnect_community()
            authorize.assert_not_called()
    def test_revoked_or_expired_device_cannot_be_ready(self):
        revoked={**self.primary,'discovery_session':{**self.session,'renewal_revoked':True}}
        self.assertEqual(oauth.community_authorization(revoked)['reason'],'device_revoked')
        expired=copy.deepcopy(self.session);expired['renewal']['absolute_expires_at']=0
        self.assertEqual(oauth.community_authorization({**self.primary,'discovery_session':expired})['reason'],'device_expired')
        oauth._save({**self.primary,'discovery_session':{**self.session,'expires_at':0}})
        with patch.object(cloud_identity,'renew',side_effect=CommunityError('AUTH_EXPIRED',401)):
            with self.assertRaises(oauth.OAuthReauthenticationRequired):oauth.get_community_session()
        self.assertTrue(oauth._load()['discovery_session']['renewal_revoked'])
        self.assertEqual(oauth.get_access_token(),self.primary['access_token'])
    def test_incomplete_state_survives_reload_without_identity_assertion(self):
        oauth._save({**self.primary,'discovery_enrollment':{'state':'incomplete','reason':'missing_identity_proof'}})
        self.assertEqual(oauth.community_authorization(oauth._load())['reason'],'missing_identity_proof')
        self.assertTrue(oauth.session_status()['connected'])
        self.assertNotIn('synthetic-primary',json.dumps(oauth.session_status()))
    def test_malformed_persisted_expiry_requires_reconnect_without_transport(self):
        for value in ('not-a-timestamp',None,True):
            malformed=copy.deepcopy(self.session);malformed['renewal']['expires_at']=value
            state=oauth.community_authorization({**self.primary,'discovery_session':malformed})
            self.assertEqual(state['action'],'reconnect')
            self.assertFalse(state['communityEnrolled'])
    def test_custom_legacy_profile_status_is_stable_and_daily_survives(self):
        with patch.object(oauth,'client_id',return_value='synthetic-custom'):
            oauth._save({**self.primary,'client_id':'synthetic-custom'})
            before=oauth.TOKEN_PATH.read_bytes()
            for _ in range(2):
                state=oauth.session_status()
                self.assertTrue(state['connected'])
                self.assertEqual(state['communityAuthorization'],{'state':'unsupported_client','reason':'unsupported_client','action':'none','communityEnrolled':False})
                self.assertEqual(oauth.get_access_token(),self.primary['access_token'])
                self.assertNotIn('synthetic-primary',json.dumps(state))
            self.assertEqual(oauth.TOKEN_PATH.read_bytes(),before)
    def test_actual_reconnect_http_rejects_custom_client_without_launch_or_write(self):
        import server,threading,urllib.request,urllib.error
        from http.server import ThreadingHTTPServer
        with patch.object(oauth,'client_id',return_value='synthetic-custom'),patch.object(server,'start_oauth_login') as launch:
            oauth._save({**self.primary,'client_id':'synthetic-custom'});before=oauth.TOKEN_PATH.read_bytes()
            http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            base=f'http://127.0.0.1:{http.server_port}'
            with patch.object(server,'RUNNING_PORT',http.server_port):
                threading.Thread(target=http.serve_forever,daemon=True).start()
                try:
                    request=urllib.request.Request(base+'/api/community/reconnect',data=b'{}',headers={'Content-Type':'application/json','Origin':base,'Sec-Fetch-Site':'same-origin'})
                    with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request,timeout=5)
                    self.assertEqual(error.exception.code,409)
                    with error.exception as response:body=json.load(response)
                    self.assertEqual(body,{'code':'UNSUPPORTED_CLIENT','error':oauth.COMMUNITY_UNSUPPORTED_CLIENT})
                    launch.assert_not_called()
                    self.assertEqual(oauth.TOKEN_PATH.read_bytes(),before)
                finally:http.shutdown();http.server_close()
    def test_custom_profile_survives_child_process_restart(self):
        import os,subprocess
        with patch.object(oauth,'client_id',return_value='synthetic-custom'):
            oauth._save({**self.primary,'client_id':'synthetic-custom'})
        code="from discovery import oauth; state=oauth.session_status(); assert state['connected']; assert state['communityAuthorization']['reason']=='unsupported_client'; assert state['communityAuthorization']['action']=='none'; print('custom-client restart passed')"
        result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True,timeout=10,
            env={**os.environ,'CIVITAI_HISTORY_DATA_DIR':str(self.root),'CIVITAI_OAUTH_CLIENT_ID':'synthetic-custom'})
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('custom-client restart passed',result.stdout)

if __name__=='__main__':unittest.main()
