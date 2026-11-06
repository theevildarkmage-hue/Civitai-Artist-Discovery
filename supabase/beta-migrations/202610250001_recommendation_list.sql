-- Forward-only lightweight list. Earlier applied migrations remain unchanged.
begin;
create function community_private.recommendation_list(p_member bigint,p_cursor bigint,p_limit integer,p_now timestamptz)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare rows jsonb;next_cursor text;
begin
 perform community_private.assert_member(p_member);
 if p_limit is null or p_limit not between 1 and 50 or (p_cursor is not null and p_cursor<=0)
 then raise exception 'INVALID_REQUEST';end if;
 with page as materialized (
  select r.seq,r.image_id,r.creator_id,r.submitted_at,m.username
  from community_recommendations r join community_members m on m.civitai_id=r.member_id
  where r.status='active' and r.submitted_at>p_now-interval '168 hours' and r.submitted_at<=p_now
   and not exists(select 1 from community_private.blocks b where b.blocked and b.member_id in (r.member_id,r.creator_id))
   and (p_cursor is null or r.seq<p_cursor)
  order by r.seq desc limit p_limit+1
 ),shown as(select * from page order by seq desc limit p_limit)
 select coalesce((select jsonb_agg(jsonb_build_object('imageId',image_id,'creatorId',creator_id,
  'recommender',username,'submittedAt',submitted_at) order by seq desc) from shown),'[]'),
  case when (select count(*) from page)>p_limit then (select min(seq)::text from shown) else null end
 into rows,next_cursor;
 return jsonb_build_object('apiVersion',1,'feedVersion',3,'items',rows,'nextCursor',next_cursor);
end $$;
revoke all on function community_private.recommendation_list(bigint,bigint,integer,timestamptz) from public,anon,authenticated,service_role;
create function public.community_recommendation_list(p_member bigint,p_cursor bigint default null,p_limit integer default 24)
returns jsonb language sql security definer set search_path=public,pg_temp as $$
 select community_private.recommendation_list(p_member,p_cursor,p_limit,statement_timestamp());
$$;
revoke all on function public.community_recommendation_list(bigint,bigint,integer) from public,anon,authenticated;
grant execute on function public.community_recommendation_list(bigint,bigint,integer) to service_role;
-- Governance and existing rollback functions/grants/RLS are untouched.
commit;
