-- LOCAL CANDIDATE ONLY. Apply transactionally AFTER the four pinned migrations.
-- Precondition: separately provisioned NOLOGIN/NOBYPASSRLS community_operator role.
-- No operator login, password, identity, invitation or enabled policy is seeded.
do $$ begin
  if not exists(select 1 from pg_roles where rolname='community_operator'
      and not rolcanlogin and not rolsuper and not rolbypassrls and not rolcreaterole and not rolcreatedb and not rolreplication) then
    raise exception 'Restricted community_operator role required';
  end if;
end $$;
create schema community_private;
revoke all on schema community_private from public,anon,authenticated,service_role,community_operator;
create table community_private.configuration(singleton boolean primary key default true check(singleton),ready boolean not null default false);
insert into community_private.configuration values(true,false);
create table community_private.operators(
  login name primary key, actor_id bigint not null check(actor_id>0),
  expires_at timestamptz not null check(isfinite(expires_at)), revoked boolean not null default false);
create table community_private.invitations(
  member_id bigint primary key check(member_id>0),
  expires_at timestamptz not null check(isfinite(expires_at)),revoked boolean not null default false);
create table community_private.blocks(member_id bigint primary key check(member_id>0),blocked boolean not null);
create table community_private.reports(
  seq bigint generated always as identity primary key,
  image_id bigint not null references public.community_recommendations(image_id),
  reporter_id bigint not null check(reporter_id>0),
  reason text not null check(reason in ('inappropriate','abuse','ownership','unavailable')),
  created_at timestamptz not null default clock_timestamp(),
  decision text not null default 'pending' check(decision in ('pending','resolved','dismissed')),
  unique(image_id,reporter_id));
create index community_report_queue on community_private.reports(seq) where decision='pending';
create table community_private.moderator_audit(
  seq bigint generated always as identity primary key,actor_id bigint not null,
  actor_login name not null,action text not null,target bigint,reason text not null,
  at timestamptz not null default clock_timestamp());
create table community_private.artwork_reviews(
  image_id bigint primary key references public.community_recommendations(image_id),
  state text not null default 'pending' check(state in ('pending','eligible','quarantined')),
  checked_at timestamptz,valid_until timestamptz,lease_until timestamptz,lease_id uuid,
  failure text check(failure in ('unavailable','rating','owner','transient')));
insert into community_private.artwork_reviews(image_id) select image_id from public.community_recommendations;
do $$ declare t text; begin
  foreach t in array array['configuration','operators','invitations','blocks','reports','moderator_audit','artwork_reviews'] loop
    execute format('alter table community_private.%I enable row level security',t);
    execute format('revoke all on community_private.%I from public,anon,authenticated,service_role,community_operator',t);
  end loop;
end $$;
revoke all on all sequences in schema community_private from public,anon,authenticated,service_role,community_operator;

-- One global security lock precedes every member/artist/image lock. Revocation,
-- blocks, replay and admission serialize at a defined transaction boundary.
create function community_private.assert_member(p_member bigint,p_write boolean default false) returns void
language plpgsql security definer set search_path=public,pg_temp as $$
begin
  perform pg_advisory_xact_lock(7530230001);
  if p_member is null or p_member<=0 or not coalesce((select ready from community_private.configuration where singleton),false)
     or (select count(*) from community_private.invitations where not revoked and expires_at>clock_timestamp())>5
     or not exists(select 1 from community_private.invitations where member_id=p_member and not revoked and expires_at>clock_timestamp()) then
    raise exception 'FORBIDDEN';
  end if;
  if p_write and exists(select 1 from community_private.blocks where member_id=p_member and blocked) then raise exception 'SUBMITTER_BLOCKED'; end if;
end $$;
revoke all on function community_private.assert_member(bigint,boolean) from public,anon,authenticated,service_role,community_operator;
create function public.community_access(p_member bigint) returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
begin perform community_private.assert_member(p_member);return jsonb_build_object('invited',true);end $$;

