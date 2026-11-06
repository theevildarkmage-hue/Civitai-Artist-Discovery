-- Owned disposable native cluster only. All synthetic load is rolled back.
begin;
insert into public.community_members(civitai_id,username)
select 9000000000000+n,'PlanMember'||n from generate_series(1,10000) n;
insert into public.community_recommendations(image_id,creator_id,member_id,week)
select 9000000000000+n,9000000020000+n,9000000000000+n,
date_trunc('week',clock_timestamp() at time zone 'UTC')::date from generate_series(1,10000) n;
analyze public.community_recommendations;
explain(analyze,buffers) select image_id,creator_id,member_id from public.community_recommendations
where status='active' order by seq desc limit 25;
explain(analyze,buffers) select count(*) from public.community_recommendations
where member_id=9000000000001 and week=date_trunc('week',clock_timestamp() at time zone 'UTC')::date;
explain(analyze,buffers) select count(*) from public.community_recommendations
where creator_id=9000000020001 and week=date_trunc('week',clock_timestamp() at time zone 'UTC')::date;
rollback;
