"""Repeatable Python + Node release checks, with optional owned PostgreSQL/Edge tier.

No live service is used. Every run retains logs and reports skipped prerequisites;
an exit code of zero without --native is a source-only pass, not a full gate.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def source_fingerprint():
    listing=subprocess.check_output(['git','ls-files','-z','--cached','--others','--exclude-standard'],cwd=ROOT)
    value=hashlib.sha256()
    for name in sorted(set(listing.decode().split('\0'))-{''}):
        path=ROOT/name
        if path.is_file():
            value.update(name.encode());value.update(b'\0');value.update(path.read_bytes())
    return value.hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True,help='New evidence directory outside source')
    p.add_argument('--timeout',type=int,default=180,help='Per Python/Node entry timeout')
    p.add_argument('--native',action='store_true',help='Also run owned PostgreSQL + real Edge HTTP tier')
    p.add_argument('--bin',type=Path,help='PostgreSQL bin directory for --native')
    p.add_argument('--http-deno',type=Path)
    p.add_argument('--http-postgrest',type=Path)
    args=p.parse_args()
    out=args.output.resolve()
    if out==ROOT or ROOT in out.parents:p.error('Evidence must be outside this source worktree')
    if out.exists():p.error('Use a new output directory; stale receipts are not resumed')
    if not 10<=args.timeout<=900:p.error('Per-entry timeout must be 10..900 seconds')
    out.mkdir(parents=True)
    env={k:v for k,v in os.environ.items() if not (k.upper().startswith(('PG','SUPABASE_','CIVITAI_','COMMUNITY_')) or
        (k.startswith('CAD_') and k!='CAD_POSTGRES_BIN') or any(word in k.upper() for word in ('ACCESS_TOKEN','REFRESH_TOKEN')))}
    env.update(PYTHONIOENCODING='utf-8',PYTHONUNBUFFERED='1',PYTHONDONTWRITEBYTECODE='1')
    before=source_fingerprint()
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    rows=[]
    def run(name,command,timeout):
        started=time.monotonic()
        with (out/(name+'.log')).open('w',encoding='utf-8') as log:
            process=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            try:code=process.wait(timeout=timeout);state='passed' if code==0 else 'failed'
            except subprocess.TimeoutExpired:
                state='timeout';code=None
                if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                else:process.kill()
                process.wait()
        text=(out/(name+'.log')).read_text(encoding='utf-8',errors='replace')
        if state=='passed' and 'SKIP:' in text:state='skipped'
        row={'name':name,'state':state,'exitCode':code,'seconds':round(time.monotonic()-started,2)}
        rows.append(row)
        (out/'entries.json').write_text(json.dumps(rows,indent=2))
        print(json.dumps(row),flush=True)
        return row
    # The existing runner owns each profile, port, process and browser artifact.
    run('python',[sys.executable,str(ROOT/'scripts/run_tests.py'),'--timeout',str(args.timeout),'--output',str(out/'python')],max(3600,args.timeout*200))
    python_summary=json.loads((out/'python/summary.json').read_text()) if (out/'python/summary.json').exists() else None
    node=shutil.which('node')
    if node:
        for path in sorted((ROOT/'tests').glob('*.mjs')):
            run('node-'+path.stem,[node,'--experimental-transform-types',str(path)],args.timeout)
    else:rows.append({'name':'node','state':'unavailable','reason':'Node executable unavailable'})
    native_state='unavailable'
    if args.native:
        if not all((args.bin,args.http_deno,args.http_postgrest)):
            rows.append({'name':'native','state':'unavailable','reason':'Owned PostgreSQL, Deno and PostgREST paths are required'})
        else:
            row=run('native',[sys.executable,str(ROOT/'scripts/validate-postgres.py'),
               '--bin',str(args.bin),'--output',str(out/'native'),'--repeats','1',
               '--device-renewal','--community-access','--release-candidate','--identity-sessions',
               '--deployment-rehearsal','--private-beta',
               '--http-deno',str(args.http_deno),'--http-postgrest',str(args.http_postgrest)],1800)
            native_state=row['state']
            accepted=run('native-accepted-feed',[sys.executable,str(ROOT/'scripts/validate-postgres.py'),
               '--bin',str(args.bin),'--output',str(out/'native-accepted-feed'),'--accepted-feed',
               '--http-deno',str(args.http_deno),'--http-postgrest',str(args.http_postgrest)],600)
            if accepted['state']!='passed':native_state=accepted['state']
    else:rows.append({'name':'native','state':'unavailable','reason':'Not requested; run --native with owned tool paths for the complete tier'})
    after=source_fingerprint()
    failures=[r['name'] for r in rows if r['state'] in ('failed','timeout')]
    unavailable=[r['name'] for r in rows if r['state']=='unavailable']
    skipped_python=[r['name'] for r in json.loads((out/'python/results.json').read_text())
                    if r['state']=='skipped'] if (out/'python/results.json').exists() else []
    result={'sourceCommit':commit,'sourceFingerprintBefore':before,'sourceFingerprintAfter':after,
       'sourceUnchangedDuringRun':before==after,'pythonSummary':python_summary,'entries':rows,
       'failed':failures,'unavailable':unavailable,'skippedPythonEntries':skipped_python,
       'sourceChecksPassed':not failures and node is not None and python_summary is not None and not python_summary['failed'],
       'fullGatePassed':not failures and not unavailable and before==after and native_state=='passed',
       'limitations':['Skipped script entries are not counted as executed assertions. fullGatePassed means source modules plus the configured owned native tier passed, not that every skipped script entry separately ran. Review native detailed receipts against skippedPythonEntries.','A passing automated gate is not publication, production activation or real-account packaged acceptance approval.']}
    (out/'summary.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result),flush=True)
    raise SystemExit(1 if failures or before!=after or (args.native and unavailable) or node is None else 0)


if __name__=='__main__':main()
