"""Release migrations and actual signed PostgREST role attacks; Auth fixtures only."""
import json,time,secrets,subprocess,importlib.util,hashlib
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from discovery.community_catalog import validate_catalog
from discovery.secure_backup import transform

def backup_release(*,sql,env,output,restore_http):
    from community_deployment_rehearsal import command,catalog,history
    tables=json.loads(sql("select jsonb_agg(format('%I.%I',n.nspname,c.relname) order by n.nspname,c.relname) from pg_class c join pg_namespace n on n.oid=c.relnamespace where c.relkind='r' and (n.nspname='community_private' or (n.nspname='public' and c.relname like 'community_%') or (n.nspname='auth' and c.relname='users'));"))
    assert len(tables)==15,'Fourteen application tables plus isolated Auth fixture required'
    def state(execute):return {table:json.loads(execute("select jsonb_build_object('count',count(*),'md5',md5(coalesce(jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text)::text,'[]'))) from "+table+' t;')) for table in tables}
    before=state(sql);matrix=catalog(sql);versions=history(sql)
    dump=subprocess.run(['pg_dump','-Fc'],env=env,capture_output=True,timeout=45);assert dump.returncode==0
    encrypted=transform(dump.stdout);path=output/'release-backup.dpapi';path.write_bytes(encrypted)
    plain=transform(path.read_bytes(),decrypt=True);assert hashlib.sha256(plain).digest()==hashlib.sha256(dump.stdout).digest()
    name='cad3_release_restore_'+secrets.token_hex(5);sql('create database '+name+';revoke connect on database '+name+' from public;')
    child={**env,'PGDATABASE':name}
    restored=subprocess.run(['pg_restore','--single-transaction','--exit-on-error','-d',name],env=child,input=plain,capture_output=True,timeout=45);assert restored.returncode==0,'Release restore failed; details withheld'
    execute=lambda stmt:command(child,stmt).stdout.strip()
    after=state(execute);assert before==after and catalog(execute)==matrix and history(execute)==versions
    restored_http=restore_http(name);assert restored_http['status']==200 and restored_http['recommendCode']=='BETA_DISABLED'
    assert state(execute)['community_private.discovery_users']==before['community_private.discovery_users']
    evidence={'passed':True,'encrypted':True,'archiveSha256':hashlib.sha256(encrypted).hexdigest(),'before':before,'after':after,'migrationHistory':versions,'catalogAndBodiesEqual':True,'http':restored_http,'separateDatabaseSameCluster':True,'hostedAuthRecoveryVerified':False}
    (output/'release-restore.json').write_text(json.dumps(evidence,indent=2));return {'passed':True,'applicationTables':14,'authTables':'isolated fixture only','separateDatabaseSameCluster':True,'hostedAuthRecoveryVerified':False}

AUTH_FIXTURE=("create schema auth;create table auth.users(id uuid primary key);"
    "create function auth.jwt() returns jsonb language sql stable as $$select coalesce(nullif(current_setting('request.jwt.claims',true),'')::jsonb,'{}'::jsonb)$$;"
    "create function auth.uid() returns uuid language sql stable as $$select (auth.jwt()->>'sub')::uuid$$;"
    "create function auth.role() returns text language sql stable as $$select auth.jwt()->>'role'$$;")

