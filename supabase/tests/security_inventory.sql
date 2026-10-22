-- Read-only catalog inventory. No keys, application rows or mutating RPC calls.
select jsonb_build_object(
  'roles',(select jsonb_agg(jsonb_build_object('role',rolname,'superuser',rolsuper,'bypassRls',rolbypassrls))
    from pg_catalog.pg_roles where rolname in ('anon','authenticated','service_role',current_user,'postgres')),
  'relations',(select jsonb_agg(jsonb_build_object(
    'name',c.relname,'kind',c.relkind,'owner',pg_catalog.pg_get_userbyid(c.relowner),
    'rlsEnabled',c.relrowsecurity,'rlsForced',c.relforcerowsecurity,
    'access',(select jsonb_agg(jsonb_build_object('role',r.rolname,
      'select',pg_catalog.has_table_privilege(r.oid,c.oid,'SELECT'),
      'insert',pg_catalog.has_table_privilege(r.oid,c.oid,'INSERT'),
      'update',pg_catalog.has_table_privilege(r.oid,c.oid,'UPDATE'),
      'delete',pg_catalog.has_table_privilege(r.oid,c.oid,'DELETE'),
      'truncate',pg_catalog.has_table_privilege(r.oid,c.oid,'TRUNCATE')))
      from pg_catalog.pg_roles r where r.rolname in ('anon','authenticated','service_role',current_user,'postgres'))))
    from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid=c.relnamespace
    where n.nspname='public' and c.relname like 'community_%' and c.relkind in ('r','p','v','m')),
  'sequences',(select jsonb_agg(jsonb_build_object('name',c.relname,'owner',pg_catalog.pg_get_userbyid(c.relowner),
    'access',(select jsonb_agg(jsonb_build_object('role',r.rolname,
      'usage',pg_catalog.has_sequence_privilege(r.oid,c.oid,'USAGE'),
      'select',pg_catalog.has_sequence_privilege(r.oid,c.oid,'SELECT'),
      'update',pg_catalog.has_sequence_privilege(r.oid,c.oid,'UPDATE')))
      from pg_catalog.pg_roles r where r.rolname in ('anon','authenticated','service_role',current_user,'postgres'))))
    from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid=c.relnamespace
    where n.nspname='public' and c.relname like 'community_%' and c.relkind='S'),
  'rpcs',(select jsonb_agg(jsonb_build_object('name',p.proname,
    'arguments',pg_catalog.pg_get_function_identity_arguments(p.oid),
    'owner',pg_catalog.pg_get_userbyid(p.proowner),'securityDefiner',p.prosecdef,'configuration',p.proconfig,
    'access',(select jsonb_agg(jsonb_build_object('role',r.rolname,'execute',pg_catalog.has_function_privilege(r.oid,p.oid,'EXECUTE')))
      from pg_catalog.pg_roles r where r.rolname in ('anon','authenticated','service_role',current_user,'postgres'))))
    from pg_catalog.pg_proc p join pg_catalog.pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and p.proname like 'community_%'),
  'policies',(select coalesce(jsonb_agg(jsonb_build_object('table',tablename,'name',policyname,'roles',roles,'command',cmd)),'[]'::jsonb)
    from pg_catalog.pg_policies where schemaname='public' and tablename like 'community_%')
);
