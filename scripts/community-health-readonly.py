"""Bounded admin-only readiness snapshot; no identities, tokens or log bodies."""
import argparse,hashlib,json,os,sys,urllib.request
from datetime import datetime,timezone
from pathlib import Path
REF='aibelmhucizupzvvhdvf'

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--expected-function-version',type=int,required=True);a=p.parse_args()
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
    policy={k:values.get(k)==hashlib.sha256(v.encode()).hexdigest() for k,v in {'COMMUNITY_BETA_STATE':'disabled','COMMUNITY_ALLOWED_RATINGS':'1,2'}.items()}
    totals=read('/database/query/read-only',"""select
      (select count(*) from public.community_recommendations) recommendations,
      (select count(*) from public.community_audit) audits,
      (select count(*) from public.community_submission_requests) replay_records,
      (select count(*) from (select member_id from public.community_recommendations where week=date_trunc('week',clock_timestamp() at time zone 'UTC')::date group by member_id having count(*)>3) x) member_overcaps,
      (select count(*) from (select creator_id from public.community_recommendations where week=date_trunc('week',clock_timestamp() at time zone 'UTC')::date group by creator_id having count(*)>3) x) artist_overcaps,
      (select count(*) from public.community_recommendations r where (select count(*) from public.community_audit a where a.image_id=r.image_id and a.action='accepted')<>1) audit_mismatches,
      (select coalesce(max(requests),0) from public.community_rate_buckets where key='project:community' and started_at>clock_timestamp()-interval '60 seconds') global_requests_last_bucket;""")[0]
    passed=project.get('status')=='ACTIVE_HEALTHY' and function['version']==a.expected_function_version and function['status']=='ACTIVE' and all(policy.values()) and not any(totals[k] for k in ('member_overcaps','artist_overcaps','audit_mismatches'))
    result={'checkedAt':datetime.now(timezone.utc).isoformat(),'projectRef':REF,'passed':passed,'hostedMutation':False,'function':{k:function.get(k) for k in ('version','status','verify_jwt')},'disabledPolicyDigestsMatch':policy,'aggregates':totals,'providerCostOrInvocationMeasurements':False,'limitations':['No registry enumeration or statistics; use authenticated MFA operator registry_stats','SQL bucket counts are not billed invocations or Civitai request measurements','No provider logs or network metadata retrieved']}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2));print(json.dumps({'passed':passed,'hostedMutation':False}));return not passed

if __name__=='__main__':sys.exit(main())
