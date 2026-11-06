"""Prepare a pinned nine-to-ten migration candidate; never connects or deploys."""
from pathlib import Path
import argparse,hashlib,json,subprocess
ROOT=Path(__file__).resolve().parents[1]
MIGRATION=Path('supabase/beta-migrations/202610240002_gallery_review_window.sql')
TABLES=['public.'+name for name in ('community_members','community_recommendations','community_audit','community_submission_requests')]
TABLES+=['community_private.'+name for name in ('configuration','operators','invitations','blocks','reports','moderator_audit','artwork_reviews','discovery_users')]

def preservation():
    return 'select jsonb_build_object('+','.join("'"+table+"',(select md5(coalesce(jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text)::text,'[]')) from "+table+' t)' for table in TABLES)+') snapshot'

def build_atomic(*,expected_owner='postgres',expected_database='postgres'):
    baseline=json.loads((ROOT/'supabase/review-baseline-9.json').read_text(encoding='utf-8'))
    versions=[]
    for item in baseline['files']:
        path=ROOT/item['path'];source=path.read_text(encoding='utf-8')
        assert hashlib.sha256(source.encode()).hexdigest()==item['sqlLfSha256'],'Approved migration source changed'
        versions.append(path.stem.split('_',1)[0])
    assert len(versions)==len(set(versions))==9
    source=(ROOT/MIGRATION).read_text(encoding='utf-8')
    assert '$gallery_source$' not in source
    literal=lambda value:"'"+value.replace("'","''")+"'"
    history='array['+','.join(literal(v) for v in sorted(versions))+']::text[]'
    query=preservation()
    return f"""-- PREPARATION ONLY. Requires separate hosted deployment approval and exact project attestation.
begin;
set local lock_timeout='10s';set local statement_timeout='30s';
select pg_advisory_xact_lock(7530230001);
do $$begin
 if current_user<>{literal(expected_owner)} or current_database()<>{literal(expected_database)}
    or current_setting('transaction_read_only')<>'off' then raise exception 'Unexpected migration authority';end if;
 if (select array_agg(version order by version) from supabase_migrations.schema_migrations) is distinct from {history}
    then raise exception 'Nine-migration history mismatch; do not replay';end if;
end$$;
create temporary table gallery_preservation_before on commit drop as {query};
create temporary table gallery_acl_before on commit drop as
 select oid,proacl,proowner,prosecdef,proconfig from pg_proc where pronamespace in ('public'::regnamespace,'community_private'::regnamespace)
 and (proname like 'community_%' or pronamespace='community_private'::regnamespace);
{source}
do $$begin
 if ({query}) is distinct from (select snapshot from gallery_preservation_before) then raise exception 'Protected record drift';end if;
 if exists(select 1 from gallery_acl_before b join pg_proc p on p.oid=b.oid
   where (b.proacl,b.proowner,b.prosecdef,b.proconfig) is distinct from (p.proacl,p.proowner,p.prosecdef,p.proconfig))
   then raise exception 'Existing routine authority changed';end if;
 if has_function_privilege('anon','public.community_prepare_gallery(bigint,bigint,integer)','execute')
   or has_function_privilege('authenticated','public.community_prepare_gallery(bigint,bigint,integer)','execute')
   or not has_function_privilege('service_role','public.community_prepare_gallery(bigint,bigint,integer)','execute')
   or has_function_privilege('service_role','community_private.gallery_window(bigint,integer)','execute')
   then raise exception 'Candidate routine grant mismatch';end if;
end$$;
insert into supabase_migrations.schema_migrations(version,name,statements)
 values('202610240002','gallery_review_window',array[$gallery_source${source}$gallery_source$]);
notify pgrst,'reload schema';
commit;
"""

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--preview',action='store_true')
    args=parser.parse_args()
    git=lambda *values:subprocess.check_output(['git',*values],cwd=ROOT).decode().strip()
    dirty=bool(git('status','--porcelain'))
    if dirty and not args.preview:parser.error('Final pins require a clean validated source commit')
    bundle=build_atomic();args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'atomic-gallery-upgrade.sql').write_text(bundle,encoding='utf-8')
    baseline=json.loads((ROOT/'supabase/review-baseline-9.json').read_text(encoding='utf-8'))
    files=baseline['files']+[{'path':MIGRATION.as_posix(),'sqlLfSha256':hashlib.sha256((ROOT/MIGRATION).read_text(encoding='utf-8').encode()).hexdigest()}]
    manifest={'projectRef':baseline['projectRef'],'region':baseline['region'],'schemaModel':'automatic','preparedOnly':True,
        'sourceCommit':git('rev-parse','HEAD'),'sourceDirty':dirty,'preview':args.preview,'expectedFunctionVersion':28,
        'expectedMigrations':9,'appliedVersion':'202610240002','files':files,
        'bundleSha256Lf':hashlib.sha256(bundle.encode()).hexdigest(),
        'functionFiles':[{'path':str(p.relative_to(ROOT)).replace('\\','/'),'sha256Lf':hashlib.sha256(p.read_text(encoding='utf-8').encode()).hexdigest()} for p in (ROOT/'supabase/functions/community-v1/index.ts',ROOT/'supabase/functions/community-v1/handler.ts')],
        'rollbackFunctionSourceCommit':baseline['approvedPublicCommit'],'policiesToPreserve':{'COMMUNITY_BETA_STATE':'enabled','DISCOVERY_REGISTRY_STATE':'enabled','COMMUNITY_ALLOWED_RATINGS':'1,2'},
        'requiresSeparateHostedApproval':True}
    (args.output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({'preparedOnly':True,'preview':args.preview,'sourceCommit':manifest['sourceCommit'],'migrationCount':len(files)}))

if __name__=='__main__':main()
