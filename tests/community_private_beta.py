"""Candidate acceptance helper; only invoked inside the owned native HTTP runner."""
import concurrent.futures
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys


def run(*,second_instance,legacy_probe,restored_probe,release_probe,sql,call,request,edge,member,restart,set_fault,env,output,seed):
    assert env.get('CAD_TEST_PG_SYSTEM_IDENTIFIER')==sql('select system_identifier from pg_control_system();')
    assert Path(env['CAD_TEST_PG_DATA_DIR']).resolve()==Path(sql('show data_directory;')).resolve()
    root=Path(__file__).resolve().parents[1]
    # A fresh identity namespace prevents preceding HTTP quota/rate fixtures from
    # consuming this suite's invitation/report budgets. No counter reset is used.
    base_call,base_member=call,member
    seed+=2000000
    traffic=[]
    def call(operation='status',n=0,**fields):
        response=base_call(operation,n=n+2000000,**fields)
        traffic.append({'operation':operation,'status':response[0],'code':response[1].get('code')})
        (output/'private-beta-http.json').write_text(json.dumps(traffic,indent=2))
        return response
    def member(n):return base_member(n+2000000)
    checks=[]
    def check(name,value):
        checks.append({'name':name,'passed':bool(value)})
        (output/'private-beta.json').write_text(json.dumps({'checks':checks,'completed':False,'passed':False},indent=2))
        assert value,name
    def snapshot():
        return sql("select jsonb_object_agg(n,v) from ("+' union all '.join(
            "select '"+n+"' n,jsonb_build_object('count',count(*),'hash',md5(coalesce(jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text)::text,'[]'))) v from public."+n+" t"
            for n in ['community_members','community_recommendations','community_audit','community_submission_requests'])+") q;")
    rehearsal_before=None
    if env.get('CAD_TEST_REHEARSAL')=='1':
        from community_deployment_rehearsal import prepare
        rehearsal_before=prepare(sql,env,output)
    before=snapshot()
    candidate=root/'supabase/beta-migrations/202610230001_private_beta_security.sql'
    history_record="insert into supabase_migrations.schema_migrations values('202610230001','private_beta_security',array['rehearsed candidate']);" if rehearsal_before else ''
    sql(rehearsal_before['retryBundle'] if rehearsal_before else 'begin;'+candidate.read_text()+history_record+'commit;')
    check('migration preserves existing ledger hashes',before==snapshot())
    # Hold earlier baseline fixtures out of this targeted revalidation exercise.
    sql("update community_private.artwork_reviews set lease_until=clock_timestamp()+interval '1 day';")
    sql("notify pgrst, 'reload schema';")
    password=secrets.token_urlsafe(32)
    sql("create role cad3_beta_operator login noinherit password '"+password+"'; grant community_operator to cad3_beta_operator; grant connect on database "+env['PGDATABASE']+" to cad3_beta_operator;")
    hba=Path(env['CAD_TEST_PG_DATA_DIR'])/'pg_hba.conf'
    hba.write_text(f"host {env['PGDATABASE']} cad3_beta_operator 127.0.0.1/32 scram-sha-256\n"+hba.read_text())
    sql('select pg_reload_conf();')
    sql("insert into community_private.operators values('cad3_beta_operator',9001,clock_timestamp()+interval '1 day',false);")
    operator_env={**env,'PGUSER':'cad3_beta_operator','PGPASSWORD':password}
    def op(action,target=None,reason='Synthetic reviewed action',until=None,ok=True):
        def literal(value):return 'null' if value is None else "'"+str(value).replace("'","''")+"'"
        stmt='set role community_operator;select public.community_operator('+','.join(literal(v) for v in (action,target,reason,until))+');'
        p=subprocess.run(['psql','-X','-qAt','-v','ON_ERROR_STOP=1'],input=stmt,env=operator_env,capture_output=True,text=True,timeout=15)
        if not ok:return p.returncode!=0
        if p.returncode:(output/'operator-error.log').write_text(p.stderr.replace(password,'[redacted]'))
        assert p.returncode==0,'Operator failed (see sanitized isolated diagnostic)'
        return json.loads(p.stdout.strip())
    import time
    time.sleep(.3)
    restart('enabled')
    check('missing invitations denied',call()[1].get('code')=='FORBIDDEN')
    op('invite',seed,until='infinity',ok=False)
    check('malformed invitation rejected',sql('select count(*) from community_private.invitations;')=='0')
    op('invite',seed,until='2099-01-01T00:00:00Z',ok=False)
    check('excessive expiry rejected',sql('select count(*) from community_private.invitations;')=='0')
    until=sql("select (clock_timestamp()+interval '1 day')::text;")
    for n in range(5):op('invite',seed+n,until=until)
    check('owner consumes one of five slots',op('invite',seed+5,until=until,ok=False))
    check('unconfigured service fails closed',call()[1].get('code')=='FORBIDDEN')
    op('configure')
    sql(f"insert into community_private.invitations values({seed+99},clock_timestamp()+interval '1 day',false);")
    check('malformed oversized configuration fails closed',call()[1].get('code')=='FORBIDDEN')
    sql(f'delete from community_private.invitations where member_id={seed+99};')
    check('invited identity admitted' ,call()[0]==200)
    check('non invited denied',call(n=5)[1].get('code')=='FORBIDDEN')
    for fields in ({'memberId':seed},{'moderatorId':9001},{'invitationOnly':False},{'role':'community_operator'}):
        check('spoofed identity fields denied '+next(iter(fields)),call('status',**fields)[1].get('code')=='INVALID_REQUEST')
    r=request(edge+'?memberId='+str(seed),{'apiVersion':1,'operation':'status'},auth='Bearer '+member(5),headers={'X-Member-Id':str(seed),'X-Moderator-Id':'9001'})
    check('headers and query cannot grant invitation',r[1].get('code')=='FORBIDDEN')
    for action in ('reports','audit','block','invite','moderate'):
        check('ordinary HTTP moderator operation denied '+action,call(action)[0]!=200)
    restricted={**env,'PGUSER':'service_role','PGPASSWORD':json.loads(env['CAD_TEST_PG_ROLE_PASSWORDS'])['service_role']}
    for expression in ["public.community_operator('reports')", "public.community_moderate(1,'hidden')", "public.community_submit_legacy(1,'x',2,3,false)","public.community_gallery(null,24)","public.community_submit_once_legacy(1,'x',2,3,'aaaaaaaaaaaaaaaa')"]:
        p=subprocess.run(['psql','-X','-qAt','-v','ON_ERROR_STOP=1','-c','select '+expression],env=restricted,capture_output=True,text=True)
        check('service cannot invoke restricted '+expression.split('(')[0],p.returncode!=0)
    for role in ('anon','authenticated','service_role','cad3_beta_operator'):
        check('private tables inaccessible '+role,sql("select not has_table_privilege('"+role+"','community_private.reports','select') and not has_table_privilege('"+role+"','community_private.moderator_audit','insert');")=='t')
    check('all public RPCs deny untrusted roles',sql("select not exists(select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and p.proname like 'community_%' and (has_function_privilege('anon',p.oid,'execute') or has_function_privilege('authenticated',p.oid,'execute')));")=='t')
    cli=subprocess.run([sys.executable,'-B',str(root/'scripts/community-operator.py'),'reports'],env=operator_env,capture_output=True,text=True,timeout=20)
    check('restricted operator CLI executes bounded queue workflow',cli.returncode==0 and isinstance(json.loads(cli.stdout),list))
    cli=subprocess.run([sys.executable,'-B',str(root/'scripts/community-operator.py'),'reports'],env=restricted,capture_output=True,text=True,timeout=20)
    check('operator CLI rejects service credential identity',cli.returncode!=0)
    check('operator cannot invent moderator actor',op('block',seed+99,reason='',ok=False))
    image=seed+80001
    check('first recommendation accepted',call('recommend',artwork=image,idempotencyKey='beta-isolated-key-00001')[0]==200)
    check('successful replay',call('recommend',artwork=image,idempotencyKey='beta-isolated-key-00001')[1].get('replayed') is True)
    ledger=snapshot()
    op('block',seed)
    for operation in ('verify','recommend'):
        check('blocked '+operation,call(operation,artwork=image+2)[1].get('code')=='SUBMITTER_BLOCKED')
    check('blocked replay denied',call('recommend',artwork=image,idempotencyKey='beta-isolated-key-00001')[1].get('code')=='SUBMITTER_BLOCKED')
    check('block preserves ledger and quota',ledger==snapshot())
    second=second_instance()
    check('simultaneous second instance enforces block',request(second,{'apiVersion':1,'operation':'recommend','artwork':image+2},auth='Bearer '+member(0))[1].get('code')=='SUBMITTER_BLOCKED')
    check('blocked historical recommendations hidden',image not in [x['imageId'] for x in call('gallery',n=1)[1].get('items',[])])
    restart('enabled')
    check('block persists across instance restart',call('verify',artwork=image+2)[1].get('code')=='SUBMITTER_BLOCKED')
    op('unblock',seed)
    check('audited unblock restores eligibility',call('verify',artwork=image+2)[0]==200)
    check('report existing artwork',call('report',n=1,artwork=image,reason='abuse')[0]==200)
    for _ in range(3):check('duplicate report idempotent',call('report',n=1,artwork=image,reason='ownership')[1].get('duplicate') is True)
    check('one minimal report row',sql(f'select count(*) from community_private.reports where image_id={image};')=='1')
    check('reports do not moderate automatically',sql(f"select status from community_recommendations where image_id={image};")=='active')
    check('unknown report reason rejected',call('report',n=1,artwork=image,reason='free text')[1].get('code')=='INVALID_REQUEST')
    check('nonexistent target rejected',call('report',n=1,artwork=seed+99999,reason='abuse')[1].get('code')=='INVALID_ARTWORK')
    reports=op('reports');report=next(x for x in reports if x['image_id']==image)
    op('resolve_report',report['seq'])
    check('report decision audit',sql("select count(*)>0 from community_private.moderator_audit where action='resolve_report' and actor_id=9001 and actor_login='cad3_beta_operator';")=='t')
    op('hide',image)
    check('moderation preserves recommendation',sql(f"select status from community_recommendations where image_id={image};")=='hidden')
    check('legacy and actor reason audit pair',sql(f"select exists(select 1 from community_audit where image_id={image} and action='hidden') and exists(select 1 from community_private.moderator_audit where target={image} and action='hide' and length(reason)>0);")=='t')
    # Failed audit insertion rolls back the action in the same transaction.
    sql("create function community_private.fail_audit() returns trigger language plpgsql as $$ begin raise exception 'Synthetic audit outage';end $$; create trigger beta_audit_failure before insert on community_private.moderator_audit for each row execute function community_private.fail_audit();")
    check('audit failure rejects blocking transaction',op('block',seed+99,ok=False))
    check('audit failure leaves no block',sql(f'select count(*) from community_private.blocks where member_id={seed+99};')=='0')
    sql('drop trigger beta_audit_failure on community_private.moderator_audit;drop function community_private.fail_audit();')
    # Central revocation/expiry, including retained replay keys.
    op('revoke_invite',seed)
    check('revoked status denied',call()[1].get('code')=='FORBIDDEN')
    check('second instance enforces invitation revocation',request(second,{'apiVersion':1,'operation':'status'},auth='Bearer '+member(0))[1].get('code')=='FORBIDDEN')
    check('revoked replay denied',call('recommend',artwork=image,idempotencyKey='beta-isolated-key-00001')[1].get('code')=='FORBIDDEN')
    op('invite',seed,until=until)
    sql(f"update community_private.invitations set expires_at=clock_timestamp()-interval '1 second' where member_id={seed};")
    check('expired invitation denied',call()[1].get('code')=='FORBIDDEN')
    op('invite',seed,until=until)
    # Ordinary reports remain available read-only; no submissions or withdrawals.
    restart('read_only')
    check('read only reporting advertised',call()[1].get('beta',{}).get('reporting') is True)
    check('read only report accepted',call('report',n=2,artwork=image,reason='unavailable')[0]==200)
    for operation in ('verify','recommend','withdraw'):
        check('read only denies '+operation,call(operation,artwork=image)[1].get('code')=='READ_ONLY')
    restart('disabled');before_disabled=sql('select count(*) from community_private.reports;')
    check('disabled reports denied',call('report',n=2,artwork=image,reason='abuse')[1].get('code')=='BETA_DISABLED')
    check('disabled report has no record',before_disabled==sql('select count(*) from community_private.reports;'))
    restart('enabled')
    # Use an untouched invited member to race its last available weekly slots.
    def recommend(j):return call('recommend',n=3,artwork=seed+81001+2*j,idempotencyKey='beta-race-key-'+str(j).zfill(8))
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:results=list(pool.map(recommend,range(5)))
    check('concurrent quota exactly three accepted',sum(s==200 for s,_ in results)==3)
    check('concurrent rejected requests preserve audit pairing',sql(f"select (select count(*) from community_recommendations where member_id={seed+3})=3 and (select count(*) from community_submission_requests where member_id={seed+3})=3;")=='t')
    # Stale metadata is quarantined until bounded revalidation succeeds.
    fresh=seed+82001
    check('artwork accepted before metadata change',call('recommend',n=4,artwork=fresh)[0]==200)
    def stale():sql(f"update community_private.artwork_reviews set valid_until=clock_timestamp()-interval '1 second',lease_until=null where image_id={fresh};")
    def state():return sql(f"select state from community_private.artwork_reviews where image_id={fresh};")
    for fault in ('missing','private','video','unavailable','rating','changed_owner'):
        stale();set_fault(fault);response=call('gallery',n=4)
        check('bounded unavailable revalidation '+fault,response[0]==200 and state()=='quarantined' and fresh not in [x['imageId'] for x in response[1].get('items',[])])
    set_fault('');stale()
    check('eligible metadata recovers',call('gallery',n=4)[0]==200 and state()=='eligible')
    stale()
    leased=json.loads(sql(f'select public.community_review_candidates({seed+4});'))
    check('revalidation batch bounded',len(leased)<=3 and any(x['imageId']==fresh for x in leased))
    check('shared lease prevents second worker',json.loads(sql(f'select public.community_review_candidates({seed+4});'))==[])
    previous=next(x for x in leased if x['imageId']==fresh)
    sql(f"update community_private.artwork_reviews set lease_until=clock_timestamp()-interval '1 second' where image_id={fresh};")
    renewed=next(x for x in json.loads(sql(f'select public.community_review_candidates({seed+4});')) if x['imageId']==fresh)
    stale_sql=f"select public.community_record_review({seed+4},{fresh},{fresh+100000},'{previous['lease']}',null);"
    denied=subprocess.run(['psql','-X','-qAt','-v','ON_ERROR_STOP=1'],input=stale_sql,env=env,capture_output=True,text=True)
    check('expired worker nonce cannot overwrite newer review',denied.returncode!=0)
    sql(f"select public.community_record_review({seed+4},{fresh},{fresh+100000},'{renewed['lease']}',null);")
    # Daily report budget survives process restart; duplicate rows do not spend it.
    # Explicit historical fixtures avoid depending on how many records the
    # preceding lab retained. One per distinct member/artist preserves quota shape.
    targets=[]
    for i in range(11):
        target=seed+90000+i;fixture_member=seed+300+i;targets.append(target)
        sql(f"insert into community_members values({fixture_member},'SyntheticReportTarget');insert into community_recommendations(image_id,creator_id,member_id,week) values({target},{seed+91000+i},{fixture_member},date_trunc('week',clock_timestamp() at time zone 'UTC')::date);insert into community_audit(image_id,action) values({target},'accepted');")
    for target in targets[:9]:
        check('bounded report budget accepts first ten',call('report',n=2,artwork=target,reason='abuse')[0]==200)
    check('daily report budget rejects eleventh',call('report',n=2,artwork=targets[9],reason='abuse')[1].get('code')=='RATE_LIMITED')
    restart('enabled')
    check('report budget persists across instances',call('report',n=2,artwork=targets[10],reason='abuse')[1].get('code')=='RATE_LIMITED')
    # Candidate configuration, reports and blocks survive an actual database restart.
    durable=snapshot()
    stopped=subprocess.run(['pg_ctl','-D',env['CAD_TEST_PG_DATA_DIR'],'-m','fast','-w','stop'],env=env,capture_output=True,text=True,timeout=20)
    assert stopped.returncode==0
    try:check('candidate database outage fails closed',call('report',n=4,artwork=fresh,reason='abuse')[1].get('code')=='UNAVAILABLE')
    finally:
        with (output/'beta-postgres-restart.log').open('w') as stream:
            started=subprocess.run(['pg_ctl','-D',env['CAD_TEST_PG_DATA_DIR'],'-l',str(output/'beta-postgres.log'),'-w','start'],env=env,stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,timeout=30)
        assert started.returncode==0
    for _ in range(50):
        if call(n=4)[0]==200:break
        time.sleep(.2)
    else:raise AssertionError('Candidate service did not reconnect')
    check('candidate database recovery preserves ledger',snapshot()==durable)
    # Operator identity is bound to actual login, not SET ROLE/GUC values.
    sql("update community_private.operators set revoked=true where login='cad3_beta_operator';")
    check('revoked operator denied',op('reports',ok=False))
    sql("update community_private.operators set revoked=false where login='cad3_beta_operator';")
    check('all operator actions have authenticated actor/reason',sql("select not exists(select 1 from community_private.moderator_audit where actor_id<>9001 or actor_login<>'cad3_beta_operator' or length(reason)<3);")=='t')
    rehearsal=None
    if rehearsal_before:
        from community_deployment_rehearsal import finish
        rehearsal=finish(sql=sql,env=env,output=output,before=rehearsal_before,call=call,member=member,request=request,edge=edge,restart=restart,legacy_probe=legacy_probe,restored_probe=restored_probe,seed=seed)
    release=release_probe() if env.get('CAD_TEST_RELEASE')=='1' else None
    result={'releaseCandidate':release,'deploymentRehearsal':rehearsal,'passed':True,'checks':len(checks),'simulatedCivitaiIdentity':True,'realPostgreSQL':True,'realPostgREST':True,'realDeno':True,'migrationPreservedLedger':True}
    (output/'private-beta.json').write_text(json.dumps({'passed':True,'completed':True,'checks':checks},indent=2))
    (output/'private-beta-summary.json').write_text(json.dumps(result,indent=2))
    return result


if __name__=='__main__':print('SKIP: invoked only by owned native runner --private-beta')
