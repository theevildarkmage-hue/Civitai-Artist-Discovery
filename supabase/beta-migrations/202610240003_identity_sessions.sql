-- Additive candidate only. Apply through a reviewed, pinned deployment.
begin;
create table public.community_identity_replays (
  assertion_hash text primary key check (assertion_hash ~ '^[0-9a-f]{64}$'),
  nonce_hash text not null unique check (nonce_hash ~ '^[0-9a-f]{64}$'),
  expires_at timestamptz not null
);
create table public.community_identity_sessions (
  session_hash text primary key check (session_hash ~ '^[0-9a-f]{64}$'),
  civitai_id bigint not null check (civitai_id between 1 and 999999999999999),
  username text not null check (length(username) between 1 and 200),
  created_at timestamptz not null default clock_timestamp(),
  expires_at timestamptz not null,
  revoked boolean not null default false,
  check (expires_at > created_at and expires_at <= created_at + interval '12 hours 1 minute')
);
create index community_identity_sessions_member on public.community_identity_sessions(civitai_id);
create index community_identity_sessions_expiry on public.community_identity_sessions(expires_at);
alter table public.community_identity_replays enable row level security;
alter table public.community_identity_replays force row level security;
alter table public.community_identity_sessions enable row level security;
alter table public.community_identity_sessions force row level security;
revoke all on public.community_identity_replays,public.community_identity_sessions from public,anon,authenticated,service_role;

create function public.community_identity_exchange(p_assertion_hash text,p_nonce_hash text,
  p_session_hash text,p_member bigint,p_username text,p_proof_expires timestamptz)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare until_time timestamptz:=clock_timestamp()+interval '12 hours';
begin
  if p_assertion_hash is null or p_nonce_hash is null or p_session_hash is null
    or p_assertion_hash !~ '^[0-9a-f]{64}$' or p_nonce_hash !~ '^[0-9a-f]{64}$'
    or p_session_hash !~ '^[0-9a-f]{64}$' or p_member is null
    or p_member not between 1 and 999999999999999 or p_username is null
    or length(p_username) not between 1 and 200 or p_proof_expires is null
    or p_proof_expires<=clock_timestamp() or p_proof_expires>clock_timestamp()+interval '1 hour 1 minute'
    then raise exception 'AUTH_EXPIRED'; end if;
  -- Bound maintenance work; an anonymous challenge never reaches SQL.
  delete from public.community_identity_replays where assertion_hash in
    (select assertion_hash from public.community_identity_replays where expires_at<clock_timestamp() limit 500);
  delete from public.community_identity_sessions where session_hash in
    (select session_hash from public.community_identity_sessions where expires_at<clock_timestamp() limit 500);
  -- Unique constraints, in the same transaction as issuance, prevent cross-instance
  -- replay and concurrent duplicate exchanges. No raw assertion/session is stored.
  insert into public.community_identity_replays values(p_assertion_hash,p_nonce_hash,p_proof_expires+interval '1 minute');
  insert into public.community_identity_sessions(session_hash,civitai_id,username,expires_at)
    values(p_session_hash,p_member,p_username,until_time);
  return jsonb_build_object('expiresAt',extract(epoch from until_time)::bigint);
exception when unique_violation then raise exception 'AUTH_EXPIRED';
end $$;

create function public.community_identity_resolve(p_session_hash text)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare result jsonb;
begin
  if p_session_hash is null or p_session_hash !~ '^[0-9a-f]{64}$' then raise exception 'AUTH_EXPIRED'; end if;
  select jsonb_build_object('id',civitai_id,'username',username) into result
    from public.community_identity_sessions where session_hash=p_session_hash
      and not revoked and expires_at>clock_timestamp();
  if result is null then raise exception 'AUTH_EXPIRED'; end if;
  return result;
end $$;
create function public.community_identity_revoke(p_session_hash text)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
begin
  if p_session_hash is null or p_session_hash !~ '^[0-9a-f]{64}$' then raise exception 'AUTH_EXPIRED'; end if;
  update public.community_identity_sessions set revoked=true where session_hash=p_session_hash;
  return jsonb_build_object('revoked',true);
end $$;
revoke all on function public.community_identity_exchange(text,text,text,bigint,text,timestamptz) from public,anon,authenticated;
revoke all on function public.community_identity_resolve(text) from public,anon,authenticated;
revoke all on function public.community_identity_revoke(text) from public,anon,authenticated;
grant execute on function public.community_identity_exchange(text,text,text,bigint,text,timestamptz) to service_role;
grant execute on function public.community_identity_resolve(text) to service_role;
grant execute on function public.community_identity_revoke(text) to service_role;
commit;
