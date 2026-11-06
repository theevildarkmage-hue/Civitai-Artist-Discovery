-- Additive candidate: no historical records, grants or quotas are reset.
-- Callers still require fresh server-verified identity and an active invitation.
create function community_private.gallery_window(p_cursor bigint,p_limit integer) returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
declare rows jsonb;page_size integer;begin
  if p_limit is null or p_limit not between 1 and 50 or (p_cursor is not null and p_cursor<=0) then raise exception 'INVALID_REQUEST';end if;
  select coalesce(jsonb_agg(to_jsonb(q) order by q.seq desc),'[]'::jsonb) into rows from
    (select r.seq,r.image_id,r.creator_id,r.member_id,r.submitted_at,m.username,
       coalesce(v.state='eligible' and v.valid_until>clock_timestamp(),false) as eligible,
       coalesce(v.lease_id is not null and v.lease_until>clock_timestamp(),false) as pending
     from public.community_recommendations r
     join public.community_members m on m.civitai_id=r.member_id
     join community_private.artwork_reviews v using(image_id)
     where r.status='active' and (p_cursor is null or r.seq<p_cursor)
       and not exists(select 1 from community_private.blocks b where b.member_id=r.member_id and b.blocked)
     order by r.seq desc limit p_limit+1) q;
  -- Warm pages retain the requested size. Cold pages are a bounded raw page_window,
  -- never a global search for the first few *already eligible* recommendations.
  page_size=case when exists(select 1 from jsonb_array_elements(rows) with ordinality x(entry,n)
    where n<=p_limit and not (entry->>'eligible')::boolean) then least(3,p_limit) else p_limit end;
  return jsonb_build_object('rows',rows,'pageSize',page_size);
end $$;
revoke all on function community_private.gallery_window(bigint,integer) from public,anon,authenticated,service_role;

create function public.community_prepare_gallery(p_member bigint,p_cursor bigint default null,p_limit integer default 24) returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
declare page_window jsonb;result jsonb;begin
  perform community_private.assert_member(p_member);
  page_window=community_private.gallery_window(p_cursor,p_limit);
  with wanted as (select (entry->>'image_id')::bigint image_id
      from jsonb_array_elements(page_window->'rows') with ordinality x(entry,n)
      where n<=(page_window->>'pageSize')::integer and not (entry->>'eligible')::boolean),
    picked as (select v.image_id from community_private.artwork_reviews v join wanted using(image_id)
      where (v.valid_until is null or v.valid_until<=clock_timestamp())
        and (v.lease_until is null or v.lease_until<=clock_timestamp())
      order by v.image_id limit 3 for update of v skip locked),
    leased as (update community_private.artwork_reviews v set lease_id=gen_random_uuid(),lease_until=clock_timestamp()+interval '60 seconds'
      from picked where v.image_id=picked.image_id returning v.image_id,v.lease_id)
  select coalesce(jsonb_agg(jsonb_build_object('imageId',r.image_id,'creatorId',r.creator_id,'lease',leased.lease_id)),'[]'::jsonb)
    into result from leased join public.community_recommendations r using(image_id);
  return result;
end $$;
revoke all on function public.community_prepare_gallery(bigint,bigint,integer) from public,anon,authenticated;
grant execute on function public.community_prepare_gallery(bigint,bigint,integer) to service_role;

create or replace function public.community_beta_gallery(p_member bigint,p_cursor bigint default null,p_limit integer default 24) returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
declare page_window jsonb;rows jsonb;next_cursor text;pending boolean;begin
  perform community_private.assert_member(p_member);
  page_window=community_private.gallery_window(p_cursor,p_limit);
  with shown as (select entry from jsonb_array_elements(page_window->'rows') with ordinality x(entry,n)
    where n<=(page_window->>'pageSize')::integer)
  select coalesce(jsonb_agg(jsonb_build_object('imageId',(entry->>'image_id')::bigint,'creatorId',(entry->>'creator_id')::bigint,
      'recommender',entry->>'username','submittedAt',entry->'submitted_at') order by (entry->>'seq')::bigint desc)
      filter(where (entry->>'eligible')::boolean),'[]'::jsonb),
    case when jsonb_array_length(page_window->'rows')>(page_window->>'pageSize')::integer then min((entry->>'seq')::bigint)::text else null end,
    coalesce(bool_or(not (entry->>'eligible')::boolean and (entry->>'pending')::boolean),false)
    into rows,next_cursor,pending from shown;
  return jsonb_build_object('apiVersion',1,'items',rows,'nextCursor',next_cursor,'reviewPending',pending);
end $$;

create or replace function public.community_record_review(p_member bigint,p_image bigint,p_creator bigint,p_lease uuid,p_failure text default null) returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
begin
  perform community_private.assert_member(p_member);
  if p_failure is not null and p_failure not in ('unavailable','rating','owner','transient','viewer') then raise exception 'INVALID_REQUEST';end if;
  if not exists(select 1 from public.community_recommendations where image_id=p_image and creator_id=p_creator) then raise exception 'INVALID_ARTWORK';end if;
  if p_failure='viewer' then
    -- A single viewer's denial is not a global artwork verdict or cooldown.
    update community_private.artwork_reviews set lease_id=null,lease_until=null
      where image_id=p_image and lease_id=p_lease and lease_until>clock_timestamp();
  else
    update community_private.artwork_reviews set
      state=case when p_failure is null then 'eligible' when p_failure='transient' then state else 'quarantined' end,
      checked_at=clock_timestamp(),
      valid_until=case when p_failure is null then clock_timestamp()+interval '15 minutes' when p_failure='transient' then valid_until else null end,
      lease_until=case when p_failure='transient' then clock_timestamp()+interval '60 seconds' else clock_timestamp()+interval '15 minutes' end,
      failure=p_failure,lease_id=null where image_id=p_image and lease_id=p_lease and lease_until>clock_timestamp();
  end if;
  if not found then raise exception 'INVALID_REQUEST';end if;
  return jsonb_build_object('recorded',true);
end $$;

-- Retain the previous RPC during compatible rollback; retry fairness follows the
-- last attempt rather than permanently preferring NULL-validity quarantines.
create or replace function public.community_review_candidates(p_member bigint) returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
declare result jsonb;begin
  perform community_private.assert_member(p_member);
  with picked as (select v.image_id from community_private.artwork_reviews v
    join public.community_recommendations r using(image_id)
    where r.status='active' and not exists(select 1 from community_private.blocks b where b.member_id=r.member_id and b.blocked)
      and (v.valid_until is null or v.valid_until<=clock_timestamp()) and (v.lease_until is null or v.lease_until<=clock_timestamp())
    order by v.checked_at nulls first,v.image_id limit 3 for update of v skip locked),
  leased as (update community_private.artwork_reviews v set lease_until=clock_timestamp()+interval '60 seconds',lease_id=gen_random_uuid()
    from picked where v.image_id=picked.image_id returning v.image_id,v.lease_id)
  select coalesce(jsonb_agg(jsonb_build_object('imageId',r.image_id,'creatorId',r.creator_id,'lease',leased.lease_id)),'[]'::jsonb) into result
    from leased join public.community_recommendations r using(image_id);
  return result;
end $$;

create index if not exists community_rate_buckets_started_at_idx on public.community_rate_buckets(started_at);
