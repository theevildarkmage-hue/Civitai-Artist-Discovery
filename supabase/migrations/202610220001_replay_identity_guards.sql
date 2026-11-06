-- Additive function hardening; no table/data rewrite. Apply after all three
-- original migrations, transactionally. Display-name changes are not a different
-- recommendation: replay binds verified member, image and immutable uploader ID.
create or replace function public.community_submit_once(p_member bigint,p_name text,p_image bigint,p_creator bigint,p_key text)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare previous bigint; previous_creator bigint; result jsonb;
begin
  if p_member is null or p_member<=0 or p_image is null or p_image<=0
     or p_creator is null or p_creator<=0 or p_name is null
     or length(p_name) not between 1 and 200
     or p_key is null or p_key !~ '^[A-Za-z0-9_-]{16,80}$' then
    raise exception 'INVALID_REQUEST';
  end if;
  if p_member=p_creator then raise exception 'SELF_RECOMMENDATION'; end if;
  perform pg_advisory_xact_lock(hashtextextended('community-member:'||p_member,0));
  select k.image_id,r.creator_id into previous,previous_creator
    from community_submission_requests k
    join community_recommendations r on r.image_id=k.image_id
    where k.member_id=p_member and k.request_key=p_key;
  if found then
    if previous<>p_image or previous_creator<>p_creator then raise exception 'INVALID_REQUEST'; end if;
    return jsonb_build_object('apiVersion',1,'imageId',previous,'quota',community_quota(p_member),'replayed',true);
  end if;
  result:=community_submit(p_member,p_name,p_image,p_creator,false);
  insert into community_submission_requests values(p_member,p_key,p_image);
  return result;
end; $$;
-- Reassert the public boundary explicitly rather than rely on prior ACLs.
revoke all on function public.community_submit_once(bigint,text,bigint,bigint,text) from public,anon,authenticated;
grant execute on function public.community_submit_once(bigint,text,bigint,bigint,text) to service_role;
