"""Own a fresh password-protected native PostgreSQL cluster; never use an existing URL."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin', required=True, type=Path, help='Verified PostgreSQL bin directory')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--community-access',action='store_true',help='Test default-invite/public policy only in the owned release rehearsal')
    parser.add_argument('--device-renewal',action='store_true',help='Test the rotating device authorization candidate in the owned cluster')
    parser.add_argument('--identity-sessions',action='store_true',help='Apply/test identity-session candidate only in the owned cluster')
    parser.add_argument('--private-beta',action='store_true',help='Apply candidate only to owned HTTP test DB')
    parser.add_argument('--http-deno', type=Path, help='Verified native Deno for real HTTP integration')
    parser.add_argument('--http-postgrest', type=Path, help='Verified native PostgREST for real HTTP integration')
    parser.add_argument('--http-live-readonly', action='store_true', help='Bounded live UserInfo/artwork preview using the selected protected session')
    parser.add_argument('--manual-test-lab',action='store_true',help='Keep the owned local service and removable Test Lab running after validation')
    parser.add_argument('--beta-only',action='store_true',help='Fast owned beta iteration, skips baseline HTTP scenarios')
    parser.add_argument('--deployment-rehearsal',action='store_true',help='Owned interruption/operator/backup/recovery rehearsal')
    parser.add_argument('--release-candidate',action='store_true',help='Registry/owner-administration migrations and signed API role tests')
    args = parser.parse_args()
    if args.community_access and not (args.release_candidate and args.identity_sessions):parser.error('--community-access requires release rehearsal and identity sessions')
    if args.device_renewal and not args.community_access:parser.error('--device-renewal requires community access rehearsal')
    if args.deployment_rehearsal and not args.private_beta:parser.error('--deployment-rehearsal requires --private-beta')
    if args.release_candidate and not args.private_beta:parser.error('--release-candidate requires --private-beta')
    if args.release_candidate and not args.deployment_rehearsal:parser.error('--release-candidate requires --deployment-rehearsal')
    if args.beta_only and not args.private_beta:parser.error('--beta-only requires --private-beta')
    if bool(args.http_deno) != bool(args.http_postgrest):
        parser.error('Supply both HTTP runtime paths')
    if (args.manual_test_lab or args.http_live_readonly) and not args.http_deno:
        parser.error('Manual Lab and live preview require both HTTP runtime paths')
    if args.private_beta and not args.http_deno:parser.error('Private beta tests require HTTP runtimes')
    if not 1 <= args.repeats <= 20:
        parser.error('Use 1..20 repetitions')
    binary = args.bin.resolve()
    suffix = '.exe' if os.name == 'nt' else ''
    for name in ('initdb', 'pg_ctl', 'psql'):
        if not (binary / (name + suffix)).is_file():
            parser.error('Native PostgreSQL binaries are required')
    cluster = Path(tempfile.mkdtemp(prefix='cad3-native-pg-')).resolve()
    if cluster.is_relative_to(ROOT):
        raise RuntimeError('Cluster must be outside the source repository')
    output = (args.output or cluster / 'evidence').resolve()
    output.mkdir(parents=True, exist_ok=True)
    password = secrets.token_urlsafe(32)
    role_passwords = {name: secrets.token_urlsafe(32) for name in ('anon', 'authenticated', 'service_role')}
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    database = 'cad3_security_' + secrets.token_hex(6)
    env = {k:v for k,v in os.environ.items() if not k.startswith(('PG', 'CAD_TEST_', 'CAD_ALLOW_ISOLATED'))}
    env.update(PGHOST='127.0.0.1', PGPORT=str(port), PGUSER='cad3_admin', PGPASSWORD=password,
               PGDATABASE='postgres', PGCONNECT_TIMEOUT='5', PGCLIENTENCODING='UTF8',
               PATH=str(binary)+os.pathsep+env.get('PATH',''), PYTHONIOENCODING='utf-8')
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    def command(name, *values, input=None, child_env=None):
        if name == 'pg_ctl' and 'start' in values:
            # Windows background children may inherit pipe handles. A file avoids
            # communicate() waiting for the whole server lifetime after pg_ctl exits.
            with (cluster/'startup.log').open('w',encoding='utf-8') as log:
                result = subprocess.run([str(binary/(name+suffix)), *values], stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT, env=child_env or env, timeout=60,
                    creationflags=creationflags)
            result.stdout=(cluster/'startup.log').read_text(encoding='utf-8',errors='replace')
            result.stderr=''
            return result
        return subprocess.run([str(binary/(name+suffix)), *values], input=input,
            env=child_env or env, capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=60, creationflags=creationflags)
    def sql(statement, *, child_env=None):
        return command('psql', '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1', input=statement, child_env=child_env)
    def require(result, label):
        if result.returncode:
            raise RuntimeError(label+' failed (credentials and statement withheld)')
        return result.stdout.strip()
    summary = {'clusterDirectory':str(cluster), 'port':port, 'database':database,
        'listenAddress':'127.0.0.1', 'networkAuthentication':'scram-sha-256',
        'installedService':False, 'existingDatabaseUsed':False, 'runs':[], 'passed':False}
    started = False
    def save():
        (output/'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    try:
        summary['version'] = require(command('psql', '--version'), 'Version check')
        require(command('initdb', '-D', str(cluster/'data'), '--username=cad3_admin',
                        '--auth=scram-sha-256', '--pwprompt', '--encoding=UTF8', '--locale=C',
                        input=password+'\n'+password+'\n',
                        child_env={**env, **({'OSTYPE':'msys'} if os.name=='nt' else {})}), 'initdb')
        summary['plaintextCredentialFilesCreated'] = False
        config = cluster/'data/postgresql.conf'
        with config.open('a', encoding='utf-8') as stream:
            stream.write("\nlisten_addresses='127.0.0.1'\nport="+str(port)+
                "\npassword_encryption='scram-sha-256'\nlog_statement='none'\nlog_min_error_statement='panic'\n")
        # No trust rules, LAN address ranges, or ambient operating-system identity.
        (cluster/'data/pg_hba.conf').write_text(
            'host all cad3_admin 127.0.0.1/32 scram-sha-256\n'
            f'host {database} anon,authenticated,service_role 127.0.0.1/32 scram-sha-256\n'
            'host all all 0.0.0.0/0 reject\nhost all all ::0/0 reject\n', encoding='utf-8')
        require(command('pg_ctl', '-D', str(cluster/'data'), '-l', str(cluster/'server.log'), '-w', 'start'), 'Cluster start')
        started = True
        summary['systemIdentifier'] = require(sql('select system_identifier from pg_control_system();'), 'Cluster identity')
        require(sql(f'create database {database}; revoke connect on database {database} from public;'), 'Database creation')
        env['PGDATABASE'] = database
        statements = []
        for role, secret in role_passwords.items():
            statements.append(f"create role {role} login {'bypassrls' if role=='service_role' else 'nobypassrls'} password '{secret}';")
        require(sql(''.join(statements)+f'grant connect on database {database} to anon,authenticated,service_role;'), 'Dedicated role setup')
        bad = sql('select 1;', child_env={**env, 'PGPASSWORD':secrets.token_urlsafe(32)})
        assert bad.returncode != 0 and 'password authentication failed' in bad.stderr
        summary['wrongPasswordRejected'] = True
        migrations = sorted((ROOT/'supabase/migrations').glob('*.sql'))
        # Rehearse the proposed all-or-nothing bootstrap in a second empty DB
        # inside this same owned cluster. No persistent profile or host is used.
        atomic_database=database+'_atomic'
        require(sql(f'create database {atomic_database}; revoke connect on database {atomic_database} from public;'),'Atomic bootstrap database')
        atomic_env={**env,'PGDATABASE':atomic_database}
        require(sql('begin;\n'+'\n'.join(m.read_text() for m in migrations)+'\ncommit;',child_env=atomic_env),'Ordered atomic bootstrap')
        inventory_sql=(ROOT/'supabase/tests/security_inventory.sql').read_text()
        atomic_inventory=json.loads(require(sql(inventory_sql,child_env=atomic_env),'Atomic inventory'))
        assert len(atomic_inventory['relations'])==5 and len(atomic_inventory['rpcs'])==7
        assert all(t['rlsEnabled'] for t in atomic_inventory['relations'])
        if args.private_beta:
            require(sql('create role community_operator nologin noinherit nobypassrls;',child_env=atomic_env),'Restricted operator group')
            candidate=(ROOT/'supabase/beta-migrations/202610230001_private_beta_security.sql').read_text()
            require(sql('begin;\n'+candidate+'\ncommit;',child_env=atomic_env),'Candidate atomic bootstrap rehearsal')
            summary['privateBetaAtomicBootstrapPassed']=True
        summary['orderedAtomicBootstrapPassed']=True
        summary['migrations'] = []
        for migration in migrations:
            if migration.name == '202610210001_community_rpc_guards.sql':
                summary['originalNullLimitAccepted'] = sql('select public.community_gallery(null,null);').returncode == 0
                summary['originalNullPreviewAccepted'] = sql("select public.community_submit(99000001,'NullProbe',99000002,99000003,null);").returncode == 0
                # Exercise explicit revocation against permissive inherited grants,
                # rather than relying only on native PostgreSQL's empty defaults.
                require(sql('grant create on schema public to public; grant all on all tables in schema public to public,anon,authenticated,service_role; '
                            'grant all on all sequences in schema public to public,anon,authenticated,service_role;'), 'Permissive-grant fixture')
                summary['permissiveGrantFixtureAppliedBeforeHardening'] = True
            require(command('psql', '-X', '-v', 'ON_ERROR_STOP=1', '--single-transaction', '-f', str(migration)), migration.name)
            summary['migrations'].append(migration.name)
            if migration.name == '202610210001_community_rpc_guards.sql':
                require(command('psql', '-X', '-v', 'ON_ERROR_STOP=1', '--single-transaction', '-f', str(migration)), 'Guard migration repeat')
                summary['guardMigrationReapplied'] = True
        # Original create migrations are one-shot, not IF NOT EXISTS. A transaction
        # must reject their replay without leaving partial changes.
        catalog_query=("select md5(string_agg(c.oid::text||':'||coalesce(c.relacl::text,''),',' order by c.oid)) "
                       "from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public';")
        schema_before = require(sql(catalog_query), 'Schema replay baseline')
        replay = command('psql', '-X', '-v', 'ON_ERROR_STOP=1', '--single-transaction', '-f', str(migrations[0]))
        assert replay.returncode != 0 and 'already exists' in replay.stderr
        summary['oneShotMigrationReplayRejected'] = True
        assert schema_before == require(sql(catalog_query), 'Schema replay integrity')
        summary['schemaUnchangedAfterRejectedReplay'] = True
        env.update(CAD_ALLOW_ISOLATED_DB_TESTS='1',
            CAD_TEST_DATABASE_URL=f'postgresql://cad3_admin@127.0.0.1:{port}/{database}',
            CAD_TEST_PG_SYSTEM_IDENTIFIER=summary['systemIdentifier'], CAD_TEST_PG_DATA_DIR=str(cluster/'data'),
            CAD_TEST_PG_ROLE_PASSWORDS=json.dumps(role_passwords))
        plans=command('psql','-X','-v','ON_ERROR_STOP=1','-f',str(ROOT/'supabase/tests/query_plans.sql'))
        (output/'query-plans.log').write_text(require(plans,'Rolled-back synthetic query plans'),encoding='utf-8')
        summary['syntheticQueryPlansPassed']=True
        inventory=require(command('psql','-X','-A','-t','-v','ON_ERROR_STOP=1','-f',str(ROOT/'supabase/tests/security_inventory.sql')),'Security inventory')
        (output/'privilege-matrix.json').write_text(json.dumps(json.loads(inventory),indent=2),encoding='utf-8')
        guard=migrations[-1]
        require(command('psql','-X','-v','ON_ERROR_STOP=1','--single-transaction','-f',str(guard)),'Replay guard reapplication')
        summary['replayGuardReapplied']=True
        save()
        for index in range(args.repeats):
            start = time.monotonic()
            result = subprocess.run([sys.executable, '-B', str(ROOT/'tests/community_postgres.py')],
                cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180,
                creationflags=creationflags)
            (output/f'postgres-{index+1}.log').write_text(result.stdout+result.stderr, encoding='utf-8')
            summary['runs'].append({'suite':'community_postgres', 'iteration':index+1,
                'passed':result.returncode==0, 'seconds':round(time.monotonic()-start,2)})
            save(); print(json.dumps(summary['runs'][-1]), flush=True)
            if result.returncode:
                raise RuntimeError('PostgreSQL suite failed; inspect the private per-run log')
            remaining = json.loads(require(sql("select json_build_object("
                "'members',(select count(*) from public.community_members),"
                "'recommendations',(select count(*) from public.community_recommendations),"
                "'audits',(select count(*) from public.community_audit),"
                "'replayKeys',(select count(*) from public.community_submission_requests),"
                "'rateBuckets',(select count(*) from public.community_rate_buckets));"), 'Suite cleanup verification'))
            assert not any(remaining.values()), 'Suite left unexpected persistent test state'
            summary['runs'][-1]['remainingRowsAfterCleanup'] = remaining
        edge = ROOT/'tests/community_edge_postgres.mjs'
        if edge.exists():
            child_env={**env,'PGUSER':'service_role','PGPASSWORD':role_passwords['service_role']}
            result=subprocess.run(['node','--experimental-transform-types',str(edge)],cwd=ROOT,env=child_env,
                capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120,creationflags=creationflags)
            (output/'edge-postgres.log').write_text(result.stdout+result.stderr,encoding='utf-8')
            summary['runs'].append({'suite':'community_edge_postgres','passed':result.returncode==0})
            if result.returncode:raise RuntimeError('SQL-backed handler test failed; inspect private log')
            counts=require(sql("select json_build_object('recommendations',(select count(*) from public.community_recommendations),"
                "'audits',(select count(*) from public.community_audit where action='accepted'),"
                "'replayKeys',(select count(*) from public.community_submission_requests));"), 'Handler persisted state')
            summary['handlerPersistedState']=json.loads(counts)
            assert summary['handlerPersistedState']=={'recommendations':12,'audits':12,'replayKeys':12}
        if args.http_deno:
            env['CAD_TEST_BETA_ACCEPTANCE']='1' if args.private_beta else '0'
            env['CAD_TEST_BETA_ONLY']='1' if args.beta_only else '0'
            env['CAD_TEST_REHEARSAL']='1' if args.deployment_rehearsal else '0'
            env['CAD_TEST_RELEASE']='1' if args.release_candidate else '0'
            env.update(CAD_ALLOW_LOCAL_HTTP_TESTS='1',CAD_TEST_DENO=str(args.http_deno.resolve()),
                       CAD_TEST_POSTGREST=str(args.http_postgrest.resolve()),CAD_TEST_HTTP_OUTPUT=str(output/'http'))
            if args.http_live_readonly:env['CAD_TEST_LIVE_READONLY']='1'
            if args.manual_test_lab:env['CAD_TEST_MANUAL_LAB']='1'
            result=subprocess.run([sys.executable,'-u','-B',str(ROOT/'tests/community_http.py')],cwd=ROOT,env=env,
                capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=None if args.manual_test_lab else 360,creationflags=creationflags)
            (output/'http.log').write_text(result.stdout+result.stderr,encoding='utf-8')
            summary['runs'].append({'suite':'community_http','passed':result.returncode==0})
            if result.returncode:raise RuntimeError('Real HTTP integration failed; inspect private log')
        if args.identity_sessions:
            import runpy
            preparation=runpy.run_path(str(ROOT/'scripts/prepare-identity-candidate.py'))
            require(sql('create schema if not exists supabase_migrations; create table if not exists supabase_migrations.schema_migrations(version text primary key,name text,statements text[]);'+
                ''.join("insert into supabase_migrations.schema_migrations(version) values('"+v+"') on conflict do nothing;" for v in preparation['BASE'])),'Owned migration-history fixture')
            _,_,bundle=preparation['build_bundle'](expected_owner='cad3_admin',expected_database=database)
            if args.community_access:bundle=preparation['include_access_policy'](bundle)
            if args.device_renewal:bundle=preparation['include_device_renewal'](bundle)
            interrupted=sql(bundle.rsplit('commit;',1)[0]+"select 1/0;\ncommit;")
            assert interrupted.returncode!=0 and require(sql("select to_regclass('public.community_identity_sessions') is null;"),'Atomic rollback probe')=='t'
            summary['identityAtomicFailureRolledBack']=True
            require(sql(bundle),'Identity-session atomic candidate')
            assert require(sql("select count(*) from supabase_migrations.schema_migrations where version='202610240003';"),'Identity history record')=='1'
            summary['identityAtomicHistoryRecorded']=True
            result=subprocess.run([sys.executable,str(ROOT/'tests/identity_sessions_postgres.py')],cwd=ROOT,env=env,capture_output=True,text=True,timeout=120,creationflags=creationflags)
            (output/'identity-sessions.log').write_text(result.stdout+result.stderr,encoding='utf-8')
            summary['runs'].append({'suite':'identity_sessions_postgres','passed':result.returncode==0})
            if result.returncode:raise RuntimeError('Identity-session SQL tests failed; inspect owned evidence')
            result=subprocess.run(['node','--experimental-transform-types',str(ROOT/'tests/community_identity_session.mjs')],cwd=ROOT,env={**env,'CAD_TEST_IDENTITY_NATIVE':'1','CAD_TEST_DEVICE_RENEWAL':'1' if args.device_renewal else '0'},capture_output=True,text=True,timeout=120,creationflags=creationflags)
            (output/'identity-edge-native.log').write_text(result.stdout+result.stderr,encoding='utf-8')
            summary['runs'].append({'suite':'community_identity_session_native','passed':result.returncode==0})
            if result.returncode:raise RuntimeError('Identity Edge/native SQL tests failed; inspect owned evidence')
        if args.community_access:
            result=subprocess.run([sys.executable,str(ROOT/'tests/community_access_policy_postgres.py')],cwd=ROOT,env=env,capture_output=True,text=True,timeout=120,creationflags=creationflags)
            (output/'community-access-policy.log').write_text(result.stdout+result.stderr,encoding='utf-8')
            summary['runs'].append({'suite':'community_access_policy_postgres','passed':result.returncode==0})
            if result.returncode:raise RuntimeError('Community policy acceptance failed; inspect owned evidence')
        summary['extensions'] = require(sql('select extname from pg_extension order by extname;'), 'Extensions').splitlines()
        summary['passed'] = True
    finally:
        if started or (cluster/'data/postmaster.pid').exists():
            stop = command('pg_ctl','-D',str(cluster/'data'),'-m','fast','-w','stop')
            summary['stoppedOwnedCluster'] = stop.returncode == 0
            if stop.returncode: summary['passed'] = False
        save()
        print('PostgreSQL evidence: '+str(output), flush=True)
    if not summary['passed']: raise SystemExit(1)


if __name__ == '__main__': main()
