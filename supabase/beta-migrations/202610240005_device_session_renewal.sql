-- Candidate only, after migrations 11 and 12. No activation or historical rewrite.
begin;
create table public.community_device_families(
 id uuid primary key default gen_random_uuid(),member_id bigint not null check(member_id>0),
 username text not null check(length(username) between 1 and 200),device_key jsonb not null,
 current_hash text not null unique check(current_hash~'^[0-9a-f]{64}$'),
 previous_hash text,request_hash text,created_at timestamptz not null default clock_timestamp(),
 idle_until timestamptz not null default clock_timestamp()+interval '30 days',
 absolute_until timestamptz not null default clock_timestamp()+interval '90 days',revoked boolean not null default false
);
create index community_device_previous on public.community_device_families(previous_hash);
create table public.community_device_proofs(proof_hash text primary key check(proof_hash~'^[0-9a-f]{64}$'),
 expires_at timestamptz not null default clock_timestamp()+interval '5 minutes');
alter table public.community_identity_sessions add column family_id uuid references public.community_device_families(id);
create index community_identity_family on public.community_identity_sessions(family_id);
alter table public.community_device_families enable row level security;
alter table public.community_device_families force row level security;
alter table public.community_device_proofs enable row level security;
alter table public.community_device_proofs force row level security;
revoke all on public.community_device_families,public.community_device_proofs from public,anon,authenticated,service_role,community_operator;

create function public.community_device_enroll(p_assertion_hash text,p_nonce_hash text,p_session_hash text,p_refresh_hash text,
 p_device_key jsonb,p_member bigint,p_username text,p_proof_expires timestamptz) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare family public.community_device_families%rowtype;result jsonb;
begin
 if p_refresh_hash is null or p_refresh_hash!~'^[0-9a-f]{64}$' or p_device_key is null
   or jsonb_typeof(p_device_key)<>'object' or p_device_key->>'kty'<>'EC' or p_device_key->>'crv'<>'P-256'
   or not (p_device_key ?& array['kty','crv','x','y']) or (select count(*) from jsonb_object_keys(p_device_key))<>4
   then raise exception 'AUTH_EXPIRED';end if;
 result:=public.community_identity_exchange(p_assertion_hash,p_nonce_hash,p_session_hash,p_member,p_username,p_proof_expires);
 insert into public.community_device_families(member_id,username,device_key,current_hash)
   values(p_member,p_username,p_device_key,p_refresh_hash) returning * into family;
 update public.community_identity_sessions set family_id=family.id where session_hash=p_session_hash;
 return result||jsonb_build_object('refreshExpiresAt',extract(epoch from family.idle_until)::bigint,
   'absoluteExpiresAt',extract(epoch from family.absolute_until)::bigint);
end $$;

create function public.community_device_lookup(p_refresh_hash text) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare family public.community_device_families%rowtype;
begin
 if p_refresh_hash is null or p_refresh_hash!~'^[0-9a-f]{64}$' then return jsonb_build_object('denied',true);end if;
 select * into family from public.community_device_families where current_hash=p_refresh_hash or previous_hash=p_refresh_hash;
 if not found or family.revoked or family.idle_until<=clock_timestamp() or family.absolute_until<=clock_timestamp()
   then return jsonb_build_object('denied',true);end if;
 return jsonb_build_object('memberId',family.member_id,'deviceKey',family.device_key);
end $$;

create function public.community_device_rotate(p_refresh_hash text,p_request_hash text,p_attempt_hash text,
 p_next_refresh_hash text,p_session_hash text,p_username text) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare family public.community_device_families%rowtype;until_time timestamptz;access_result jsonb;
