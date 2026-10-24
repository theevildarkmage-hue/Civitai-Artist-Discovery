"""Prepare a pinned atomic identity-session upgrade. No connections or secrets."""
import argparse,hashlib,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
VERSION='202610240003'
BASE=['202610140001','202610150001','202610210001','202610220001','202610230001','202610230002','202610230003','202610230004','202610240001','202610240002']

def build_bundle(*,expected_owner='postgres',expected_database='postgres'):
    migration=ROOT/f'supabase/beta-migrations/{VERSION}_identity_sessions.sql'
    source=migration.read_text(encoding='utf-8')
    assert '$identity_migration$' not in source
    body='\n'.join(line for line in source.splitlines() if line not in ('begin;','commit;'))
    expected='array['+','.join("'"+version+"'" for version in BASE)+']::text[]'
    bundle="""-- Prepared only; separate hosted deployment approval is required.
-- Project identity aibelmhucizupzvvhdvf must also be attested through Management API.
begin;
do $$begin
 if current_user<>'postgres' or current_database()<>'postgres' then raise exception 'Migration authority mismatch';end if;
 if (select array_agg(version order by version) from supabase_migrations.schema_migrations) is distinct from EXPECTED then raise exception 'Migration history mismatch';end if;
 if to_regclass('public.community_identity_sessions') is not null or to_regclass('public.community_identity_replays') is not null then raise exception 'Candidate already present; stop';end if;
end$$;
create temporary table identity_preservation_before(relation text primary key,rows bigint,digest text) on commit drop;
do $$declare r record;n bigint;h text;begin
 for r in select n.nspname,c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace
  where c.relkind='r' and ((n.nspname='public' and c.relname like 'community_%') or n.nspname='community_private') loop
   execute format('select count(*),md5(coalesce(jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text)::text,''[]'')) from %I.%I t',r.nspname,r.relname) into n,h;
   insert into identity_preservation_before values(format('%I.%I',r.nspname,r.relname),n,h);
 end loop;
end$$;
""".replace('EXPECTED',expected)+body+"""
do $$declare r record;n bigint;h text;begin
 for r in select * from identity_preservation_before loop
  execute 'select count(*),md5(coalesce(jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text)::text,''[]'')) from '||r.relation||' t' into n,h;
  if n<>r.rows or h<>r.digest then raise exception 'Existing record integrity mismatch';end if;
 end loop;
end$$;
"""+f"insert into supabase_migrations.schema_migrations(version,name,statements) values('{VERSION}','identity_sessions',array[$identity_migration${source}$identity_migration$]);\nnotify pgrst,'reload schema';\ncommit;\n"
    bundle=bundle.replace("current_user<>'postgres'", "current_user<>'"+expected_owner.replace("'","''")+"'").replace("current_database()<>'postgres'", "current_database()<>'"+expected_database.replace("'","''")+"'")
    return migration,source,bundle


def include_access_policy(bundle):
    path=ROOT/'supabase/beta-migrations/202610240004_community_access_policy.sql'
    source=path.read_text(encoding='utf-8')
    assert '$community_policy_migration$' not in source
    body='\n'.join(line for line in source.splitlines() if line not in ('begin;','commit;'))
    # Compare all original configuration fields; only the new default is excluded.
    return bundle.rsplit('commit;',1)[0]+body+"""
do $$declare r record;n bigint;h text;projection text;begin
 for r in select * from identity_preservation_before loop
  projection:=case when r.relation='community_private.configuration' then 'to_jsonb(t)-''access_mode''' else 'to_jsonb(t)' end;
  execute 'select count(*),md5(coalesce(jsonb_agg('||projection||' order by ('||projection||')::text)::text,''[]'')) from '||r.relation||' t' into n,h;
  if n<>r.rows or h<>r.digest then raise exception 'Existing record integrity mismatch after access policy';end if;
 end loop;
 if (select access_mode from community_private.configuration where singleton)<>'invite_only' then raise exception 'Unexpected policy activation';end if;
end$$;
"""+"insert into supabase_migrations.schema_migrations(version,name,statements) values('202610240004','community_access_policy',array[$community_policy_migration$"+source+"$community_policy_migration$]);\nnotify pgrst,'reload schema';\ncommit;\n"


