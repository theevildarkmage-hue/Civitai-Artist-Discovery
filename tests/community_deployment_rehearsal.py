"""Owned native deployment rehearsal. Never accepts an external connection URL."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from discovery.community_catalog import validate_catalog,BASE_TABLES,PRIVATE_TABLES,BASE_VERSIONS
from discovery.secure_backup import transform


def command(env,statement,*,check=True):
    p=subprocess.run(['psql','-X','-qAt','-v','ON_ERROR_STOP=1'],input=statement,env=env,
        capture_output=True,text=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if check and p.returncode:raise RuntimeError('Owned rehearsal SQL failed; SQL/credential detail withheld')
    return p


def catalog(sql):
    def canonical(value):
        if isinstance(value,dict):return {k:canonical(v) for k,v in value.items()}
        if isinstance(value,list):return sorted([canonical(v) for v in value],key=lambda v:json.dumps(v,sort_keys=True))
        if isinstance(value,str) and value.startswith('search_path='):return value.replace(' ','')
        return value
    return canonical(json.loads(sql((ROOT/'supabase/tests/security_inventory_v2.sql').read_text())))


def history(sql):return json.loads(sql("select coalesce(jsonb_agg(jsonb_build_object('version',version,'name',name,'statementsMd5',md5(coalesce(array_to_string(statements,chr(10)),''))) order by version),'[]') from supabase_migrations.schema_migrations;"))


def data_snapshot(sql,private=False):
    result={}
    for name in sorted(BASE_TABLES|(PRIVATE_TABLES if private else set())):
        result[name]=json.loads(sql("select jsonb_build_object('count',count(*),'md5',md5(coalesce(jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text)::text,'[]'))) from "+name+" t;"))
    result['quotaRows']=json.loads(sql("select coalesce(jsonb_agg(to_jsonb(q) order by member_id),'[]') from (select m.civitai_id member_id,count(r.image_id) filter(where r.week=date_trunc('week',clock_timestamp() at time zone 'UTC')::date) used from community_members m left join community_recommendations r on r.member_id=m.civitai_id group by m.civitai_id) q;"))
    return result


def prepare(sql,env,output):
    output=output/'deployment-rehearsal';output.mkdir(exist_ok=True)
    assert sql('select system_identifier from pg_control_system();')==env['CAD_TEST_PG_SYSTEM_IDENTIFIER']
    assert Path(sql('show data_directory;')).resolve()==Path(env['CAD_TEST_PG_DATA_DIR']).resolve()
    # Earlier baseline HTTP scenarios may deliberately finish with empty replay
    # state. Establish our own historical ledger through the real baseline RPC,
    # rather than relying on another phase's incidental cleanup behavior.
    fixture=8000000000+secrets.randbelow(1000000)*10
    for offset in range(3):
        result=json.loads(sql(f"select community_submit_once({fixture},'SyntheticMigrationMember',{fixture+offset+1},{fixture+9},'rehearsal_{fixture}_{offset}');"))
        assert result.get('imageId')==fixture+offset+1
    sql('create schema supabase_migrations;create table supabase_migrations.schema_migrations(version text primary key,name text,statements text[]);')
    for path in sorted((ROOT/'supabase/migrations').glob('*.sql')):
        version,name=path.stem.split('_',1)
        digest=hashlib.sha256(path.read_text().encode()).hexdigest()
        sql("insert into supabase_migrations.schema_migrations values('"+version+"','"+name+"',array['sha256:"+digest+"']);")
    before={'matrix':catalog(sql),'migrations':history(sql),'data':data_snapshot(sql)}
    assert all(before['data']['public.'+n]['count']>0 for n in ('community_members','community_recommendations','community_audit','community_submission_requests'))
    verdict=validate_catalog(before['matrix'],'baseline',owner='cad3_admin',migrations=before['migrations'])
    assert verdict['passed'],verdict['errors']
    (output/'baseline.json').write_text(json.dumps(before,indent=2))
    (output/'baseline-verification.json').write_text(json.dumps(verdict,indent=2))
    assert not validate_catalog(before['matrix'],'candidate',owner='cad3_admin',migrations=before['migrations'])['passed']
    import importlib.util
    spec=importlib.util.spec_from_file_location('prepare_candidate',ROOT/'scripts/prepare-community-candidate.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    candidate=builder.build_bundle(expected_owner='cad3_admin',expected_database=env['PGDATABASE'])
    (output/'rehearsed-local-bundle.sql').write_text(candidate,encoding='utf-8')
    before['retryBundle']=candidate
    app='cad3-migration-interruption-'+secrets.token_hex(6)
    child_env={**env,'PGAPPNAME':app}
    proc=subprocess.Popen(['psql','-X','-qAt','-v','ON_ERROR_STOP=1'],env=child_env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    proc.stdin.write(candidate.rsplit('\ncommit;',1)[0]+'\nselect pg_sleep(30);commit;')
    proc.stdin.close();proc.stdin=None
    pid=None
    try:
        for _ in range(150):
            value=sql("select pid from pg_stat_activity where application_name='"+app+"' and wait_event='PgSleep';")
            if value:pid=int(value);break
            if proc.poll() is not None:break
            time.sleep(.1)
        assert pid is not None,'Interrupted migration did not reach the owned sleep marker'
        assert sql('select pg_terminate_backend('+str(pid)+');')=='t'
        proc.communicate(timeout=15)
        assert proc.returncode!=0
    finally:
        if proc.poll() is None:proc.terminate();proc.communicate(timeout=10)
    assert sql("select to_regnamespace('community_private') is null;")=='t'
    assert data_snapshot(sql)==before['data'] and catalog(sql)==before['matrix'] and history(sql)==before['migrations']
    (output/'interruption.json').write_text(json.dumps({'passed':True,'terminatedOwnedBackendOnly':True,'allDdlAndHistoryRolledBack':True,'baselineDataAndCatalogUnchanged':True},indent=2))
    return before


def finish(*,sql,env,output,before,call,member,request,edge,restart,legacy_probe,restored_probe,seed):
    output=output/'deployment-rehearsal';checks=[]
    def check(name,value):
        checks.append({'name':name,'passed':bool(value)})
        (output/'checks.json').write_text(json.dumps({'completed':False,'checks':checks},indent=2))
        assert value,name
    matrix=catalog(sql);versions=history(sql)
    verdict=validate_catalog(matrix,'candidate',owner='cad3_admin',migrations=versions)
    check('candidate full catalog accepted',verdict['passed'])
    check('candidate cannot pass baseline model',not validate_catalog(matrix,'baseline',owner='cad3_admin',migrations=versions)['passed'])
    (output/'candidate.json').write_text(json.dumps({'matrix':matrix,'migrations':versions},indent=2))
    (output/'candidate-verification.json').write_text(json.dumps(verdict,indent=2))
    pins={r['schema']+'.'+r['name']:r['bodyMd5'] for r in matrix['rpcs']}
    (output/'candidate-function-pins.json').write_text(json.dumps(pins,indent=2))
    mutations={
      'runtime moderation grant':"grant execute on function public.community_operator(text,bigint,text,timestamptz) to service_role;",
      'anonymous report grant':"grant execute on function public.community_report(bigint,bigint,text) to anon;",
      'private report table grant':"grant select on community_private.reports to authenticated;",
      'column-only grant':"grant select(reason) on community_private.reports to authenticated;",
      'private schema exposure':"grant usage on schema community_private to anon;",
      'public schema creation':"grant create on schema public to service_role;",
      'private sequence access':"grant usage on sequence community_private.reports_seq_seq to anon;",
      'missing RLS':"alter table community_private.blocks disable row level security;",
      'unsafe search path':"alter function community_private.assert_member(bigint,boolean) set search_path=pg_temp;",
      'unsafe ownership':"alter function public.community_access(bigint) owner to service_role;",
      'operator group elevation':"alter role community_operator inherit;",
      'untrusted operator membership':"grant community_operator to authenticated;",
      'lost operator grant':"revoke execute on function public.community_operator(text,bigint,text,timestamptz) from community_operator;",
      'unexpected overload':"create function public.community_access(text) returns jsonb language sql security definer set search_path=public,pg_temp as $$select '{}'::jsonb$$;",
      'changed privileged body':"create or replace function public.community_access(p_member bigint) returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$begin return '{}'::jsonb;end$$;",
    }
    for name,change in mutations.items():
        # Catalog evidence from a real changed transaction, rolled back on exit.
        p=command(env,'begin;'+change+(ROOT/'supabase/tests/security_inventory_v2.sql').read_text()+'rollback;')
        altered=json.loads(p.stdout.strip())
        check('verifier rejects real SQL '+name,not validate_catalog(altered,'candidate',owner='cad3_admin',migrations=versions,expected_definitions=pins)['passed'])
    check('negative probes restore original catalog',catalog(sql)==matrix)
    # Rehearse admin-managed operator lifecycle without publishing either secret.
    sql("insert into community_private.operators values(session_user,9000,clock_timestamp()+interval '1 day',false) on conflict(login) do nothing;")
    login='cad3_operator_rehearsal';first=secrets.token_urlsafe(40);second=secrets.token_urlsafe(40)
    from datetime import datetime,timedelta,timezone
    until=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    role_env={**env,'PGUSER':login,'PGPASSWORD':first}
    hba=Path(env['CAD_TEST_PG_DATA_DIR'])/'pg_hba.conf'
    hba.write_text(f"host {env['PGDATABASE']} {login} 127.0.0.1/32 scram-sha-256\n"+hba.read_text());sql('select pg_reload_conf();')
    def provision(action,password=None):
        args=[sys.executable,'-B',str(ROOT/'scripts/provision-community-operator.py'),action,'--login',login,'--reason','Synthetic rehearsal action']
        if action=='create':args+=['--member-id','9002']
        if action in ('create','rotate'):args+=['--until',until]
        p=subprocess.run(args,env={**env,**({'COMMUNITY_OPERATOR_NEW_PASSWORD':password} if password else {})},capture_output=True,text=True,timeout=40)
        return p.returncode==0
    def operator(password):
        p=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/community-operator.py'),'reports'],env={**role_env,'PGPASSWORD':password},capture_output=True,text=True,timeout=20)
        return p.returncode==0
    check('operator provisioned without secret output',provision('create',first))
    check('first credential authorizes restricted workflow',operator(first))
    check('ordinary runtime cannot moderate',command({**env,'PGUSER':'service_role','PGPASSWORD':json.loads(env['CAD_TEST_PG_ROLE_PASSWORDS'])['service_role']},"select public.community_operator('reports');",check=False).returncode!=0)
    held=subprocess.Popen(['psql','-X','-qAt','-v','ON_ERROR_STOP=1'],env=role_env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    held.stdin.write('set role community_operator;select pg_sleep(30);');held.stdin.close();held.stdin=None
    try:
        for _ in range(60):
            if sql("select count(*)>0 from pg_stat_activity where usename='"+login+"' and wait_event='PgSleep';")=='t':break
            time.sleep(.1)
        else:raise AssertionError('Owned operator session not ready')
        check('credential rotation completed',provision('rotate',second))
        held.communicate(timeout=10)
        check('rotation terminates existing session',held.returncode!=0)
    finally:
        if held.poll() is None:held.terminate();held.communicate(timeout=10)
    check('old password no longer authenticates',not operator(first))
    check('new password authorizes workflow',operator(second))
    check('emergency disable completed',provision('disable'))
    check('disabled operator denied',not operator(second))
    check('disabled credential can be safely rotated',provision('rotate',first))
    check('replacement after disable authorizes workflow',operator(first))
    check('credential revoked',provision('revoke'))
    check('revoked credentials denied',not operator(first) and not operator(second))
    check('lifecycle audit derives actual admin identity',sql("select count(*)>=6 and bool_and(actor_id=9000 and actor_login=session_user and length(reason)>0) from community_private.moderator_audit where action like 'operator_%';")=='t')
    check('operator lifecycle retains strict catalog',validate_catalog(catalog(sql),'candidate',owner='cad3_admin',migrations=versions,expected_definitions=pins)['passed'])
    # Encrypted custom archive, with plaintext only in process memory/stdin pipes.
    started=time.monotonic();state=data_snapshot(sql,True);source_catalog=catalog(sql)
    dump=subprocess.run(['pg_dump','-Fc'],env=env,capture_output=True,timeout=30)
    assert dump.returncode==0,'Owned dump failed; output withheld'
    digest=hashlib.sha256(dump.stdout).hexdigest();cipher=transform(dump.stdout)
    archive=output/'candidate-backup.dpapi';archive.write_bytes(cipher)
    plain=transform(archive.read_bytes(),decrypt=True)
    check('encrypted archive integrity verified',hashlib.sha256(plain).hexdigest()==digest)
    corrupt=bytearray(cipher);corrupt[-1]^=1
    try:transform(bytes(corrupt),decrypt=True);corrupt_denied=False
    except RuntimeError:corrupt_denied=True
    check('tampered archive rejected',corrupt_denied)
    listed=subprocess.run(['pg_restore','--list'],input=plain,env=env,capture_output=True,timeout=20)
    check('archive TOC readable after verified decryption',listed.returncode==0)
    restored='cad3_candidate_restore_'+secrets.token_hex(5)
    sql('create database '+restored+';revoke connect on database '+restored+' from public;')
    restore_env={**env,'PGDATABASE':restored}
    restored_result=subprocess.run(['pg_restore','--single-transaction','--exit-on-error','-d',restored],input=plain,env=env,capture_output=True,timeout=45)
    check('restore into separate target completed',restored_result.returncode==0)
    def target_sql(statement):return command(restore_env,statement).stdout.strip()
    check('all twelve tables and quota state preserved',data_snapshot(target_sql,True)==state)
    check('five-version migration history preserved',history(target_sql)==versions)
    check('ownership ACL RLS and function bodies preserved',catalog(target_sql)==source_catalog)
    check('restored catalog accepted by candidate model',validate_catalog(catalog(target_sql),'candidate',owner='cad3_admin',migrations=history(target_sql),expected_definitions=pins)['passed'])
    (output/'backup-evidence.json').write_text(json.dumps({'passed':True,'archiveSha256':hashlib.sha256(cipher).hexdigest(),'plaintextArchiveSha256':digest,
        'encrypted':True,'protection':'Windows account-bound DPAPI; no plaintext/key file','restoredDatabase':restored,
        'allTableHashes':state,'elapsedSeconds':round(time.monotonic()-started,3),'separateDatabaseSameOwnedCluster':True,
        'rolePasswordsNotInDatabaseArchive':True,'hostedRecoveryVerified':False},indent=2))
    # Exact version-10 source is exercised in a separate endpoint, never replacing
    # the active candidate. Its unscoped gallery dependency is now denied.
    restart('disabled')
    # Use another invited principal's remaining budget; do not reset throttles
    # accumulated by the earlier adversarial admission/replay checks.
    legacy=legacy_probe(seed+4,member(4))
    check('exact v10 source incompatible with candidate database',legacy['status']==503 and legacy['code']=='UNAVAILABLE')
    check('candidate remains disabled after failed legacy path',call('gallery',n=4)[1].get('code')=='BETA_DISABLED')
    recovered=restored_probe(restored,member(4))
    check('restored candidate HTTP status accepted',recovered['status']==200)
    check('restored candidate HTTP disabled writes denied',recovered['recommendCode']=='BETA_DISABLED')
    check('failed rollout leaves last compatible service running',recovered['failedRolloutExited'] and recovered['statusAfterFailedRollout']==200)
    # No recommendation is ever submitted to the restored target.
    after_state=data_snapshot(target_sql,True)
    for name in ('public.community_members','public.community_recommendations','public.community_audit','public.community_submission_requests'):
        check('HTTP recovery preserves '+name,after_state[name]==state[name])
    result={'passed':True,'checks':checks,'beforeMigration':before['data'],'finalSourceState':state,
        'legacyCompatibility':legacy,'restoredHttp':recovered,'hostedMutations':False}
    (output/'result.json').write_text(json.dumps(result,indent=2))
    (output/'checks.json').write_text(json.dumps({'completed':True,'passed':True,'checks':checks},indent=2))
    return {'passed':True,'checks':len(checks),'encryptedBackupRestore':True,'operatorLifecycle':True,'interruptedMigrationRecovered':True,'hostedRecoveryVerified':False}


if __name__=='__main__':print('SKIP: owned native runner --deployment-rehearsal only')
