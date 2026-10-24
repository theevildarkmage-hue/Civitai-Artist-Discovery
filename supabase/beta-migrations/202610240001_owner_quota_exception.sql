-- Owner-approved personal weekly quota exception for verified account 11107642.
-- Apply atomically after eight reviewed migrations. No quota reset or ACL change.
-- Public wrappers still require invitations/blocks; legacy RPC remains inaccessible.
create or replace function public.community_submit_legacy(p_member bigint,p_name text,p_image bigint,p_creator bigint,
                                       p_preview boolean default false) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare stamp timestamptz; w date;
begin
  if p_member is null or p_image is null or p_creator is null or p_name is null or p_preview is null
     or p_member<=0 or p_image<=0 or p_creator<=0 or length(p_name) not between 1 and 200 then
    raise exception 'INVALID_REQUEST';
  end if;
  -- Always lock member first, then artist, then image. Disjoint namespaces;
  -- no code path acquires a member lock after holding an artist/image lock.
  perform pg_advisory_xact_lock(hashtextextended('community-member:'||p_member,0));
  perform pg_advisory_xact_lock(hashtextextended('community-artist:'||p_creator,0));
  perform pg_advisory_xact_lock(hashtextextended('community-image:'||p_image,0));
  stamp:=clock_timestamp(); w:=date_trunc('week',stamp at time zone 'UTC')::date;
  if exists(select 1 from community_recommendations where image_id=p_image) then raise exception 'DUPLICATE'; end if;
  if p_member=p_creator then raise exception 'SELF_RECOMMENDATION'; end if;
  -- Count ALL accepted records, including withdrawals and moderation removals.
  if p_member<>11107642 and (select count(*) from community_recommendations where member_id=p_member and week=w)>=3 then
    raise exception 'MEMBER_QUOTA';
  end if;
  if (select count(*) from community_recommendations where creator_id=p_creator and week=w)>=3 then
    raise exception 'ARTIST_QUOTA';
  end if;
  if not p_preview then
    insert into community_members values(p_member,p_name)
      on conflict(civitai_id) do update set username=excluded.username;
    insert into community_recommendations(image_id,creator_id,member_id,submitted_at,week)
      values(p_image,p_creator,p_member,stamp,w);
    insert into community_audit(image_id,action) values(p_image,'accepted');
  end if;
  return jsonb_build_object('apiVersion',1,'imageId',p_image,'quota',community_quota(p_member));
end; $$;

create or replace function public.community_quota(p_member bigint) returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
declare result jsonb;
begin
  perform community_private.assert_member(p_member);
  result:=public.community_quota_legacy(p_member);
  if p_member=11107642 then
    return result || jsonb_build_object('unlimited',true,'limit',null,'remaining',null);
  end if;
  return result || jsonb_build_object('unlimited',false);
end $$;

-- Explicit owner-workflow attribution, not independently verified moderator identity.
insert into community_private.moderator_audit(actor_id,actor_login,action,target,reason)
values(1,'owner-admin:discovery3','owner_personal_quota_exception',11107642,
       'Owner approved unlimited personal testing recommendations; artist cap, invitations, blocks, duplicate and self-recommendation guards unchanged.');