def include_device_renewal(bundle):
    source=(ROOT/'supabase/beta-migrations/202610240005_device_session_renewal.sql').read_text(encoding='utf-8')
    assert '$device_renewal_migration$' not in source
    body='\n'.join(line for line in source.splitlines() if line not in ('begin;','commit;'))
    # Reuse the already-reviewed original-row comparison after the last migration.
    preservation=bundle[bundle.rindex('do $$declare r record;'):bundle.rindex('end$$;')+7]
    return bundle.rsplit('commit;',1)[0]+body+'\n'+preservation+"\ninsert into supabase_migrations.schema_migrations(version,name,statements) values('202610240005','device_session_renewal',array[$device_renewal_migration$"+source+"$device_renewal_migration$]);\nnotify pgrst,'reload schema';\ncommit;\n"


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--include-community-access',action='store_true',help='Include default-invite migration 12 in the same atomic transaction')
    parser.add_argument('--include-device-renewal',action='store_true',help='Include rotating device renewal migration 13; requires access policy')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();out=args.output.resolve()
    if not (args.include_device_renewal and args.include_community_access):parser.error('Current runtime requires both access policy and device renewal migrations; supply both include flags')
    if out.is_relative_to(ROOT) and not out.is_relative_to(ROOT/'reports'):parser.error('Use ignored reports or an external review directory')
    out.mkdir(parents=True,exist_ok=True)
    migration,source,bundle=build_bundle()
    if args.include_community_access:bundle=include_access_policy(bundle)
    if args.include_device_renewal:bundle=include_device_renewal(bundle)
    (out/'atomic-upgrade.sql').write_bytes(bundle.encode('utf-8'))
    names=['supabase/functions/community-v1/'+name for name in ('index.ts','handler.ts','identity-handler.ts','identity.ts','renewal.ts')]+['supabase/config.toml',str(migration.relative_to(ROOT)).replace('\\','/')]
    if args.include_community_access:names.append('supabase/beta-migrations/202610240004_community_access_policy.sql')
    if args.include_device_renewal:names.append('supabase/beta-migrations/202610240005_device_session_renewal.sql')
    function_dir=out/'function'/'community-v1'
    allowed={Path(name).name for name in names if name.endswith('.ts')}
    if function_dir.exists() and any(p.name not in allowed for p in function_dir.iterdir()):
        parser.error('Function bundle contains unreviewed files; use a new output directory')
    files={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names}
    for name in names:
        if name.endswith('.ts'):
            target=out/'function'/'community-v1'/Path(name).name
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/name).read_bytes())
    manifest={'preparedOnly':True,'projectRef':'aibelmhucizupzvvhdvf','sourceCommit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
      'workingTreeClean':not subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip(),
      'expectedMigrations':BASE,'candidateMigration':VERSION,'lastReportedFunctionVersion':30,'hostedBaselineReverificationRequired':True,
      'requiredNewSecrets':['DISCOVERY_IDENTITY_CHALLENGE_KEY'],
      'candidateMigrations':[VERSION]+(['202610240004'] if args.include_community_access else [])+(['202610240005'] if args.include_device_renewal else []),
      'defaultAccessMode':'invite_only','publicPolicySupported':args.include_community_access,
      'verifyJWT':False,'legacyClientUpgradeRequired':True,'ownerCivitaiKeyRequired':False,'policyChanges':False,'existingRecordsMustMatch':True,'files':files,'atomicSQLSHA256':hashlib.sha256(bundle.encode()).hexdigest()}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({'prepared':True,'files':len(files),'connections':0,'workingTreeClean':manifest['workingTreeClean']}))

if __name__=='__main__':main()
