"""Opt-in actual PostgreSQL concurrency tests; no SQLite substitution.

Needs psql on PATH and an approved disposable database with both migrations applied.
Uses a random numeric namespace and cleans only rows it creates. Never run against
production. Connection credentials stay in the child environment, not command args.
"""
import concurrent.futures
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import secrets
import shutil
import subprocess


def run():
    if os.environ.get('CAD_ALLOW_ISOLATED_DB_TESTS')!='1':
        print('SKIP: set CAD_ALLOW_ISOLATED_DB_TESTS=1 for a disposable PostgreSQL database'); return
    url=os.environ.get('CAD_TEST_DATABASE_URL')
    if not url or not shutil.which('psql'):raise RuntimeError('Provide CAD_TEST_DATABASE_URL and psql')
    env={**os.environ,'PGDATABASE':url}
    def sql(statement):
        result=subprocess.run(['psql','-X','-A','-t','-v','ON_ERROR_STOP=1','-c',statement],
                              env=env,capture_output=True,text=True,timeout=30)
        return result.returncode,result.stdout,result.stderr
    seed=8_000_000_000+secrets.randbelow(100_000_000)
    members=list(range(seed,seed+20)); images=list(range(seed+100,seed+180))
    rate_key=f'isolated:{seed}'
    def require(statement):
        result=sql(statement)
        assert result[0]==0,'Isolated PostgreSQL assertion failed; inspect private SQL logs'
        return result[1]
    def denied(statement,expected):
        result=sql(statement)
        assert result[0]!=0 and expected.lower() in result[2].lower(), 'Expected PostgreSQL rejection missing'
    def submit(member,image,artist):
        return sql(f"select public.community_submit({member},'IsolatedTest',{image},{artist});")
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            result=list(pool.map(lambda n:submit(members[0],images[n],seed+300+n),range(8)))
        assert sum(r[0]==0 for r in result)==3,'Member quota concurrency failed'
        assert all(r[0]==0 or 'MEMBER_QUOTA' in r[2] for r in result),'Unexpected member result'
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            result=list(pool.map(lambda n:submit(members[n+1],images[n+10],seed+400),range(8)))
        assert sum(r[0]==0 for r in result)==3,'Artist quota concurrency failed'
        assert all(r[0]==0 or 'ARTIST_QUOTA' in r[2] for r in result),'Unexpected artist result'
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            result=list(pool.map(lambda n:submit(members[n+10],images[30],seed+500),range(8)))
        assert sum(r[0]==0 for r in result)==1,'Duplicate concurrency failed'
        assert all(r[0]==0 or 'DUPLICATE' in r[2] for r in result),'Unexpected duplicate result'
        test=Path(__file__).resolve().parents[1]/'supabase/tests/community_rules.sql'
        result=subprocess.run(['psql','-X','-v','ON_ERROR_STOP=1','-f',str(test)],env=env,
                              capture_output=True,text=True,timeout=30)
        assert result.returncode==0,'PostgreSQL rule/permission tests failed (inspect isolated DB privately)'
        # Fresh member/key namespaces: replay is atomic and scoped to the verified member.
        member=members[18]; key=f'isolated-request-{seed}'
        once=f"public.community_submit_once({member},'IsolatedTest',{images[35]},{seed+600},'{key}')"
        require(f'select {once};');require(f'select {once};')
        require(f"""do $$ begin
          assert (select count(*) from public.community_recommendations where image_id={images[35]})=1;
          assert (select count(*) from public.community_audit where image_id={images[35]})=1;
          assert (select count(*) from public.community_submission_requests where member_id={member})=1;
          assert (public.community_quota({member})->>'used')::int=1;
        end $$;""")
        denied(f"select public.community_submit_once({member},'IsolatedTest',{images[36]},{seed+601},'{key}');",'INVALID_REQUEST')
        require(f"select public.community_submit_once({members[19]},'OtherIdentity',{images[37]},{seed+602},'{key}');")

        # Actual role execution, not only has_*_privilege introspection.
        for role in ('anon','authenticated'):
            denied(f'SET SESSION AUTHORIZATION {role}; SELECT * FROM public.community_recommendations;', 'permission denied')
            denied(f"SET SESSION AUTHORIZATION {role}; SELECT public.community_submit({member},'Spoofed',{images[38]},{seed+603});", 'permission denied')
            denied(f'SET SESSION AUTHORIZATION {role}; SET ROLE service_role;', 'permission denied')
            # Temporarily grant SELECT inside a rolled-back transaction to establish
            # that default-deny RLS itself works even if table privileges are relaxed.
            output=require(f"""begin; grant select on public.community_recommendations to {role};
              set local role {role};
              select set_config('request.jwt.claims','{{"role":"service_role"}}',true);
              select 'RLS_ROWS='||count(*) from public.community_recommendations; rollback;""")
            assert 'RLS_ROWS=0' in output,'Forged role claims bypassed RLS'
            denied(f"""begin; grant insert on public.community_recommendations to {role}; set local role {role};
              insert into public.community_recommendations(seq,image_id,creator_id,member_id,week)
              overriding system value values({seed+10000},{images[38]},{seed+603},{member},
                date_trunc('week',clock_timestamp() at time zone 'UTC')::date);""",'row-level security')

        # Server UTC, independent of the caller's session timezone. Historical fixtures
        # test the Sunday/Monday constraint without introducing a production clock override.
        utc_now=datetime.now(timezone.utc)
        expected_week=(utc_now-timedelta(days=utc_now.weekday())).date().isoformat()
        for zone in ('Pacific/Kiritimati','America/Los_Angeles','UTC'):
            output=require(f"set time zone '{zone}'; select 'UTC_WEEK='||(public.community_quota({member})->>'week');")
            assert 'UTC_WEEK='+expected_week in output
        require(f"""insert into public.community_recommendations(image_id,creator_id,member_id,submitted_at,week)
          select image,{seed+700},{member},(date_trunc('week',clock_timestamp() at time zone 'UTC')-interval '7 days') at time zone 'UTC',
            (date_trunc('week',clock_timestamp() at time zone 'UTC')-interval '7 days')::date
          from unnest(array[{images[40]},{images[41]},{images[42]}]::bigint[]) as image;
          do $$ begin assert (public.community_quota({member})->>'used')::int=1; end $$;""")
        require(f"""begin; insert into public.community_recommendations(image_id,creator_id,member_id,submitted_at,week)
          values({images[43]},{seed+701},{member},'2026-10-19T00:30:00+14:00','2026-10-12'); rollback;""")
        denied(f"""insert into public.community_recommendations(image_id,creator_id,member_id,submitted_at,week)
          values({images[44]},{seed+701},{member},'2026-10-19T00:30:00+14:00','2026-10-19');""",'check constraint')
        require(f"""do $$ begin
          for i in 1..30 loop perform public.community_take_rate('{rate_key}'); end loop;
          begin perform public.community_take_rate('{rate_key}'); raise exception 'Throttle bypass';
          exception when raise_exception then if sqlerrm<>'RATE_LIMITED' then raise; end if; end;
        end $$;""")
        denied(f"select public.community_take_rate('{rate_key}');",'RATE_LIMITED')
        require(f"""update public.community_rate_buckets set started_at=clock_timestamp()-interval '3 minutes' where key='{rate_key}';
          select public.community_take_rate('{rate_key}');
          do $$ begin assert (select requests from public.community_rate_buckets where key='{rate_key}')=1; end $$;""")
        print('PASS: actual PostgreSQL races, atomic audit/replay, independent member keys, role/RLS/claim escalation, UTC boundaries and throttling')
    finally:
        statement=f"""begin;
          delete from public.community_submission_requests where member_id between {members[0]} and {members[-1]};
          delete from public.community_audit where image_id between {images[0]} and {images[-1]};
          delete from public.community_recommendations where image_id between {images[0]} and {images[-1]};
          delete from public.community_members where civitai_id between {members[0]} and {members[-1]};
          delete from public.community_rate_buckets where key='{rate_key}'; commit;"""
        if sql(statement)[0]:raise RuntimeError('Isolated PostgreSQL test cleanup failed')

if __name__=='__main__':run()
