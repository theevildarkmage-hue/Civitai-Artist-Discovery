"""Separate least-privileged provider grant; no primary token crosses hosted transport."""
import test_safety
import copy,concurrent.futures,time,unittest,json,io,contextlib
from unittest.mock import patch
from discovery import oauth
from discovery.community import SupabaseCommunityBackend,CommunityError
from discovery.registry import RegistryClient
class IdentityGrantTests(unittest.TestCase):
    def primary(self):
        return {'access_token':'synthetic-write','refresh_token':'synthetic-write-refresh','scope':917539,'client_id':'client','expires_at':2**31,'identity':{'id':7,'username':'Owner'}}
    def grant(self):
        return {'access_token':'synthetic-read','refresh_token':'synthetic-read-refresh','scope':33,'client_id':'client','expires_at':2**31,'identity':{'id':7,'username':'Owner'}}
    def test_login_separates_grants_and_preserves_primary(self):
        main=self.primary();read=self.grant();saved=[];calls=[]
        def authorize(scope,timeout):calls.append(scope);return copy.deepcopy(read if scope==33 else main)
        with patch.object(oauth,'_authorize_tokens',side_effect=authorize),patch.object(oauth,'_userinfo',return_value={'id':7,'username':'Owner'}),patch.object(oauth,'_verify_identity_grant',side_effect=lambda token,expected:token),patch.object(oauth,'_save',side_effect=lambda value:saved.append(copy.deepcopy(value))):
            self.assertEqual(oauth.login(),main['identity'])
        self.assertEqual(calls,[917539,33]);self.assertEqual(saved[0]['access_token'],'synthetic-write');self.assertEqual(saved[0]['community_identity']['access_token'],'synthetic-read')
    def test_legacy_and_malformed_grants_never_fall_back(self):
        for value in (None,{},self.primary(),{**self.grant(),'scope':True},{**self.grant(),'identity':{'id':8}},{**self.grant(),'client_id':'other'}):
            primary=self.primary()
            if value is not None:primary['community_identity']=value
            with patch.object(oauth,'_load',return_value=primary),patch.object(oauth,'client_id',return_value='client'),patch.object(oauth,'_post',side_effect=AssertionError('Unexpected token request')):
                with self.assertRaises(oauth.OAuthReauthenticationRequired):oauth.get_community_access_token()
    def test_hosted_transport_ignores_primary_argument(self):
        calls=[];backend=SupabaseCommunityBackend('https://test.supabase.co/functions/v1/community-v1',fetch=lambda url,token,payload:(calls.append(token) or {'apiVersion':1}),identity_token_provider=lambda:'synthetic-read')
        backend.request('status','synthetic-write',{});self.assertEqual(calls,['synthetic-read'])
        backend.identity_token_provider=None
        with self.assertRaises(CommunityError):backend.request('status','synthetic-write',{})
        self.assertEqual(calls,['synthetic-read'])
    def test_refresh_deduplicates_and_never_replaces_primary(self):
        stored=self.primary();stored['community_identity']={**self.grant(),'expires_at':0};posts=[]
        def post(name,body):
            posts.append(body['refresh_token']);time.sleep(.01);return {'access_token':'synthetic-fresh-read','refresh_token':'synthetic-fresh-refresh','scope':33,'expires_in':3600}
        def save(value):stored.clear();stored.update(copy.deepcopy(value))
        with patch.object(oauth,'_load',side_effect=lambda:copy.deepcopy(stored)),patch.object(oauth,'client_id',return_value='client'),patch.object(oauth,'_post',side_effect=post),patch.object(oauth,'_verify_identity_grant',side_effect=lambda value,expected:value),patch.object(oauth,'_save',side_effect=save):
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:values=list(pool.map(lambda _:oauth.get_community_access_token(),range(8)))
        self.assertEqual(values,['synthetic-fresh-read']*8);self.assertEqual(posts,['synthetic-read-refresh']);self.assertEqual(stored['access_token'],'synthetic-write');self.assertEqual(stored['scope'],917539)
    def test_scope_widening_fails_closed_without_breaking_primary(self):
        stored=self.primary();stored['community_identity']={**self.grant(),'expires_at':0};saved=[]
        with patch.object(oauth,'_load',return_value=stored),patch.object(oauth,'client_id',return_value='client'),patch.object(oauth,'_post',return_value={'access_token':'wrong','scope':917539}),patch.object(oauth,'_save',side_effect=lambda v:saved.append(copy.deepcopy(v))):
            with self.assertRaises(oauth.OAuthReauthenticationRequired):oauth.get_community_access_token()
        self.assertEqual(saved[0]['access_token'],'synthetic-write');self.assertTrue(saved[0]['community_identity']['reauth_required']);self.assertNotIn('reauth_required',saved[0])
    def test_provider_scope_is_checked_independently_of_token_response(self):
        for actual,expected_ok in ((33,True),(917539,False),(None,False),('33',False)):
            provider={'id':7,'tokenScope':actual,'subject':{'type':'oauth'}}
            class Opener:
                def open(inner,request,timeout):
                    self.assertEqual(request.full_url,'https://civitai.com/api/v1/me')
                    self.assertEqual(request.get_header('Authorization'),'Bearer synthetic-read')
                    return contextlib.closing(io.BytesIO(json.dumps(provider).encode()))
            with patch.object(oauth.urllib.request,'build_opener',return_value=Opener()),patch.object(oauth,'_userinfo',return_value={'sub':'7','username':'Verified'}):
                if expected_ok:self.assertEqual(oauth._verify_identity_grant(self.grant(),7)['identity']['username'],'Verified')
                else:
                    with self.assertRaises(oauth.OAuthSetupError):oauth._verify_identity_grant(self.grant(),7)

    def test_provider_scope_formats_are_exact(self):
        for scope in (33,'33',[33],['33']):
            self.assertTrue(oauth._is_identity_scope(scope),repr(scope))
        for scope in (None,True,33.0,[],['33','917539'],['33','33'],[['33']],917539,['917539'],'33 917539',{'scope':33}):
            self.assertFalse(oauth._is_identity_scope(scope),repr(scope))

    def test_singleton_scope_response_verified_and_canonicalized(self):
        class Opener:
            def open(inner,request,timeout):
                return contextlib.closing(io.BytesIO(json.dumps({'id':7,'tokenScope':33,'subject':{'type':'oauth'}}).encode()))
        with patch.object(oauth.urllib.request,'build_opener',return_value=Opener()),patch.object(oauth,'_userinfo',return_value={'sub':'7','username':'Verified'}):
            grant=oauth._verify_identity_grant({**self.grant(),'scope':['33']},7)
        self.assertEqual(grant['scope'],33)

    def test_registry_call_site_uses_restricted_provider(self):
        # Security regression at the actual server wiring; never read stored OAuth.
        source=(test_safety.ROOT/'server.py').read_text(encoding='utf-8')
        self.assertIn('REGISTRY.authenticated(identity,get_community_access_token)',source)
        self.assertNotIn('REGISTRY.authenticated(identity,get_access_token)',source)
if __name__=='__main__':unittest.main()
