"""Actual SQL tests, only inside the attested disposable native runner cluster."""
import concurrent.futures,json,os,subprocess
from pathlib import Path
from urllib.parse import urlsplit

def run():
    if os.environ.get('CAD_ALLOW_ISOLATED_DB_TESTS')!='1':
        print('SKIP: owned native runner required');return
    url=urlsplit(os.environ['CAD_TEST_DATABASE_URL'])
    if url.hostname!='127.0.0.1' or not url.port or url.password:raise RuntimeError('Owned loopback cluster required')
    env={**os.environ,'PGHOST':'127.0.0.1','PGPORT':str(url.port),'PGUSER':url.username,'PGDATABASE':url.path[1:]}
    credentials=json.loads(env['CAD_TEST_PG_ROLE_PASSWORDS'])
    def sql(statement,role=None):
        return subprocess.run(['psql','-X','-A','-t','-v','ON_ERROR_STOP=1','-c',statement],env=env if role is None else {**env,'PGUSER':role,'PGPASSWORD':credentials[role]},capture_output=True,text=True,timeout=30)
    assert sql('select system_identifier from pg_control_system();').stdout.strip()==env['CAD_TEST_PG_SYSTEM_IDENTIFIER']
    assert Path(sql('show data_directory').stdout.strip()).resolve()==Path(env['CAD_TEST_PG_DATA_DIR']).resolve()
    passed=0
    def check(condition):
        nonlocal passed
        assert condition;passed+=1
    def exchange(a='a',n='b',s='c'):
        return "select community_identity_exchange('"+a*64+"','"+n*64+"','"+s*64+"',7,'Synthetic',clock_timestamp()+interval '1 hour');"
    baseline=sql("select (select count(*) from community_recommendations),(select count(*) from community_audit),(select count(*) from community_submission_requests);").stdout
    for role in ('anon','authenticated'):
        for table in ('community_identity_sessions','community_identity_replays'):
            for statement in (f'select * from {table};',f'insert into {table} default values;',f'update {table} set expires_at=clock_timestamp();',f'delete from {table};'):
                result=sql(statement,role);check(result.returncode!=0 and 'permission denied' in result.stderr)
        for statement in (exchange(),"select community_identity_resolve('"+'c'*64+"');","select community_identity_revoke('"+'c'*64+"');"):
            result=sql(statement,role);check(result.returncode!=0 and 'permission denied' in result.stderr)
    for table in ('community_identity_sessions','community_identity_replays'):
        for statement in (f'select * from {table};',f'insert into {table} default values;',f'update {table} set expires_at=clock_timestamp();',f'delete from {table};'):
            result=sql(statement,'service_role');check(result.returncode!=0 and 'permission denied' in result.stderr)
    check(sql('begin;'+exchange()+'rollback;','service_role').returncode==0)
    check(sql('select count(*) from community_identity_sessions;').stdout.strip()=='0')
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _:sql(exchange(),'service_role'),range(8)))
    check(sum(result.returncode==0 for result in results)==1)
    check(sql('select count(*) from community_identity_sessions;').stdout.strip()=='1')
    check(sql("select community_identity_resolve('"+'c'*64+"');",'service_role').returncode==0)
    check(sql(exchange('d','b','e'),'service_role').returncode!=0)
    check(sql("select community_identity_revoke('"+'c'*64+"');",'service_role').returncode==0)
    check(sql("select community_identity_resolve('"+'c'*64+"');",'service_role').returncode!=0)
    check(sql(exchange('f','0','1'),'service_role').returncode==0)
    check(sql("update community_identity_sessions set created_at=clock_timestamp()-interval '2 hours',expires_at=clock_timestamp()-interval '1 hour' where session_hash='"+'1'*64+"';").returncode==0)
    check(sql("select community_identity_resolve('"+'1'*64+"');",'service_role').returncode!=0)
    check(sql("select community_identity_exchange(null,null,null,null,null,null);",'service_role').returncode!=0)
    check(sql("select community_identity_resolve(null);",'service_role').returncode!=0)
    check(sql("select community_identity_revoke(null);",'service_role').returncode!=0)
    check(sql("select (select count(*) from community_recommendations),(select count(*) from community_audit),(select count(*) from community_submission_requests);").stdout==baseline)
    check(sql("select count(*) from pg_tables where schemaname='public' and tablename like 'community_identity_%' and rowsecurity;").stdout.strip()=='2')
    if sql("select to_regclass('public.community_device_families') is not null;").stdout.strip()=='t':
        for role in ('anon','authenticated','service_role'):
            for table in ('community_device_families','community_device_proofs'):
                for statement in (f'select * from {table};',f'insert into {table} default values;',f'update {table} set '+('revoked=true;' if table=='community_device_families' else 'expires_at=clock_timestamp();'),f'delete from {table};'):
                    result=sql(statement,role);check(result.returncode!=0 and 'permission denied' in result.stderr)
            result=sql("select community_private.owner_device_revoke(7,'Forged client');",role)
            check(result.returncode!=0 and 'permission denied' in result.stderr)
        for role in ('anon','authenticated'):
            for statement in ("select community_device_lookup('"+'8'*64+"');", "select community_device_rotate(null,null,null,null,null,null);", "select community_device_enroll(null,null,null,null,null,null,null,null);"):
                result=sql(statement,role);check(result.returncode!=0 and 'permission denied' in result.stderr)
        check(json.loads(sql("select community_device_lookup('"+'8'*64+"');",'service_role').stdout)['denied'])
    print(json.dumps({'passed':passed,'nativeSQL':True,'concurrentExchangeRequests':8,'admitted':1,'historicalCountsPreserved':True,'hostedRequests':0}))

if __name__=='__main__':run()
