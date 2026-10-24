"""Owner-admin release migrations and actual signed PostgREST role attacks."""
import json,time,secrets,subprocess,importlib.util,hashlib
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from discovery.community_catalog import validate_catalog
from discovery.secure_backup import transform

def backup_release(*,sql,env,output,restore_http):
    from community_deployment_rehearsal import command,catalog,history
    tables=json.loads(sql("select jsonb_agg(format('%I.%I',n.nspname,c.relname) order by n.nspname,c.relname) from pg_class c join pg_namespace n on n.oid=c.relnamespace where c.relkind='r' and (n.nspname='community_private' or (n.nspname='public' and c.relname like 'community_%'));"))
    assert len(tables)==13,'Thirteen application tables required; no Auth fixture'
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
    (output/'release-restore.json').write_text(json.dumps(evidence,indent=2));return {'passed':True,'applicationTables':13,'authTables':'none required','separateDatabaseSameCluster':True,'hostedAuthRecoveryVerified':False}

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
    execute('begin;'+history+baseline+"select community_submit_once(8800000001,'SyntheticHistorical',8800000002,8800000003,'synthetic_atomic_replay');commit;")
    before=data_snapshot(execute)
    spec=importlib.util.spec_from_file_location('release_builder',ROOT/'scripts/prepare-community-candidate.py');builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    bundle=builder.build_automatic_bundle(expected_owner='cad3_admin',expected_database=name)
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
    verdict=validate_catalog(matrix,'automatic',owner='cad3_admin',migrations=history);assert verdict['passed'],verdict['errors']
    (output/'release-atomic.json').write_text(json.dumps({'passed':True,'interruptionRolledBack':True,'retryPassed':True,'before':before,'after':after,'migrations':history,'catalog':verdict},indent=2))

def rehearse_registry_upgrade(sql,env,output):
    from community_deployment_rehearsal import command
    name='cad3_registry_upgrade_'+secrets.token_hex(5);sql('create database '+name+';')
    child={**env,'PGDATABASE':name}
    execute=lambda stmt:command(child,stmt).stdout.strip()
    setup='create schema supabase_migrations;create table supabase_migrations.schema_migrations(version text primary key,name text,statements text[]);'
    paths=list(sorted((ROOT/'supabase/migrations').glob('*.sql')))+list(sorted((ROOT/'supabase/beta-migrations').glob('20261023000[123]_*.sql')))
    for path in paths:
        source=path.read_text();version,label=path.stem.split('_',1)
        setup+=source+"\ninsert into supabase_migrations.schema_migrations values('"+version+"','"+label+"',array[$source$"+source+"$source$]);"
    execute('begin;'+setup+"select community_registry_upsert(8890000001,'PreservedRegistryFixture','3.0.0-beta.1','Linux','2026-10-23');commit;")
    before=execute("select (to_jsonb(u)-'disclosure_version')::text from community_private.discovery_users u;")
    spec=importlib.util.spec_from_file_location('upgrade_builder',ROOT/'scripts/prepare-community-candidate.py');builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    upgrade=builder.build_registry_upgrade(expected_owner='cad3_admin',expected_database=name)
    failed=command(child,upgrade.rsplit('commit;',1)[0]+"do $$begin raise exception 'INTERRUPTED_UPGRADE';end$$;commit;",check=False)
    assert failed.returncode!=0
    assert execute("select count(*)=1 from information_schema.columns where table_schema='community_private' and table_name='discovery_users' and column_name='disclosure_version';")=='t'
    assert before==execute("select (to_jsonb(u)-'disclosure_version')::text from community_private.discovery_users u;")
    execute(upgrade)
    assert before==execute('select to_jsonb(u)::text from community_private.discovery_users u;')
    assert execute("select count(*)=8 from supabase_migrations.schema_migrations;")=='t'
    assert command(child,upgrade,check=False).returncode!=0
    assert before==execute('select to_jsonb(u)::text from community_private.discovery_users u;')
    (output/'registry-upgrade.json').write_text(json.dumps({'passed':True,'rollbackPreservesSevenMigrationSchema':True,'retryPreservesAllSixFields':True,'duplicateMigrationRejected':True,'seededRegistryRows':1,'hostedMutation':False},indent=2))


