"""Real Deno identity handler -> PostgREST -> owned PostgreSQL. Synthetic sessions."""
import base64,hashlib,hmac,json,os,secrets,socket,subprocess,time,urllib.request,urllib.error
from pathlib import Path


def run(sql,env,deno,postgrest,output):
    root=Path(__file__).resolve().parents[1]
    def port():
        with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
    rest_port,edge_port=port(),port();rest=f'http://127.0.0.1:{rest_port}';edge=f'http://127.0.0.1:{edge_port}'
    secret=secrets.token_urlsafe(48)
    def b64(value):return base64.urlsafe_b64encode(value).decode().rstrip('=')
    def jwt(role):
        value=b64(b'{"alg":"HS256","typ":"JWT"}')+'.'+b64(json.dumps({'role':role,'exp':int(time.time())+3600}).encode())
        return value+'.'+b64(hmac.new(secret.encode(),value.encode(),hashlib.sha256).digest())
    def request(url,body=None,auth=None,method=None):
        headers={'Content-Type':'application/json'}
        if auth:headers['Authorization']='Bearer '+auth
        req=urllib.request.Request(url,data=None if body is None else json.dumps(body).encode(),headers=headers,method=method)
        try:
            with urllib.request.urlopen(req,timeout=15) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:
            data=e.read()
            try:return e.code,json.loads(data)
            except ValueError:return e.code,{}
    owner='sdc_'+'x'*43;other='sdc_'+'y'*43
    sql("""update community_private.configuration set ready=true;
      insert into community_members values(7100001,'SyntheticOwner');
      insert into community_private.invitations values(7100001,clock_timestamp()+interval '1 day',false);
      insert into community_recommendations(image_id,creator_id,member_id,submitted_at,week) select i,8100000+i,7100001,clock_timestamp(),date_trunc('week',clock_timestamp() at time zone 'UTC')::date from generate_series(1,125)i;
    """+''.join("insert into community_identity_sessions(session_hash,civitai_id,username,expires_at) values('"+hashlib.sha256(token.encode()).hexdigest()+f"',{member},'Synthetic',clock_timestamp()+interval '1 hour');" for token,member in ((owner,7100001),(other,7100002))))
    baseline=sql("select md5(jsonb_agg(to_jsonb(r) order by seq)::text) from community_recommendations r;")
    plans=[json.loads(sql("explain (analyze,format json) select community_recommendation_list(7100001,null,50);"))[0]
           for _ in range(3)]
    (output/'accepted-feed-query-plans.json').write_text(json.dumps(plans,indent=2))
    procs=[];streams=[]
    def launch(name,args,extra):
        stream=(output/(name+'.log')).open('w',encoding='utf-8');streams.append(stream)
        child={**env,**extra};child.pop('SUPABASE_SECRET_KEYS',None)
        p=subprocess.Popen(args,cwd=root,env=child,stdout=stream,stderr=stream,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0);procs.append(p);return p
    def wait(url,p):
        for _ in range(100):
            if p.poll() is not None:raise RuntimeError('Owned integration service exited')
            try:request(url);return
            except OSError:time.sleep(.1)
        raise RuntimeError('Owned service startup timeout')
    try:
        p=launch('accepted-postgrest',[str(postgrest)],{'PGRST_DB_URI':f"postgresql://{env['PGUSER']}@127.0.0.1:{env['PGPORT']}/{env['PGDATABASE']}",
            'PGRST_DB_SCHEMAS':'public','PGRST_DB_ANON_ROLE':'anon','PGRST_JWT_SECRET':secret,'PGRST_SERVER_HOST':'127.0.0.1','PGRST_SERVER_PORT':str(rest_port)})
        wait(rest,p)
        p=launch('accepted-edge',[str(deno),'run','--no-check','--allow-env','--allow-net=127.0.0.1',str(root/'supabase/tests/accepted_feed_service.ts')],
            {'CAD_ALLOW_ISOLATED_DB_TESTS':'1','CAD_FEED_TEST_REST':rest,'CAD_FEED_TEST_PORT':str(edge_port),
             'SUPABASE_URL':'https://fixture.supabase.co','SUPABASE_SERVICE_ROLE_KEY':jwt('service_role'),
             'COMMUNITY_BETA_STATE':'enabled','COMMUNITY_ALLOWED_RATINGS':'1,2'})
        # Missing modern-key setting must be truly absent, not an invalid empty config.
        # launch() inherits it only if caller supplies it, so remove inherited value above instead.
        wait(edge+'/metrics',p)
        def call(extra=None,token=owner):return request(edge,{'apiVersion':1,'operation':'gallery','limit':50,**(extra or {})},token)
        pages=[];times=[];cursor=None
        while True:
            start=time.perf_counter();code,page=call({'cursor':cursor});times.append((time.perf_counter()-start)*1000)
            assert code==200,(code,page)
            pages.append(page);cursor=page['nextCursor']
            if not cursor:break
        assert [len(p['items']) for p in pages]==[50,50,25]
        assert len({i['imageId'] for p in pages for i in p['items']})==125
        assert call(token=other)[1]['code']=='INVITE_ONLY'
        assert call(token='sdc_'+'z'*43)[0]==401
        assert call({'limit':0})[0]==400
        assert len(call({'limit':24})[1]['items'])==24
        assert call({'filters':{'member':7100001}})[0]==400
        denied=0
        for role in ('anon','authenticated'):
            for table in ('community_recommendations','community_members','community_audit','community_submission_requests',
                          'community_identity_sessions','community_device_families','community_identity_replays'):
                code,_=request(rest+'/'+table,auth=jwt(role));assert code in (401,403),(role,table,code);denied+=1
            for rpc,args in [('community_recommendation_list',{'p_member':7100001}),
                             ('community_submit',{'p_member':7100001,'p_name':'Forged','p_image':900,'p_creator':901}),
                             ('community_prepare_gallery',{'p_member':7100001})]:
                code,_=request(rest+'/rpc/'+rpc,args,jwt(role));assert code in (401,403),(role,rpc,code);denied+=1
        metrics=request(edge+'/metrics')[1];assert metrics['artwork']==0
        assert not {'community_prepare_gallery','community_review_candidates','community_record_review','community_beta_gallery'}&set(metrics['rpcs'])
        assert baseline==sql("select md5(jsonb_agg(to_jsonb(r) order by seq)::text) from community_recommendations r;")
        result={'passed':True,'pageSizes':[50,50,25],'pageHTTPms':times,'httpDenials':denied,
            'databaseQueryExecutionMs':[p['Execution Time'] for p in plans],
            'edgeAuthorizationCases':5,'externalArtworkRequests':0,'nativeDenoPostgRESTPostgreSQL':True,'syntheticIdentity':True,
            'recommendationsUnchanged':True,'hostedRequests':0}
        (output/'accepted-feed-http.json').write_text(json.dumps(result,indent=2));return result
    finally:
        for p in reversed(procs):
            if p.poll() is None:p.terminate();p.wait(timeout=10)
        for s in streams:s.close()
