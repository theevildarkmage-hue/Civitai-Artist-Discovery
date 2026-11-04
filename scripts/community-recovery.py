"""Maintained Community health, encrypted export and owned-cluster restore.

Hosted requests are read-only. Restore/bootstrap only create an owned loopback
cluster; this tool cannot restore into a supplied database URL or hosted project.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from discovery.secure_backup import transform

SCOPE="(n.nspname='community_private' or (n.nspname='public' and c.relname like 'community_%'))"
LIMIT=32_000_000
SCHEMA_SQL="""select jsonb_build_object(
'columns',(select jsonb_agg(jsonb_build_object('table',c.relname,'column',a.attname,'position',a.attnum,'type',format_type(a.atttypid,a.atttypmod),'notnull',a.attnotnull,'identity',a.attidentity,'generated',a.attgenerated,'default',pg_get_expr(d.adbin,d.adrelid)) order by c.relname,a.attnum) from pg_class c join pg_namespace n on n.oid=c.relnamespace join pg_attribute a on a.attrelid=c.oid left join pg_attrdef d on d.adrelid=c.oid and d.adnum=a.attnum where SCOPE and c.relkind='r' and a.attnum>0 and not a.attisdropped),
'constraints',(select jsonb_agg(jsonb_build_object('table',c.relname,'name',x.conname,'definition',pg_get_constraintdef(x.oid),'validated',x.convalidated) order by c.relname,x.conname) from pg_constraint x join pg_class c on c.oid=x.conrelid join pg_namespace n on n.oid=c.relnamespace where SCOPE),
'indexes',(select jsonb_agg(to_jsonb(q) order by tablename,indexname) from (select tablename,indexname,indexdef from pg_indexes where schemaname='community_private' or(schemaname='public' and tablename like 'community_%')) q),
'triggers',(select coalesce(jsonb_agg(pg_get_triggerdef(t.oid) order by t.tgname),'[]') from pg_trigger t join pg_class c on c.oid=t.tgrelid join pg_namespace n on n.oid=c.relnamespace where SCOPE and not t.tgisinternal)) snapshot;""".replace('SCOPE',SCOPE)

def canonical(value):
    if isinstance(value,dict):return {k:canonical(v) for k,v in value.items() if k!='bodyStoredMd5'}
    if isinstance(value,list):return sorted([canonical(v) for v in value],key=lambda v:json.dumps(v,sort_keys=True))
    if isinstance(value,str):return value.replace('\r\n','\n').replace('search_path=public, pg_temp','search_path=public,pg_temp')
    return value

def digest(value):return hashlib.sha256(json.dumps(canonical(value),sort_keys=True,separators=(',',':')).encode()).hexdigest()
def file_digest(path):return hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()
def literal(value):return "'"+str(value).replace("'","''")+"'"
def json_literal(value):return literal(json.dumps(value))+'::jsonb'
def schema_query():return SCHEMA_SQL

def snapshot_query(tables,sequences):
    if any(not re.fullmatch(r'(public|community_private)\.[a-z_]+',x) for x in [*tables,*sequences]):raise ValueError('Invalid inventory name')
    parts=["'schema',("+SCHEMA_SQL.removesuffix(' snapshot;')+")"]
    parts += [literal(t)+",(select coalesce(jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text),'[]') from "+t+' t)' for t in tables]
    parts += ["'migrations',(select jsonb_agg(jsonb_build_object('version',version,'name',name,'statements',statements) order by version) from supabase_migrations.schema_migrations)",
              "'functionDefinitions',(select jsonb_agg(pg_get_functiondef(p.oid) order by p.proname) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='community_private' or(n.nspname='public' and p.proname like 'community_%'))"]
    parts += [literal(s)+",(select jsonb_build_object('last_value',last_value,'is_called',is_called) from "+s+')' for s in sequences]
    return 'select jsonb_build_object('+','.join(parts)+') snapshot;'

def source_interval_changes(captured,current):
    changed=sorted(k for k in set(captured)|set(current) if captured.get(k)!=current.get(k))
    if set(changed)-{'public.community_rate_buckets'}:raise RuntimeError('Protected state changed during capture')
    return changed

def load_manifest(path):
    path=path.resolve()
    manifest=json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('format')!=1 or manifest.get('environment') not in ('development','production'):raise ValueError('Unsupported recovery manifest')
    versions=[]
    for item in manifest['files']:
        source=(ROOT/item['path']).resolve()
        if not source.is_relative_to(ROOT) or not item['path'].startswith('supabase/') or source.suffix!='.sql':raise ValueError('Invalid manifest path')
        if file_digest(source)!=item['sha256Lf']:raise RuntimeError('Pinned migration source changed')
        if not re.fullmatch(r'\d{12}',item['version']):raise ValueError('Invalid migration version')
        versions.append(item['version'])
    if versions!=sorted(set(versions)):raise ValueError('Duplicate/unordered manifest versions')
    if not re.fullmatch(r'[0-9a-f]{64}',manifest.get('catalogSha256','')) or not re.fullmatch(r'[0-9a-f]{64}',manifest.get('schemaSha256','')):raise ValueError('Unsealed manifest')
    return manifest

def validate_history(rows,manifest):
    if [r['version'] for r in rows]!=[f['version'] for f in manifest['files']]:raise RuntimeError('Migration history differs from selected environment')
    for row,item in zip(rows,manifest['files'],strict=True):
        text=(ROOT/item['path']).read_text(encoding='utf-8')
        statements=row['statements']
        if not isinstance(statements,list) or not statements:raise RuntimeError('Migration statement history missing')
        # Supabase may store the complete source or split it at statement delimiters.
        if len(statements)==1:
            if statements[0].replace('\r\n','\n')!=text:raise RuntimeError('Migration statement source mismatch')
        else:
            remaining=text
            for statement in statements:
                statement=statement.replace('\r\n','\n').strip();remaining=remaining.lstrip()
                if not remaining.startswith(statement):raise RuntimeError('Split migration statement mismatch')
                remaining=remaining[len(statement):]
                if not remaining.startswith(';'):raise RuntimeError('Split migration delimiter missing')
                remaining=remaining[1:]
            if remaining.strip():raise RuntimeError('Extra migration statement text')

def validate_state(snapshot,catalog,manifest):
    validate_history(snapshot['migrations'],manifest)
    actual={k for k in snapshot if k.startswith(('public.','community_private.'))}
    if actual!=set(manifest['tables']+manifest['sequences']):raise RuntimeError('Exact object inventory differs')
    if digest(snapshot['schema'])!=manifest['schemaSha256']:raise RuntimeError('Schema/constraint/index/trigger drift')
    if digest(catalog)!=manifest['catalogSha256']:raise RuntimeError('Routine/role/ACL/RLS drift')

def secure_token(environment='development'):
    if environment not in ('development','production'):raise ValueError('Explicit credential environment required')
    if environment=='production':
        token=os.environ.get('SUPABASE_ACCESS_TOKEN')
        if not token or not token.strip():raise RuntimeError('Production requires an explicitly injected SUPABASE_ACCESS_TOKEN; Development credential fallback is forbidden')
        return token
    spec=importlib.util.spec_from_file_location('recovery_audit',ROOT/'scripts/community-audit-readonly.py')
    audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
    return audit.secure_token()

class ReadOnlyManagement:
    def __init__(self,project_ref,token):
        if not re.fullmatch(r'[a-z]{20}',project_ref):raise ValueError('Invalid project reference')
        self.ref=project_ref;self.token=token
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        self.opener=urllib.request.build_opener(NoRedirect)
    def read(self,path='',query=None):
        if path not in ('','/functions','/secrets','/database/query/read-only'):raise ValueError('Read-only operation required')
        if (query is not None)!=(path=='/database/query/read-only'):raise ValueError('Read-only query transport mismatch')
        request=urllib.request.Request('https://api.supabase.com/v1/projects/'+self.ref+path,data=None if query is None else json.dumps({'query':query}).encode(),headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        try:
            with self.opener.open(request,timeout=30) as response:body=response.read(LIMIT+1)
            if len(body)>LIMIT:raise RuntimeError('Bounded recovery response exceeded; no partial backup')
            return json.loads(body)
        except urllib.error.HTTPError as error:
            status=error.code;error.close();raise RuntimeError(f'Read-only management HTTP {status}; body withheld') from None
        except (OSError,ValueError):raise RuntimeError('Read-only management failed; details withheld') from None
    def verify(self):
        project=self.read()
        if project.get('id')!=self.ref or project.get('status')!='ACTIVE_HEALTHY':raise RuntimeError('Project identity/health mismatch')
        authority=self.read('/database/query/read-only',"select current_setting('transaction_read_only') as read_only")
        if authority!=[{'read_only':'on'}]:raise RuntimeError('Read-only SQL authority mismatch')

def capture(manager,manifest):
    q=lambda s:manager.read('/database/query/read-only',s)
    tables=[r['name'] for r in q("select n.nspname||'.'||c.relname as name from pg_class c join pg_namespace n on n.oid=c.relnamespace where c.relkind='r' and "+SCOPE+' order by 1')]
    seqs=[r['name'] for r in q("select n.nspname||'.'||c.relname as name from pg_class c join pg_namespace n on n.oid=c.relnamespace where c.relkind='S' and "+SCOPE+' order by 1')]
    if set(tables)!=set(manifest['tables']) or set(seqs)!=set(manifest['sequences']):raise RuntimeError('Hosted inventory differs; stop before export')
    snapshot=q(snapshot_query(tables,seqs))[0]['snapshot']
    catalog=next(iter(q((ROOT/'supabase/tests/security_inventory_v2.sql').read_text())[0].values()))
    validate_state(snapshot,catalog,manifest)
    return snapshot,catalog

class OwnedPostgres:
    """No external connection strings: every restore targets this owned cluster."""
    def __init__(self,bins):self.bins=bins.resolve();self.directory=None
    def __enter__(self):
        self.directory=Path(tempfile.mkdtemp(prefix='cad3-community-recovery-')).resolve()
        if os.name=='nt':
            principal=subprocess.check_output(['whoami'],text=True).strip()
            subprocess.run(['icacls',str(self.directory),'/inheritance:r','/grant:r',principal+':(OI)(CI)F'],capture_output=True,check=True)
        else:self.directory.chmod(0o700)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        password=secrets.token_urlsafe(40)
        # libpq gives PGHOSTADDR/PGSERVICE and PGOPTIONS independent authority:
        # overriding PGHOST alone does not isolate the connection or SQL parser.
        # Never pass hosted/application credentials into the native restore tools.
        inherited={key:value for key,value in os.environ.items()
                   if not key.upper().startswith(('PG','SUPABASE','CAD','CIVITAI'))}
        self.env={**inherited,'PGHOST':'127.0.0.1','PGPORT':str(port),'PGUSER':'postgres',
                  'PGDATABASE':'postgres','PGPASSWORD':password,'PGCONNECT_TIMEOUT':'5',
                  'PGTZ':'UTC','PGOPTIONS':'-c standard_conforming_strings=on','OSTYPE':'msys'}
        try:
            self.command('initdb','-D',str(self.directory/'data'),'--username=postgres','--auth=scram-sha-256','--pwprompt','--encoding=UTF8','--locale=C',input=password+'\n'+password+'\n')
            with (self.directory/'data/postgresql.conf').open('a') as file:file.write(f"\nlisten_addresses='127.0.0.1'\nport={port}\n")
            self.command('pg_ctl','-D',str(self.directory/'data'),'-l',str(self.directory/'server.log'),'-w','start')
            self.sql('create role anon;create role authenticated;create role service_role bypassrls;create role community_operator nologin noinherit;')
            return self
        except BaseException:self.__exit__(None,None,None);raise
    def command(self,name,*args,input=None,expect_error=None):
        executable=self.bins/(name+('.exe' if os.name=='nt' else ''))
        flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
        if name=='pg_ctl':
            with (self.directory/'control.log').open('ab') as log:process=subprocess.run([str(executable),*args],stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=self.env,timeout=30,**flags)
        else:process=subprocess.run([str(executable),*args],input=None if input is None else input.encode(),capture_output=True,env=self.env,timeout=90,**flags)
        stderr=getattr(process,'stderr',b'') or b''
        if expect_error:
            if not process.returncode or expect_error.encode() not in stderr:raise RuntimeError('Expected disposable SQL denial missing')
            return ''
        if process.returncode:raise RuntimeError('Owned PostgreSQL operation failed; private diagnostics withheld')
        return (getattr(process,'stdout',b'') or b'').decode().strip()
    def sql(self,text,expect_error=None):return self.command('psql','-X','-qAt','-v','ON_ERROR_STOP=1','-v','VERBOSITY=verbose',input=text,expect_error=expect_error)
    def bootstrap(self,manifest):
        self.sql('create schema supabase_migrations;create table supabase_migrations.schema_migrations(version text primary key,name text,statements text[]);')
        for item in manifest['files']:
            text=(ROOT/item['path']).read_text(encoding='utf-8')
            self.sql(text if re.search(r'^begin;',text,re.M|re.I) else 'begin;'+text+'commit;')
            self.sql("insert into supabase_migrations.schema_migrations values("+literal(item['version'])+','+literal(Path(item['path']).stem.split('_',1)[1])+',array['+literal(text)+']);')
    def state(self,manifest):
        snapshot=json.loads(self.sql(snapshot_query(manifest['tables'],manifest['sequences'])))
        catalog=json.loads(self.sql((ROOT/'supabase/tests/security_inventory_v2.sql').read_text()))
        return snapshot,catalog
    def restore(self,snapshot,manifest):
        dependencies=json.loads(self.sql("select coalesce(jsonb_agg(jsonb_build_array(conrelid::regclass::text,confrelid::regclass::text)),'[]') from pg_constraint where contype='f' and connamespace in ('public'::regnamespace,'community_private'::regnamespace)"))
        qualified=lambda name:name if '.' in name else 'public.'+name
        pending=set(manifest['tables']);ordered=[]
        while pending:
            available=sorted(t for t in pending if not any(qualified(a)==t and qualified(b) in pending and qualified(a)!=qualified(b) for a,b in dependencies))
            if not available:raise RuntimeError('Unsupported foreign-key cycle')
            ordered+=available;pending.difference_update(available)
        text='begin;truncate '+','.join(manifest['tables'])+',supabase_migrations.schema_migrations;'
        for table in ordered:text+='insert into '+table+' overriding system value select * from jsonb_populate_recordset(null::'+table+','+json_literal(snapshot[table])+');'
        text+='insert into supabase_migrations.schema_migrations select * from jsonb_populate_recordset(null::supabase_migrations.schema_migrations,'+json_literal(snapshot['migrations'])+');'
        for seq in manifest['sequences']:text+='select setval('+literal(seq)+','+str(int(snapshot[seq]['last_value']))+','+str(bool(snapshot[seq]['is_called'])).lower()+');'
        self.sql(text+'commit;')
    def __exit__(self,*args):
        if self.directory is None:return
        if (self.directory/'data/postmaster.pid').exists():self.command('pg_ctl','-D',str(self.directory/'data'),'-m','fast','-w','stop')
        target=self.directory.resolve()
        if target.parent!=Path(tempfile.gettempdir()).resolve() or not target.name.startswith('cad3-community-recovery-'):raise RuntimeError('Unsafe owned cleanup target')
        shutil.rmtree(target);self.directory=None

def verify_archive(path,manifest,bins,*,project_ref=None):
    if path.stat().st_size>LIMIT:raise RuntimeError('Backup archive exceeds supported bound')
    document=json.loads(transform(path.read_bytes(),decrypt=True))
    if document.get('format')!=1 or document.get('manifestSha256')!=digest(manifest):raise RuntimeError('Archive manifest identity mismatch')
    expected_ref=project_ref or manifest.get('projectRef')
    if manifest.get('projectRef') is not None and expected_ref!=manifest['projectRef']:raise RuntimeError('Project differs from selected archive manifest')
    if not isinstance(expected_ref,str) or not re.fullmatch(r'[a-z]{20}',expected_ref):raise RuntimeError('Archive verification requires a bound project reference')
    if document.get('projectRef')!=expected_ref:raise RuntimeError('Archive project identity mismatch')
    snapshot=document['snapshot'];catalog=document['catalog'];validate_state(snapshot,catalog,manifest)
    with OwnedPostgres(bins) as cluster:
        cluster.bootstrap(manifest)
        local,local_catalog=cluster.state(manifest);validate_state(local,local_catalog,manifest)
        # Execute only pinned repository sources. Definition/history strings from
        # an archive are data for equality checks, never executable restore input.
        cluster.restore(snapshot,manifest)
        restored,restored_catalog=cluster.state(manifest)
        if canonical(restored)!=canonical(snapshot) or canonical(restored_catalog)!=canonical(catalog):raise RuntimeError('Restored state/security differs')
        denials=0
        for role in ('anon','authenticated','service_role','community_operator'):
            for expr in ('select * from community_private.invitations',"select community_private.owner_admin('invite',999999999999,'Recovery denial test','infinity')"):
                cluster.sql('begin;set local role '+role+';'+expr+';rollback;',expect_error='42501');denials+=1
    return {'passed':True,'tablesRestored':len(manifest['tables']),'sequencesRestored':len(manifest['sequences']),'migrationCount':len(manifest['files']),'sqlPermissionDenials':denials,'archiveSha256':hashlib.sha256(path.read_bytes()).hexdigest(),'ownedClusterRemoved':True,'hostedMutation':False,'limitations':['Windows-account-bound DPAPI; not cross-account recovery','Community application scope; not Supabase Auth, Storage objects or secret values']}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('health','backup','verify','bootstrap'))
    parser.add_argument('--manifest',type=Path,default=ROOT/'supabase/recovery/development-v16.json')
    parser.add_argument('--project-ref',help='Explicit target; must match a bound manifest')
    parser.add_argument('--output',type=Path,required=True,help='New report directory; never overwrites an earlier backup')
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--bin',type=Path,help='Native PostgreSQL bin directory for owned-cluster operations')
    parser.add_argument('--expected-function-version',type=int)
    parser.add_argument('--expected-community-state',choices=('disabled','read_only','enabled'))
    parser.add_argument('--expected-registry-state',choices=('disabled','enabled'))
    parser.add_argument('--expected-access-mode',choices=('invite_only','public'))
    args=parser.parse_args(argv);manifest=load_manifest(args.manifest)
    if args.action in ('verify','bootstrap','backup') and not args.bin:parser.error('--bin is required for a verified recovery operation')
    if args.action=='verify' and not args.archive:parser.error('--archive required')
    if args.output.exists():parser.error('Output exists; use a new directory, do not overwrite evidence')
    args.output.mkdir(parents=True)
    if args.action=='verify':result=verify_archive(args.archive,manifest,args.bin,project_ref=args.project_ref)
    elif args.action=='bootstrap':
        with OwnedPostgres(args.bin) as cluster:
            cluster.bootstrap(manifest);snapshot,catalog=cluster.state(manifest);validate_state(snapshot,catalog,manifest)
        result={'passed':True,'environment':manifest['environment'],'migrationCount':len(manifest['files']),'ownedClusterRemoved':True,'hostedMutation':False}
    else:
        ref=args.project_ref or manifest.get('projectRef')
        if not ref:parser.error('Unbound Production manifest requires --project-ref')
        if manifest.get('projectRef') not in (None,ref):parser.error('Project differs from selected manifest')
        manager=ReadOnlyManagement(ref,secure_token(manifest['environment']));manager.verify()
        snapshot,catalog=capture(manager,manifest)
        configuration=snapshot['community_private.configuration']
        if len(configuration)!=1:raise RuntimeError('Community configuration singleton missing')
        access_mode=configuration[0].get('access_mode')
        expected_access=args.expected_access_mode or ('invite_only' if manifest['environment']=='development' else None)
        if expected_access is not None and access_mode!=expected_access:raise RuntimeError('Community access mode differs')
        function=next((f for f in manager.read('/functions') if f.get('slug')=='community-v1'),None)
        if not function or function.get('status')!='ACTIVE' or function.get('verify_jwt') is not False:raise RuntimeError('Edge function health/gateway mismatch')
        if args.expected_function_version is not None and function.get('version')!=args.expected_function_version:raise RuntimeError('Edge function revision differs')
        policy={}
        expected={k:v for k,v in {'COMMUNITY_BETA_STATE':args.expected_community_state,'DISCOVERY_REGISTRY_STATE':args.expected_registry_state}.items() if v is not None}
        if expected:
            values={row['name']:row.get('value') for row in manager.read('/secrets')}
            policy={k:values.get(k)==hashlib.sha256(v.encode()).hexdigest() for k,v in expected.items()}
            if not all(policy.values()):raise RuntimeError('Explicit hosted policy differs')
        result={'passed':True,'projectRef':ref,'environment':manifest['environment'],'migrationCount':len(manifest['files']),'tableCount':len(manifest['tables']),'rowCounts':{t:len(snapshot[t]) for t in manifest['tables']},'function':{k:function.get(k) for k in ('slug','version','status','verify_jwt')},'configuration':{'ready':configuration[0].get('ready'),'accessMode':access_mode},'policyDigestsMatch':policy,'hostedMutation':False}
        if args.action=='backup':
            document={'format':1,'projectRef':ref,'capturedAt':datetime.now(timezone.utc).isoformat(),'manifestSha256':digest(manifest),'snapshot':snapshot,'catalog':catalog}
            path=args.output/'community.dpapi';path.write_bytes(transform(json.dumps(document,separators=(',',':')).encode()))
            result['restore']=verify_archive(path,manifest,args.bin,project_ref=ref)
    (args.output/'verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'passed':result['passed'],'action':args.action,'hostedMutation':False}))
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except (RuntimeError,ValueError,KeyError,AssertionError) as error:
        print(str(error) if isinstance(error,(RuntimeError,ValueError)) else 'Recovery integrity validation failed; details withheld',file=sys.stderr)
        raise SystemExit(1)