def run(*,sql,env,output,api,register):
    checks=[]
    def check(name,value):
        checks.append({'name':name,'passed':bool(value)})
        (output/'release-checks.json').write_text(json.dumps({'checks':checks,'passed':False},indent=2))
        assert value,name
    rehearse_registry_upgrade(sql,env,output);check('incremental seven-to-eight migration preservation rollback and retry',True)
    rehearse_atomic(sql,env,output);check('atomic eight-version interruption retry and preservation',True)
    for filename in ('202610230002_application_registry.sql','202610230003_owner_administration.sql','202610230004_automatic_registry.sql'):
        path=ROOT/'supabase/beta-migrations'/filename;source=path.read_text();version,name=path.stem.split('_',1)
        sql('begin;'+source+"insert into supabase_migrations.schema_migrations values('"+version+"','"+name+"',array[$release$"+source+"$release$]);commit;")
    sql("notify pgrst,'reload schema';");time.sleep(.5)
    matrix=json.loads(sql((ROOT/'supabase/tests/security_inventory_v2.sql').read_text()))
    history=json.loads(sql("select jsonb_agg(jsonb_build_object('version',version) order by version) from supabase_migrations.schema_migrations;"))
    verdict=validate_catalog(matrix,'automatic',owner='cad3_admin',migrations=history)
    check('eight-migration automatic registry catalog',verdict['passed'])
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
    check('no Supabase Auth schema dependency',sql("select to_regnamespace('auth') is null;")=='t')
    def owner(action,target=None,reason='Synthetic owner administration'):
        value='null' if target is None else str(int(target))
        return json.loads(sql("select community_private.owner_admin('"+action+"',"+value+",'"+reason+"');"))
    stats=owner('registry_stats')
    check('owner-only aggregates',stats['users']==1)
    for role in ('anon','ordinary','service','operator'):
        for table in ('community_private.discovery_users','community_private.invitations','community_private.moderator_audit'):
            check('private enumeration denied '+role+' '+table,api(role,table,None)[0]>=400)
        for method in ('POST','PATCH','DELETE'):
            response=api(role,'community_private.discovery_users',{'__method':method,'civitai_id':999,'username':'Forged','app_version':'x','platform':'Linux'})
            check('registry direct '+method+' denied '+role,response[0]>=400 and response[1].get('code') in ('PGRST106','42501','PGRST205'))
    check('operator cannot invoke runtime registry RPC',api('operator','community_registry_upsert',{'p_member':999,'p_name':'Forged','p_version':'x','p_platform':'Linux'})[0]>=400)
    check('ordinary user cannot invoke runtime registry RPC',api('ordinary','community_registry_upsert',{'p_member':999,'p_name':'Forged','p_version':'x','p_platform':'Linux'})[0]>=400)
    check('moderator identity spoof rejected',api('operator','community_admin',{'p_action':'registry_stats','p_actor':999})[0]>=400)
    check('owner registry deletion',owner('registry_delete',8800000000)['completed'])
    check('registry deleted without Community deletion',sql('select count(*)=0 from community_private.discovery_users;')=='t')
    register();sql("update community_private.discovery_users set last_seen=clock_timestamp()-interval '181 days';")
    check('owner retention executes',owner('registry_retention')['completed'])
    check('retention deletes expired registry only',sql('select count(*)=0 from community_private.discovery_users;')=='t')
    check('explicit owner administration audit identifier',sql("select count(*)=2 and bool_and(actor_id=1 and actor_login='owner-admin:discovery3') from community_private.moderator_audit where action in ('registry_delete','registry_retention');")=='t')
    for role in ('anon','authenticated','service_role','community_operator'):
        check('owner routine execute grant denied '+role,sql("select not has_function_privilege('"+role+"','community_private.owner_admin(text,bigint,text,timestamptz)','execute');")=='t')
        # Test actual SQL roles, not only function ACL inventory or missing routes.
        block="do $test$ begin begin perform community_private.owner_admin('registry_stats',null,'Denied role test');raise exception 'UNEXPECTED_ADMIN_ACCESS';exception when insufficient_privilege then null;end;end $test$;"
        sql('begin;set local role '+role+';'+block+'rollback;')
        check('actual owner routine denial '+role,True)
    # Exercise the complete replacement owner path inside one disposable rollback.
    operations="begin;delete from community_private.invitations;select community_private.owner_admin('configure',null,'Synthetic owner setup');"
    for number in range(5):
        operations+="select community_private.owner_admin('invite',"+str(8810000000+number)+",'Synthetic invitation',clock_timestamp()+interval '1 day');"
    operations+="do $$begin begin perform community_private.owner_admin('invite',8810000005,'Synthetic sixth account',clock_timestamp()+interval '1 day');raise exception 'LIMIT_BYPASS';exception when raise_exception then if sqlerrm<>'INVITE_LIMIT' then raise;end if;end;end$$;"
    operations+="select community_private.owner_admin('revoke_invite',8810000000,'Synthetic revocation');select community_private.owner_admin('invite',8810000005,'Synthetic replacement',clock_timestamp()+interval '1 day');"
    operations+="select community_private.owner_admin('block',8810000005,'Synthetic durable block');do $$begin begin perform community_access(8810000005);raise exception 'BLOCK_BYPASS';exception when raise_exception then if sqlerrm<>'SUBMITTER_BLOCKED' then raise;end if;end;end$$;select community_private.owner_admin('unblock',8810000005,'Synthetic unblock');select community_access(8810000005);"
    operations+="insert into community_members values(8810000006,'SyntheticOwnerFixture');insert into community_recommendations(image_id,creator_id,member_id,week) values(8810000007,8810000008,8810000006,date_trunc('week',clock_timestamp() at time zone 'UTC')::date);"
    operations+="insert into community_private.reports(image_id,reporter_id,reason) values(8810000007,8810000005,'abuse'),(8810000007,8810000004,'ownership');"
    operations+="select community_private.owner_admin('reports',null,'Synthetic report review');select community_private.owner_admin('audit',null,'Synthetic audit review');"
    operations+="select community_private.owner_admin('resolve_report',(select seq from community_private.reports where image_id=8810000007 and reporter_id=8810000005),'Synthetic resolution');select community_private.owner_admin('dismiss_report',(select seq from community_private.reports where image_id=8810000007 and reporter_id=8810000004),'Synthetic dismissal');"
    operations+="select community_private.owner_admin('hide',8810000007,'Synthetic hide');select community_private.owner_admin('remove',8810000007,'Synthetic removal');"
    operations+="do $$begin if (select status from community_recommendations where image_id=8810000007)<>'removed' or (select count(*) from community_audit where image_id=8810000007)<>2 or (select count(*) from community_private.reports where image_id=8810000007 and decision<>'pending')<>2 then raise exception 'MODERATION_FAILED';end if;end$$;rollback;"
    preserved=sql("select count(*) from community_private.moderator_audit;")
    sql(operations)
    check('owner invitations five-account limit revocation block reports and moderation rollback',preserved==sql("select count(*) from community_private.moderator_audit;"))
    before=sql('select count(*) from community_private.moderator_audit;')
    sql("begin;select community_private.owner_admin('registry_stats',null,'Rollback validation');rollback;")
    check('rolled-back owner action has no persistent audit',before==sql('select count(*) from community_private.moderator_audit;'))
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _:json.loads(sql("select community_registry_upsert(8890000010,'ConcurrentFixture','3.0.0-beta.1','Linux');")),range(12)))
    check('twelve concurrent registrations make one row and one activity write',sum(item['updated'] for item in results)==1 and sql('select count(*)=1 from community_private.discovery_users where civitai_id=8890000010;')=='t')
    sql('delete from community_private.discovery_users where civitai_id=8890000010;')
    from owner_quota_postgres import run as owner_quota_acceptance
    owner_quota=owner_quota_acceptance(sql,output)
    result={'passed':True,'checks':checks,'realPostgREST':True,'ownerAdministration':True,'hostedVerified':False,'ownerQuota':owner_quota}
    (output/'release-checks.json').write_text(json.dumps(result,indent=2));return {'passed':True,'checks':len(checks),'ownerAdministration':True}

if __name__=='__main__':print('SKIP: run through owned native private-beta runner')
