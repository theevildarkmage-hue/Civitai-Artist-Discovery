"""Prepare the fifth-migration-only transaction and manifest; no connections."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
REF='aibelmhucizupzvvhdvf'
BASE=('202610140001','202610150001','202610210001','202610220001')


def preservation_query():
    names=('community_members','community_recommendations','community_audit','community_submission_requests')
    return 'select jsonb_build_object('+','.join("'"+name+"',(select jsonb_build_object('count',count(*),'hash',md5(coalesce(jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text)::text,'[]'))) from public."+name+' t)' for name in names)+') snapshot'


def build_bundle(*,expected_owner='postgres',expected_database='postgres'):
    # Internal test arguments permit the identical transaction in the owned
    # native database. The CLI exposes no target overrides for hosted artifacts.
    candidate=(ROOT/'supabase/beta-migrations/202610230001_private_beta_security.sql').read_text(encoding='utf-8')
    assert '$cad3_source$' not in candidate
    query=preservation_query()
    return """-- PREPARATION ONLY. Separate owner approval and exact project attestation required.
-- Target project aibelmhucizupzvvhdvf / us-west-2. SQL cannot attest project ref.
begin;
do $cad3$ begin
 if current_user<>OWNER or current_database()<>DATABASE then raise exception 'Unexpected migration operator/database';end if;
 if to_regnamespace('community_private') is not null then raise exception 'Candidate already present; do not rerun';end if;
 if (select array_agg(version order by version) from supabase_migrations.schema_migrations where version in ('202610140001','202610150001','202610210001','202610220001','202610230001')) is distinct from array['202610140001','202610150001','202610210001','202610220001']::text[] then raise exception 'Four-migration history mismatch';end if;
 if not exists(select 1 from pg_roles where rolname='community_operator') then
   create role community_operator nologin noinherit nosuperuser nocreatedb nocreaterole noreplication nobypassrls;
 end if;
 if not exists(select 1 from pg_roles where rolname='community_operator' and not rolcanlogin and not rolinherit and not rolsuper and not rolbypassrls and not rolcreaterole and not rolcreatedb and not rolreplication)
    or exists(select 1 from pg_auth_members where member=(select oid from pg_roles where rolname='community_operator')) then raise exception 'Restricted operator group prerequisite mismatch';end if;
end $cad3$;
""".replace('OWNER',"'"+expected_owner+"'").replace('DATABASE',"'"+expected_database+"'")+'create temporary table cad3_preservation_before on commit drop as '+query+';\n'+candidate+"""
do $cad3$ begin
 if (QUERY) is distinct from (select snapshot from cad3_preservation_before) then raise exception 'Durable data preservation mismatch';end if;