alter function public.community_submit(bigint,text,bigint,bigint,boolean) rename to community_submit_legacy;
alter function public.community_submit_once(bigint,text,bigint,bigint,text) rename to community_submit_once_legacy;
alter function public.community_withdraw(bigint,bigint) rename to community_withdraw_legacy;
alter function public.community_quota(bigint) rename to community_quota_legacy;
revoke all on function public.community_submit_legacy(bigint,text,bigint,bigint,boolean),
 public.community_submit_once_legacy(bigint,text,bigint,bigint,text),public.community_withdraw_legacy(bigint,bigint),
 public.community_quota_legacy(bigint),public.community_gallery(bigint,integer),public.community_moderate(bigint,text)
 from public,anon,authenticated,service_role,community_operator;

create function public.community_quota(p_member bigint) returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
begin perform community_private.assert_member(p_member);return public.community_quota_legacy(p_member);end $$;
create function public.community_submit(p_member bigint,p_name text,p_image bigint,p_creator bigint,p_preview boolean default false) returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
declare result jsonb;begin
  perform community_private.assert_member(p_member,true);
  result:=public.community_submit_legacy(p_member,p_name,p_image,p_creator,p_preview);
  if p_preview is false then insert into community_private.artwork_reviews(image_id,state,checked_at,valid_until)
    values(p_image,'eligible',clock_timestamp(),clock_timestamp()+interval '15 minutes') on conflict(image_id) do nothing;end if;
  return result;
end $$;
create function public.community_submit_once(p_member bigint,p_name text,p_image bigint,p_creator bigint,p_key text) returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
declare previous bigint;previous_creator bigint;result jsonb;begin
  perform community_private.assert_member(p_member,true);
  if p_name is null or length(p_name) not between 1 and 200 or p_image is null or p_image<=0
    or p_creator is null or p_creator<=0 or p_key is null or p_key !~ '^[A-Za-z0-9_-]{16,80}$' then raise exception 'INVALID_REQUEST';end if;
  if p_member=p_creator then raise exception 'SELF_RECOMMENDATION';end if;
  perform pg_advisory_xact_lock(hashtextextended('community-member:'||p_member,0));
  select k.image_id,r.creator_id into previous,previous_creator from public.community_submission_requests k
    join public.community_recommendations r on r.image_id=k.image_id where k.member_id=p_member and k.request_key=p_key;
  if found then
    if previous<>p_image or previous_creator<>p_creator then raise exception 'INVALID_REQUEST';end if;
    return jsonb_build_object('apiVersion',1,'imageId',previous,'quota',public.community_quota(p_member),'replayed',true);
  end if;
  result:=public.community_submit(p_member,p_name,p_image,p_creator,false);
  insert into public.community_submission_requests values(p_member,p_key,p_image);
  return result;
end $$;
create function public.community_withdraw(p_member bigint,p_image bigint) returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
begin perform community_private.assert_member(p_member);return public.community_withdraw_legacy(p_member,p_image);end $$;

create function public.community_report(p_member bigint,p_image bigint,p_reason text) returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
begin
  perform community_private.assert_member(p_member);
  if p_reason is null or p_reason not in ('inappropriate','abuse','ownership','unavailable') then raise exception 'INVALID_REQUEST';end if;
  if not exists(select 1 from public.community_recommendations where image_id=p_image) then raise exception 'INVALID_ARTWORK';end if;
  if exists(select 1 from community_private.reports where image_id=p_image and reporter_id=p_member) then
    return jsonb_build_object('apiVersion',1,'reported',true,'duplicate',true);end if;
  perform public.community_take_rate('report:'||p_member);
  if (select count(*) from community_private.reports where reporter_id=p_member and created_at>clock_timestamp()-interval '1 day')>=10 then raise exception 'RATE_LIMITED';end if;
  insert into community_private.reports(image_id,reporter_id,reason) values(p_image,p_member,p_reason);
  return jsonb_build_object('apiVersion',1,'reported',true,'duplicate',false);
end $$;

