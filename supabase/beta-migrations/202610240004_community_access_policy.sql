-- Local candidate: beta invitations by default; public access requires owner action.
-- Apply after migration 202610240003. No activation, invitations or ledger writes.
begin;
alter table community_private.configuration add column access_mode text not null
  default 'invite_only' check(access_mode in ('invite_only','public'));

create or replace function community_private.assert_member(p_member bigint,p_write boolean default false) returns void
language plpgsql security definer set search_path=pg_catalog,public as $$
declare mode text; invitation community_private.invitations%rowtype;
begin
  perform pg_advisory_xact_lock(7530230001);
  if p_member is null or p_member<=0 then raise exception 'INVALID_REQUEST';end if;
  select access_mode into mode from community_private.configuration where singleton and ready;
  if mode is null then raise exception 'BETA_DISABLED';end if;
  if exists(select 1 from community_private.blocks where member_id=p_member and blocked) then raise exception 'SUBMITTER_BLOCKED';end if;
  if mode='public' then return;end if;
  if mode<>'invite_only' or (select count(*) from community_private.invitations where not revoked and expires_at>clock_timestamp())>5 then raise exception 'BETA_DISABLED';end if;
  select * into invitation from community_private.invitations where member_id=p_member;
  if not found then raise exception 'INVITE_ONLY';end if;
  if invitation.revoked then raise exception 'INVITE_REVOKED';end if;
  if invitation.expires_at<=clock_timestamp() then raise exception 'INVITE_EXPIRED';end if;
end $$;

create or replace function public.community_access(p_member bigint) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare mode text;
begin
  perform community_private.assert_member(p_member);
  select access_mode into mode from community_private.configuration where singleton;
  return jsonb_build_object('accessMode',mode,'invited',mode='invite_only');
end $$;

-- Private, invoker-only owner administration. Never grant to distributed roles.
create function community_private.owner_access_policy(p_mode text,p_reason text) returns jsonb
language plpgsql security invoker set search_path=pg_catalog,public as $$
begin
  if session_user<>current_user or current_user is distinct from
    pg_get_userbyid((select proowner from pg_proc where oid='community_private.owner_access_policy(text,text)'::regprocedure))
    then raise exception 'FORBIDDEN';end if;
  if p_mode is null or p_mode not in ('invite_only','public') or p_reason is null or length(p_reason) not between 3 and 256 then raise exception 'INVALID_REQUEST';end if;
  perform pg_advisory_xact_lock(7530230001);
  update community_private.configuration set access_mode=p_mode where singleton;
  if not found then raise exception 'BETA_DISABLED';end if;
  insert into community_private.moderator_audit(actor_id,actor_login,action,target,reason)
    values(1,'owner-admin:discovery3','community_access_policy:'||p_mode,null,p_reason);
  return jsonb_build_object('accessMode',p_mode);
end $$;
revoke all on function community_private.owner_access_policy(text,text) from public,anon,authenticated,service_role,community_operator;

-- Preserve the approved testing exception only in invitation beta. GA has the
-- ordinary three-per-member limit, including for the owner. Artist cap unchanged.
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
  if (p_member<>11107642 or (select access_mode from community_private.configuration where singleton)='public') and (select count(*) from community_recommendations where member_id=p_member and week=w)>=3 then
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
  if p_member=11107642 and (select access_mode from community_private.configuration where singleton)='invite_only' then
    return result || jsonb_build_object('unlimited',true,'limit',null,'remaining',null);
  end if;
  return result || jsonb_build_object('unlimited',false);
end $$;


commit;
