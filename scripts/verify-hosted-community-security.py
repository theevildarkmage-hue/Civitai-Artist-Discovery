"""Read-only hosted catalog assertions after an approved deployment. No user rows or handler calls."""
import argparse,json,os,sys,urllib.request,urllib.error
from pathlib import Path

REF='aibelmhucizupzvvhdvf'
ROOT=Path(__file__).resolve().parents[1]
EXPECTED={'community_members','community_recommendations','community_audit','community_rate_buckets','community_submission_requests'}
RPCS={'community_quota','community_gallery','community_take_rate','community_submit','community_submit_once','community_withdraw','community_moderate'}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-ref',required=True,choices=[REF])
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--http-denials',action='store_true',help='After approval, use SUPABASE_PUBLISHABLE_KEY for bounded read/invalid-RPC probes. No valid recommendation inputs.')
    args=parser.parse_args()
    token=os.environ.get('SUPABASE_ACCESS_TOKEN')
    if not token:parser.error('SUPABASE_ACCESS_TOKEN unavailable; no request performed. Never paste credentials into arguments.')
    base='https://api.supabase.com/v1/projects/'+args.project_ref
    def read(path,query=None):
        assert path in ('/database/query/read-only','/functions','/database/migrations')
        payload=json.dumps({'query':query}).encode() if query else None
        req=urllib.request.Request(base+path,data=payload,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)
        except urllib.error.HTTPError as error:
            code=error.code;error.close();raise RuntimeError(f'Read-only audit HTTP {code}; response body withheld') from None
    query=(ROOT/'supabase/tests/security_inventory.sql').read_text()
    rows=read('/database/query/read-only',query)
    matrix=next(iter(rows[0].values()))
    errors=[]
    tables=matrix.get('relations') or []
    if {t['name'] for t in tables}!=EXPECTED:errors.append('Missing/extra Community relations')
    if matrix.get('policies'):errors.append('Unexpected Community RLS policies')
    for table in tables:
        if table['kind']!='r' or not table['rlsEnabled'] or table['owner']!='postgres':errors.append('Table ownership/RLS mismatch: '+table['name'])
        for access in table['access']:
            if access['role'] in ('anon','authenticated','service_role') and any(access[k] for k in ('select','insert','update','delete','truncate')):errors.append('Direct table privilege: '+table['name']+'/'+access['role'])
    sequences=matrix.get('sequences') or []
    if {s['name'] for s in sequences}!={'community_recommendations_seq_seq','community_audit_seq_seq'}:errors.append('Missing/extra Community sequences')
    for sequence in sequences:
        if sequence['owner']!='postgres':errors.append('Unexpected sequence owner: '+sequence['name'])
        for access in sequence['access']:
            if access['role'] in ('anon','authenticated','service_role') and any(access[k] for k in ('usage','select','update')):errors.append('Direct sequence privilege: '+sequence['name']+'/'+access['role'])
    routines=matrix.get('rpcs') or []
    if {r['name'] for r in routines}!=RPCS or len(routines)!=len(RPCS):errors.append('Missing/extra RPCs or overloads')
    for routine in routines:
        if routine['owner']!='postgres' or not routine['securityDefiner'] or ['search_path=public,pg_temp']!=[c.replace(' ','') for c in routine['configuration'] or []]:errors.append('RPC owner/search_path mismatch: '+routine['name'])
        for access in routine['access']:
            if access['role'] in ('anon','authenticated') and access['execute']:errors.append('Public RPC execution: '+routine['name'])
            if access['role']=='service_role' and not access['execute']:errors.append('Missing service RPC execution: '+routine['name'])
    functions=read('/functions')
    function=next((f for f in functions if f.get('slug')=='community-v1'),None)
    if not function or function.get('verify_jwt') is not False:errors.append('Missing function or unexpected platform JWT configuration')
    migrations=read('/database/migrations')
    versions={m.get('version') for m in migrations}
    if not {'202610140001','202610150001','202610210001','202610220001'}.issubset(versions):errors.append('Incomplete four-migration ledger; inspect schema before any repair')
    probes=[]
    if args.http_denials and not errors:
        public_key=os.environ.get('SUPABASE_PUBLISHABLE_KEY')
        if not public_key:parser.error('SUPABASE_PUBLISHABLE_KEY unavailable; no HTTP probes performed')
        def probe(path,body=None,auth=None):
            payload=json.dumps(body).encode() if body is not None else None
            headers={'apikey':public_key,'Content-Type':'application/json'}
            if auth:headers['Authorization']='Bearer '+auth
            req=urllib.request.Request('https://'+REF+'.supabase.co/rest/v1/'+path,data=payload,headers=headers)
            try:
                with urllib.request.urlopen(req,timeout=8) as response:code=response.status
            except urllib.error.HTTPError as error:code=error.code;error.close()
            probes.append({'path':path,'status':code,'authenticatedTestRole':bool(auth)})
            if code not in (401,403,404):errors.append('Expected HTTP denial missing: '+path)
        # Anonymous and, if supplied later, a dedicated Supabase test identity.
        identities=[None]
        if os.environ.get('SUPABASE_TEST_USER_JWT'):identities.append(os.environ['SUPABASE_TEST_USER_JWT'])
        for auth in identities:
            for table in sorted(EXPECTED):probe(table+'?limit=1',auth=auth)
            for name,payload in (
                ('community_quota',{'p_member':0}),('community_gallery',{}),
                ('community_take_rate',{'p_key':''}),
                ('community_submit',{'p_member':0,'p_name':'','p_image':0,'p_creator':0}),
                ('community_submit_once',{'p_member':0,'p_name':'','p_image':0,'p_creator':0,'p_key':''}),
                ('community_withdraw',{'p_member':0,'p_image':0}),
                ('community_moderate',{'p_image':0,'p_status':'invalid'})):
                probe('rpc/'+name,payload,auth)
    result={'projectRef':args.project_ref,'passed':not errors,'errors':errors,'matrix':matrix,'httpDenialProbes':probes,
        'limitations':['Catalog assertions plus optional bounded GET/invalid-input RPC denials only. No direct hosted CRUD mutations, valid recommendations, handler calls or Civitai OAuth. Authenticated-role HTTP coverage requires a dedicated Supabase test JWT.']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'passed':result['passed'],'errors':errors}));return bool(errors)

if __name__=='__main__':sys.exit(main())
