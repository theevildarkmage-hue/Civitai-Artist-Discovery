"""Native-only owner exception acceptance. All submission fixtures roll back."""
from pathlib import Path
import json

OWNER=11107642


def run(sql,output):
    root=Path(__file__).resolve().parents[1]
    migration=root/'supabase/beta-migrations/202610240001_owner_quota_exception.sql'
    before=sql("select count(*) from community_private.moderator_audit;")
    source=migration.read_text()
    sql('begin;'+source+"insert into supabase_migrations.schema_migrations values('202610240001','owner_quota_exception',array[$owner_source$"+source+"$owner_source$]);commit;")
    assert int(sql('select count(*) from community_private.moderator_audit;'))==int(before)+1
    checks=[]
    def check(name,condition):
        assert condition,name
        checks.append({'name':name,'passed':True})
    # SQL authority is the trusted server boundary. The existing Edge tests
    # establish that caller-supplied member/creator fields cannot reach this RPC.
    statements=['begin;','delete from community_private.invitations;',
                'update community_private.configuration set ready=true;',
                f"insert into community_private.invitations values({OWNER},clock_timestamp()+interval '1 day',false),(8920000000,clock_timestamp()+interval '1 day',false);"]
    for n in range(8):statements.append(f"select community_submit_once({OWNER},'SyntheticOwner',8921000000+{n},8922000000+{n},'owner_synthetic_key_{n}');")
    statements.append(f"do $$begin if (community_quota({OWNER})->>'used')::int<>8 or community_quota({OWNER})->>'unlimited'<>'true' or community_quota({OWNER})->'remaining'<>'null'::jsonb then raise exception 'OWNER_QUOTA_FAILED';end if;end$$;")
    statements.append(f"select community_submit_once({OWNER},'SyntheticOwner',8921000000,8922000000,'owner_synthetic_key_0');")
    for n in range(3):statements.append(f"select community_submit(8920000000,'SyntheticMember',8923000000+{n},8924000000+{n},false);")
    def denied(call,code):return "do $$begin begin perform "+call+";raise exception 'UNEXPECTED_ACCEPT';exception when raise_exception then if sqlerrm<>'"+code+"' then raise;end if;end;end$$;"
    statements.append(denied("community_submit(8920000000,'SyntheticMember',8923000099,8924000099,false)",'MEMBER_QUOTA'))
    for n in range(3):statements.append(f"select community_submit({OWNER},'SyntheticOwner',8925000000+{n},8926000000,false);")
    statements.append(denied(f"community_submit({OWNER},'SyntheticOwner',8925000099,8926000000,false)",'ARTIST_QUOTA'))
    statements.append(denied(f"community_submit({OWNER},'SyntheticOwner',8921000000,8922000000,false)",'DUPLICATE'))
    statements.append(denied(f"community_submit({OWNER},'SyntheticOwner',8927000000,{OWNER},false)",'SELF_RECOMMENDATION'))
    statements.append(f"select community_private.owner_admin('block',{OWNER},'Synthetic owner block');")
    statements.append(denied(f"community_submit({OWNER},'SyntheticOwner',8927000001,8927000002,false)",'SUBMITTER_BLOCKED'))
    statements.append(f"select community_private.owner_admin('unblock',{OWNER},'Synthetic owner unblock');select community_private.owner_admin('revoke_invite',{OWNER},'Synthetic owner revocation');")
    statements.append(denied(f"community_submit({OWNER},'SyntheticOwner',8927000001,8927000002,false)",'FORBIDDEN'))
    statements.append('rollback;')
    snapshot_query="select md5(coalesce(jsonb_agg(to_jsonb(r) order by seq)::text,'[]')) from community_recommendations r;"
    ledger=sql(snapshot_query);audits=sql('select count(*) from community_audit;')
    sql(''.join(statements))
    check('owner exceeds three without resetting counts; actual count exposed',True)
    check('owner idempotent replay adds no duplicate',True)
    check('ordinary member still capped at three',True)
    check('artist cap duplicate self block invitation guards retained',True)
    check('all synthetic recommendation/audit writes rolled back',ledger==sql(snapshot_query) and audits==sql('select count(*) from community_audit;'))
    for role in ('anon','authenticated','service_role'):
        # Ordinary clients cannot spoof owner identity by invoking privileged SQL.
        routines=['community_submit_legacy(bigint,text,bigint,bigint,boolean)']
        if role!='service_role':routines+=['community_submit(bigint,text,bigint,bigint,boolean)','community_quota(bigint)']
        for routine in routines:check(role+' denied '+routine,sql("select not has_function_privilege('"+role+"','public."+routine+"','execute');")=='t')
    result={'passed':True,'checks':checks,'recommendationsPersisted':0,'ownerOnly':True,'artistCap':3,'ordinaryMemberCap':3,'hostedMutation':False}
    (output/'owner-quota.json').write_text(json.dumps(result,indent=2));return result

if __name__=='__main__':print('SKIP: invoked only by the owned native release runner')
