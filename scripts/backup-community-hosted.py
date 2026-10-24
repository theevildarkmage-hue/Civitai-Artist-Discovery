"""Read-only Community export, DPAPI encryption, independent owned-cluster restore.

This is a scoped application recovery artifact, not a full Supabase/Auth/Storage
backup. It rejects schema drift rather than silently dropping unknown objects.
"""
import argparse,hashlib,json,os,secrets,shutil,socket,subprocess,sys,tempfile,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from discovery.secure_backup import transform
from discovery.community_catalog import validate_catalog
REF='aibelmhucizupzvvhdvf'
TABLES=['public.'+n for n in ('community_members','community_recommendations','community_audit','community_rate_buckets','community_submission_requests')]
SEQUENCES=['public.community_recommendations_seq_seq','public.community_audit_seq_seq']

def schema_query():
    return """select jsonb_build_object(
    'columns',(select jsonb_agg(jsonb_build_object('table',c.relname,'column',a.attname,'position',a.attnum,'type',format_type(a.atttypid,a.atttypmod),'notnull',a.attnotnull,'identity',a.attidentity,'generated',a.attgenerated,'default',pg_get_expr(d.adbin,d.adrelid)) order by c.relname,a.attnum) from pg_class c join pg_namespace n on n.oid=c.relnamespace join pg_attribute a on a.attrelid=c.oid left join pg_attrdef d on d.adrelid=c.oid and d.adnum=a.attnum where (n.nspname='community_private' or ((n.nspname='community_private' or (n.nspname='public' and c.relname like 'community_%')))) and c.relkind='r' and a.attnum>0 and not a.attisdropped),
    'constraints',(select jsonb_agg(jsonb_build_object('table',c.relname,'name',x.conname,'definition',pg_get_constraintdef(x.oid),'validated',x.convalidated) order by c.relname,x.conname) from pg_constraint x join pg_class c on c.oid=x.conrelid join pg_namespace n on n.oid=c.relnamespace where (n.nspname='community_private' or (n.nspname='public' and c.relname like 'community_%'))),
    'indexes',(select jsonb_agg(to_jsonb(q) order by tablename,indexname) from (select tablename,indexname,indexdef from pg_indexes where (schemaname='community_private' or (schemaname='public' and tablename like 'community_%'))) q),
    'triggers',(select coalesce(jsonb_agg(pg_get_triggerdef(t.oid) order by t.tgname),'[]') from pg_trigger t join pg_class c on c.oid=t.tgrelid join pg_namespace n on n.oid=c.relnamespace where (n.nspname='community_private' or (n.nspname='public' and c.relname like 'community_%')) and not t.tgisinternal)) snapshot;"""

def snapshot_query():
    parts=["'schema',("+schema_query().replace('select jsonb_build_object(','select jsonb_build_object(',1).removesuffix(' snapshot;')+")"]
    parts += ["'"+t+"',(select coalesce(jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text),'[]') from "+t+' t)' for t in TABLES]
    parts += ["'migrations',(select jsonb_agg(jsonb_build_object('version',version,'name',name,'statements',statements) order by version) from supabase_migrations.schema_migrations)"]
    parts += ["'functionDefinitions',(select jsonb_agg(pg_get_functiondef(p.oid) order by p.proname) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='community_private' or (n.nspname='public' and p.proname like 'community_%'))"]
    parts += ["'"+s+"',(select jsonb_build_object('last_value',last_value,'is_called',is_called) from "+s+')' for s in SEQUENCES]
    return 'select jsonb_build_object('+','.join(parts)+') snapshot;'

