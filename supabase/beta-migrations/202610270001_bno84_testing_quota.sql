-- Owner-approved invite-only beta personal quota exception for bno84.
-- Retains artist caps, access policy, identity, duplicate, replay and RLS controls.
begin;

do $quota_patch$ declare definition text; begin
 definition:=pg_get_functiondef('community_quota(bigint)'::regprocedure);
 if md5(definition)<>'b8adaafb61b18e7649f0b1b0c96a21ec' then raise exception 'QUOTA_SOURCE_MISMATCH';end if;
 execute replace(definition,'p_member=11107642','p_member in (11107642,12594272)');
end $quota_patch$;

do $quota_patch$ declare definition text; begin
 definition:=pg_get_functiondef('community_submit_legacy(bigint,text,bigint,bigint,boolean)'::regprocedure);
 if md5(definition)<>'97f6071810106dd7a65a33bcaea04b1a' then raise exception 'QUOTA_SOURCE_MISMATCH';end if;
 execute replace(definition,'p_member<>11107642','p_member not in (11107642,12594272)');
end $quota_patch$;

insert into community_private.moderator_audit(actor_id,actor_login,action,target,reason) values(1,'owner-admin:discovery3','beta_personal_quota_exception',12594272,'Owner approved unlimited personal recommendations for bno84 during invite-only beta; artist cap and all other submission protections remain.');
commit;
