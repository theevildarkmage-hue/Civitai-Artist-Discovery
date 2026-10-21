"""Opt-in actual PostgreSQL concurrency tests; no SQLite substitution.

Needs psql on PATH and an approved disposable database with both migrations applied.
Uses a random numeric namespace and cleans only rows it creates. Never run against
production. Connection credentials stay in the child environment, not command args.
"""
import concurrent.futures
import json
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
import time
from urllib.parse import urlsplit, unquote


def run():
    if os.environ.get('CAD_ALLOW_ISOLATED_DB_TESTS')!='1':
        print('SKIP: set CAD_ALLOW_ISOLATED_DB_TESTS=1 for a disposable PostgreSQL database'); return
    url=os.environ.get('CAD_TEST_DATABASE_URL')
    if not url or not shutil.which('psql'):raise RuntimeError('Provide CAD_TEST_DATABASE_URL and psql')
    parsed=urlsplit(url)
    if (parsed.scheme!='postgresql' or parsed.hostname!='127.0.0.1' or not parsed.port
            or parsed.password or not os.environ.get('CAD_TEST_PG_SYSTEM_IDENTIFIER')
            or not os.environ.get('CAD_TEST_PG_DATA_DIR')):
        raise RuntimeError('Use the owned native cluster runner, never an existing database URL')
    # libpq does not expand a URI supplied via PGDATABASE's environment default.
    # Parse nonsecret connection coordinates and keep the password solely in env.
    env={**os.environ,'PGDATABASE':unquote(parsed.path.lstrip('/')),
         'PGHOST':'127.0.0.1','PGPORT':str(parsed.port),'PGUSER':unquote(parsed.username or '')}
    credentials=json.loads(os.environ.get('CAD_TEST_PG_ROLE_PASSWORDS','{}'))
    def sql(statement, role=None):
        child_env=env if role is None else {**env,'PGUSER':role,'PGPASSWORD':credentials[role]}
        result=subprocess.run(['psql','-X','-A','-t','-v','ON_ERROR_STOP=1','-c',statement],
                              env=child_env,capture_output=True,text=True,timeout=30)
        return result.returncode,result.stdout,result.stderr
    identity=sql('select system_identifier from pg_control_system();')
    location=sql('show data_directory;')
    if (identity[0] or identity[1].strip()!=os.environ['CAD_TEST_PG_SYSTEM_IDENTIFIER'] or location[0]
            or Path(location[1].strip()).resolve()!=Path(os.environ['CAD_TEST_PG_DATA_DIR']).resolve()):
        raise RuntimeError('PostgreSQL cluster identity mismatch; no test mutations performed')
    seed=8_000_000_000+secrets.randbelow(100_000_000)
    members=list(range(seed,seed+2000)); images=list(range(seed+100,seed+6100))
    rate_key=f'isolated:{seed}'
    tables=('community_members','community_recommendations','community_audit',
            'community_rate_buckets','community_submission_requests')
    def require(statement,role=None):
        result=sql(statement,role)
        assert result[0]==0,'Isolated PostgreSQL assertion failed: '+result[2]
        return result[1].strip()
    def denied(statement,expected,role=None):
        result=sql(statement,role)
        assert result[0]!=0 and expected.lower() in result[2].lower(), 'Expected PostgreSQL rejection missing: '+result[2]
    def submit(member,image,artist):
        return sql(f"select public.community_submit({member},'IsolatedTest',{image},{artist});")
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            result=list(pool.map(lambda n:submit(members[0],images[n],seed+300+n),range(8)))
        assert sum(r[0]==0 for r in result)==3,'Member quota concurrency failed: '+repr([r[2] for r in result])
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
            for table in tables:
                output=require(f"""begin; grant select on public.{table} to {role};
                  set local role {role};
                  select set_config('request.jwt.claims','{{"role":"service_role"}}',true);
                  select 'RLS_ROWS='||count(*) from public.{table}; rollback;""")
                assert 'RLS_ROWS=0' in output,'Forged role claims bypassed RLS'
            denied(f"""begin; grant insert on public.community_recommendations to {role}; set local role {role};
              insert into public.community_recommendations(seq,image_id,creator_id,member_id,week)
              overriding system value values({seed+10000},{images[38]},{seed+603},{member},
                date_trunc('week',clock_timestamp() at time zone 'UTC')::date);""",'row-level security')
            for table,insert in (
                ('community_members',f"insert into public.community_members values({members[40]},'Spoof');"),
                ('community_audit',f"insert into public.community_audit(seq,image_id,action) overriding system value values({seed+11000},{images[35]},'accepted');"),
                ('community_rate_buckets',f"insert into public.community_rate_buckets values('isolated-rls-{seed}',clock_timestamp(),1);"),
                ('community_submission_requests',f"insert into public.community_submission_requests values({member},'isolated-rls-{seed}',{images[35]});")):
                denied(f'begin; grant insert on public.{table} to {role}; set local role {role}; '+insert,'row-level security')
        require("""do $$ begin
          assert (select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
            where n.nspname='public' and c.relname like 'community_%' and c.relkind='r' and c.relrowsecurity)=5;
          assert not exists(select 1 from pg_policies where schemaname='public' and tablename like 'community_%');
        end $$;""")

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

        # Real password-authenticated sessions, including forged/missing identity
        # contexts. SQL settings are caller-controlled data, not JWT verification.
        for role in ('anon','authenticated'):
            assert require('select current_user;',role)==role
            for claims in ('', 'malformed', '{"sub":"7","role":"service_role","exp":1}',
                           '{"sub":"8","role":"authenticated","revoked":true}'):
                context="select set_config('request.jwt.claims',"+"'"+claims+"',false);"
                for table in tables:
                    for action in (f'select * from public.{table};',f'delete from public.{table};'):
                        denied(context+action,'permission denied',role)
                denied(context+f"select public.community_submit({members[50]},'Spoof',{images[50]},{seed+10000});",'permission denied',role)
            denied(f"insert into public.community_members values ({members[50]},'Spoof');",'permission denied',role)
            denied("update public.community_recommendations set member_id=1;",'permission denied',role)
            denied('set role service_role;','permission denied',role)
            denied('create function public.escalation() returns int language sql as $$ select 1 $$;','permission denied',role)
        assert require('select current_user;','service_role')=='service_role'
        denied('select * from public.community_recommendations;','permission denied','service_role')
        require(f"select public.community_submit({members[50]},'ServiceMember',{images[50]},{seed+10000});",'service_role')

        # Service RPC contract rejects null/invalid values rather than silently
        # bypassing a preview branch or an unbounded LIMIT.
        denied('select public.community_gallery(null,null);','INVALID_REQUEST','service_role')
        for invalid in ('0','-1','null'):
            denied(f"select public.community_submit({members[51]},'Invalid',{invalid},{seed+10001});",'INVALID_REQUEST','service_role')
            denied(f"select public.community_submit({invalid},'Invalid',{images[51]},{seed+10001});",'INVALID_REQUEST','service_role')
            denied(f"select public.community_submit({members[51]},'Invalid',{images[51]},{invalid});",'INVALID_REQUEST','service_role')
        denied(f"select public.community_submit({members[51]},null,{images[51]},{seed+10001});",'INVALID_REQUEST','service_role')
        denied(f"select public.community_submit({members[51]},'Invalid',{images[51]},{seed+10001},null);",'INVALID_REQUEST','service_role')

        # Ten member identities delegated by a trusted service session; ordinary
        # database roles still cannot assert their own identity or read any ledger.
        for n in range(10):
            for slot in range(3):
                require(f"select public.community_submit_once({members[60+n]},'Member{n}',{images[100+n*4+slot]},{seed+11000+n*4+slot},'isolated-ten-{seed}-{n}-{slot}');",'service_role')
            denied(f"select public.community_submit({members[60+n]},'Member{n}',{images[100+n*4+3]},{seed+12000+n});",'MEMBER_QUOTA','service_role')
        print('PASS: ten independent delegated members each accepted 3 and rejected 4th',flush=True)

        def race(statements):
            barrier=threading.Barrier(len(statements))
            def worker(statement):
                barrier.wait(timeout=10)
                return sql(statement,'service_role')
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(statements)) as pool:
                return list(pool.map(worker,statements))
        def accepted(results,count,code):
            assert sum(r[0]==0 for r in results)==count,repr(results)
            assert all(r[0]==0 or code in r[2] for r in results),repr(results)
        for trial in range(5):
            member=members[100+trial]; artist=seed+13000+trial; offset=200+trial*20
            for n in range(2):
                require(f"select public.community_submit({member},'OneSlot',{images[offset+n]},{artist+n+100});",'service_role')
                require(f"select public.community_submit({members[200+trial*10+n]},'ArtistSlot',{images[offset+2+n]},{artist});",'service_role')
            accepted(race([f"select public.community_submit({member},'OneSlot',{images[offset+4+n]},{artist+200+n});" for n in range(2)]),1,'MEMBER_QUOTA')
            accepted(race([f"select public.community_submit({members[300+trial*10+n]},'ArtistSlot',{images[offset+6+n]},{artist});" for n in range(6)]),1,'ARTIST_QUOTA')
            accepted(race([f"select public.community_submit({members[400+trial*10+n]},'Duplicate',{images[offset+12]},{artist+300});" for n in range(6)]),1,'DUPLICATE')
            key=f'isolated-race-{seed}-{trial}'
            same=f"select public.community_submit_once({members[500+trial]},'Replay',{images[offset+13]},{artist+400},'{key}');"
            replays=race([same]*6);assert all(r[0]==0 for r in replays),repr(replays)
            assert sum('"replayed": true' in r[1] for r in replays)==5
            require(f"""do $$ begin
              assert (select count(*) from public.community_recommendations where image_id={images[offset+13]})=1;
              assert (select count(*) from public.community_audit where image_id={images[offset+13]})=1;
              assert (select count(*) from public.community_submission_requests where member_id={members[500+trial]})=1;
            end $$;""")
        print('PASS: 5 synchronized rounds: one remaining member/artist slot, simultaneous duplicates, six-way identical replay',flush=True)
        accepted(race([f"select public.community_submit_once({members[550]},'ConflictingReplay',{images[450+n]},{seed+14500+n},'isolated-conflict-{seed}');"
                       for n in range(6)]),1,'INVALID_REQUEST')
        race_key=rate_key+'-race'
        require(f"do $$ begin for i in 1..28 loop perform public.community_take_rate('{race_key}'); end loop; end $$;",'service_role')
        accepted(race([f"select public.community_take_rate('{race_key}');"]*6),2,'RATE_LIMITED')
        print('PASS: conflicting simultaneous replay accepts once; six throttle contenders accept exactly two remaining requests',flush=True)

        # Deliberate audit persistence failure must roll back recommendation/member/
        # replay state in the exact same transaction, then permit an ordinary retry.
        trigger=f'isolated_failure_{seed}'
        failing=images[500]; failure_member=members[600]
        require(f"""create function public.{trigger}() returns trigger language plpgsql as $$ begin
          if new.image_id={failing} then raise exception 'ISOLATED_PERSISTENCE_FAILURE'; end if; return new; end $$;
          create trigger {trigger} before insert on public.community_audit for each row execute function public.{trigger}();""")
        try:
            once=f"select public.community_submit_once({failure_member},'Rollback',{failing},{seed+15000},'isolated-rollback-{seed}');"
            denied(once,'ISOLATED_PERSISTENCE_FAILURE','service_role')
            require(f"""do $$ begin
              assert not exists(select 1 from public.community_members where civitai_id={failure_member});
              assert not exists(select 1 from public.community_recommendations where image_id={failing});
              assert not exists(select 1 from public.community_submission_requests where member_id={failure_member});
            end $$;""")
        finally:
            require(f'drop trigger {trigger} on public.community_audit; drop function public.{trigger}();')
        require(once,'service_role')
        # Caller transaction rollback also releases quota and its replay key.
        require(f"begin; select public.community_submit_once({members[601]},'Rollback',{images[501]},{seed+15001},'isolated-explicit-{seed}'); rollback;",'service_role')
        require(f'do $$ begin assert not exists(select 1 from public.community_recommendations where image_id={images[501]}); end $$;')
        print('PASS: injected persistence failure and explicit rollback leave no partial acceptance/audit/replay',flush=True)

        # A real lock holder is confirmed through pg_locks before issuing a bounded
        # contender. Retry after rollback must succeed without partial state.
        lockmember=members[700]
        holder=subprocess.Popen(['psql','-X','-q','-v','ON_ERROR_STOP=1'],env=env,stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,text=True)
        try:
            holder.stdin.write(f"begin; select pg_advisory_xact_lock(hashtextextended('community-member:{lockmember}',0));\n");holder.stdin.flush()
            for _ in range(100):
                if require(f"select count(*) from pg_locks where locktype='advisory' and granted and pid<>{os.getpid()};")!='0':break
                time.sleep(.02)
            else:raise AssertionError('Lock holder was not ready')
            blocked=f"select public.community_submit({lockmember},'Contention',{images[600]},{seed+16000});"
            denied("set lock_timeout='100ms';"+blocked,'lock timeout','service_role')
        finally:
            holder.stdin.write('rollback;\n\\q\n');holder.stdin.flush();holder.communicate(timeout=10)
        require(blocked,'service_role')
        print('PASS: actual lock timeout rolls back and retry succeeds after lock release',flush=True)
        # Contrived opposing external locks exercise the server's deadlock victim
        # rollback. Production RPC lock order never reverses these acquisitions.
        a=f"hashtextextended('isolated-deadlock-a:{seed}',0)"
        b=f"hashtextextended('isolated-deadlock-b:{seed}',0)"
        deadlocks=race([f'begin; select pg_advisory_xact_lock({first}); select pg_sleep(.3); select pg_advisory_xact_lock({second}); rollback;'
                        for first,second in ((a,b),(b,a))])
        assert sum(r[0]==0 for r in deadlocks)==1,repr(deadlocks)
        assert sum('deadlock detected' in r[2] for r in deadlocks)==1,repr(deadlocks)
        require(f'begin; select pg_advisory_xact_lock({a}); select pg_advisory_xact_lock({b}); rollback;','service_role')
        print('PASS: deliberate external deadlock chooses one victim and releases locks for retry',flush=True)
        require(f"""do $$ begin
          assert not exists(select 1 from public.community_recommendations r left join public.community_audit a
            on a.image_id=r.image_id and a.action='accepted' where r.member_id between {members[0]} and {members[-1]}
            and r.week=date_trunc('week',clock_timestamp() at time zone 'UTC')::date
            group by r.image_id having count(a.seq)<>1);
          assert not exists(select 1 from public.community_recommendations where member_id between {members[0]} and {members[-1]}
            group by member_id,week having count(*)>3);
        end $$;""")
        metrics=require(f"""select json_build_object('acceptedThisWeek',
          (select count(*) from public.community_recommendations where member_id between {members[0]} and {members[-1]}
             and week=date_trunc('week',clock_timestamp() at time zone 'UTC')::date),
          'acceptedAudits',(select count(*) from public.community_audit where image_id between {images[0]} and {images[-1]}),
          'replayKeys',(select count(*) from public.community_submission_requests where member_id between {members[0]} and {members[-1]}),
          'synchronizedRounds',5,'memberSlotAcceptedPerRound',1,'artistSlotAcceptedPerRound',1,
          'duplicateAcceptedPerRound',1,'sameKeyRequestsPerRound',6,'sameKeyReplaysPerRound',5);""")
        print(metrics,flush=True)
        print('PASS: actual PostgreSQL races, atomic audit/replay, independent member keys, role/RLS/claim escalation, UTC boundaries and throttling')
    finally:
        statement=f"""begin;
          delete from public.community_submission_requests where member_id between {members[0]} and {members[-1]};
          delete from public.community_audit where image_id between {images[0]} and {images[-1]};
          delete from public.community_recommendations where image_id between {images[0]} and {images[-1]};
          delete from public.community_members where civitai_id between {members[0]} and {members[-1]};
          delete from public.community_rate_buckets where key like '{rate_key}%'; commit;"""
        if sql(statement)[0]:raise RuntimeError('Isolated PostgreSQL test cleanup failed')

if __name__=='__main__':run()
