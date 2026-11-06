-- Follows private_beta_security. No collection is enabled or identity seeded.
create table community_private.discovery_users(
  civitai_id bigint primary key check(civitai_id>0),
  username text not null check(length(username) between 1 and 200),
  first_seen timestamptz not null default clock_timestamp(),
  last_seen timestamptz not null default clock_timestamp(),
  app_version text not null check(length(app_version) between 1 and 64),
  platform text not null check(platform in ('Windows','Linux','macOS','Other')),
  disclosure_version text not null check(disclosure_version='2026-10-23')
);
alter table community_private.discovery_users enable row level security;
revoke all on community_private.discovery_users from public,anon,authenticated,service_role,community_operator;

-- The Edge handler alone supplies an independently verified Civitai identity.
-- No Community membership, invitation, quota or audit record is created here.
create function public.community_registry_upsert(p_member bigint,p_name text,p_version text,p_platform text,p_disclosure text)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare changed bigint;begin
  if p_member is null or p_member<=0 or p_name is null or length(p_name) not between 1 and 200
     or p_version is null or length(p_version) not between 1 and 64
     or p_platform is null or p_platform not in ('Windows','Linux','macOS','Other')
     or p_disclosure is distinct from '2026-10-23' then raise exception 'INVALID_REQUEST';end if;
  insert into community_private.discovery_users(civitai_id,username,app_version,platform,disclosure_version)
    values(p_member,p_name,p_version,p_platform,p_disclosure)
    on conflict(civitai_id) do update set username=excluded.username,last_seen=clock_timestamp(),
      app_version=excluded.app_version,platform=excluded.platform,disclosure_version=excluded.disclosure_version
    where discovery_users.last_seen<=clock_timestamp()-interval '12 hours';
  get diagnostics changed=row_count;
  return jsonb_build_object('apiVersion',1,'registered',true,'updated',changed=1,'retryAfterSeconds',43200);
end $$;
revoke all on function public.community_registry_upsert(bigint,text,text,text,text) from public,anon,authenticated,service_role,community_operator;
grant execute on function public.community_registry_upsert(bigint,text,text,text,text) to service_role;
