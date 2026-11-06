"""Generate a pinned atomic bootstrap and manifest locally; never connect or deploy."""
import argparse,hashlib,json,subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REF='aibelmhucizupzvvhdvf'
FILES=('202610140001_community_v1.sql','202610150001_submission_idempotency.sql',
       '202610210001_community_rpc_guards.sql','202610220001_replay_identity_guards.sql')
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-ref',required=True,choices=[REF])
    parser.add_argument('--output-directory',required=True,type=Path)
    args=parser.parse_args();output=args.output_directory.resolve()
    if output.is_relative_to(ROOT) and not output.is_relative_to(ROOT/'reports'):
        parser.error('Use an external directory or ignored reports directory')
    output.mkdir(parents=True,exist_ok=True)
    records=[];parts=[]
    for name in FILES:
        path=ROOT/'supabase/migrations'/name
        records.append({'file':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        parts.append('-- '+name+'\n'+path.read_text())
    sql="""-- PREPARATION ONLY. Requires separate approval and verified target connection.
-- Target reference: aibelmhucizupzvvhdvf, us-west-2. SQL cannot attest project ref.
begin;
do $$ begin
  if current_user<>'postgres' or current_database()<>'postgres' then
    raise exception 'Unexpected migration operator/database';
  end if;
  if exists(select 1 from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid=c.relnamespace
    where n.nspname='public' and c.relname like 'community_%')
    or exists(select 1 from pg_catalog.pg_proc p join pg_catalog.pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and p.proname like 'community_%') then
    raise exception 'Bootstrap requires no existing Community objects; do not reset them';
  end if;
end $$;
"""+'\n'.join(parts)+'\ncommit;\n'
    bundle=output/'staging-bootstrap.sql';bundle.write_text(sql,encoding='utf-8')
    manifest={'projectRef':REF,'region':'us-west-2','sourceCommit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
        'sourceWorkingTreeClean':not subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip(),
        'migrations':records,'bundleSha256':hashlib.sha256(bundle.read_bytes()).hexdigest(),
        'policy':{'COMMUNITY_BETA_STATE':'disabled','COMMUNITY_ALLOWED_RATINGS':'1,2'},
        'executed':False,'migrationLedger':'After successful commit and catalog validation only, explicitly register the four applied versions. Do not rerun one-shot DDL or mark unapplied SQL as applied.'}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest,indent=2))
if __name__=='__main__':main()
