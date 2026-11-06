"""Read-only hosted catalog assertions after an approved deployment. No user rows or handler calls."""
import argparse,json,os,sys,urllib.request,urllib.error
sys.path.insert(0,str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from discovery.community_catalog import validate_catalog
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
    parser.add_argument('--schema-model',choices=['baseline','candidate','release','automatic'],default='baseline')
    parser.add_argument('--catalog-file',type=Path,help='Offline local catalog only; never claims hosted verification')
    parser.add_argument('--expected-owner',default='postgres',help='Offline local catalog owner only; hosted owner is always postgres')
    parser.add_argument('--expected-definitions',type=Path,help='Reviewed LF-normalized native function body fingerprints')
    parser.add_argument('--expected-function-version',type=int)
    args=parser.parse_args()
    if args.catalog_file and args.http_denials:parser.error('Offline catalogs cannot run hosted probes')
    token=os.environ.get('SUPABASE_ACCESS_TOKEN')
    if not token and not args.catalog_file:parser.error('SUPABASE_ACCESS_TOKEN unavailable; no request performed. Never paste credentials into arguments.')
    base='https://api.supabase.com/v1/projects/'+args.project_ref
    def read(path,query=None):
        assert path in ('/database/query/read-only','/functions','/database/migrations')
        payload=json.dumps({'query':query}).encode() if query else None
        req=urllib.request.Request(base+path,data=payload,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)
        except urllib.error.HTTPError as error:
            code=error.code;error.close();raise RuntimeError(f'Read-only audit HTTP {code}; response body withheld') from None
    query=(ROOT/'supabase/tests/security_inventory_v2.sql').read_text()
    if args.catalog_file:
        document=json.loads(args.catalog_file.read_text(encoding='utf-8'))
        matrix=document.get('matrix',document)
        migrations=document.get('migrations')
        functions=[]
    else:
        rows=read('/database/query/read-only',query)
        matrix=next(iter(rows[0].values()))
        migrations=read('/database/migrations')
        functions=read('/functions')
    expected=None
    if args.expected_definitions:expected=json.loads(args.expected_definitions.read_text(encoding='utf-8'))
    verdict=validate_catalog(matrix,args.schema_model,owner=args.expected_owner if args.catalog_file else 'postgres',
        migrations=migrations,expected_definitions=expected)
    errors=list(verdict['errors'])
    function=next((f for f in functions if f.get('slug')=='community-v1'),None)
    if not args.catalog_file:
        if not function or function.get('verify_jwt') is not False or function.get('status')!='ACTIVE':
            errors.append('Missing/inactive function or unexpected platform JWT configuration')
        if args.expected_function_version is not None and (not function or function.get('version')!=args.expected_function_version):
            errors.append('Unexpected function revision; source compatibility must be checked separately')
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
    result={'projectRef':args.project_ref,'schemaModel':args.schema_model,
        'transport':'offline catalog' if args.catalog_file else 'hosted read-only Management API',
        'passed':not errors,'errors':errors,'checks':verdict['checks'],'matrix':matrix,'migrations':migrations,
        'function':{k:function.get(k) for k in ('slug','version','status','verify_jwt')} if function else None,
        'deploymentVerified':False,'httpDenialProbes':probes,
        'functionBodyFingerprintFormat':'as recorded in offline catalog' if args.catalog_file else 'LF-normalized; bodyStoredMd5 retains original bytes',
        'limitations':['Catalog verification is not function-source or deployment acceptance. Offline results make no hosted claim. Catalog assertions plus optional bounded GET/invalid-input RPC denials only. No direct hosted CRUD mutations, valid recommendations, handler calls or Civitai OAuth. Authenticated-role HTTP coverage requires a dedicated Supabase test JWT.']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'passed':result['passed'],'errors':errors}));return bool(errors)

if __name__=='__main__':sys.exit(main())
