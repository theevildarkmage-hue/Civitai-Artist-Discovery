"""Opt-in actual PostgreSQL concurrency tests; no SQLite substitution.

Needs psql on PATH and an approved disposable database with the migration applied.
Uses a random numeric namespace and cleans only rows it creates. Never run against
production. Connection credentials stay in the child environment, not command args.
"""
import concurrent.futures
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
    members=list(range(seed,seed+20)); images=list(range(seed+100,seed+140))
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
        print('PASS: actual PostgreSQL member/artist/duplicate races, rule and permission tests')
    finally:
        statement=f"""begin;
          delete from public.community_audit where image_id between {images[0]} and {images[-1]};
          delete from public.community_recommendations where image_id between {images[0]} and {images[-1]};
          delete from public.community_members where civitai_id between {members[0]} and {members[-1]}; commit;"""
        if sql(statement)[0]:raise RuntimeError('Isolated PostgreSQL test cleanup failed')

if __name__=='__main__':run()
