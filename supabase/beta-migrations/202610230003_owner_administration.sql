-- Supersedes the UNAPPLIED Auth-operator candidate by explicit owner request.
-- No Auth account, MFA, hosted login, or public administrative RPC is required.
create function community_private.owner_admin(p_action text,p_target bigint default null,p_reason text default null,p_until timestamptz default null)
returns jsonb language plpgsql security invoker set search_path=public,pg_temp as $$
declare result jsonb;begin
  -- This is infrastructure-owner administration, not per-moderator authentication.
  -- INVOKER plus owner/session checks reject PostgREST even with forged JWT claims.
  if session_user<>current_user or current_user is distinct from
     pg_get_userbyid((select proowner from pg_proc where oid='community_private.owner_admin(text,bigint,text,timestamptz)'::regprocedure))
     then raise exception 'FORBIDDEN';end if;
  if p_reason is null or length(p_reason) not between 3 and 256 then raise exception 'INVALID_REQUEST';end if;
  perform pg_advisory_xact_lock(7530230001);
  if p_action='invitation_status' then
    if p_target is null or p_target<=0 then raise exception 'INVALID_REQUEST';end if;
    select coalesce((select jsonb_build_object('memberId',member_id,'expiresAt',expires_at,'revoked',revoked) from community_private.invitations where member_id=p_target),'{}'::jsonb) into result;
    insert into community_private.moderator_audit(actor_id,actor_login,action,target,reason) values(1,'owner-admin:discovery3',p_action,p_target,p_reason);return result;
  end if;
  if p_action='registry_stats' then
    select jsonb_build_object('users',count(*),'active24h',count(*) filter(where last_seen>clock_timestamp()-interval '24 hours')) into result from community_private.discovery_users;
    insert into community_private.moderator_audit(actor_id,actor_login,action,target,reason) values(1,'owner-admin:discovery3',p_action,p_target,p_reason);return result;
  end if;
  if p_action='reports' then
    select coalesce(jsonb_agg(to_jsonb(q)),'[]'::jsonb) into result from
      (select seq,image_id,reporter_id,reason,created_at,decision from community_private.reports
       where seq>coalesce(p_target,0) order by seq limit 50) q;
    insert into community_private.moderator_audit(actor_id,actor_login,action,target,reason) values(1,'owner-admin:discovery3',p_action,p_target,p_reason);return result;
  end if;
  if p_action='audit' then
    select coalesce(jsonb_agg(to_jsonb(q)),'[]'::jsonb) into result from
      (select * from community_private.moderator_audit where seq>coalesce(p_target,0) order by seq limit 50) q;
    insert into community_private.moderator_audit(actor_id,actor_login,action,target,reason) values(1,'owner-admin:discovery3',p_action,p_target,p_reason);return result;
  end if;
  if p_reason is null or length(p_reason) not between 3 and 256 then raise exception 'INVALID_REQUEST';end if;
  if p_action not in ('configure','registry_retention') and (p_target is null or p_target<=0) then raise exception 'INVALID_REQUEST';end if;
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
  when 'registry_delete' then delete from community_private.discovery_users where civitai_id=p_target;
  when 'registry_retention' then
    -- Fixed conservative bound; execution requires separately approved retention.
    delete from community_private.discovery_users where last_seen<clock_timestamp()-interval '180 days';
  else raise exception 'INVALID_REQUEST';end case;
  insert into community_private.moderator_audit(actor_id,actor_login,action,target,reason) values(1,'owner-admin:discovery3',p_action,p_target,p_reason);
  return jsonb_build_object('apiVersion',1,'completed',true);
end $$;
revoke all on function community_private.owner_admin(text,bigint,text,timestamptz) from public,anon,authenticated,service_role,community_operator;
revoke execute on function public.community_operator(text,bigint,text,timestamptz) from community_operator;


-- Status errors disclose only the requesting member's eligibility, never a roster.
create or replace function public.community_access(p_member bigint) returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
declare invitation community_private.invitations%rowtype;begin
  perform pg_advisory_xact_lock(7530230001);
  if p_member is null or p_member<=0 then raise exception 'INVALID_REQUEST';end if;
  if not coalesce((select ready from community_private.configuration where singleton),false)
    or (select count(*) from community_private.invitations where not revoked and expires_at>clock_timestamp())>5 then raise exception 'BETA_DISABLED';end if;
  if exists(select 1 from community_private.blocks where member_id=p_member and blocked) then raise exception 'SUBMITTER_BLOCKED';end if;
  select * into invitation from community_private.invitations where member_id=p_member;
  if not found then raise exception 'INVITE_ONLY';end if;
  if invitation.revoked then raise exception 'INVITE_REVOKED';end if;
  if invitation.expires_at<=clock_timestamp() then raise exception 'INVITE_EXPIRED';end if;
  return jsonb_build_object('invited',true);
end $$;
