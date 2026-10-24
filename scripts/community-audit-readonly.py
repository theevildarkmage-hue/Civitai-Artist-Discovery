"""Fixed, minimized hosted audit queries; no arbitrary SQL or mutation interface.

Local owner-account tool, never bundled with the application. Credentials remain
inside this process. This is not a sandbox against other code under the same user.
"""
import argparse,hashlib,importlib.util,json,os,subprocess,sys,urllib.request,urllib.error
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
REF='aibelmhucizupzvvhdvf'
LAUNCHER_SHA256='a5e2e0e1d532fd1332daa209b2365510dcfbff24b75e55b7a6d92ff70957733a'
AUTHORITY="select current_user,current_database(),current_setting('transaction_read_only') as read_only"
QUERIES={
 'migrations':"select version,name,md5(coalesce(array_to_string(statements,E'\\n'),'')) as statement_md5 from supabase_migrations.schema_migrations order by version",
 'definitions':"select n.nspname as schema,p.proname as name,oidvectortypes(p.proargtypes) as signature,pg_get_functiondef(p.oid) as definition from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='community_private' or (n.nspname='public' and p.proname like 'community_%') order by n.nspname,p.proname,signature",
 'indexes':"select schemaname,tablename,indexname,indexdef from pg_indexes where schemaname='community_private' or (schemaname='public' and tablename like 'community_%') order by schemaname,tablename,indexname",
 'performance':"select schemaname,relname,n_live_tup,n_dead_tup,seq_scan,idx_scan,pg_total_relation_size(relid) as total_bytes from pg_stat_user_tables where schemaname='community_private' or (schemaname='public' and relname like 'community_%') order by schemaname,relname",
 'counts':"select (select count(*) from community_recommendations) recommendations,(select count(*) from community_audit) audits,(select count(*) from community_submission_requests) replay_records,(select count(*) from community_private.invitations) invitations,(select count(*) from community_private.blocks) blocks,(select count(*) from community_private.reports) reports,(select count(*) from community_private.discovery_users) registry_records",
}

def secure_token():
    token=os.environ.get('SUPABASE_ACCESS_TOKEN')
    if token:return token
    path=Path(os.environ['LOCALAPPDATA'])/'CAD3Tools/supabase-2.121.0/secure_env.py'
    if hashlib.sha256(path.read_bytes()).hexdigest()!=LAUNCHER_SHA256:
        raise RuntimeError('Secure launcher fingerprint differs; review it without recreating credentials')
    spec=importlib.util.spec_from_file_location('secure_audit_credential',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.load_token()

def transport(token):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args):return None
    opener=urllib.request.build_opener(NoRedirect)
    def read(path='',query=None):
        if path not in ('','/functions','/database/query/read-only'):
            raise ValueError('Audit endpoint not allowed')
        if (query is not None)!=(path=='/database/query/read-only'):
            raise ValueError('Audit query transport mismatch')
        request=urllib.request.Request('https://api.supabase.com/v1/projects/'+REF+path,
            data=None if query is None else json.dumps({'query':query}).encode(),
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        try:
            with opener.open(request,timeout=30) as response:
                body=response.read(2_000_001)
            if len(body)>2_000_000:raise RuntimeError('Audit response exceeds bound')
            return json.loads(body)
        except urllib.error.HTTPError as error:
            code=error.code;error.close()
            raise RuntimeError(f'Read-only audit HTTP {code}; response withheld') from None
        except (OSError,ValueError):raise RuntimeError('Read-only audit unavailable; response withheld') from None
    return read

def collect(read):
    project=read()
    if project.get('id')!=REF or project.get('region')!='us-west-2':
        raise RuntimeError('Exact audit project identity mismatch')
    authority=read('/database/query/read-only',AUTHORITY)
    if not authority or authority[0].get('read_only')!='on':
        raise RuntimeError('Audit SQL did not attest read-only authority')
    result={'projectRef':REF,'checkedAt':datetime.now(timezone.utc).isoformat(),
        'project':{k:project.get(k) for k in ('id','region','status')},'authority':authority,
        'hostedMutation':False,'querySha256':{},'limitations':[
        'Fixed catalog queries and aggregate counts; no private user/recommendation rows, OAuth sessions, logs, Auth accounts or secret endpoints.',
        'No SQL or HTTP denial writes are attempted. Metadata is not a substitute for an authenticated runtime test.',
        'Local same-account processes can independently access Windows Credential Manager; this helper is not an OS sandbox.']}
    queries={'catalog':(ROOT/'supabase/tests/security_inventory_v2.sql').read_text(encoding='utf-8'),**QUERIES}
    from discovery.diagnostics import redact_sensitive
    def sanitize(value):
        if isinstance(value,str):return redact_sensitive(value)
        if isinstance(value,list):return [sanitize(v) for v in value]
        if isinstance(value,dict):return {k:sanitize(v) for k,v in value.items()}
        return value
    for name,query in queries.items():
        result[name]=sanitize(read('/database/query/read-only',query))
        result['querySha256'][name]=hashlib.sha256(query.encode()).hexdigest()
    result['functions']=[{k:f.get(k) for k in ('slug','version','status','verify_jwt','updated_at')} for f in read('/functions')]
    try:
        result['localSourceCommit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip()
        result['localSourceDirty']=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip())
    except (OSError,subprocess.CalledProcessError):result['localSourceCommit']=None
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=collect(transport(secure_token()))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'projectRef':REF,'readOnlyVerified':True,'sections':len(result['querySha256']),'output':str(args.output),'credentialValuesPrinted':False}))

if __name__=='__main__':main()
