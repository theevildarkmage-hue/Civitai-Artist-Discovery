-- psql "$ISOLATED_TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/community_rules.sql
-- Dedicated disposable project only; records roll back at the end.
begin;
do $$
declare r jsonb;
begin
  r:=public.community_submit(900001,'TestMember',990001,910001,true);
  assert (r->'quota'->>'used')::int=0, 'preview consumes nothing';
  perform public.community_submit(900001,'TestMember',990001,910001);
  perform public.community_submit(900002,'Other',990002,910001);
  perform public.community_submit(900003,'Third',990003,910001);
  begin
    perform public.community_submit(900004,'Fourth',990004,910001);
    raise exception 'Artist quota was bypassed';
  exception when raise_exception then if sqlerrm<>'ARTIST_QUOTA' then raise; end if; end;
  begin
    perform public.community_submit(900002,'Other',990001,910001);
    raise exception 'Duplicate was accepted';
  exception when raise_exception then if sqlerrm<>'DUPLICATE' then raise; end if; end;
  begin
    perform public.community_submit(900001,'TestMember',990005,900001);
    raise exception 'Self recommendation accepted';
  exception when raise_exception then if sqlerrm<>'SELF_RECOMMENDATION' then raise; end if; end;
  perform public.community_submit(900001,'TestMember',990006,910002);
  perform public.community_submit(900001,'TestMember',990007,910003);
  perform public.community_withdraw(900001,990001);
  perform public.community_moderate(990006,'hidden');
  begin
    perform public.community_submit(900001,'TestMember',990008,910004);
    raise exception 'Member quota/refund bypass';
  exception when raise_exception then if sqlerrm<>'MEMBER_QUOTA' then raise; end if; end;
  assert (public.community_quota(900001)->>'used')::int=3, 'No refunds';
  assert not has_function_privilege('anon','public.community_submit(bigint,text,bigint,bigint,boolean)','EXECUTE'), 'anon RPC exposed';
  assert not has_function_privilege('authenticated','public.community_submit(bigint,text,bigint,bigint,boolean)','EXECUTE'), 'authenticated RPC exposed';
  assert not has_table_privilege('anon','public.community_recommendations','SELECT'), 'Metadata exposed';
end; $$;
rollback;
