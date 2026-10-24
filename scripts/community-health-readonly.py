"""Bounded admin-only readiness snapshot; no identities, tokens or log bodies."""
import argparse,hashlib,json,os,sys,urllib.request
from datetime import datetime,timezone
from pathlib import Path
REF='aibelmhucizupzvvhdvf'

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--expected-function-version',type=int,required=True)
    p.add_argument('--expected-migrations',type=int,choices=(8,9,10),default=8)
    p.add_argument('--expected-community-state',choices=('disabled','read_only','enabled'),default='disabled')
    p.add_argument('--expected-registry-state',choices=('disabled','enabled'),default='disabled');a=p.parse_args()
    token=os.environ.get('SUPABASE_ACCESS_TOKEN')
    if not token:p.error('Use the existing secure management launcher')
    def read(path,query=None):
        assert path in ('','/functions','/secrets','/database/query/read-only')
        req=urllib.request.Request('https://api.supabase.com/v1/projects/'+REF+path,data=json.dumps({'query':query}).encode() if query else None,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=20) as r:return json.load(r)
        except Exception:raise RuntimeError('Read-only health request failed; response withheld') from None
    project=read('');assert project['id']==REF and project['region']=='us-west-2'
    function=next(f for f in read('/functions') if f['slug']=='community-v1')
    values={s['name']:s.get('value') for s in read('/secrets')}
    policy={k:values.get(k)==hashlib.sha256(v.encode()).hexdigest() for k,v in {'COMMUNITY_BETA_STATE':a.expected_community_state,'COMMUNITY_ALLOWED_RATINGS':'1,2'}.items()}
    policy['registryStateMatches']=values.get('DISCOVERY_REGISTRY_STATE') in ((None,hashlib.sha256(b'disabled').hexdigest()) if a.expected_registry_state=='disabled' else (hashlib.sha256(b'enabled').hexdigest(),))
    policy['ownerWindowClosed']=values.get('COMMUNITY_OWNER_TEST_EXPIRES_AT')==hashlib.sha256(b'2000-01-01T00:00:00Z').hexdigest()
    owner_filter=' and member_id<>11107642' if a.expected_migrations>=9 else ''
    totals=read('/database/query/read-only',"""select
      (select count(*) from public.community_recommendations) recommendations,
      (select count(*) from public.community_audit) audits,
      (select count(*) from public.community_submission_requests) replay_records,
      (select count(*) from community_private.discovery_users) registry_records,
      (select count(*) from supabase_migrations.schema_migrations) migrations,
      (select count(*) from (select member_id from public.community_recommendations where week=date_trunc('week',clock_timestamp() at time zone 'UTC')::date"""+owner_filter+""" group by member_id having count(*)>3) x) member_overcaps,
      (select count(*) from (select creator_id from public.community_recommendations where week=date_trunc('week',clock_timestamp() at time zone 'UTC')::date group by creator_id having count(*)>3) x) artist_overcaps,
      (select count(*) from public.community_recommendations r where (select count(*) from public.community_audit a where a.image_id=r.image_id and a.action='accepted')<>1) audit_mismatches,
      (select coalesce(max(requests),0) from public.community_rate_buckets where key like 'token:%' and started_at>clock_timestamp()-interval '60 seconds') credential_requests_last_bucket,
      (select coalesce(max(requests),0) from public.community_rate_buckets where key like 'member:%' and started_at>clock_timestamp()-interval '60 seconds') member_requests_last_bucket,
      position('p_member<>11107642' in pg_get_functiondef('public.community_submit_legacy(bigint,text,bigint,bigint,boolean)'::regprocedure))>0 owner_exception_active,
      to_regprocedure('public.community_prepare_gallery(bigint,bigint,integer)') is not null gallery_window_active;""")[0]
    passed=project.get('status')=='ACTIVE_HEALTHY' and function['version']==a.expected_function_version and function['status']=='ACTIVE' and function.get('verify_jwt') is False and totals['migrations']==a.expected_migrations and (a.expected_migrations<9 or totals.get('owner_exception_active') is True) and (a.expected_migrations!=10 or totals.get('gallery_window_active') is True) and all(policy.values()) and not any(totals[k] for k in ('member_overcaps','artist_overcaps','audit_mismatches'))
    result={'checkedAt':datetime.now(timezone.utc).isoformat(),'projectRef':REF,'passed':passed,'hostedMutation':False,'function':{k:function.get(k) for k in ('version','status','verify_jwt')},'expectedPolicyDigestsMatch':policy,'aggregates':totals,'providerCostOrInvocationMeasurements':False,'limitations':['Explicit expected migration/policy health only; aggregate registry count, no identity enumeration. Owner-only registry_stats access uses the local management workflow, not a separate Auth/MFA account.','SQL bucket counts are not billed invocations or Civitai request measurements','No provider logs or network metadata retrieved']}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2));print(json.dumps({'passed':passed,'hostedMutation':False}));return not passed

if __name__=='__main__':sys.exit(main())
