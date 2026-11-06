from pathlib import Path
import json
def run(sql,raw,output):
 m=(Path(__file__).resolve().parents[1]/'supabase/beta-migrations/202610250001_recommendation_list.sql').read_text()
 sql(m.rsplit('commit;',1)[0]+'rollback;');sql(m)
 setup="begin;update community_private.configuration set ready=true;insert into community_members values(7000001,'Viewer'),(7000002,'Submitter');insert into community_private.invitations values(7000001,clock_timestamp()+interval '1 day',false);"
 cases=[]
 for n in (0,3,13,24,50,51,125):
  insert=f"insert into community_recommendations(image_id,creator_id,member_id,submitted_at,week) select i,8000000+i,7000002,statement_timestamp(),date_trunc('week',statement_timestamp() at time zone 'UTC')::date from generate_series(1,{n})i;"
  verify=f"""do $$declare p jsonb;c bigint;seen bigint[]='{{}}';x jsonb;begin loop p:=community_recommendation_list(7000001,c,50);
   assert jsonb_array_length(p->'items')=least(50,{n}-cardinality(seen));
   for x in select * from jsonb_array_elements(p->'items') loop assert not x?'artwork';assert not (x->>'imageId')::bigint=any(seen);seen:=array_append(seen,(x->>'imageId')::bigint);end loop;
   exit when p->>'nextCursor' is null;assert c is null or (p->>'nextCursor')::bigint<c;c:=(p->>'nextCursor')::bigint;end loop;assert cardinality(seen)={n};end$$;rollback;"""
  sql(setup+insert+verify);cases.append({'dataset':n,'pageSizes':[min(50,n-i) for i in range(0,n,50)] or [0]})
 # Each sql invocation is a separate psql connection; use one transaction per assertion block.
 for age,expected in [('0 hours',1),('144 hours',1),('167 hours 59 minutes 59 seconds',1),('168 hours',0),('169 hours',0)]:
  sql(setup+f"insert into community_recommendations(image_id,creator_id,member_id,submitted_at,week) values(1,8000001,7000002,statement_timestamp()-interval '{age}',date_trunc('week',(statement_timestamp()-interval '{age}') at time zone 'UTC')::date);do $$begin assert jsonb_array_length(community_recommendation_list(7000001)->'items')={expected};end$$;rollback;")
 denials=0
 sql(setup+"""insert into community_recommendations(image_id,creator_id,member_id,submitted_at,week)
 select i,8000000+i,7000002,statement_timestamp(),date_trunc('week',statement_timestamp() at time zone 'UTC')::date from generate_series(1,125)i;
 update community_recommendations set status='hidden' where image_id>100;
 update community_recommendations set status='removed' where image_id between 91 and 100;
 update community_recommendations set status='withdrawn' where image_id between 81 and 90;
 do $$declare p jsonb;c bigint;h text;begin
 select md5(jsonb_agg(to_jsonb(r) order by seq)::text) into h from community_recommendations r;
 p:=community_recommendation_list(7000001,null,50);assert jsonb_array_length(p->'items')=50;
 assert (p->'items'->0->>'imageId')::int=80;c:=(p->>'nextCursor')::bigint;
 assert h=(select md5(jsonb_agg(to_jsonb(r) order by seq)::text) from community_recommendations r);
 update community_recommendations set status='hidden' where seq<c and image_id=30;
 p:=community_recommendation_list(7000001,c,50);assert jsonb_array_length(p->'items')=29;
 insert into community_private.blocks values(8000080,true);
 assert (community_recommendation_list(7000001)->'items'->0->>'imageId')::int=79;
 update community_private.blocks set member_id=7000002;
 assert jsonb_array_length(community_recommendation_list(7000001)->'items')=0;
 update community_private.invitations set revoked=true;
 begin perform community_recommendation_list(7000001);raise exception 'BYPASS';exception when others then assert sqlerrm='INVITE_REVOKED';end;
 end$$;rollback;""")
 for role in ('anon','authenticated'):
  for op in ['select community_recommendation_list(7000001)','select * from community_recommendations',"insert into community_members values(1,'Forged')"]:
   p=raw(f'begin;set local role {role};{op};rollback;');assert p.returncode!=0 and 'permission denied' in p.stderr;denials+=1
 result=dict(passed=True,pagination=cases,expiryBoundaryCases=5,untrustedDenials=denials,mixedEligibility=True,moderationBetweenPages=True,creatorAndSubmitterBlocks=True,revocation=True,readImmutable=True,hostedRequests=0)
 (output/'accepted-feed.json').write_text(json.dumps(result,indent=2));return result
