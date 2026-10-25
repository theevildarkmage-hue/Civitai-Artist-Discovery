"""Identity-only login/exchange and cloud transport credential boundaries."""
import test_safety
import copy,time,unittest
from unittest.mock import patch
from discovery import oauth,cloud_identity
from discovery.community import SupabaseCommunityBackend,CommunityError

SESSION='sdc_'+'s'*43
class IdentitySessionTests(unittest.TestCase):
    def primary(self):
        return {'access_token':'civitai_write_canary','refresh_token':'civitai_refresh_canary','scope':917539,
            'client_id':'client','expires_at':2**31,'identity':{'id':7,'username':'Owner'}}
    def session(self):return {'session':SESSION,'expires_at':2**31,'member_id':7}
    def test_one_login_grant_only_and_no_assertion_persisted(self):
        tokens={**self.primary(),'id_token':'signed.identity.assertion'};saved=[]
        with patch.object(oauth,'client_id',return_value='client'),patch.object(cloud_identity,'challenge',return_value={'nonce':'n'*43}),patch.object(cloud_identity,'exchange',return_value=self.session()) as exchange,patch.object(oauth,'_authorize_tokens',return_value=tokens) as authorize,patch.object(oauth,'_userinfo',return_value={'sub':'7','username':'Owner'}),patch.object(oauth,'_save',side_effect=lambda x:saved.append(copy.deepcopy(x))):
            self.assertEqual(oauth.login()['id'],7)
        authorize.assert_called_once_with(917539,300,nonce='n'*43)
        self.assertEqual(exchange.call_args.args[0],'signed.identity.assertion')
        self.assertNotIn('id_token',saved[0]);self.assertNotIn('community_identity',saved[0])
        self.assertEqual(saved[0]['access_token'],'civitai_write_canary');self.assertEqual(saved[0]['discovery_session'],self.session())
    def test_cloud_challenge_outage_preserves_local_login(self):
        saved=[]
        with patch.object(cloud_identity,'challenge',side_effect=OSError('Synthetic outage')),patch.object(oauth,'_authorize_tokens',return_value=self.primary()) as authorize,patch.object(oauth,'_userinfo',return_value={'id':7,'username':'Owner'}),patch.object(oauth,'_save',side_effect=lambda x:saved.append(x)):
            self.assertEqual(oauth.login()['id'],7)
        self.assertIsNone(authorize.call_args.kwargs['nonce']);self.assertNotIn('discovery_session',saved[0])
    def test_cloud_exchange_failure_preserves_local_login_without_fallback(self):
        saved=[]
        with patch.object(cloud_identity,'challenge',return_value={'nonce':'n'*43}),patch.object(cloud_identity,'exchange',side_effect=RuntimeError('Invalid proof')),patch.object(oauth,'_authorize_tokens',return_value={**self.primary(),'id_token':'proof'}) as authorize,patch.object(oauth,'_userinfo',return_value={'id':7,'username':'Owner'}),patch.object(oauth,'_save',side_effect=lambda x:saved.append(x)):
            oauth.login()
        self.assertEqual(authorize.call_count,1);self.assertNotIn('discovery_session',saved[0]);self.assertNotIn('id_token',saved[0])
    def test_session_provider_ignores_legacy_read_and_primary_credentials(self):
        for session in (None,{},self.primary(),{'session':'civitai_read_canary','expires_at':2**31,'member_id':7},{**self.session(),'member_id':8},{**self.session(),'expires_at':0}):
            primary={**self.primary(),'community_identity':{'access_token':'civitai_read_canary'}}
            if session is not None:primary['discovery_session']=session
            with patch.object(oauth,'_load',return_value=primary),patch.object(oauth,'client_id',return_value='client'),patch.object(oauth,'_post',side_effect=AssertionError('Must never refresh a cloud API grant')):
                with self.assertRaises(oauth.OAuthReauthenticationRequired):oauth.get_community_session()
    def test_valid_session_and_compatibility_alias_return_only_app_session(self):
        with patch.object(oauth,'_load',return_value={**self.primary(),'discovery_session':self.session()}),patch.object(oauth,'client_id',return_value='client'):
            self.assertEqual(oauth.get_community_session(),SESSION);self.assertEqual(oauth.get_community_access_token(),SESSION)
    def test_hosted_transport_refuses_every_non_session_provider(self):
        calls=[]
        for token in ('civitai_write_canary','civitai_read_canary','signed.identity.assertion','',None):
            backend=SupabaseCommunityBackend(cloud_identity.ENDPOINT,fetch=lambda *args:calls.append(args),identity_token_provider=lambda:token)
            with self.assertRaises(CommunityError):backend.request('status','civitai_write_canary',{})
        self.assertEqual(calls,[])
    def test_all_cloud_operations_use_only_app_session(self):
        calls=[];backend=SupabaseCommunityBackend(cloud_identity.ENDPOINT,fetch=lambda *args:(calls.append(args) or {'apiVersion':1}),identity_token_provider=lambda:SESSION)
        for operation in ('status','gallery','verify','recommend','withdraw','report'):backend.request(operation,'civitai_write_canary',{})
        self.assertEqual(len(calls),6);self.assertTrue(all(x[1]==SESSION for x in calls));self.assertNotIn('civitai_',str(calls))
    def test_exchange_body_allowlist_and_no_bearer(self):
        calls=[];pending={'challenge':'synthetic-envelope','expiresAt':2000}
        def send(*args):calls.append(args);return {'apiVersion':2,'session':SESSION,'expiresAt':44000,'member':{'id':7}}
        result=cloud_identity.exchange('header.payload.signature',pending,7,send=send,clock=lambda:1000)
        self.assertEqual(result['session'],SESSION);self.assertIsNone(calls[0][1]);self.assertEqual(set(calls[0][2]),{'apiVersion','operation','challenge','identityAssertion'})
    def test_exchange_rejects_api_tokens_before_transport(self):
        for token in ('civitai_write_canary','civitai_read_canary','a'*9000):
            with self.assertRaises(RuntimeError):cloud_identity.exchange(token,{'expiresAt':2000},7,send=lambda *args:self.fail('Credential sent'),clock=lambda:1000)
    def test_exchange_checks_account_and_bounded_session(self):
        for change in ({'member':{'id':8}},{'expiresAt':999},{'expiresAt':999999},{'session':'civitai_read_canary'}):
            result={'apiVersion':2,'session':SESSION,'expiresAt':44000,'member':{'id':7},**change}
            with self.assertRaises(RuntimeError):cloud_identity.exchange('h.p.s',{'expiresAt':2000,'challenge':'envelope'},7,send=lambda *args:result,clock=lambda:1000)
    def test_challenge_validates_audience_and_deadline(self):
        valid={'apiVersion':2,'nonce':'n'*43,'audience':'client','expiresAt':1300,'challenge':'envelope'}
        def send(*args):return {**valid,'devicePublicKey':args[2]['devicePublicKey']}
        received=cloud_identity.challenge('client',send=send,clock=lambda:1000)
        self.assertEqual(received['audience'],'client');self.assertIn('_device',received)
        for patch_value in ({'audience':'attacker'},{'expiresAt':999},{'expiresAt':99999},{'nonce':'invalid'}):
            with self.assertRaises(RuntimeError):cloud_identity.challenge('client',send=lambda *a:{**valid,'devicePublicKey':a[2]['devicePublicKey'],**patch_value},clock=lambda:1000)
    def test_server_and_backend_wiring_never_use_api_provider(self):
        source=(test_safety.ROOT/'server.py').read_text(encoding='utf-8')
        self.assertIn('REGISTRY.authenticated(identity,get_community_session)',source)
        self.assertNotIn('REGISTRY.authenticated(identity,get_access_token)',source)

if __name__=='__main__':unittest.main()