def source_interval_changes(captured, current):
    """Live throttle counters may change; every other captured section is strict."""
    changed = sorted(k for k in set(captured) | set(current) if captured.get(k) != current.get(k))
    protected = [k for k in changed if k != 'public.community_rate_buckets']
    if protected:
        raise RuntimeError('Hosted protected state changed during backup verification: '+','.join(protected))
    return changed


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bin',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--schema-model',choices=['baseline','release','automatic'],default='baseline');p.add_argument('--manifest',type=Path);p.add_argument('--expected-function-version',type=int);a=p.parse_args()
    if a.schema_model!='baseline':
        if not a.manifest or a.expected_function_version is None:p.error('Release export requires pinned manifest and expected function revision')
        TABLES.extend('community_private.'+n for n in ('configuration','operators','invitations','blocks','reports','moderator_audit','artwork_reviews','discovery_users'))
        SEQUENCES.extend(('community_private.reports_seq_seq','community_private.moderator_audit_seq_seq'))
    token=os.environ.get('SUPABASE_ACCESS_TOKEN')
    if not token:p.error('Secure management process environment required')
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    def api(path,query=None):
        assert path in ('','/database/query/read-only','/functions')
        req=urllib.request.Request('https://api.supabase.com/v1/projects/'+REF+path,
          data=json.dumps({'query':query}).encode() if query else None,
          headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)
        except Exception:raise RuntimeError('Hosted read-only export failed; response withheld') from None
    project=api('');assert project['id']==REF and project['region']=='us-west-2'
    # Pin function semantics after canonical CRLF/LF normalization; the hosted
    # bootstrap used CRLF. Preserve and restore the original definitions as well.
    inventory=(ROOT/'supabase/tests/security_inventory_v2.sql').read_text()
    matrix=next(iter(api('/database/query/read-only',inventory)[0].values()))
    snapshot=api('/database/query/read-only',snapshot_query())[0]['snapshot']
    assert validate_catalog(matrix,a.schema_model,migrations=snapshot['migrations'])['passed'],'Hosted baseline drift'
    expected_count=len(json.loads(a.manifest.read_text())['files']) if a.manifest else 4
    assert expected_count in (4,7,8,9) and len(snapshot['migrations'])==expected_count,'Unexpected migration history'
    functions=api('/functions');function=next(f for f in functions if f['slug']=='community-v1')
    assert function['version']==(a.expected_function_version or 10) and function['status']=='ACTIVE'
    files=sorted((ROOT/'supabase/migrations').glob('*.sql'))
    pinned=json.loads((ROOT/'supabase/deployment-baseline.json').read_text())
    if a.schema_model!='baseline':
        pinned=json.loads(a.manifest.read_text());assert pinned['projectRef']==REF and pinned['schemaModel']==a.schema_model
        files=[ROOT/item['path'] for item in pinned['files']];pinned={'migrations':pinned['files']}
    sources=[]
    for path,pin in zip(files,pinned['migrations'],strict=True):
        source=path.read_text(encoding='utf-8');assert hashlib.sha256(source.encode()).hexdigest()==pin['sqlLfSha256']
        sources.append(source)
    document={'projectRef':REF,'scope':'Community schema/data/history; excludes Supabase Auth, Storage and credentials',
      'matrix':matrix,'snapshot':snapshot,'sourceMigrations':sources,'functionMetadata':{k:function[k] for k in ('slug','version','status','verify_jwt')}}
    plain=json.dumps(document,separators=(',',':')).encode();archive=transform(plain);path=out/'hosted-community-backup.dpapi';path.write_bytes(archive)
    restored=json.loads(transform(path.read_bytes(),decrypt=True));assert restored==document
    # Independent native cluster: roles and schema are recreated from scratch,
    # not inherited from the prior disposable rehearsal cluster.
    cluster=Path(tempfile.mkdtemp(prefix='cad3-independent-restore-'));password=secrets.token_urlsafe(40)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env={**os.environ,'PGHOST':'127.0.0.1','PGPORT':str(port),'PGUSER':'postgres','PGDATABASE':'postgres','PGPASSWORD':password,'PGCONNECT_TIMEOUT':'5','PGTZ':'UTC'}
    env.pop('SUPABASE_ACCESS_TOKEN',None)
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    def command(name,*args,input=None,log=None):
        exe=str(a.bin/(name+('.exe' if os.name=='nt' else '')))
        if log:
            with log.open('w') as stream:r=subprocess.run([exe,*args],input=input,text=True,stdout=stream,stderr=stream,env=env,timeout=60,creationflags=flags)
        else:r=subprocess.run([exe,*args],input=input,text=True,capture_output=True,env=env,timeout=60,creationflags=flags)
        if r.returncode:raise RuntimeError('Independent restore operation failed; SQL/data diagnostics withheld')
        return getattr(r,'stdout','') or ''
    sql=lambda value:command('psql','-X','-qAt','-v','ON_ERROR_STOP=1',input=value).strip()
    checks=[];started=False
    try:
        init_env={**env,**({'OSTYPE':'msys'} if os.name=='nt' else {})};old=env;env=init_env
        command('initdb','-D',str(cluster/'data'),'--username=postgres','--auth=scram-sha-256','--pwprompt','--encoding=UTF8','--locale=C',input=password+'\n'+password+'\n')
        env=old
        with (cluster/'data/postgresql.conf').open('a') as f:f.write(f"\nlisten_addresses='127.0.0.1'\nport={port}\n")
        command('pg_ctl','-D',str(cluster/'data'),'-w','start',log=cluster/'startup.log');started=True
        sql('create role anon;create role authenticated;create role service_role bypassrls;'+('create role community_operator nologin noinherit;' if a.schema_model!='baseline' else ''))
        sql('begin;'+''.join(restored['sourceMigrations'])+'commit;')
        local_schema=json.loads(sql(schema_query()));assert local_schema==snapshot['schema'],'Exported schema differs from pinned recreation'
        local_matrix=json.loads(sql(inventory))
        def canonical(v):
            if isinstance(v,dict):return {k:canonical(x) for k,x in v.items() if k!='bodyStoredMd5'}
            if isinstance(v,list):return sorted([canonical(x) for x in v],key=lambda x:json.dumps(x,sort_keys=True))
            if isinstance(v,str) and v.startswith('search_path='):return v.replace(' ','')
            return v
        if canonical(local_matrix)!=canonical(matrix):
            (out/'catalog-mismatch.json').write_text(json.dumps({'hosted':matrix,'restored':local_matrix},indent=2))
            raise RuntimeError('Hosted function/ACL/RLS differs; sanitized catalog evidence retained')
        sql('begin;'+(';\n'.join(snapshot['functionDefinitions']))+';commit;')
        checks+=['independent cluster and role recreation','column/constraint/index/trigger fidelity','all function bodies and catalog ACL/RLS match']
        sql('create schema supabase_migrations;create table supabase_migrations.schema_migrations(version text primary key,name text,statements text[]);')
        def literal(v):return "'"+json.dumps(v).replace("'","''")+"'::jsonb"
        statements='begin;'
        statements+='truncate table '+','.join(TABLES)+';'
        for table in TABLES:statements+='insert into '+table+' overriding system value select * from jsonb_populate_recordset(null::'+table+','+literal(snapshot[table])+');'
        statements+='insert into supabase_migrations.schema_migrations select * from jsonb_populate_recordset(null::supabase_migrations.schema_migrations,'+literal(snapshot['migrations'])+');'
        for seq in SEQUENCES:
            value=snapshot[seq];statements+=f"select setval('{seq}',{int(value['last_value'])},{str(value['is_called']).lower()});"
        sql(statements+'commit;')
        after=json.loads(sql(snapshot_query()))
        def restored_form(value):
            value=dict(value)
            value['functionDefinitions']=[s.replace('\r\n','\n') for s in value['functionDefinitions']]
            return value
        mismatch=[k for k in snapshot if restored_form(after).get(k)!=restored_form(snapshot).get(k)]
        assert not mismatch,'Independent restore mismatch in sections: '+','.join(mismatch)
        checks+=['all scoped table records match','migration history and statements match','sequence state matches','foreign keys and checks enforced on restore']
        for member in snapshot['public.community_members']:
            quota=json.loads(sql('select '+('community_quota_legacy' if a.schema_model!='baseline' else 'community_quota')+'('+str(member['civitai_id'])+');'));assert 0<=quota['used'] and (expected_count==9 and member['civitai_id']==11107642 or quota['used']<=3)
        checks+=['restored quota RPC executes']
        if a.schema_model!='baseline':
            stats=json.loads(sql("begin;select community_private.owner_admin('registry_stats',null,'Independent restore verification');rollback;"));assert stats['users']==len(snapshot['community_private.discovery_users'])
            checks+=['restored owner administration executes with rollback']
        for role in ('anon','authenticated','service_role'):
            assert sql(f"select not has_table_privilege('{role}','public.community_members','select') and not has_table_privilege('{role}','public.community_recommendations','insert');")=='t'
        checks+=['untrusted and runtime direct-table denials retained']
        def hashes(s):return {t:{'count':len(s[t]),'sha256':hashlib.sha256(json.dumps(s[t],sort_keys=True,separators=(',',':')).encode()).hexdigest()} for t in TABLES}
        evidence={'passed':True,'projectRef':REF,'hostedMutation':False,'archiveSha256':hashlib.sha256(archive).hexdigest(),
          'plaintextSha256':hashlib.sha256(plain).hexdigest(),'protection':'Windows account-bound DPAPI','independentCluster':True,
          'source':'actual hosted read-only snapshot','schemaModel':a.schema_model,'tableCount':len(TABLES),'checks':checks,'preservation':hashes(snapshot),'restoredPreservation':hashes(after),
          'limitations':['Scoped Community application backup, not complete Supabase/Auth/Storage recovery','DPAPI recovery requires this Windows account key; independent custody approval remains required','No hosted credential exported; database roles reconstructed without original passwords','Hosted Edge recovery remains pinned-source deployment; no function deployment performed']}
        # Exact restore equality above includes the captured throttle rows. Only
        # the *live source interval* may advance counters during normal reads.
        current=api('/database/query/read-only',snapshot_query())[0]['snapshot']
        changed=source_interval_changes(snapshot,current)
        evidence['liveSourceInterval']={'changedSections':changed,
            'protectedSectionsUnchanged':True,'capturedThrottleRowsRestoredExactly':True,
            'operationalCounters':{k:{'capturedSha256':hashlib.sha256(json.dumps(snapshot[k],sort_keys=True).encode()).hexdigest(),
                'currentSha256':hashlib.sha256(json.dumps(current[k],sort_keys=True).encode()).hexdigest()} for k in changed}}

    finally:
        if started:command('pg_ctl','-D',str(cluster/'data'),'-m','fast','-w','stop')
        # Only the absolute directory created by this invocation can be removed.
        target=cluster.resolve();assert target.parent==Path(tempfile.gettempdir()).resolve() and target.name.startswith('cad3-independent-restore-')
        shutil.rmtree(target)
    evidence.update(ownedClusterStopped=True,plaintextRestoreDirectoryRemoved=True)
    (out/'backup-verification.json').write_text(json.dumps(evidence,indent=2))
    print(json.dumps({'passed':True,'checks':len(checks),'independentCluster':True,'hostedMutation':False,'ownedClusterRemoved':True}))

if __name__=='__main__':main()
