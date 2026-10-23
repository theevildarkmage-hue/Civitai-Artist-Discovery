-- Registry simplification. Apply once, transactionally after the seven pinned migrations.
-- Preserve all six approved fields and remove only obsolete disclosure bookkeeping.
lock table community_private.discovery_users in access exclusive mode;
create temporary table cad3_registry_minimal_before on commit drop as
 select coalesce(jsonb_agg(to_jsonb(u)-'disclosure_version' order by civitai_id),'[]'::jsonb) snapshot
 from community_private.discovery_users u;
drop function public.community_registry_upsert(bigint,text,text,text,text);
alter table community_private.discovery_users drop column disclosure_version;
create function public.community_registry_upsert(p_member bigint,p_name text,p_version text,p_platform text)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare changed bigint;begin
  if p_member is null or p_member<=0 or p_name is null or length(p_name) not between 1 and 200
     or p_version is null or length(p_version) not between 1 and 64
     or p_platform is null or p_platform not in ('Windows','Linux','macOS','Other') then raise exception 'INVALID_REQUEST';end if;
  insert into community_private.discovery_users(civitai_id,username,app_version,platform)
    values(p_member,p_name,p_version,p_platform)
    on conflict(civitai_id) do update set username=excluded.username,last_seen=clock_timestamp(),
      app_version=excluded.app_version,platform=excluded.platform
    where discovery_users.last_seen<=clock_timestamp()-interval '12 hours';
  get diagnostics changed=row_count;
  return jsonb_build_object('apiVersion',1,'registered',true,'updated',changed=1,'retryAfterSeconds',43200);
end $$;
revoke all on function public.community_registry_upsert(bigint,text,text,text) from public,anon,authenticated,service_role,community_operator;
grant execute on function public.community_registry_upsert(bigint,text,text,text) to service_role;

do $$begin
 if (select coalesce(jsonb_agg(to_jsonb(u) order by civitai_id),'[]'::jsonb) from community_private.discovery_users u)
    is distinct from (select snapshot from cad3_registry_minimal_before) then raise exception 'Registry preservation mismatch';end if;
end$$;
