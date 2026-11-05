"""Synthetic OAuth canaries at urllib's final HTTPS boundary; zero external I/O.

The search-key regressions intentionally fail on the audited 9a candidate.
Optional frozen mode executes the actual candidate's extracted Python modules.
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
from discovery import oauth,cloud_identity,community,registry,history,diagnostics

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
        return [(index,key) for index,event in enumerate(self.events) if urllib.parse.urlsplit(event['url']).hostname.endswith('.supabase.co')
                for key,value in CANARIES.items() if value in json.dumps(event)]
    def record(self,name):
        OBSERVATIONS.append({'name':name,'requests':len(self.events),'supabaseRequests':sum(urllib.parse.urlsplit(e['url']).hostname.endswith('.supabase.co') for e in self.events),
            'prohibitedFields':[{'event':i,'credentialKind':kind} for i,kind in self.prohibited()],
            'fields':[{'host':urllib.parse.urlsplit(e['url']).hostname,'method':e['method'],'headerNames':sorted(k.lower() for k in e['headers']),
                'queryFieldNames':sorted(urllib.parse.parse_qs(urllib.parse.urlsplit(e['url']).query)),
                'bodyFields':sorted(json.loads(e['body'])) if e['body'].startswith('{') else []} for e in self.events]})

class Isolation(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='cad3-oauth-isolation-');self.root=Path(self.temp.name);self.stack=ExitStack()
        for name,value in [('APP_DATA',self.root),('TOKEN_PATH',self.root/'oauth_tokens.dpapi'),('LEGACY_TOKEN_PATH',self.root/'oauth_tokens.dpapi'),('_ROTATED_PENDING',None)]:self.stack.enter_context(patch.object(oauth,name,value))
        self.stack.enter_context(patch.object(oauth,'client_id',return_value=oauth.BUILTIN_CLIENT_ID))
        self.stack.enter_context(patch.object(urllib.request,'_opener',None))
    def tearDown(self):self.stack.close();self.temp.cleanup()
    def intercept(self,boundary):
        def intercepted(handler,request):return boundary.open(handler,request)
        self.stack.enter_context(patch.object(urllib.request.HTTPSHandler,'https_open',intercepted))
    def safe(self,boundary,name):boundary.record(name);self.assertFalse(boundary.prohibited(),'Prohibited credential canary reached Supabase transport boundary; values withheld')
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
        stored=oauth._load();self.assertNotIn('id_token',stored)
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
        self.safe(boundary,'login_callback_storage_registry_operations_renewal_logout')
        exchanges=[e for e in boundary.events if e['body'].startswith('{') and json.loads(e['body']).get('operation')=='identityExchange']
        self.assertEqual(len(exchanges),1);self.assertEqual(json.loads(exchanges[0]['body'])['identityAssertion'],ASSERTION)
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
            self.safe(boundary,'oauth_form_redirect_'+str(code))
    def search(self,boundary,url=None):
        self.intercept(boundary)
        self.stack.enter_context(patch.object(history,'SEARCH_CLIENT_KEY',CANARIES['api']))
        if url:self.stack.enter_context(patch.object(history,'SEARCH_URL',url))
        archive=history.HistoryArchive(self.root/'history');archive._search_request({'limit':1})
    def test_search_redirect_must_not_forward_api_key_to_supabase(self):
        boundary=Boundary(redirect_search=True);self.search(boundary)
        self.safe(boundary,'search_cross_host_redirect')
    def test_configured_search_destination_must_reject_supabase(self):
        import discovery.search
        namespace={'__package__':'discovery','__name__':'discovery._audit_search'}
        code=SEARCH_CODE or compile(Path(discovery.search.__file__).read_text(encoding='utf-8'),discovery.search.__file__,'exec')
        with patch.dict(os.environ,{'CIVITAI_SEARCH_HOST':'https://masrxykkbqngtbeeqpbq.supabase.co','CIVITAI_SEARCH_INDEX':'audit','CIVITAI_SEARCH_CLIENT_KEY':CANARIES['api']}):exec(code,namespace)
        self.assertEqual(namespace['SEARCH_CLIENT_KEY'],CANARIES['api'])
        boundary=Boundary();self.search(boundary,namespace['SEARCH_URL'])
        self.safe(boundary,'configured_search_destination')

def frozen(executable):
    global oauth,cloud_identity,community,registry,history,diagnostics,MODE,SEARCH_CODE
    from PyInstaller.archive.readers import CArchiveReader
    import discovery
    pyz=CArchiveReader(str(executable)).open_embedded_archive('PYZ.pyz');sys.frozen=True;sys._MEIPASS=str(executable.parent/'_internal')
    # Use the actual shipped stdlib redirect implementation as well as app code.
    wire=types.ModuleType('urllib.request');wire.__file__=str(executable.parent/'_internal/urllib/request.py');wire.__package__='urllib'
    sys.modules['urllib.request']=wire;urllib.request=wire;exec(pyz.extract('urllib.request'),wire.__dict__)
    SEARCH_CODE=pyz.extract('discovery.search')
    for short in ['build_config','device_identity','diagnostics','community','cloud_identity','registry','oauth','search','history']:
        name='discovery.'+short;module=types.ModuleType(name);module.__file__=str(executable.parent/'_internal/discovery'/(short+'.py'));module.__package__='discovery'
        sys.modules[name]=module;setattr(discovery,short,module);exec(pyz.extract(name),module.__dict__)
    oauth,cloud_identity,community,registry,history,diagnostics=[sys.modules['discovery.'+n] for n in ['oauth','cloud_identity','community','registry','history','diagnostics']]
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