begin
 if p_refresh_hash is null or p_request_hash is null or p_attempt_hash is null or p_next_refresh_hash is null or p_session_hash is null
   or p_refresh_hash!~'^[0-9a-f]{64}$' or p_request_hash!~'^[0-9a-f]{64}$' or p_attempt_hash!~'^[0-9a-f]{64}$'
   or p_next_refresh_hash!~'^[0-9a-f]{64}$' or p_session_hash!~'^[0-9a-f]{64}$'
   or p_username is null or length(p_username) not between 1 and 200 then return jsonb_build_object('denied',true);end if;
 select * into family from public.community_device_families where current_hash=p_refresh_hash or previous_hash=p_refresh_hash for update;
 if not found or family.revoked or family.idle_until<=clock_timestamp() or family.absolute_until<=clock_timestamp()
   then return jsonb_build_object('denied',true);end if;
 delete from public.community_device_proofs where proof_hash in
   (select proof_hash from public.community_device_proofs where expires_at<clock_timestamp() limit 500);
 delete from public.community_identity_sessions where session_hash in
   (select session_hash from public.community_identity_sessions where expires_at<clock_timestamp() limit 500);
 insert into public.community_device_proofs(proof_hash) values(p_attempt_hash) on conflict do nothing;
 if not found then return jsonb_build_object('denied',true);end if;
 if family.current_hash=p_refresh_hash then
  update public.community_device_families set previous_hash=current_hash,request_hash=p_request_hash,
    current_hash=p_next_refresh_hash,idle_until=least(absolute_until,clock_timestamp()+interval '30 days'),username=p_username where id=family.id;
 elsif family.request_hash is distinct from p_request_hash or family.current_hash is distinct from p_next_refresh_hash then
  -- Return instead of raising: revocation must commit on authenticated token reuse.
  update public.community_device_families set revoked=true where id=family.id;
  return jsonb_build_object('denied',true);
 end if;
 update public.community_device_families set idle_until=least(absolute_until,clock_timestamp()+interval '30 days'),username=p_username where id=family.id;
 -- Identity and Community authorization are distinct: denied Community access
 -- does not disable the independent minimal registry or the local Civitai grant.
 begin access_result:=public.community_access(family.member_id);
 exception when others then
  if sqlerrm not in ('INVITE_ONLY','INVITE_REVOKED','INVITE_EXPIRED','SUBMITTER_BLOCKED','BETA_DISABLED') then raise;end if;
  access_result:=jsonb_build_object('code',sqlerrm);
 end;
 until_time:=least(clock_timestamp()+interval '12 hours',family.absolute_until);
 insert into public.community_identity_sessions(session_hash,civitai_id,username,expires_at,family_id)
   values(p_session_hash,family.member_id,p_username,until_time,family.id);
 return jsonb_build_object('expiresAt',extract(epoch from until_time)::bigint,
   'refreshExpiresAt',extract(epoch from least(family.absolute_until,clock_timestamp()+interval '30 days'))::bigint,
   'absoluteExpiresAt',extract(epoch from family.absolute_until)::bigint,'communityAccess',access_result);
end $$;

create or replace function public.community_identity_resolve(p_session_hash text) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare result jsonb;
begin
 if p_session_hash is null or p_session_hash!~'^[0-9a-f]{64}$' then raise exception 'AUTH_EXPIRED';end if;
 select jsonb_build_object('id',s.civitai_id,'username',s.username) into result
 from public.community_identity_sessions s left join public.community_device_families f on f.id=s.family_id
 where s.session_hash=p_session_hash and not s.revoked and s.expires_at>clock_timestamp()
   and (s.family_id is null or (not f.revoked and f.idle_until>clock_timestamp() and f.absolute_until>clock_timestamp()));
 if result is null then raise exception 'AUTH_EXPIRED';end if;return result;
end $$;
create or replace function public.community_identity_revoke(p_session_hash text) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
begin
 if p_session_hash is null or p_session_hash!~'^[0-9a-f]{64}$' then raise exception 'AUTH_EXPIRED';end if;
 update public.community_device_families set revoked=true where id=(select family_id from public.community_identity_sessions where session_hash=p_session_hash);
 update public.community_identity_sessions set revoked=true where session_hash=p_session_hash;
 return jsonb_build_object('revoked',true);
end $$;
revoke all on function public.community_device_enroll(text,text,text,text,jsonb,bigint,text,timestamptz),
 public.community_device_lookup(text),public.community_device_rotate(text,text,text,text,text,text) from public,anon,authenticated,community_operator;
grant execute on function public.community_device_enroll(text,text,text,text,jsonb,bigint,text,timestamptz),
 public.community_device_lookup(text),public.community_device_rotate(text,text,text,text,text,text) to service_role;
create function community_private.owner_device_revoke(p_member bigint,p_reason text) returns jsonb
language plpgsql security invoker set search_path=pg_catalog,public as $$
begin
 if session_user<>current_user or current_user is distinct from
  pg_get_userbyid((select proowner from pg_proc where oid='community_private.owner_device_revoke(bigint,text)'::regprocedure))
  then raise exception 'FORBIDDEN';end if;
 if p_member is null or p_member<=0 or p_reason is null or length(p_reason) not between 3 and 256 then raise exception 'INVALID_REQUEST';end if;
 update public.community_device_families set revoked=true where member_id=p_member;
 insert into community_private.moderator_audit(actor_id,actor_login,action,target,reason)
   values(1,'owner-admin:discovery3','device_authorization_revoke',p_member,p_reason);
 return jsonb_build_object('revoked',true);
end $$;
revoke all on function community_private.owner_device_revoke(bigint,text) from public,anon,authenticated,service_role,community_operator;
commit;
