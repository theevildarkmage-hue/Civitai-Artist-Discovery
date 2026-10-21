-- Undeployed Community v1. Apply only to a separately approved Supabase project.
create table public.community_members (
  civitai_id bigint primary key check (civitai_id > 0),
  username text not null check (length(username) between 1 and 200)
);
create table public.community_recommendations (
  seq bigint generated always as identity primary key,
  image_id bigint not null unique check (image_id > 0),
  creator_id bigint not null check (creator_id > 0),
  member_id bigint not null references public.community_members(civitai_id),
  submitted_at timestamptz not null default clock_timestamp(),
  week date not null,
  status text not null default 'active' check (status in ('active','withdrawn','hidden','removed')),
  check (creator_id <> member_id),
  check (week = date_trunc('week',submitted_at at time zone 'UTC')::date)
);
create index community_member_week on public.community_recommendations(member_id,week);
create index community_artist_week on public.community_recommendations(creator_id,week);
create index community_active_page on public.community_recommendations(seq desc) where status='active';
create table public.community_audit (
  seq bigint generated always as identity primary key,
  image_id bigint not null references public.community_recommendations(image_id),
  action text not null check (action in ('accepted','withdrawn','hidden','removed')),
  at timestamptz not null default clock_timestamp()
);
create table public.community_rate_buckets (
  key text primary key, started_at timestamptz not null, requests integer not null
);
alter table public.community_members enable row level security;
alter table public.community_recommendations enable row level security;
alter table public.community_audit enable row level security;
alter table public.community_rate_buckets enable row level security;
-- No anon/authenticated policies. Only Edge Functions access these tables.
revoke all on public.community_members,public.community_recommendations,
  public.community_audit,public.community_rate_buckets from anon,authenticated;

create function public.community_quota(p_member bigint) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare start_utc timestamp := date_trunc('week',clock_timestamp() at time zone 'UTC'); used bigint;
begin
  select count(*) into used from community_recommendations
    where member_id=p_member and week=start_utc::date;
  return jsonb_build_object('used',used,'limit',3,'remaining',greatest(0,3-used),
    'week',start_utc::date,'resetsAt',(start_utc + interval '7 days') at time zone 'UTC');
end;
$$;

create function public.community_take_rate(p_key text) returns void
language plpgsql security definer set search_path = public, pg_temp as $$
declare n integer;
begin
  if length(p_key) > 100 then raise exception 'INVALID_REQUEST'; end if;
  -- TTL cleanup keeps only short-lived digests/counters, never tokens or IPs.
  delete from community_rate_buckets where started_at < clock_timestamp()-interval '2 minutes';
  insert into community_rate_buckets as b values(p_key,clock_timestamp(),1)
  on conflict(key) do update set
    requests=case when b.started_at < clock_timestamp()-interval '60 seconds' then 1 else b.requests+1 end,
    started_at=case when b.started_at < clock_timestamp()-interval '60 seconds' then clock_timestamp() else b.started_at end
  returning requests into n;
  if n>30 then raise exception 'RATE_LIMITED'; end if;
end; $$;

create function public.community_submit(p_member bigint,p_name text,p_image bigint,p_creator bigint,
                                       p_preview boolean default false) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare stamp timestamptz; w date;
begin
  if p_member<=0 or p_image<=0 or p_creator<=0 or length(p_name) not between 1 and 200 then
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
  if (select count(*) from community_recommendations where member_id=p_member and week=w)>=3 then
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

create function public.community_withdraw(p_member bigint,p_image bigint) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  perform 1 from community_recommendations where image_id=p_image and member_id=p_member for update;
  if not found then raise exception 'FORBIDDEN'; end if;
  update community_recommendations set status='withdrawn'
    where image_id=p_image and member_id=p_member and status='active';
  if found then insert into community_audit(image_id,action) values(p_image,'withdrawn'); end if;
  return jsonb_build_object('apiVersion',1,'imageId',p_image);
end; $$;

create function public.community_moderate(p_image bigint,p_status text) returns void
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  if p_status not in ('hidden','removed') then raise exception 'INVALID_REQUEST'; end if;
  update community_recommendations set status=p_status where image_id=p_image;
  if not found then raise exception 'INVALID_ARTWORK'; end if;
  insert into community_audit(image_id,action) values(p_image,p_status);
end; $$;

create function public.community_gallery(p_cursor bigint default null,p_limit integer default 24) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare rows jsonb; next_cursor text;
begin
  if p_limit not between 1 and 50 or p_cursor<=0 then raise exception 'INVALID_REQUEST'; end if;
  with page as (
    select r.*,m.username from community_recommendations r join community_members m on m.civitai_id=r.member_id
    where status='active' and (p_cursor is null or seq<p_cursor) order by seq desc limit p_limit+1
  ), shown as (select * from page order by seq desc limit p_limit)
  select coalesce((select jsonb_agg(jsonb_build_object('imageId',image_id,'creatorId',creator_id,
      'recommender',username,'submittedAt',submitted_at) order by seq desc) from shown),'[]'::jsonb),
      case when (select count(*) from page)>p_limit then (select min(seq)::text from shown) else null end
  into rows,next_cursor;
  return jsonb_build_object('apiVersion',1,'items',rows,'nextCursor',next_cursor);
end; $$;

-- PostgreSQL grants new functions to PUBLIC by default: explicitly remove it.
revoke all on function public.community_quota(bigint),public.community_take_rate(text),
 public.community_submit(bigint,text,bigint,bigint,boolean),public.community_withdraw(bigint,bigint),
 public.community_moderate(bigint,text),public.community_gallery(bigint,integer) from public,anon,authenticated;
grant execute on function public.community_quota(bigint),public.community_take_rate(text),
 public.community_submit(bigint,text,bigint,bigint,boolean),public.community_withdraw(bigint,bigint),
 public.community_moderate(bigint,text),public.community_gallery(bigint,integer) to service_role;
