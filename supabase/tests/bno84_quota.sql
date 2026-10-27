begin;
update community_private.configuration set ready=true,access_mode='invite_only';
insert into community_private.invitations values(8999000000000,clock_timestamp()+interval '1 day',false);
do $test$
declare m bigint;i integer;q jsonb;
begin
 for m in select unnest(array[11107642::bigint,12594272::bigint]) loop
  for i in 1..4 loop
   perform community_submit(m,'Synthetic quota test',8990000000000+m*10+i,8980000000000+m*10+i,false);
  end loop;
  q:=community_quota(m);
  if not (q->>'unlimited')::boolean or (q->>'used')::int<4 or q->>'limit' is not null then raise exception 'UNLIMITED_TEST_FAILED';end if;
 end loop;
 for i in 1..3 loop
  perform community_submit(8999000000000,'Synthetic ordinary',8970000000000+i,8960000000000+i,false);
 end loop;
 begin perform community_submit(8999000000000,'Synthetic ordinary',8970000000004,8960000000004,false);raise exception 'EXPECTED_MEMBER_CAP';
 exception when others then if sqlerrm<>'MEMBER_QUOTA' then raise;end if;end;
 for i in 1..3 loop
  perform community_submit(12594272,'Synthetic quota test',8950000000000+i,8940000000000,false);
 end loop;
 begin perform community_submit(12594272,'Synthetic quota test',8950000000004,8940000000000,false);raise exception 'EXPECTED_ARTIST_CAP';
 exception when others then if sqlerrm<>'ARTIST_QUOTA' then raise;end if;end;
 begin perform community_submit(12594272,'Synthetic quota test',8950000000001,8940000000000,false);raise exception 'EXPECTED_DUPLICATE';
 exception when others then if sqlerrm<>'DUPLICATE' then raise;end if;end;
 begin perform community_submit(12594272,'Synthetic quota test',8930000000000,12594272,false);raise exception 'EXPECTED_SELF';
 exception when others then if sqlerrm<>'SELF_RECOMMENDATION' then raise;end if;end;
 perform community_private.owner_admin('block',12594272,'Synthetic local block test');
 begin perform community_submit(12594272,'Synthetic quota test',8930000000001,8920000000001,false);raise exception 'EXPECTED_BLOCK';
 exception when others then if sqlerrm<>'SUBMITTER_BLOCKED' then raise;end if;end;
 perform community_private.owner_admin('unblock',12594272,'Synthetic local unblock');
 update community_private.invitations set revoked=true where member_id=12594272;
 begin perform community_submit(12594272,'Synthetic quota test',8930000000001,8920000000001,false);raise exception 'EXPECTED_REVOKED';
 exception when others then if sqlerrm<>'INVITE_REVOKED' then raise;end if;end;
 update community_private.invitations set revoked=false where member_id=12594272;
 update community_private.configuration set access_mode='public';
 for m in select unnest(array[11107642::bigint,12594272::bigint]) loop
  if (community_quota(m)->>'unlimited')::boolean then raise exception 'PUBLIC_MODE_EXEMPTION';end if;
  begin perform community_submit(m,'Synthetic quota test',8910000000000+m,8900000000000+m,false);raise exception 'EXPECTED_PUBLIC_CAP';
  exception when others then if sqlerrm<>'MEMBER_QUOTA' then raise;end if;end;
 end loop;
 if has_function_privilege('anon','community_quota(bigint)','execute') or has_function_privilege('authenticated','community_quota(bigint)','execute') then raise exception 'CLIENT_QUOTA_GRANT';end if;
 if has_function_privilege('anon','community_submit_legacy(bigint,text,bigint,bigint,boolean)','execute') or has_function_privilege('authenticated','community_submit_legacy(bigint,text,bigint,bigint,boolean)','execute') or has_function_privilege('service_role','community_submit_legacy(bigint,text,bigint,bigint,boolean)','execute') then raise exception 'LEGACY_CLIENT_GRANT';end if;
end $test$;
rollback;