-- Shared leases bound all instances together: at most 3 exact-image validations
-- per gallery request, one lease per image, no background poller or stale display.
create function public.community_review_candidates(p_member bigint) returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
declare result jsonb;begin
  perform community_private.assert_member(p_member);
  with picked as (select v.image_id from community_private.artwork_reviews v
    join public.community_recommendations r using(image_id)
    where r.status='active' and not exists(select 1 from community_private.blocks b where b.member_id=r.member_id and b.blocked)
      and (v.valid_until is null or v.valid_until<=clock_timestamp()) and (v.lease_until is null or v.lease_until<=clock_timestamp())
    order by v.valid_until nulls first,v.image_id limit 3 for update of v skip locked),
  leased as (update community_private.artwork_reviews v set lease_until=clock_timestamp()+interval '60 seconds',lease_id=gen_random_uuid()
    from picked where v.image_id=picked.image_id returning v.image_id,v.lease_id)
  select coalesce(jsonb_agg(jsonb_build_object('imageId',r.image_id,'creatorId',r.creator_id,'lease',leased.lease_id)),'[]'::jsonb) into result
    from leased join public.community_recommendations r using(image_id);
  return result;
end $$;
create function public.community_record_review(p_member bigint,p_image bigint,p_creator bigint,p_lease uuid,p_failure text default null) returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
begin
  perform community_private.assert_member(p_member);
  if p_failure is not null and p_failure not in ('unavailable','rating','owner','transient') then raise exception 'INVALID_REQUEST';end if;
  if not exists(select 1 from public.community_recommendations where image_id=p_image and creator_id=p_creator) then raise exception 'INVALID_ARTWORK';end if;
  update community_private.artwork_reviews set state=case when p_failure is null then 'eligible' else 'quarantined' end,
    checked_at=clock_timestamp(),valid_until=case when p_failure is null then clock_timestamp()+interval '15 minutes' else null end,
    lease_until=case when p_failure='transient' then clock_timestamp()+interval '60 seconds' else clock_timestamp()+interval '15 minutes' end,
    failure=p_failure,lease_id=null where image_id=p_image and lease_id=p_lease and lease_until>clock_timestamp();
  if not found then raise exception 'INVALID_REQUEST';end if;
  return jsonb_build_object('recorded',true);
end $$;
create function public.community_beta_gallery(p_member bigint,p_cursor bigint default null,p_limit integer default 24) returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
declare rows jsonb;next_cursor text;begin
  perform community_private.assert_member(p_member);
  if p_limit is null or p_limit not between 1 and 50 or (p_cursor is not null and p_cursor<=0) then raise exception 'INVALID_REQUEST';end if;
  with page as (select r.*,m.username from public.community_recommendations r
    join public.community_members m on m.civitai_id=r.member_id
    join community_private.artwork_reviews v using(image_id)
    where r.status='active' and v.state='eligible' and v.valid_until>clock_timestamp()
      and not exists(select 1 from community_private.blocks b where b.member_id=r.member_id and b.blocked)
      and (p_cursor is null or r.seq<p_cursor) order by r.seq desc limit p_limit+1),
  shown as (select * from page order by seq desc limit p_limit)
  select coalesce((select jsonb_agg(jsonb_build_object('imageId',image_id,'creatorId',creator_id,
    'recommender',username,'submittedAt',submitted_at) order by seq desc) from shown),'[]'::jsonb),
    case when (select count(*) from page)>p_limit then (select min(seq)::text from shown) else null end into rows,next_cursor;
  return jsonb_build_object('apiVersion',1,'items',rows,'nextCursor',next_cursor);
end $$;

