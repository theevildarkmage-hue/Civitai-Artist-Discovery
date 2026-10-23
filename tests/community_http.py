"""Owned Deno HTTP -> actual PostgREST -> native PostgreSQL integration.

Civitai is represented by a local signed-token/UserInfo/artwork issuer. No real
account credentials, social writes, SQL adapter or production database is used.
"""
import os
if os.environ.get('CAD_ALLOW_LOCAL_HTTP_TESTS')!='1' or not os.environ.get('CAD_TEST_PG_SYSTEM_IDENTIFIER'):
    print('SKIP: use the owned native runner with --http-deno and --http-postgrest')
else:
    import base64
    import concurrent.futures
    import hashlib
    import http.client
    import hmac
    import json
    from pathlib import Path
    import secrets
    import socket
    import subprocess
    import sys
    import tempfile
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlsplit, parse_qs
    import urllib.request
    import urllib.error

    ROOT=Path(__file__).resolve().parents[1]
    output=Path(os.environ['CAD_TEST_HTTP_OUTPUT']);output.mkdir(parents=True,exist_ok=True)
    scratch=Path(tempfile.mkdtemp(prefix='cad3-http-service-'))
    processes=[];handles=[]
    env=os.environ.copy();secret=secrets.token_urlsafe(48);issuer_secret=secrets.token_urlsafe(48)
    seed=3_000_000_000+secrets.randbelow(1000000)
    creators={};fault='';received=[];scenarios=[];public_active=False;public_artworks={};public_members={}
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    def sql(statement):
        child=subprocess.run(['psql','-X','-A','-t','-v','ON_ERROR_STOP=1'],input=statement+'\n',
            env=env,capture_output=True,text=True,encoding='utf-8',timeout=20,creationflags=flags)
        assert child.returncode==0,child.stderr
        return child.stdout.strip()
    assert sql('select system_identifier from pg_control_system();')==env['CAD_TEST_PG_SYSTEM_IDENTIFIER']
    assert Path(sql('show data_directory;')).resolve()==Path(env['CAD_TEST_PG_DATA_DIR']).resolve()
    def port():
        with socket.socket() as probe:probe.bind(('127.0.0.1',0));return probe.getsockname()[1]
    def b64(value):return base64.urlsafe_b64encode(value).decode().rstrip('=')
    def token(claims,key):
        value=b64(b'{"alg":"HS256","typ":"JWT"}')+'.'+b64(json.dumps(claims,separators=(',',':')).encode())
        return value+'.'+b64(hmac.new(key.encode(),value.encode(),hashlib.sha256).digest())
    def member(n,expired=False):return token({'sub':str(seed+n),'exp':int(time.time())+(-10 if expired else 3600),'iss':'local-test-only'},issuer_secret)
    service_key=token({'role':'service_role','exp':int(time.time())+(86400 if env.get('CAD_TEST_MANUAL_LAB')=='1' else 3600)},secret)
    anon_key=token({'role':'authenticated','sub':str(seed),'exp':int(time.time())+3600},secret)
    def request(url,body=None,*,auth=None,headers=None,method=None):
        data=json.dumps(body).encode() if isinstance(body,dict) else body
        values={'Content-Type':'application/json',**(headers or {})}
        if auth is not None:values['Authorization']=auth
        req=urllib.request.Request(url,data=data,headers=values,method=method)
        try:
            with urllib.request.urlopen(req,timeout=15) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as error:
            try:value=json.load(error)
            except Exception:value={}
            return error.code,value
    class Issuer(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            global fault
            path=urlsplit(self.path);status=200
            if path.path=='/userinfo':
                try:
                    bearer=self.headers.get('Authorization','').removeprefix('Bearer ')
                    head,payload,signature=bearer.split('.')
                    assert json.loads(base64.urlsafe_b64decode(head+'='*(-len(head)%4)))['alg']=='HS256'
                    assert hmac.compare_digest(signature,b64(hmac.new(issuer_secret.encode(),(head+'.'+payload).encode(),hashlib.sha256).digest()))
                    claims=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
                    assert claims['exp']>time.time() and claims['iss']=='local-test-only' and fault!='revoked'
                    if fault=='slow':time.sleep(.35)
                    if public_active:
                        assert int(claims['sub']) in public_members and claims.get('aud')=='cad3-local-lab' and claims.get('simulated') is True
                    data={'sub':claims['sub'],'id':int(claims['sub']),
                        'username':public_members[int(claims['sub'])]['username'] if public_active else 'SignedMember'+claims['sub']}
                except Exception:status=401;data={}
            elif fault in ('unavailable','limited','missing','private'):
                status={'unavailable':503,'limited':429,'missing':404,'private':403}[fault];data={}
            else:
                if path.path=='/api/v1/images':image=int(parse_qs(path.query)['imageId'][0])
                else:image=json.loads(parse_qs(path.query)['input'][0])['json']['id']
                creator=creators.get(image,image+100000)
                if fault=='changed_owner':creator+=99999
                if public_active:
                    art=public_artworks.get(image)
                    if not art:status=404;data={}
                    elif path.path=='/api/v1/images':data={'items':[art['rest']]}
                    else:data={'result':{'data':{'json':art['detail']}}}
                elif path.path=='/api/v1/images':
                    data={'items':[{'id':image,'type':'video' if fault=='video' else 'image',
                        'username':f'Artist{creator}','browsingLevel':16 if fault=='rating' else 1 if image%2 else 2,
                        'url':'https://image.civitai.com/synthetic-local-only.jpeg'}]}
                else:data={'result':{'data':{'json':{'id':image,'user':{'id':creator,'username':f'Artist{creator}'},
                    'availability':'Public','ingestion':'Scanned','needsReview':None,
                    'publishedAt':'2026-01-01T00:00:00Z','nsfwLevel':16 if fault=='rating' else 1 if image%2 else 2}}}}
            raw=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)));self.end_headers()
            try:self.wfile.write(raw)
            except OSError:pass
    issuer=ThreadingHTTPServer(('127.0.0.1',0),Issuer)
    threading.Thread(target=issuer.serve_forever,daemon=True).start()
    rest_port,edge_port=port(),port();rest=f'http://127.0.0.1:{rest_port}';edge=f'http://127.0.0.1:{edge_port}'
    def launch(name,args,extra):
        stream=(output/(name+'.log')).open('a',encoding='utf-8');handles.append(stream)
        process=subprocess.Popen(args,cwd=ROOT,env={**env,**extra},stdout=stream,stderr=stream,creationflags=flags)
        processes.append(process);return process
    def wait(url,process):
        for _ in range(100):
            if process.poll() is not None:raise RuntimeError('Owned '+url.split(':')[-1]+' service exited; inspect redacted log')
            try:request(url,{},method='POST');return
            except OSError:time.sleep(.1)
        raise RuntimeError('Owned service did not become ready')
    def start_rest(database_override=None,port_override=None,role='cad3_rest',password=None):
        return launch('postgrest',[env['CAD_TEST_POSTGREST']],{
            'PGRST_DB_URI':f"postgresql://{role}@127.0.0.1:{env['PGPORT']}/{database_override or env['PGDATABASE']}",
            'PGPASSWORD':password or rest_password,'PGRST_DB_SCHEMAS':'public','PGRST_DB_ANON_ROLE':'anon',
            'PGRST_JWT_SECRET':secret,'PGRST_SERVER_HOST':'127.0.0.1','PGRST_SERVER_PORT':str(port_override or rest_port),
            'PGRST_DB_POOL':'4','PGRST_DB_POOL_ACQUISITION_TIMEOUT':'1','PGRST_LOG_LEVEL':'crit'})
    def start_edge(live=False,state='enabled',private_beta=False,port_override=None,rest_override=None,registry_state='disabled',distributed_limit=False):
        return launch('deno',[env['CAD_TEST_DENO'],'run','--no-config','--no-lock','--no-prompt',
            '--allow-env=SUPABASE_URL,SUPABASE_SERVICE_ROLE_KEY,SUPABASE_SECRET_KEYS,CAD_TEST_UPSTREAM_URL,CAD_TEST_EDGE_PORT,CAD_TEST_LIVE_READONLY,COMMUNITY_BETA_STATE,COMMUNITY_ALLOWED_RATINGS,COMMUNITY_OWNER_TEST_EXPIRES_AT,CAD_TEST_PRIVATE_BETA,DISCOVERY_REGISTRY_STATE,CAD_TEST_DISTRIBUTED_LIMIT',
            '--allow-net=127.0.0.1'+(',auth.civitai.com,civitai.red' if live else ''),str(ROOT/'supabase/tests/local_service.ts')],{
            'SUPABASE_URL':rest_override or rest,'SUPABASE_SERVICE_ROLE_KEY':service_key,'COMMUNITY_BETA_STATE':state,'COMMUNITY_ALLOWED_RATINGS':'1,2' if private_beta else '1,2,4,8,16',
            # The shared handler reads this even outside owner-only mode. Pin an
            # expired synthetic value; never inherit an interactive write window.
            'COMMUNITY_OWNER_TEST_EXPIRES_AT':'2000-01-01T00:00:00Z',
            'CAD_TEST_PRIVATE_BETA':'1' if private_beta else '0',
            'DISCOVERY_REGISTRY_STATE':registry_state,
            'CAD_TEST_DISTRIBUTED_LIMIT':'1' if distributed_limit else '0',
            'CAD_TEST_UPSTREAM_URL':f'http://127.0.0.1:{issuer.server_port}','CAD_TEST_EDGE_PORT':str(port_override or edge_port),
            'CAD_TEST_LIVE_READONLY':'1' if live else '0',
            'DENO_NO_UPDATE_CHECK':'1','DENO_DIR':str(scratch/'deno-cache')})
    def release_probe():
        from community_release import run as release_run,backup_release
        auth_url=None;registry_url=None
        def api(role,name,body):
            nonlocal auth_url
            if auth_url is None:
                password=secrets.token_urlsafe(40)
                sql("create role authenticator login noinherit password '"+password+"';grant anon,authenticated,service_role to authenticator;grant connect on database "+env['PGDATABASE']+" to authenticator;")
                hba=Path(env['CAD_TEST_PG_DATA_DIR'])/'pg_hba.conf';hba.write_text(f"host {env['PGDATABASE']} authenticator 127.0.0.1/32 scram-sha-256\n"+hba.read_text());sql('select pg_reload_conf();')
                rest_test_port=port();auth_url=f'http://127.0.0.1:{rest_test_port}'
                wait(auth_url,start_rest(port_override=rest_test_port,role='authenticator',password=password))
            claims={'role':'service_role' if role=='service' else 'anon' if role=='anon' else 'authenticated','exp':int(time.time())+3600,
                    'sub':'00000000-0000-0000-0000-000000000001' if role.startswith('operator') or role=='forged' else '00000000-0000-0000-0000-000000000002',
                    'aal':'aal1' if role=='operator_aal1' else 'aal2'}
            bearer='Bearer '+token(claims,'invalid-signature-key-not-authorized' if role=='forged' else secret)
            if name.startswith('community_private.'):
                payload=dict(body or {});method=payload.pop('__method','GET')
                return request(auth_url+'/'+name.split('.')[1]+'?select=*&limit=1',payload if method!='GET' else None,
                    method=method,auth=bearer,headers={'Accept-Profile':'community_private','Content-Profile':'community_private'})
            return request(auth_url+'/rpc/'+name,body,auth=bearer)
        def register(extra=None):
            nonlocal registry_url
            if registry_url is None:
                registry_port=port();registry_url=f'http://127.0.0.1:{registry_port}'
                wait(registry_url,start_edge(state='disabled',private_beta=True,port_override=registry_port,registry_state='enabled'))
            bearer=token({'sub':'8800000000','exp':int(time.time())+3600,'iss':'local-test-only'},issuer_secret)
            return request(registry_url,{'apiVersion':1,'operation':'register','appVersion':'3.0.0-beta.1','platform':'Linux',**(extra or {})},auth='Bearer '+bearer)[1]
        result=release_run(sql=sql,env=env,output=output,api=api,register=register)
        assert register().get('registered') is True
        restore_token=token({'sub':'8800000000','exp':int(time.time())+3600,'iss':'local-test-only'},issuer_secret)
        result['releaseRestore']=backup_release(sql=sql,env=env,output=output,restore_http=lambda db:restored_probe(db,restore_token))
        urls=[]
        for _ in range(2):
            endpoint_port=port();url=f'http://127.0.0.1:{endpoint_port}';urls.append(url)
            wait(url,start_edge(state='disabled',private_beta=True,port_override=endpoint_port,distributed_limit=True))
        statuses=[]
        for n in range(35):
            bearer=token({'sub':str(8900000000+n),'exp':int(time.time())+3600,'iss':'local-test-only'},issuer_secret)
            statuses.append(request(urls[n%2],{'apiVersion':1,'operation':'status'},auth='Bearer '+bearer)[0])
        assert statuses==[200]*30+[429]*5,statuses
        # Rejected RPCs roll back their attempted increment. The bucket records
        # admitted calls, while the HTTP evidence records all 35 attempts.
        assert sql("select requests=30 from community_rate_buckets where key='project:community';")=='t'
        evidence={'passed':True,'instances':2,'requests':35,'accepted':30,'rateLimited':5,'billedInvocationCap':False,'newCommunityRecords':0}
        (output/'distributed-limit.json').write_text(json.dumps(evidence,indent=2));result['distributedLimit']=evidence
        return result
    def legacy_probe(owner,owner_token):
        legacy_dir=scratch/'legacy-v10';legacy_dir.mkdir(exist_ok=True)
        # Immutable regression source, never packaged/deployed; no private Git ancestry.
        old=(ROOT/'tests/fixtures/community-legacy-handler.ts').read_bytes().replace(b'\r\n',b'\n')
        digest=hashlib.sha256(old).hexdigest()
        # The reviewed hosted download used CRLF; this Git blob uses LF. Their
        # canonical bytes were compared, and both immutable digests are recorded.
        assert digest=='f879ae189551dfed71649002c9b6f4708c4e07a6e6b0e55272d94c31c3d898eb','Pinned v10 source mismatch'
        (legacy_dir/'handler.ts').write_bytes(old)
        launcher=(ROOT/'supabase/tests/local_service.ts').read_text().replace("../functions/community-v1/handler.ts","./handler.ts").replace("invitationOnly:Deno.env.get('CAD_TEST_PRIVATE_BETA')==='1'","ownerOnly:true")
        (legacy_dir/'serve.ts').write_text(launcher)
        legacy_port=port();url=f'http://127.0.0.1:{legacy_port}'
        proc=launch('legacy-v10',[env['CAD_TEST_DENO'],'run','--no-config','--no-lock','--no-prompt',
            '--allow-env=SUPABASE_URL,SUPABASE_SERVICE_ROLE_KEY,SUPABASE_SECRET_KEYS,CAD_TEST_UPSTREAM_URL,CAD_TEST_EDGE_PORT,CAD_TEST_LIVE_READONLY,COMMUNITY_BETA_STATE,COMMUNITY_ALLOWED_RATINGS,COMMUNITY_TEST_OWNER_ID,COMMUNITY_OWNER_TEST_EXPIRES_AT,CAD_TEST_DISTRIBUTED_LIMIT',
            '--allow-net=127.0.0.1',str(legacy_dir/'serve.ts')],{
            'SUPABASE_URL':rest,'SUPABASE_SERVICE_ROLE_KEY':service_key,'CAD_TEST_UPSTREAM_URL':f'http://127.0.0.1:{issuer.server_port}',
            'CAD_TEST_EDGE_PORT':str(legacy_port),'CAD_TEST_LIVE_READONLY':'0','COMMUNITY_BETA_STATE':'read_only',
            'COMMUNITY_TEST_OWNER_ID':str(owner),'COMMUNITY_OWNER_TEST_EXPIRES_AT':'2000-01-01T00:00:00Z','COMMUNITY_ALLOWED_RATINGS':'1,2'})
        wait(url,proc)
        status,value=request(url,{'apiVersion':1,'operation':'gallery'},auth='Bearer '+owner_token)
        proc.terminate();proc.wait(timeout=10)
        return {'status':status,'code':value.get('code'),'pinnedVersion10HandlerSha256':digest,
            'reviewedHostedCrlfSha256':'30708e456dac1929e30fc0deabc44703126777a063075e557952fcd4877430a5','separateEndpoint':True}
    def restored_probe(database,owner_token):
        rest_test_port,edge_test_port=port(),port()
        sql('grant connect on database '+database+' to cad3_rest;')
        hba=Path(env['CAD_TEST_PG_DATA_DIR'])/'pg_hba.conf'
        hba.write_text(f"host {database} cad3_rest 127.0.0.1/32 scram-sha-256\n"+hba.read_text());sql('select pg_reload_conf();')
        rest_url=f'http://127.0.0.1:{rest_test_port}';edge_url=f'http://127.0.0.1:{edge_test_port}'
        rest_proc=start_rest(database_override=database,port_override=rest_test_port);wait(rest_url,rest_proc)
        edge_proc=start_edge(state='disabled',private_beta=True,port_override=edge_test_port,rest_override=rest_url);wait(edge_url,edge_proc)
        status,_=request(edge_url,{'apiVersion':1,'operation':'status'},auth='Bearer '+owner_token)
        _,denial=request(edge_url,{'apiVersion':1,'operation':'recommend','artwork':0},auth='Bearer '+owner_token)
        broken=scratch/'broken-rollout.ts';broken.write_text("throw new Error('Synthetic failed rollout');")
        failed=launch('failed-rollout',[env['CAD_TEST_DENO'],'run','--no-config','--no-lock',str(broken)],{})
        failed.wait(timeout=20)
        after,_=request(edge_url,{'apiVersion':1,'operation':'status'},auth='Bearer '+owner_token)
        edge_proc.terminate();edge_proc.wait(timeout=10);rest_proc.terminate();rest_proc.wait(timeout=10)
        return {'status':status,'recommendCode':denial.get('code'),'failedRolloutExited':failed.returncode!=0,'statusAfterFailedRollout':after}
    def call(operation='status',n=0,**fields):
        result=request(edge,{'apiVersion':1,'operation':operation,**fields},auth='Bearer '+member(n))
        received.append(result);return result
    def counts():return json.loads(sql(f"select json_build_object('recommendations',(select count(*) from community_recommendations where member_id between {seed} and {seed+999}), 'audit',(select count(*) from community_audit a join community_recommendations r using(image_id) where r.member_id between {seed} and {seed+999}), 'keys',(select count(*) from community_submission_requests where member_id between {seed} and {seed+999}));"))
    def race(values):
        barrier=threading.Barrier(len(values))
        def send(value):barrier.wait(timeout=10);return call('recommend',**value)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(values)) as pool:return list(pool.map(send,values))
    def scenario(name,values,accepted,code,replayed=0):
        before=counts();results=race(values);after=counts()
        assert sum(status==200 for status,_ in results)==accepted,(name,results)
        assert all(status==200 or value.get('code')==code for status,value in results),(name,results)
        assert sum(bool(value.get('replayed')) for _,value in results)==replayed
        assert after['recommendations']-before['recommendations']==accepted-replayed
        assert after['audit']-before['audit']==accepted-replayed
        scenarios.append({'scenario':name,'sent':len(values),'acceptedResponses':accepted,'replayed':replayed,
            'rejected':len(values)-accepted,'persisted':accepted-replayed,'before':before,'after':after})
        (output/'progress.json').write_text(json.dumps({'stage':'HTTP races','scenarios':len(scenarios)}))
    try:
        rest_password=secrets.token_urlsafe(32)
        sql(f"create role cad3_rest login noinherit password '{rest_password}'; grant anon,authenticated,service_role to cad3_rest; grant connect on database {env['PGDATABASE']} to cad3_rest;")
        hba=Path(env['CAD_TEST_PG_DATA_DIR'])/'pg_hba.conf'
        hba.write_text(f"host {env['PGDATABASE']} cad3_rest 127.0.0.1/32 scram-sha-256\n"+hba.read_text())
        sql('select pg_reload_conf();')
        rest_process=start_rest();wait(rest,rest_process)
        edge_process=start_edge();wait(edge,edge_process)
        if env.get('CAD_TEST_BETA_ONLY')=='1':
            from community_private_beta import run as run_beta
            def beta_restart_fast(state):
                global edge_process
                edge_process.terminate();edge_process.wait(timeout=10)
                edge_process=start_edge(state=state,private_beta=True);wait(edge,edge_process)
            def beta_fault_fast(value):
                global fault
                fault=value
            def second_instance():
                second_port=port();process=start_edge(private_beta=True,port_override=second_port)
                url=f'http://127.0.0.1:{second_port}';wait(url,process);return url
            beta_result=run_beta(second_instance=second_instance,legacy_probe=legacy_probe,restored_probe=restored_probe,release_probe=release_probe,sql=sql,call=call,request=request,edge=edge,member=member,
                restart=beta_restart_fast,set_fault=beta_fault_fast,env=env,output=output,seed=seed)
            (output/'summary.json').write_text(json.dumps({'passed':True,'privateBeta':beta_result,'baselineHttpSkipped':True},indent=2))
            raise SystemExit(0)
        initial=call()
        if initial[0]!=200:
            req=urllib.request.Request(rest+'/rpc/community_take_rate',data=b'{"p_key":"http-diagnostic"}',
                headers={'Content-Type':'application/json','Authorization':'Bearer '+service_key})
            with urllib.request.urlopen(req,timeout=10) as response:
                print(json.dumps({'rateRpcStatus':response.status,'rateRpcBodyBytes':len(response.read()),'handlerStatus':initial[0]}),flush=True)
        assert initial[0]==200,initial
        before_policy=counts()
        for state in ('read_only','disabled'):
            edge_process.terminate();edge_process.wait(timeout=10);edge_process=start_edge(state=state);wait(edge,edge_process)
            status,value=call(n=950);assert status==200 and value['beta']['submissions'] is False
            assert call('gallery',n=950)[0]==(200 if state=='read_only' else 503)
            assert call('recommend',n=950,artwork=seed+10000)[1]['code']==('READ_ONLY' if state=='read_only' else 'BETA_DISABLED')
            assert counts()==before_policy
        edge_process.terminate();edge_process.wait(timeout=10);edge_process=start_edge();wait(edge,edge_process)
        scenarios.append({'scenario':'real-http-policy-changes','states':['read_only','disabled','enabled'],'persisted':0})
        # No Civitai credential or identity assertion is accepted from the caller.
        for auth in (None,'Basic invalid','Bearer invalid','Bearer '+member(0,expired=True)):
            status,value=request(edge,{'apiVersion':1,'operation':'status'},auth=auth)
            assert status==401,(status,value)
        fault='revoked';assert call()[1]['code']=='AUTH_EXPIRED';fault=''
        for fields in ({'memberId':seed+1},{'creatorId':42},{'owner':seed}):
            assert call('recommend',artwork=seed+10000,**fields)[1]['code']=='INVALID_REQUEST'
        assert call('moderate',artwork=seed+10000)[1]['code']=='INVALID_REQUEST'
        for payload,headers,method in ((b'null',{},'POST'),(b'{',{},'POST'),(b'x'*9000,{},'POST'),
            (b'{}',{'Content-Type':'text/plain'},'POST'),(None,{},'GET'),(b'{}',{'Origin':'https://evil.test'},'POST')):
            status,_=request(edge,payload,headers=headers,method=method,auth='Bearer '+member(0));assert status in (400,403)
        for jwt in (None,anon_key,token({'role':'service_role'},'forged-key-not-the-test-secret')):
            status,_=request(rest+'/rpc/community_submit',{'p_member':seed,'p_name':'Spoof','p_image':seed+20000,'p_creator':seed+20001},auth=('Bearer '+jwt) if jwt else None)
            assert status in (401,403)
            status,_=request(rest+'/community_recommendations',auth=('Bearer '+jwt) if jwt else None,method='GET');assert status in (401,403)
        denied_requests=0
        public_jwt=token({'role':'anon','exp':int(time.time())+3600},secret)
        for jwt in (None,public_jwt,anon_key):
            headers={'apikey':'synthetic-public-api-key'}
            auth='Bearer '+jwt if jwt else None
            for table in ('community_members','community_recommendations','community_audit','community_rate_buckets','community_submission_requests'):
                for method,body in (('GET',None),('POST',{}),('PATCH',{}),('DELETE',None)):
                    status,_=request(rest+'/'+table,body,auth=auth,headers=headers,method=method)
                    assert status in (401,403),(table,method,status)
                    denied_requests+=1
            for name,body in (
                ('community_quota',{'p_member':seed}),('community_gallery',{}),
                ('community_take_rate',{'p_key':'forged-client'}),
                ('community_submit',{'p_member':seed,'p_name':'Spoof','p_image':seed+20000,'p_creator':seed+20001}),
                ('community_submit_once',{'p_member':seed,'p_name':'Spoof','p_image':seed+20000,'p_creator':seed+20001,'p_key':'forged-client-key'}),
                ('community_withdraw',{'p_member':seed,'p_image':seed+20000}),
                ('community_moderate',{'p_image':seed+20000,'p_status':'hidden'})):
                status,_=request(rest+'/rpc/'+name,body,auth=auth,headers=headers)
                assert status in (401,403,404),(name,status)
                denied_requests+=1
        scenarios.append({'scenario':'public-client-all-table-and-rpc-denials','requests':denied_requests,'persisted':0})
        lost={'apiVersion':1,'operation':'recommend','artwork':seed+29000,'idempotencyKey':f'http-lost-reply-{seed}'}
        before_lost=counts()
        status,_=request(edge,lost,auth='Bearer '+member(990),headers={'X-CAD-Test-Lost-Reply':'1'})
        assert status==503
        committed=counts();assert committed['recommendations']==before_lost['recommendations']+1
        status,retry=request(edge,lost,auth='Bearer '+member(990))
        assert status==200 and retry.get('replayed') is True and counts()==committed
        scenarios.append({'scenario':'committed-but-response-lost-retry','persisted':1,'retryAddedRecords':0})
        # Actual PostgREST role switch + RLS, rather than only SQL ACL rejection.
        sql('grant select,insert on community_recommendations to authenticated; grant usage on sequence community_recommendations_seq_seq to authenticated;')
        try:
            status,value=request(rest+'/community_recommendations',auth='Bearer '+anon_key,method='GET');assert status==200 and value==[]
            status,_=request(rest+'/community_recommendations',{'image_id':seed+10000,'creator_id':seed+10001,'member_id':seed,'week':'2026-10-19'},auth='Bearer '+anon_key)
            assert status==403
        finally:sql('revoke all on community_recommendations from authenticated; revoke all on sequence community_recommendations_seq_seq from authenticated;')
        for case,code in [('missing','INVALID_ARTWORK'),('video','INVALID_ARTWORK'),('unavailable','UNAVAILABLE'),('limited','RATE_LIMITED')]:
            before=counts();fault=case;assert call('recommend',artwork=seed+10000)[1]['code']==code;fault='';assert counts()==before
        # A partial body must not occupy a worker indefinitely. No upstream calls.
        before=counts();started=time.monotonic();connection=http.client.HTTPConnection('127.0.0.1',edge_port,timeout=8)
        try:
            connection.putrequest('POST','/');connection.putheader('Content-Type','application/json')
            connection.putheader('Content-Length','100');connection.putheader('Authorization','Bearer '+member(500))
            connection.endheaders();connection.send(b'{');response=connection.getresponse()
            assert response.status==503;response.read()
        finally:connection.close()
        elapsed=time.monotonic()-started;assert 4.5<=elapsed<8 and counts()==before
        scenarios.append({'scenario':'slow-body-deadline','seconds':round(elapsed,2),'status':503,'persisted':0})
        # Saturate only localhost UserInfo, not Civitai. Production admission cap 16.
        fault='slow';time.sleep(1.05)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
                responses=list(pool.map(lambda n:call(n=600+n),range(24)))
            assert any(s==503 for s,_ in responses) and any(s==200 for s,_ in responses),responses
            assert all(s in (200,503) for s,_ in responses)
            scenarios.append({'scenario':'concurrent-admission','sent':24,'accepted':sum(s==200 for s,_ in responses),
                'rejected':sum(s==503 for s,_ in responses),'persisted':0})
        finally:fault=''
        creators[seed+10000]=seed
        assert call('recommend',artwork=seed+10000)[1]['code']=='SELF_RECOMMENDATION'
        creators.pop(seed+10000)
        # Every transaction below enters through real Deno HTTP and PostgREST.
        for trial in range(3):
            time.sleep(1.05)
            n=10+trial;image=seed+20000+trial*100
            for offset in (0,1):assert call('recommend',n=n,artwork=image+offset)[0]==200
            scenario('one-member-slot',[{'n':n,'artwork':image+2+j} for j in range(2)],1,'MEMBER_QUOTA')
            artist=seed+80000+trial
            for j in range(8):creators[image+10+j]=artist
            for j in (0,1):assert call('recommend',n=30+trial*10+j,artwork=image+10+j)[0]==200
            scenario('one-artist-slot',[{'n':30+trial*10+2+j,'artwork':image+12+j} for j in range(6)],1,'ARTIST_QUOTA')
            scenario('duplicate',[{'n':100+trial*10+j,'artwork':image+30} for j in range(6)],1,'DUPLICATE')
            scenario('same-key-replay',[{'n':150+trial,'artwork':image+40,'idempotencyKey':f'http-replay-{seed}-{trial}'}]*6,6,'',5)
            assert call('recommend',n=150+trial,artwork=image+41,idempotencyKey=f'http-replay-{seed}-{trial}')[1]['code']=='INVALID_REQUEST'
        time.sleep(1.05)
        for n in range(10):
            for slot in range(3):assert call('recommend',n=n,artwork=seed+40000+n*10+slot)[0]==200
            assert call('recommend',n=n,artwork=seed+40000+n*10+3)[1]['code']=='MEMBER_QUOTA'
        # True transaction failure in the audit insert, observed through HTTP.
        fail_image=seed+50000
        sql(f"create function public.http_failure() returns trigger language plpgsql as $$ begin if new.image_id={fail_image} then raise exception 'PRIVATE_SQL_FAILURE'; end if; return new; end $$; create trigger http_failure before insert on community_audit for each row execute function public.http_failure();")
        before=counts();status,value=call('recommend',n=200,artwork=fail_image,idempotencyKey=f'http-failure-{seed}')
        assert status==503 and value['code']=='UNAVAILABLE' and counts()==before
        assert 'PRIVATE_SQL_FAILURE' not in json.dumps(value)
        sql('drop trigger http_failure on community_audit; drop function public.http_failure();')
        assert call('recommend',n=200,artwork=fail_image,idempotencyKey=f'http-failure-{seed}')[0]==200
        # Pool pressure: lock the member so concurrent HTTP calls queue in PostgREST.
        holder=subprocess.Popen(['psql','-X','-q'],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env,text=True,creationflags=flags)
        try:
            holder.stdin.write(f"begin; select pg_advisory_xact_lock(hashtextextended('community-member:{seed+250}',0));\n");holder.stdin.flush();time.sleep(.15)
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                pending=[pool.submit(call,'recommend',250,artwork=seed+51000+j) for j in range(10)]
                time.sleep(1.4);holder.stdin.write('rollback;\n\\q\n');holder.stdin.flush();holder.communicate(timeout=5)
                results=[p.result() for p in pending]
            assert all(s in (200,409,503) for s,_ in results),results
            assert sum(s==200 for s,_ in results)<=3
            scenarios.append({'scenario':'pool-pressure','sent':10,'responses':{str(s):sum(x==s for x,_ in results) for s,_ in results}})
        finally:
            if holder.poll() is None:holder.terminate();holder.wait()
        # Local IP ingress ceiling is tested with forged forwarding headers.
        time.sleep(1.05)
        results=[request(edge,{},headers={'X-Forwarded-For':f'192.0.2.{n%250}'},method='POST') for n in range(205)]
        assert any(s==429 for s,_ in results)
        time.sleep(1.05);assert call(n=300)[0]==200
        # Per-member counters are separate from peer ingress and use real SQL.
        results=[call(n=301) for _ in range(31)];assert results[-1][1]['code']=='RATE_LIMITED'
        # Shared gallery client is exercised by a helper while infrastructure is alive.
        sys.path.insert(0,str(ROOT/'tests'))
        from community_http_gallery import run_gallery
        (output/'progress.json').write_text(json.dumps({'stage':'Shared gallery browser'}))
        def restart_gallery_service():
            global edge_process
            edge_process=start_edge()
            return edge_process
        gallery=run_gallery(edge,member,seed,creators,sql,scratch,output,lambda:edge_process,restart_gallery_service)
        live_result=None
        if env.get('CAD_TEST_LIVE_READONLY')=='1':
            processes[-1].terminate();processes[-1].wait(timeout=10)
            live_process=start_edge(live=True);wait(edge,live_process)
            before_live=sql('select count(*) from community_recommendations;')
            probe=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/probe-community-readonly.py'),
                '--url',edge,'--output',str(output/'live-handler.json')],env=env,capture_output=True,text=True,timeout=90,creationflags=flags)
            assert probe.returncode==0,'Read-only live probe failed; token/details withheld'
            live_result=json.loads((output/'live-handler.json').read_text())
            assert sql('select count(*) from community_recommendations;')==before_live
            # Restore the synthetic service for interruption/fault acceptance.
            live_process.terminate();live_process.wait(timeout=10);restored_edge=start_edge();wait(edge,restored_edge)
        # Real PostgreSQL interruption while the HTTP process remains alive.
        pg_bin=Path(env['CAD_TEST_DENO']).parent # pg_ctl resolves from the owned runner PATH
        before=counts()
        stopped=subprocess.run(['pg_ctl','-D',env['CAD_TEST_PG_DATA_DIR'],'-m','fast','-w','stop'],env=env,capture_output=True,text=True,timeout=20,creationflags=flags)
        assert stopped.returncode==0
        status,value=call(n=400);assert status==503 and value['code']=='UNAVAILABLE'
        with (output/'postgres-restart.log').open('w') as stream:
            restarted=subprocess.run(['pg_ctl','-D',env['CAD_TEST_PG_DATA_DIR'],'-l',str(scratch/'postgres-restarted.log'),'-w','start'],env=env,stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,timeout=30,creationflags=flags)
        assert restarted.returncode==0
        for attempt in range(50):
            if call(n=401+attempt)[0]==200:break
            time.sleep(.2)
        else:raise AssertionError('PostgREST did not reconnect after PostgreSQL restart')
        assert counts()==before
        # Actual owned-database dump/restore drill, never a persistent profile.
        backup=scratch/'community-backup.dump';restore_db='cad3_restore_'+secrets.token_hex(6)
        subprocess.run(['pg_dump','-Fc','-f',str(backup)],env=env,capture_output=True,check=True,timeout=30,creationflags=flags)
        sql('create database '+restore_db+';')
        subprocess.run(['pg_restore','--exit-on-error','-d',restore_db,str(backup)],env=env,capture_output=True,check=True,timeout=30,creationflags=flags)
        restored=subprocess.run(['psql','-X','-A','-t','-c','select count(*) from community_recommendations;'],env={**env,'PGDATABASE':restore_db},capture_output=True,text=True,check=True,timeout=10,creationflags=flags)
        assert restored.stdout.strip()==sql('select count(*) from community_recommendations;')
        # Addendum: real public fixture metadata and mapped local member identities.
        http_counts=counts()
        lab_path=ROOT/'dev/community-test-lab'
        real_lab=None
        if lab_path.is_dir():
            # Phase isolation: this verified owned database is disposable. Do not
            # make the real-artwork UI page through synthetic failure-case rows.
            sql('begin; delete from community_submission_requests; delete from community_audit; delete from community_recommendations; delete from community_members; delete from community_rate_buckets; commit;')
            (output/'progress.json').write_text(json.dumps({'stage':'Real public metadata ownership tests'}))
            sys.path.insert(0,str(lab_path))
            import lab as lab_module
            catalog=lab_module.fixtures()
            public_artworks={a['rest']['id']:a for m in catalog['members'] for a in m['artworks']}
            public_members={m['creator']['id']:dict(m['creator']) for m in catalog['members']}
            def mapped(index):
                return token({'sub':str(catalog['members'][index]['creator']['id']),'exp':int(time.time())+900,
                    'iss':'local-test-only','aud':'cad3-local-lab','simulated':True},issuer_secret)
            public_active=True
            for claims in ({'sub':str(catalog['members'][0]['creator']['id']),'aud':'wrong','simulated':True},
                           {'sub':'1','aud':'cad3-local-lab','simulated':True},
                           {'sub':str(catalog['members'][0]['creator']['id']),'aud':'cad3-local-lab','simulated':False}):
                claims.update(exp=int(time.time())+900,iss='local-test-only')
                assert request(edge,{'apiVersion':1,'operation':'status'},auth='Bearer '+token(claims,issuer_secret))[0]==401
            def rename(index,name):public_members[catalog['members'][index]['creator']['id']]['username']=name
            lab=lab_module.Lab(edge,mapped,sql,scratch,output,rename=rename).start()
            manual_ready=False
            try:
                real_lab=lab_module.exercise(lab)
                (output/'progress.json').write_text(json.dumps({'stage':'Real metadata isolated member browsers'}))
                real_lab['browser']=lab_module.exercise_browser(lab)
                if env.get('CAD_TEST_MANUAL_LAB')=='1':
                    # Fresh profiles/caches for manual use after acceptance exhausts quotas.
                    lab.close();lab=lab_module.Lab(edge,mapped,sql,scratch/'manual',output,rename=rename).start()
                    manual_ready=True
            finally:
                if not manual_ready:lab.close();public_active=False
        # Persisted records always have a matching accepted audit; no duplicates/overcaps.
        sql(f"do $$ begin assert not exists(select 1 from community_recommendations r left join community_audit a on a.image_id=r.image_id and a.action='accepted' where member_id between {seed} and {seed+999} group by r.image_id having count(a.seq)<>1); assert not exists(select 1 from community_recommendations where member_id between {seed} and {seed+999} group by member_id,week having count(*)>3); end $$;")
        beta_result=None
        if env.get('CAD_TEST_BETA_ACCEPTANCE')=='1':
            from community_private_beta import run as run_beta
            def beta_restart(state):
                global edge_process
                edge_process.terminate();edge_process.wait(timeout=10)
                edge_process=start_edge(state=state,private_beta=True);wait(edge,edge_process)
            def beta_fault(value):
                global fault
                fault=value
            def second_instance():
                second_port=port();process=start_edge(private_beta=True,port_override=second_port)
                url=f'http://127.0.0.1:{second_port}';wait(url,process);return url
            beta_result=run_beta(second_instance=second_instance,legacy_probe=legacy_probe,restored_probe=restored_probe,release_probe=release_probe,sql=sql,call=call,request=request,edge=edge,member=member,
                restart=beta_restart,set_fault=beta_fault,env=env,output=output,seed=seed)
        summary={'passed':True,'runtime':'Deno','realPostgREST':True,'realPostgreSQL':True,
            'syntheticSignedUserInfo':True,'syntheticArtworkMetadata':True,'scenarios':scenarios,'counts':http_counts,
            'httpResponsesRecorded':len(received),'gallery':gallery,'postgresInterruptedAndRecovered':True,
            'dumpRestoreCompared':True,'httpRlsAfterTemporaryGrant':True,'liveHandler':live_result,
            'realFixtureLab':real_lab,'privateBeta':beta_result,'deployedSupabase':False}
        (output/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary),flush=True)
        if env.get('CAD_TEST_MANUAL_LAB')=='1':
            print('Removable manual Test Lab ready; private connection file: '+str(lab.state),flush=True)
            try:
                while not lab.stop_event.wait(1):pass
            finally:lab.close()
    finally:
        if 'lab' in globals():lab.close()
        issuer.shutdown();issuer.server_close()
        for process in reversed(processes):
            if process.poll() is None:process.terminate();process.wait(timeout=10)
        for handle in handles:handle.close()
