-- Forward-only GA correction. No backfill, access-mode change or activation.
-- Keep the existing verified identity/session transaction and its exact ACLs.
begin;
create or replace function public.community_identity_exchange(p_assertion_hash text,p_nonce_hash text,
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
  delete from public.community_identity_replays where assertion_hash in
    (select assertion_hash from public.community_identity_replays where expires_at<clock_timestamp() limit 500);
  delete from public.community_identity_sessions where session_hash in
    (select session_hash from public.community_identity_sessions where expires_at<clock_timestamp() limit 500);
  insert into public.community_identity_replays values(p_assertion_hash,p_nonce_hash,p_proof_expires+interval '1 minute');
  insert into public.community_identity_sessions(session_hash,civitai_id,username,expires_at)
    values(p_session_hash,p_member,p_username,until_time);
  -- Identity is already verified by the existing service-only exchange. A member
  -- row is attribution, not access authority: ordinary policy/block/quota checks
  -- still run on every Community operation. Registry remains independent.
  if exists(select 1 from community_private.configuration where singleton and ready and access_mode='public') then
    insert into public.community_members(civitai_id,username) values(p_member,p_username)
      on conflict(civitai_id) do update set username=excluded.username;
  end if;
  return jsonb_build_object('expiresAt',extract(epoch from until_time)::bigint);
exception when unique_violation then raise exception 'AUTH_EXPIRED';
end $$;
commit;
