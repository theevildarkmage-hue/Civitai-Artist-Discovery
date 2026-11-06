"""Synthetic OAuth canaries at urllib's final HTTPS boundary; zero external I/O.

The regressions failed on the audited 9a candidate. Source and both release
channels execute the same checks; frozen mode uses the actual packaged modules.
"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_safety
import argparse,copy,email.message,hashlib,importlib,io,json,logging,os,socket,sys,tempfile,threading,time,types,unittest,urllib.error,urllib.parse,urllib.request,urllib.response
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(test_safety.ROOT))
from discovery import oauth,cloud_identity,community,registry,history,diagnostics,social

CANARIES={'access':'AUDIT_CIVITAI_ACCESS_CANARY_8bc37','refresh':'AUDIT_CIVITAI_REFRESH_CANARY_4bd17',
          'api':'AUDIT_CIVITAI_APIKEY_CANARY_61f20','code':'AUDIT_AUTHORIZATION_CODE_CANARY_84f51',
          'cookie':'AUDIT_CIVITAI_SESSION_COOKIE_CANARY_87a22'}
ASSERTION='eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI3In0.c3ludGhldGljLXNpZ25hdHVyZQ'
SESSION='sdc_'+'s'*43;RENEWAL='sdr_'+'r'*43
SINK='https://masrxykkbqngtbeeqpbq.supabase.co/functions/v1/community-v1'
MODE='source';OBSERVATIONS=[];SEARCH_CODE=None

def response(url,value=None,code=200,headers=None):
    fields=email.message.Message()
    for key,value_ in (headers or {}).items():fields[key]=value_
    fields['Content-Type']='application/json'
    result=urllib.response.addinfourl(io.BytesIO(json.dumps(value if value is not None else {}).encode()),fields,url,code)
    result.msg='OK' if code==200 else 'Synthetic response';return result

class Boundary:
    def __init__(self,redirect_search=False,redirect_oauth=None,failure=False):
        self.events=[];self.redirect_search=redirect_search;self.redirect_oauth=redirect_oauth;self.failure=failure
    def open(self,handler,request):
        url=request.full_url;headers=dict(request.header_items());raw=request.data or b''
        self.events.append({'url':url,'method':request.get_method(),'headers':headers,'body':raw.decode(errors='replace')})
        parts=urllib.parse.urlsplit(url)
        if parts.hostname.endswith('.supabase.co'):
            if self.failure:return response(url,{'code':'UNAVAILABLE','private':list(CANARIES.values())},503)
            body=json.loads(raw) if raw else {};op=body.get('operation')
            if op=='identityChallenge':return response(url,{'apiVersion':2,'challenge':'synthetic-envelope','nonce':'n'*43,'audience':oauth.BUILTIN_CLIENT_ID,'expiresAt':int(time.time())+300,'devicePublicKey':body['devicePublicKey']})
            if op in ('identityExchange','identityRenew'):
                return response(url,{'apiVersion':2,'session':SESSION,'expiresAt':int(time.time())+43200,'member':{'id':7,'username':'Synthetic'},'renewal':{'credential':RENEWAL,'expiresAt':int(time.time())+86400,'absoluteExpiresAt':int(time.time())+2*86400}})
            return response(url,{'apiVersion':1,'registered':True})
        if parts.hostname=='auth.civitai.com':
            if self.redirect_oauth:return response(url,code=self.redirect_oauth,headers={'Location':SINK})
            if parts.path.endswith('/token'):
                return response(url,{'access_token':CANARIES['access'],'refresh_token':CANARIES['refresh'],
                    'id_token':ASSERTION,'api_key':CANARIES['api'],'session_cookie':CANARIES['cookie'],'expires_in':3600,'scope':917539})
            if parts.path.endswith('/userinfo'):return response(url,{'id':7,'username':'Synthetic'})
            return response(url,{})
        if self.redirect_search:return response(url,code=302,headers={'Location':SINK})
        return response(url,{'items':[],'results':[]})
    def prohibited(self):
        approved={'access':{'auth.civitai.com','civitai.red','civitai.com'},
                  'refresh':{'auth.civitai.com'},'code':{'auth.civitai.com'},
                  'api':{'search-new.civitai.com'},'cookie':{'auth.civitai.com'}}
        def permitted(url,kind):
            try:
                parts=urllib.parse.urlsplit(url)
                return (parts.scheme=='https' and parts.hostname in approved[kind]
                        and parts.port in (None,443) and parts.username is None and parts.password is None)
            except ValueError:return False
        return [(index,key) for index,event in enumerate(self.events)
                for key,value in CANARIES.items() if value in json.dumps(event)
                and not permitted(event['url'],key)]
    def record(self,name):
        OBSERVATIONS.append({'name':name,'requests':len(self.events),'supabaseRequests':sum(urllib.parse.urlsplit(e['url']).hostname.endswith('.supabase.co') for e in self.events),
            'prohibitedFields':[{'event':i,'credentialKind':kind} for i,kind in self.prohibited()],
            'fields':[{'host':urllib.parse.urlsplit(e['url']).hostname,'method':e['method'],'headerNames':sorted(k.lower() for k in e['headers']),
                'queryFieldNames':sorted(urllib.parse.parse_qs(urllib.parse.urlsplit(e['url']).query)),
                'bodyFields':sorted(json.loads(e['body'])) if e['body'].startswith('{') else []} for e in self.events]})

class Isolation(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='cad3-oauth-isolation-');self.root=Path(self.temp.name);self.stack=ExitStack()
        for name,value in [('APP_DATA',self.root),('TOKEN_PATH',self.root/'oauth_tokens.dpapi'),('LEGACY_TOKEN_PATH',self.root/'oauth_tokens.dpapi'),('KEYRING_ACCOUNT',hashlib.sha256(str(self.root).encode()).hexdigest()),('_ROTATED_PENDING',None)]:self.stack.enter_context(patch.object(oauth,name,value))
        self.stack.enter_context(patch.object(oauth,'client_id',return_value=oauth.BUILTIN_CLIENT_ID))
        self.stack.enter_context(patch.object(urllib.request,'_opener',None))
    def tearDown(self):
        try:
            if oauth._uses_secret_service():oauth._delete_linux()
        finally:self.stack.close();self.temp.cleanup()
    def intercept(self,boundary):
        def intercepted(handler,request):return boundary.open(handler,request)
        self.stack.enter_context(patch.object(urllib.request.HTTPSHandler,'https_open',intercepted))
    def safe(self,boundary,name):boundary.record(name);self.assertFalse(boundary.prohibited(),'Credential canary reached an unauthorized transport destination; values withheld')
    def login(self):
        with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        self.stack.enter_context(patch.object(oauth,'CALLBACK_PORT',port));self.stack.enter_context(patch.object(oauth,'REDIRECT_URI',f'http://localhost:{port}/oauth/callback'))
        def browser(url,**kwargs):
            values=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            callback=f'http://127.0.0.1:{port}/oauth/callback?'+urllib.parse.urlencode({'state':values['state'][0],'code':CANARIES['code']})
            request=urllib.request.Request(callback,headers={'Cookie':'civitai_session='+CANARIES['cookie']})
            with urllib.request.urlopen(request,timeout=5) as reply:reply.read()
            return True
        self.stack.enter_context(patch.object(oauth.webbrowser,'open',side_effect=browser))
        self.assertEqual(oauth.login(timeout=5)['id'],7)
    def test_login_callback_storage_operations_registry_renewal_and_logout(self):
        boundary=Boundary();self.intercept(boundary);self.login()
        self.assertEqual(oauth.reconnect_community(timeout=5)['id'],7)
        stored=oauth._load();self.assertNotIn('id_token',stored)
        if oauth._uses_secret_service():
            self.assertFalse(oauth.TOKEN_PATH.exists())
            serialized=oauth._linux_keyring().get_password(oauth.KEYRING_SERVICE,oauth.KEYRING_ACCOUNT)
            self.assertEqual(json.loads(serialized),stored);self.assertNotIn(ASSERTION,serialized)
        else:
            encrypted=oauth.TOKEN_PATH.read_bytes();self.assertTrue(all(v.encode() not in encrypted for v in [*CANARIES.values(),ASSERTION]))
        backend=community.SupabaseCommunityBackend(cloud_identity.ENDPOINT,identity_token_provider=oauth.get_community_session)
        for op in ['status','gallery','verify','recommend','withdraw','report']:
            backend.request(op,CANARIES['access'],{'artwork':123} if op not in ('status','gallery') else {})
        client=registry.RegistryClient('3.0.0');self.assertTrue(client.authenticated({'id':7},oauth.get_community_session))
        for _ in range(100):
            if not client.active:break
            time.sleep(.01)
        self.assertFalse(client.active)
        stored=oauth._load();stored['expires_at']=0;oauth._save(stored);self.assertEqual(oauth.get_access_token(),CANARIES['access'])
        stored=oauth._load();stored['discovery_session']['expires_at']=0;oauth._save(stored);self.assertEqual(oauth.get_community_session(),SESSION)
        oauth.disconnect();self.assertFalse(oauth.TOKEN_PATH.exists())
        if oauth._uses_secret_service():self.assertIsNone(oauth._linux_keyring().get_password(oauth.KEYRING_SERVICE,oauth.KEYRING_ACCOUNT))
        self.safe(boundary,'login_callback_storage_registry_operations_renewal_logout')
        exchanges=[e for e in boundary.events if e['body'].startswith('{') and json.loads(e['body']).get('operation')=='identityExchange']
        self.assertEqual(len(exchanges),2);self.assertEqual(json.loads(exchanges[0]['body'])['identityAssertion'],ASSERTION)
        self.assertNotIn('authorization',{k.lower() for k in exchanges[0]['headers']})
    def test_malformed_legacy_credentials_fail_before_cloud_transport(self):
        boundary=Boundary();self.intercept(boundary)
        for value in CANARIES.values():
            backend=community.SupabaseCommunityBackend(cloud_identity.ENDPOINT,identity_token_provider=lambda:value)
            with self.assertRaises(community.CommunityError):backend.request('status',CANARIES['access'],{})
            with self.assertRaises(RuntimeError):cloud_identity.exchange(value,{'expiresAt':int(time.time())+300},7)
            with self.assertRaises(RuntimeError):cloud_identity.renew({'renewal':{'credential':value,'pending_request':'p'*43}})
        self.assertFalse(boundary.events);self.safe(boundary,'malformed_credentials_rejected')
    def test_http_errors_retry_and_diagnostics_do_not_echo_canaries(self):
        boundary=Boundary(failure=True);self.intercept(boundary)
        for _ in range(2):
            try:community.bounded_json(cloud_identity.ENDPOINT,SESSION,{'apiVersion':1,'operation':'status'})
            except community.CommunityError as error:diagnostics.community_failure(self.root,'status',error)
        diagnostics.community_failure(self.root,'exchange',RuntimeError(json.dumps(CANARIES)))
        files=list(self.root.glob('error.log*'));self.assertTrue(files,'Expected real diagnostic log was not written')
        text='\n'.join(p.read_text(errors='replace') for p in files);self.assertTrue(text)
        self.assertTrue(all(value not in text for value in CANARIES.values()));self.safe(boundary,'errors_retries_diagnostics')
    def test_registry_error_logging_contains_only_fixed_categories(self):
        boundary=Boundary(failure=True);self.intercept(boundary);stream=io.StringIO();handler=logging.StreamHandler(stream)
        logger=logging.getLogger('discovery.registry');logger.addHandler(handler)
        try:
            client=registry.RegistryClient('3.0.0');client.authenticated({'id':7},lambda:SESSION)
            for _ in range(100):
                if not client.active:break
                time.sleep(.01)
            self.assertFalse(client.active);self.assertTrue(stream.getvalue());self.assertTrue(all(v not in stream.getvalue() for v in CANARIES.values()))
        finally:logger.removeHandler(handler)
        self.safe(boundary,'registry_error_logging')
    def test_unexpected_refresh_identity_assertion_must_not_be_persisted(self):
        boundary=Boundary();self.intercept(boundary);self.login()
        stored=oauth._load();stored['expires_at']=0;oauth._save(stored);oauth.get_access_token()
        # Local DPAPI storage is real; this checks minimization, not cloud leakage.
        self.safe(boundary,'unexpected_refresh_assertion')
        self.assertFalse('id_token' in oauth._load(),'Unexpected refresh identity assertion retained in protected local storage; values withheld')
    def test_stale_assertion_and_failed_rotation_pending_state_are_discarded(self):
        boundary=Boundary();self.intercept(boundary);self.login()
        stored=oauth._load();stored.update(id_token=ASSERTION,expires_at=0)
        oauth._save_stored(stored)  # Model an older encrypted profile without touching a real one.
        oauth.get_access_token();self.assertFalse('id_token' in oauth._load())
        with patch.object(oauth,'_save_stored',side_effect=PermissionError('Synthetic sharing lock')):
            with self.assertRaises(oauth.OAuthTemporarilyUnavailable):oauth._save_rotated({**stored,'id_token':ASSERTION})
        self.assertFalse('id_token' in oauth._pending_for_profile()['tokens'])
        self.safe(boundary,'stale_assertion_rotation_pending')
    def test_lost_renewal_reply_retry_contains_only_discovery_credential(self):
        boundary=Boundary();self.intercept(boundary);self.login();session=oauth._load()['discovery_session'];session['renewal']['pending_request']='p'*43
        original=boundary.open;attempts=[]
        def flaky(handler,request):
            body=json.loads(request.data) if request.data and request.data.startswith(b'{') else {}
            if body.get('operation')=='identityRenew':
                attempts.append(body)
                if len(attempts)==1:
                    boundary.events.append({'url':request.full_url,'method':request.get_method(),'headers':dict(request.header_items()),'body':request.data.decode()})
                    return response(request.full_url,{'code':'UNAVAILABLE','private':list(CANARIES.values())},503)
            return original(handler,request)
        boundary.open=flaky
        with self.assertRaises(community.CommunityError):cloud_identity.renew(session)
        self.assertEqual(cloud_identity.renew(session)['session'],SESSION)
        self.assertEqual(attempts[0]['requestId'],attempts[1]['requestId']);self.assertNotEqual(attempts[0]['attemptNonce'],attempts[1]['attemptNonce'])
        self.safe(boundary,'lost_renewal_reply_retry')
    def test_community_redirect_fails_closed(self):
        boundary=Boundary();self.intercept(boundary)
        original=boundary.open
        def redirect(handler,request):
            if request.full_url==cloud_identity.ENDPOINT:
                boundary.events.append({'url':request.full_url,'method':request.get_method(),'headers':dict(request.header_items()),'body':(request.data or b'').decode()})
                return response(request.full_url,code=302,headers={'Location':'https://other.supabase.co/sink'})
            return original(handler,request)
        boundary.open=redirect
        with self.assertRaises(community.CommunityError):community.bounded_json(cloud_identity.ENDPOINT,SESSION,{'apiVersion':1,'operation':'status'})
        self.assertEqual(len(boundary.events),1);self.safe(boundary,'community_redirect_denied')
    def test_userinfo_redirect_never_forwards_access_bearer(self):
        boundary=Boundary(redirect_oauth=302);self.intercept(boundary)
        oauth._userinfo(CANARIES['access']);self.safe(boundary,'userinfo_redirect_strips_bearer')
    def test_oauth_form_redirects_never_forward_code_or_refresh(self):
        for code in [301,302,303,307,308]:
            boundary=Boundary(redirect_oauth=code);self.intercept(boundary)
            for body in [{'grant_type':'authorization_code','code':CANARIES['code']},{'grant_type':'refresh_token','refresh_token':CANARIES['refresh']}]:
                try:oauth._post('token',body)
                except oauth.OAuthTemporarilyUnavailable:pass
            oauth._revoke(CANARIES['access'],'access_token',oauth.BUILTIN_CLIENT_ID)
            self.assertEqual(len(boundary.events),6 if code in (301,302,303) else 3)
            self.safe(boundary,'oauth_form_redirect_'+str(code))
    def test_social_read_write_redirects_never_forward_primary_bearer(self):
        for method in ['GET','POST']:
            boundary=Boundary(redirect_search=True);self.intercept(boundary)
            with patch.object(social,'get_access_token',return_value=CANARIES['access']):
                social.SocialClient()._request(urllib.request.Request('https://civitai.red/api/trpc/synthetic',method=method,data=b'{}' if method=='POST' else None))
            self.assertEqual(len(boundary.events),2);self.safe(boundary,'social_'+method+'_cross_origin_redirect')
    def search(self,boundary,url=None,body=None):
        self.intercept(boundary)
        self.stack.enter_context(patch.object(history,'SEARCH_CLIENT_KEY',CANARIES['api']))
        if url:self.stack.enter_context(patch.object(history,'SEARCH_URL',url))
        archive=history.HistoryArchive(self.root/'history');return archive._search_request(body or {'limit':1})
    def test_search_redirect_must_not_forward_api_key_to_supabase(self):
        boundary=Boundary(redirect_search=True)
        try:self.search(boundary)
        except urllib.error.HTTPError:pass
        self.assertEqual(len(boundary.events),1,'A rejected redirect must not reach its destination')
        self.safe(boundary,'search_cross_host_redirect')
    def test_search_redirects_reject_every_unapproved_origin_before_following(self):
        for target in ['https://attacker.invalid/receive','https://auth.civitai.com/receive',
                       'http://search-new.civitai.com/receive','https://search-new.civitai.com:444/receive']:
            for code in (301,302,303,307,308):
                boundary=Boundary();original=boundary.open
                def redirect(handler,request):
                    if not boundary.events:
                        boundary.events.append({'url':request.full_url,'method':request.get_method(),'headers':dict(request.header_items()),'body':(request.data or b'').decode()})
                        return response(request.full_url,code=code,headers={'Location':target})
                    return original(handler,request)
                boundary.open=redirect
                with self.assertRaises(urllib.error.HTTPError):self.search(boundary)
                self.assertEqual(len(boundary.events),1)
                self.safe(boundary,'unapproved_search_redirect_'+str(code))
    def test_configured_search_destination_must_reject_supabase(self):
        import discovery.search
        namespace={'__package__':'discovery','__name__':'discovery._audit_search'}
        code=SEARCH_CODE or compile(Path(discovery.search.__file__).read_text(encoding='utf-8'),discovery.search.__file__,'exec')
        with patch.dict(os.environ,{'CIVITAI_SEARCH_HOST':'https://masrxykkbqngtbeeqpbq.supabase.co','CIVITAI_SEARCH_INDEX':'audit','CIVITAI_SEARCH_CLIENT_KEY':CANARIES['api']}):exec(code,namespace)
        self.assertEqual(namespace['SEARCH_CLIENT_KEY'],CANARIES['api'])
        boundary=Boundary()
        try:self.search(boundary,namespace['SEARCH_URL'])
        except history.SearchBackendUnavailable:pass
        self.assertFalse(boundary.events,'An unsafe initial origin must fail before any transport')
        self.safe(boundary,'configured_search_destination')
    def test_unsafe_search_urls_and_indexes_are_rejected_before_transport(self):
        values=['http://search-new.civitai.com/indexes/images_v6/search',
                'https://search-new.civitai.com.evil.invalid/indexes/images_v6/search',
                'https://user:password@search-new.civitai.com/indexes/images_v6/search',
                'https://search-new.civitai.com:444/indexes/images_v6/search',
                'https://search-new.civitai.com/indexes/images_v6/search?key=synthetic',
                'https://search-new.civitai.com/indexes/images_v6/search#fragment',
                'https://search-new.civitai.com/indexes/../search',
                'https://search-new.civitai.com/indexes/a%2fb/search',
                'https://search-new.civitai.com\\@isolated.supabase.co/indexes/a/search',
                'https://search-new.civitai.com/indexes/a/search\r\nInjected: value']
        for value in values:
            boundary=Boundary()
            with self.assertRaises(history.SearchBackendUnavailable):self.search(boundary,value)
            self.assertFalse(boundary.events)
            self.safe(boundary,'unsafe_search_url')
    def test_authenticated_safe_redirects_preserve_required_request_fields(self):
        body={'q':'','filter':'nsfwLevel = 1','limit':50,'offset':100,'sort':['createdAt:desc']}
        for code in [301,302,303,307,308]:
            boundary=Boundary();original=boundary.open
            def redirect(handler,request):
                if not boundary.events:
                    boundary.events.append({'url':request.full_url,'method':request.get_method(),'headers':dict(request.header_items()),'body':request.data.decode()})
                    return response(request.full_url,code=code,headers={'Location':'https://search-new.civitai.com/redirected'})
                return original(handler,request)
            boundary.open=redirect;self.search(boundary,body=body)
            self.assertEqual(len(boundary.events),2)
            for event in boundary.events:self.assertEqual({k.lower():v for k,v in event['headers'].items()}['authorization'],'Bearer '+CANARIES['api'])
            self.assertEqual(json.loads(boundary.events[0]['body']),body)
            self.assertEqual(boundary.events[1]['method'],'POST' if code in (307,308) else 'GET')
            if code in (307,308):self.assertEqual(json.loads(boundary.events[1]['body']),body)
            self.safe(boundary,'safe_search_redirect_'+str(code))
    def test_search_retry_preserves_pagination_filters_and_credentials(self):
        boundary=Boundary();original=boundary.open
        def retry(handler,request):
            if not boundary.events:
                boundary.events.append({'url':request.full_url,'method':request.get_method(),'headers':dict(request.header_items()),'body':request.data.decode()})
                return response(request.full_url,code=429,headers={'Retry-After':'0'})
            return original(handler,request)
        boundary.open=retry;self.intercept(boundary)
        archive=history.HistoryArchive(self.root/'history');body={'filter':'nsfwLevel = 2','limit':24,'offset':48}
        with patch.object(history,'SEARCH_CLIENT_KEY',CANARIES['api']),patch.object(archive,'_wait_api_lane',return_value=0),patch.object(history.time,'sleep'):
            archive._search_request(body)
        self.assertEqual(len(boundary.events),2)
        self.assertTrue(all(json.loads(e['body'])==body for e in boundary.events));self.safe(boundary,'search_retry_paging_filters')
    def test_search_redirect_loops_remain_bounded(self):
        boundary=Boundary();self.intercept(boundary)
        def loop(handler,request):
            boundary.events.append({'url':request.full_url,'method':request.get_method(),'headers':dict(request.header_items()),'body':(request.data or b'').decode()})
            return response(request.full_url,code=307,headers={'Location':'https://search-new.civitai.com/loop'})
        boundary.open=loop
        with self.assertRaises(urllib.error.HTTPError):self.search(boundary)
        self.assertLessEqual(len(boundary.events),11);self.safe(boundary,'bounded_search_redirect_loop')

def frozen(executable):
    global oauth,cloud_identity,community,registry,history,diagnostics,social,MODE,SEARCH_CODE
    from PyInstaller.archive.readers import CArchiveReader
    import discovery
    pyz=CArchiveReader(str(executable)).open_embedded_archive('PYZ.pyz');sys.frozen=True;sys._MEIPASS=str(executable.parent/'_internal')
    # Use the actual shipped stdlib redirect implementation as well as app code.
    wire=types.ModuleType('urllib.request');wire.__file__=str(executable.parent/'_internal/urllib/request.py');wire.__package__='urllib'
    sys.modules['urllib.request']=wire;urllib.request=wire;exec(pyz.extract('urllib.request'),wire.__dict__)
    SEARCH_CODE=pyz.extract('discovery.search')
    for short in ['build_config','device_identity','diagnostics','community','cloud_identity','registry','oauth','social','search','history']:
        name='discovery.'+short;module=types.ModuleType(name);module.__file__=str(executable.parent/'_internal/discovery'/(short+'.py'));module.__package__='discovery'
        sys.modules[name]=module;setattr(discovery,short,module);exec(pyz.extract(name),module.__dict__)
    oauth,cloud_identity,community,registry,history,diagnostics=[sys.modules['discovery.'+n] for n in ['oauth','cloud_identity','community','registry','history','diagnostics']]
    social=sys.modules['discovery.social']
    MODE='frozen';return hashlib.sha256(executable.read_bytes()).hexdigest()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--frozen-exe',type=Path);parser.add_argument('--output',type=Path);args=parser.parse_args()
    sha=frozen(args.frozen_exe.resolve()) if args.frozen_exe else None
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Isolation))
    receipt={'mode':MODE,'pythonVersion':sys.version.split()[0],'frozenUrllibExecuted':MODE=='frozen','executableSha256':sha,'testsRun':result.testsRun,'failures':[t.id() for t,_ in result.failures],
        'errors':[t.id() for t,_ in result.errors],'externalNetworkRequests':0,'realOAuthCallbackLoopback':True,'transport':'actual urllib request/redirect processing; HTTPS final I/O replaced by synthetic boundary','observations':OBSERVATIONS}
    if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(receipt,indent=2))
    print(json.dumps({'mode':MODE,'tests':result.testsRun,'failed':len(result.failures),'errors':len(result.errors),'externalNetworkRequests':0}))
    raise SystemExit(not result.wasSuccessful())