end $cad3$;
""".replace('QUERY',query)+"insert into supabase_migrations.schema_migrations(version,name,statements) values('202610230001','private_beta_security',array[$cad3_source$"+candidate+"$cad3_source$]);\nnotify pgrst,'reload schema';\ncommit;\n"


def build_release_bundle(*,expected_owner='postgres',expected_database='postgres'):
    bundle=build_bundle(expected_owner=expected_owner,expected_database=expected_database)
    extra=''
    for filename in ('202610230002_application_registry.sql','202610230003_owner_administration.sql'):
        path=ROOT/'supabase/beta-migrations'/filename;source=path.read_text(encoding='utf-8');version,name=path.stem.split('_',1)
        assert '$cad3_release$' not in source
        extra+=source+"\ninsert into supabase_migrations.schema_migrations(version,name,statements) values('"+version+"','"+name+"',array[$cad3_release$"+source+"$cad3_release$]);\n"
    extra+="do $$ begin if ("+preservation_query()+") is distinct from (select snapshot from cad3_preservation_before) then raise exception 'Release durable-data preservation mismatch';end if;end $$;\n"
    return bundle.replace("notify pgrst,'reload schema';\ncommit;",extra+"notify pgrst,'reload schema';\ncommit;")


def automatic_extra():
    path=ROOT/'supabase/beta-migrations/202610230004_automatic_registry.sql'
    source=path.read_text(encoding='utf-8');assert '$cad3_automatic$' not in source
    return source+"\ninsert into supabase_migrations.schema_migrations(version,name,statements) values('202610230004','automatic_registry',array[$cad3_automatic$"+source+"$cad3_automatic$]);\n"


def build_automatic_bundle(*,expected_owner='postgres',expected_database='postgres'):
    bundle=build_release_bundle(expected_owner=expected_owner,expected_database=expected_database)
    return bundle.replace("notify pgrst,'reload schema';\ncommit;",automatic_extra()+"notify pgrst,'reload schema';\ncommit;")


def build_registry_upgrade(*,expected_owner='postgres',expected_database='postgres'):
    versions=sorted(set(BASE)|{'202610230001','202610230002','202610230003'})
    history="array["+','.join("'"+v+"'" for v in versions)+"]::text[]"
    preflight="do $$begin if current_user<>OWNER or current_database()<>DATABASE or (select array_agg(version order by version) from supabase_migrations.schema_migrations) is distinct from HISTORY then raise exception 'Seven-migration authority/history mismatch';end if;end$$;"
    preflight=preflight.replace('OWNER',"'"+expected_owner+"'").replace('DATABASE',"'"+expected_database+"'").replace('HISTORY',history)
    query=preservation_query()
    return 'begin;'+preflight+"create temporary table cad3_preservation_before on commit drop as "+query+';'+automatic_extra()+"do $$begin if ("+query+") is distinct from (select snapshot from cad3_preservation_before) then raise exception 'Durable data preservation mismatch';end if;end$$;notify pgrst,'reload schema';commit;"


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-ref',required=True,choices=[REF])
    parser.add_argument('--output-directory',required=True,type=Path)
    parser.add_argument('--schema-model',choices=['candidate','release','automatic'],default='candidate')
    args=parser.parse_args();out=args.output_directory.resolve()
    if out.is_relative_to(ROOT) and not out.is_relative_to(ROOT/'reports'):parser.error('Use ignored reports or external output')
    out.mkdir(parents=True,exist_ok=True)
    bundle=out/'candidate-upgrade.sql';bundle.write_text(({'candidate':build_bundle,'release':build_release_bundle,'automatic':build_automatic_bundle}[args.schema_model])(),encoding='utf-8')
    files=list(sorted((ROOT/'supabase/migrations').glob('*.sql')))+[ROOT/'supabase/beta-migrations/202610230001_private_beta_security.sql']
    if args.schema_model in ('release','automatic'):files+=list(sorted((ROOT/'supabase/beta-migrations').glob('20261023000[23]_*.sql')))
    if args.schema_model=='automatic':files.append(ROOT/'supabase/beta-migrations/202610230004_automatic_registry.sql')
    manifest={'projectRef':REF,'region':'us-west-2','sourceCommit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
      'workingTreeClean':not subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip(),'preparedOnly':True,
      'policy':{'COMMUNITY_BETA_STATE':'disabled','COMMUNITY_ALLOWED_RATINGS':'1,2'},
      'expectedHostedBaselineFunction':10,'expectedBaselineMigrations':list(BASE),'candidateVersion':'202610230001',
      'onlyCandidateApplied':args.schema_model=='candidate','historyRecordedInSameTransaction':True,
      'schemaModel':args.schema_model,'appliedVersions':[p.stem.split('_')[0] for p in files[4:]],
      'registryCollection':'disabled',
      'administration':{'model':'owner-operated Management SQL','actor':'owner-admin:discovery3','independentModeratorAttribution':False,'separateAuthRequired':False,'compatibilityRole':'NOLOGIN, no client members, legacy EXECUTE revoked'},
      'supersededUnappliedCandidate':{'path':'supabase/beta-migrations/202610230003_auth_operator.sql','sqlLfSha256':'7081d3968cd71e02acfeba92c069ea25ac9b452124765f7564225515aa702068','reason':'Explicit owner architecture revision; never deployed'},
      'files':[{'path':str(p.relative_to(ROOT)).replace('\\','/'),'rawSha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                'sqlLfSha256':hashlib.sha256(p.read_text(encoding='utf-8').encode()).hexdigest()} for p in files],
      'bundleSha256':hashlib.sha256(bundle.read_bytes()).hexdigest(),
      'functionFiles':[{'path':str(p.relative_to(ROOT)).replace('\\','/'),'sha256Lf':hashlib.sha256(p.read_text(encoding='utf-8').encode()).hexdigest()} for p in (ROOT/'supabase/functions/community-v1/index.ts',ROOT/'supabase/functions/community-v1/handler.ts')]}
    if args.schema_model=='automatic':
        upgrade=out/'registry-upgrade.sql';upgrade.write_text(build_registry_upgrade(),encoding='utf-8')
        manifest['incrementalUpgrade']={'fromVersions':sorted(set(BASE)|{'202610230001','202610230002','202610230003'}),'appliedVersions':['202610230004'],'file':upgrade.name,'sha256':hashlib.sha256(upgrade.read_bytes()).hexdigest()}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({'preparedOnly':True,'bundleSha256':manifest['bundleSha256']}))


if __name__=='__main__':main()