create function public.community_operator(p_action text,p_target bigint default null,p_reason text default null,p_until timestamptz default null) returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
declare actor bigint;result jsonb;begin
  perform pg_advisory_xact_lock(7530230001);
  select actor_id into actor from community_private.operators where login=session_user and not revoked and expires_at>clock_timestamp();
  if actor is null then raise exception 'FORBIDDEN';end if;
  if p_action='reports' then
    select coalesce(jsonb_agg(to_jsonb(q)),'[]'::jsonb) into result from
      (select seq,image_id,reporter_id,reason,created_at,decision from community_private.reports
       where seq>coalesce(p_target,0) order by seq limit 50) q;return result;
  end if;
  if p_action='audit' then
    select coalesce(jsonb_agg(to_jsonb(q)),'[]'::jsonb) into result from
      (select * from community_private.moderator_audit where seq>coalesce(p_target,0) order by seq limit 50) q;return result;
  end if;
  if p_reason is null or length(p_reason) not between 3 and 256 then raise exception 'INVALID_REQUEST';end if;
  if p_action<>'configure' and (p_target is null or p_target<=0) then raise exception 'INVALID_REQUEST';end if;
  case p_action
  when 'configure' then update community_private.configuration set ready=true where singleton;
    if not found then raise exception 'INVALID_REQUEST';end if;
  when 'invite' then
    if p_until is null or not isfinite(p_until) or p_until<=clock_timestamp() or p_until>clock_timestamp()+interval '30 days' then raise exception 'INVALID_REQUEST';end if;
    if (select count(*) from community_private.invitations where not revoked and expires_at>clock_timestamp() and member_id<>p_target)>=5 then raise exception 'INVITE_LIMIT';end if;
    insert into community_private.invitations values(p_target,p_until,false)
      on conflict(member_id) do update set expires_at=excluded.expires_at,revoked=false;
  when 'revoke_invite' then update community_private.invitations set revoked=true where member_id=p_target;
    if not found then raise exception 'INVALID_REQUEST';end if;
  when 'block' then insert into community_private.blocks values(p_target,true) on conflict(member_id) do update set blocked=true;
  when 'unblock' then update community_private.blocks set blocked=false where member_id=p_target;
    if not found then raise exception 'INVALID_REQUEST';end if;
  when 'hide','remove' then
    update public.community_recommendations set status=case p_action when 'hide' then 'hidden' else 'removed' end where image_id=p_target;
    if not found then raise exception 'INVALID_ARTWORK';end if;
    insert into public.community_audit(image_id,action) values(p_target,case p_action when 'hide' then 'hidden' else 'removed' end);
  when 'resolve_report','dismiss_report' then
    update community_private.reports set decision=case p_action when 'resolve_report' then 'resolved' else 'dismissed' end where seq=p_target and decision='pending';
    if not found then raise exception 'INVALID_REQUEST';end if;
  else raise exception 'INVALID_REQUEST';end case;
  insert into community_private.moderator_audit(actor_id,actor_login,action,target,reason) values(actor,session_user,p_action,p_target,p_reason);
  return jsonb_build_object('apiVersion',1,'completed',true);
end $$;
-- PUBLIC EXECUTE is the PostgreSQL default; every new RPC explicitly closes it.
revoke all on function public.community_access(bigint),public.community_quota(bigint),
 public.community_submit(bigint,text,bigint,bigint,boolean),public.community_submit_once(bigint,text,bigint,bigint,text),
 public.community_withdraw(bigint,bigint),public.community_report(bigint,bigint,text),
 public.community_review_candidates(bigint),public.community_record_review(bigint,bigint,bigint,uuid,text),
 public.community_beta_gallery(bigint,bigint,integer),public.community_operator(text,bigint,text,timestamptz)
 from public,anon,authenticated,service_role,community_operator;
grant execute on function public.community_access(bigint),public.community_quota(bigint),
 public.community_submit(bigint,text,bigint,bigint,boolean),public.community_submit_once(bigint,text,bigint,bigint,text),
 public.community_withdraw(bigint,bigint),public.community_report(bigint,bigint,text),
 public.community_review_candidates(bigint),public.community_record_review(bigint,bigint,bigint,uuid,text),
 public.community_beta_gallery(bigint,bigint,integer) to service_role;
grant execute on function public.community_operator(text,bigint,text,timestamptz) to community_operator;
