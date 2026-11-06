-- Prepared only; undeployed. Replay belongs to the verified member, never a browser identity.
create table public.community_submission_requests (
  member_id bigint not null references public.community_members(civitai_id),
  request_key text not null check (request_key ~ '^[A-Za-z0-9_-]{16,80}$'),
  image_id bigint not null references public.community_recommendations(image_id),
  primary key(member_id,request_key)
);
alter table public.community_submission_requests enable row level security;
revoke all on public.community_submission_requests from public,anon,authenticated;

create function public.community_submit_once(p_member bigint,p_name text,p_image bigint,p_creator bigint,p_key text)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare previous bigint; result jsonb;
begin
  if p_key is null or p_key !~ '^[A-Za-z0-9_-]{16,80}$' then raise exception 'INVALID_REQUEST'; end if;
  -- Same first lock as community_submit: concurrent requests serialize before replay lookup.
  perform pg_advisory_xact_lock(hashtextextended('community-member:'||p_member,0));
  select image_id into previous from community_submission_requests where member_id=p_member and request_key=p_key;
  if found then
    if previous<>p_image then raise exception 'INVALID_REQUEST'; end if;
    return jsonb_build_object('apiVersion',1,'imageId',p_image,'quota',community_quota(p_member),'replayed',true);
  end if;
  result:=community_submit(p_member,p_name,p_image,p_creator,false);
  insert into community_submission_requests values(p_member,p_key,p_image);
  return result;
end; $$;
revoke all on function public.community_submit_once(bigint,text,bigint,bigint,text) from public,anon,authenticated;
grant execute on function public.community_submit_once(bigint,text,bigint,bigint,text) to service_role;