def rehearse_atomic(sql,env,output):
    from community_deployment_rehearsal import command,data_snapshot
    name='cad3_release_atomic_'+secrets.token_hex(5);sql('create database '+name+';')
    child={**env,'PGDATABASE':name}
    def execute(stmt):return command(child,stmt).stdout.strip()
    history='create schema supabase_migrations;create table supabase_migrations.schema_migrations(version text primary key,name text,statements text[]);'
    baseline=''
    for path in sorted((ROOT/'supabase/migrations').glob('*.sql')):
        source=path.read_text();version,label=path.stem.split('_',1)
        baseline+=source+"\ninsert into supabase_migrations.schema_migrations values('"+version+"','"+label+"',array[$baseline$"+source+"$baseline$]);\n"
    execute('begin;'+history+baseline+AUTH_FIXTURE+"select community_submit_once(8800000001,'SyntheticHistorical',8800000002,8800000003,'synthetic_atomic_replay');commit;")
    before=data_snapshot(execute)
    spec=importlib.util.spec_from_file_location('release_builder',ROOT/'scripts/prepare-community-candidate.py');builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    bundle=builder.build_release_bundle(expected_owner='cad3_admin',expected_database=name)
    (output/'rehearsed-release-bundle.sql').write_text(bundle)
    app='cad3-release-interruption-'+secrets.token_hex(5)
    proc=subprocess.Popen(['psql','-X','-qAt','-v','ON_ERROR_STOP=1'],env={**child,'PGAPPNAME':app},stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        proc.stdin.write(bundle.rsplit('\ncommit;',1)[0]+'\nselect pg_sleep(30);commit;');proc.stdin.close();proc.stdin=None
        pid=None
        for _ in range(150):
            value=execute("select pid from pg_stat_activity where application_name='"+app+"' and wait_event='PgSleep';")
            if value:pid=int(value);break
            time.sleep(.05)
        assert pid,'Interrupted release backend not observed'
        execute('select pg_terminate_backend('+str(pid)+');');proc.communicate(timeout=10)
        assert proc.returncode!=0 and execute("select to_regnamespace('community_private') is null and (select count(*) from supabase_migrations.schema_migrations)=4;")=='t'
        assert data_snapshot(execute)==before
    finally:
        if proc.poll() is None:proc.terminate();proc.communicate(timeout=10)
    execute(bundle)
    after=data_snapshot(execute);assert after==before
    history=json.loads(execute('select jsonb_agg(jsonb_build_object(\'version\',version) order by version) from supabase_migrations.schema_migrations;'))
    matrix=json.loads(execute((ROOT/'supabase/tests/security_inventory_v2.sql').read_text()))
    verdict=validate_catalog(matrix,'release',owner='cad3_admin',migrations=history);assert verdict['passed'],verdict['errors']
    (output/'release-atomic.json').write_text(json.dumps({'passed':True,'interruptionRolledBack':True,'retryPassed':True,'before':before,'after':after,'migrations':history,'catalog':verdict},indent=2))

def run(*,sql,env,output,api,register):
    checks=[]
    def check(name,value):
        checks.append({'name':name,'passed':bool(value)})
        (output/'release-checks.json').write_text(json.dumps({'checks':checks,'passed':False},indent=2))
        assert value,name
    rehearse_atomic(sql,env,output);check('atomic seven-version interruption retry and preservation',True)
    sql(AUTH_FIXTURE)
    for filename in ('202610230002_application_registry.sql','202610230003_auth_operator.sql'):
        path=ROOT/'supabase/beta-migrations'/filename;source=path.read_text();version,name=path.stem.split('_',1)
        sql('begin;'+source+"insert into supabase_migrations.schema_migrations values('"+version+"','"+name+"',array[$release$"+source+"$release$]);commit;")
    sql("notify pgrst,'reload schema';");time.sleep(.5)
    matrix=json.loads(sql((ROOT/'supabase/tests/security_inventory_v2.sql').read_text()))
    history=json.loads(sql("select jsonb_agg(jsonb_build_object('version',version) order by version) from supabase_migrations.schema_migrations;"))
    verdict=validate_catalog(matrix,'release',owner='cad3_admin',migrations=history)
    check('seven-migration release catalog',verdict['passed'])
    (output/'release-catalog.json').write_text(json.dumps({'matrix':matrix,'migrations':history,'verdict':verdict},indent=2))
    ledger=sql("select jsonb_object_agg(n,h) from ("+' union all '.join("select '"+n+"' n,md5(coalesce(jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text)::text,'[]')) h from public."+n+" t" for n in ('community_members','community_recommendations','community_audit','community_submission_requests'))+") q;")
    first=register();check('uninvited authenticated user registers',first.get('registered') is True and first.get('updated') is True)
    first_seen=sql('select first_seen from community_private.discovery_users;')
    again=register();check('repeat registration throttled',again.get('registered') is True and again.get('updated') is False)
    check('single independent registry row',sql('select count(*)=1 from community_private.discovery_users;')=='t')
    check('no Community membership or ledger side effects',ledger==sql("select jsonb_object_agg(n,h) from ("+' union all '.join("select '"+n+"' n,md5(coalesce(jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text)::text,'[]')) h from public."+n+" t" for n in ('community_members','community_recommendations','community_audit','community_submission_requests'))+") q;"))
    check('forged registry identity rejected',register({'memberId':999}).get('code')=='INVALID_REQUEST')
    sql("update community_private.discovery_users set last_seen=clock_timestamp()-interval '13 hours',username='OldPublicName';")
    check('heartbeat resumes after twelve hours',register().get('updated') is True)
    check('fresh verified username corrects registry',sql("select username='SignedMember8800000000' from community_private.discovery_users;")=='t')
    check('first authenticated timestamp preserved',sql('select first_seen from community_private.discovery_users;')==first_seen)
    # api lazily starts a separate actual PostgREST connection as authenticator.
    for role in ('anon','ordinary','service','operator_aal1','forged'):
        result=api(role,'community_admin',{'p_action':'registry_stats'})
        check('operator denied '+role,result[0]>=400)
    stats=api('operator','community_admin',{'p_action':'registry_stats'})
    check('mapped MFA operator aggregates',stats[0]==200 and stats[1]['users']==1)
    for role in ('anon','ordinary','service','operator'):
        for table in ('community_private.discovery_users','community_private.auth_operators','community_private.invitations','community_private.moderator_audit'):
            check('private enumeration denied '+role+' '+table,api(role,table,None)[0]>=400)
        for method in ('POST','PATCH','DELETE'):
            response=api(role,'community_private.discovery_users',{'__method':method,'civitai_id':999,'username':'Forged','app_version':'x','platform':'Linux','disclosure_version':'2026-10-23'})
            check('registry direct '+method+' denied '+role,response[0]>=400 and response[1].get('code') in ('PGRST106','42501','PGRST205'))
    check('operator cannot invoke runtime registry RPC',api('operator','community_registry_upsert',{'p_member':999,'p_name':'Forged','p_version':'x','p_platform':'Linux','p_disclosure':'2026-10-23'})[0]>=400)
    check('ordinary user cannot invoke runtime registry RPC',api('ordinary','community_registry_upsert',{'p_member':999,'p_name':'Forged','p_version':'x','p_platform':'Linux','p_disclosure':'2026-10-23'})[0]>=400)
    check('moderator identity spoof rejected',api('operator','community_admin',{'p_action':'registry_stats','p_actor':999})[0]>=400)
    check('operator registry deletion',api('operator','community_admin',{'p_action':'registry_delete','p_target':8800000000,'p_reason':'Synthetic requested deletion'})[0]==200)
    check('registry deleted without Community deletion',sql('select count(*)=0 from community_private.discovery_users;')=='t')
    register();sql("update community_private.discovery_users set last_seen=clock_timestamp()-interval '181 days';")
    check('operator retention executes',api('operator','community_admin',{'p_action':'registry_retention','p_reason':'Synthetic retention approval'})[0]==200)
    check('retention deletes expired registry only',sql('select count(*)=0 from community_private.discovery_users;')=='t')
    check('operator audit derives stable mapped actor',sql("select count(*)=2 and bool_and(actor_id=9900 and actor_login='auth:00000000-0000-0000-0000-000000000001') from community_private.moderator_audit where action in ('registry_delete','registry_retention');")=='t')
    sql("update community_private.auth_operators set revoked=true;")
    check('existing signed JWT loses authority after revocation',api('operator','community_admin',{'p_action':'registry_stats'})[0]>=400)
    result={'passed':True,'checks':checks,'realPostgREST':True,'syntheticSupabaseAuthIdentity':True,'hostedVerified':False}
    (output/'release-checks.json').write_text(json.dumps(result,indent=2));return {'passed':True,'checks':len(checks),'syntheticAuthIdentity':True}

if __name__=='__main__':print('SKIP: run through owned native private-beta runner')
