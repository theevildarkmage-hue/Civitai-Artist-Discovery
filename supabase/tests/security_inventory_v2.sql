-- Read-only inventory for explicit baseline/candidate model validation. No user rows.
with recursive operator_members(oid) as (
 select oid from pg_roles where rolname='community_operator'
 union select m.member from pg_auth_members m join operator_members g on m.roleid=g.oid
 -- Infrastructure owners are already outside the restricted-login boundary.
 -- Do not relabel their existing administration descendants as moderator logins.
 where m.roleid not in (select oid from pg_roles where rolname in (current_user,'postgres'))
), principals as (
 select r.* from pg_roles r where r.rolname in ('anon','authenticated','service_role','community_operator')
 or (r.oid in (select oid from operator_members) and r.rolname not in (current_user,'postgres'))
), scope as (
 select n.oid,n.nspname from pg_namespace n where n.nspname in ('public','community_private')
)
select jsonb_build_object(
 'roles',(select jsonb_agg(jsonb_build_object('role',r.rolname,'login',r.rolcanlogin,'inherit',r.rolinherit,
   'superuser',r.rolsuper,'bypassRls',r.rolbypassrls,'createRole',r.rolcreaterole,'createDb',r.rolcreatedb,'replication',r.rolreplication,
   'memberOf',(select coalesce(jsonb_agg(g.rolname order by g.rolname),'[]'::jsonb) from pg_roles g join pg_auth_members m on m.roleid=g.oid where m.member=r.oid))) from principals r),
 'schemas',(select jsonb_agg(jsonb_build_object('name',n.nspname,'owner',pg_get_userbyid(n.nspowner),
   'access',(select jsonb_agg(jsonb_build_object('role',r.rolname,'usage',has_schema_privilege(r.oid,n.oid,'USAGE'),'create',has_schema_privilege(r.oid,n.oid,'CREATE'))) from principals r)))
   from pg_namespace n where n.nspname in ('public','community_private')),
 'relations',(select jsonb_agg(jsonb_build_object('schema',n.nspname,'name',c.relname,'kind',c.relkind,'owner',pg_get_userbyid(c.relowner),
   'rlsEnabled',c.relrowsecurity,'rlsForced',c.relforcerowsecurity,
     'columns',(select jsonb_agg(a.attname order by a.attnum) from pg_attribute a where a.attrelid=c.oid and a.attnum>0 and not a.attisdropped),
   'access',(select jsonb_agg(jsonb_build_object('role',r.rolname,
     'select',has_table_privilege(r.oid,c.oid,'SELECT'),'insert',has_table_privilege(r.oid,c.oid,'INSERT'),
     'update',has_table_privilege(r.oid,c.oid,'UPDATE'),'delete',has_table_privilege(r.oid,c.oid,'DELETE'),
     'truncate',has_table_privilege(r.oid,c.oid,'TRUNCATE'),'references',has_table_privilege(r.oid,c.oid,'REFERENCES'),
     'trigger',has_table_privilege(r.oid,c.oid,'TRIGGER'),
     'columnSelect',has_any_column_privilege(r.oid,c.oid,'SELECT'),
     'columnInsert',has_any_column_privilege(r.oid,c.oid,'INSERT'),
     'columnUpdate',has_any_column_privilege(r.oid,c.oid,'UPDATE'),
     'columnReferences',has_any_column_privilege(r.oid,c.oid,'REFERENCES'),
     'maintain',case when current_setting('server_version_num')::int>=170000 then has_table_privilege(r.oid,c.oid,'MAINTAIN') else false end)) from principals r)))
   from pg_class c join scope n on n.oid=c.relnamespace where c.relkind in ('r','p','v','m') and (n.nspname='community_private' or c.relname like 'community_%')),
 'sequences',(select jsonb_agg(jsonb_build_object('schema',n.nspname,'name',c.relname,'owner',pg_get_userbyid(c.relowner),
   'access',(select jsonb_agg(jsonb_build_object('role',r.rolname,'usage',has_sequence_privilege(r.oid,c.oid,'USAGE'),
    'select',has_sequence_privilege(r.oid,c.oid,'SELECT'),'update',has_sequence_privilege(r.oid,c.oid,'UPDATE'))) from principals r)))
   from pg_class c join scope n on n.oid=c.relnamespace where c.relkind='S' and (n.nspname='community_private' or c.relname like 'community_%')),
 'rpcs',(select jsonb_agg(jsonb_build_object('schema',n.nspname,'name',p.proname,'types',oidvectortypes(p.proargtypes),
   'owner',pg_get_userbyid(p.proowner),'securityDefiner',p.prosecdef,'configuration',p.proconfig,
   'bodyMd5',md5(replace(p.prosrc,chr(13)||chr(10),chr(10))),'bodyStoredMd5',md5(p.prosrc),
   'access',(select jsonb_agg(jsonb_build_object('role',r.rolname,'execute',has_function_privilege(r.oid,p.oid,'EXECUTE'))) from principals r)))
   from pg_proc p join scope n on n.oid=p.pronamespace where n.nspname='community_private' or p.proname like 'community_%'),
 'policies',(select coalesce(jsonb_agg(jsonb_build_object('schema',schemaname,'table',tablename,'name',policyname)),'[]'::jsonb)
   from pg_policies where schemaname='community_private' or (schemaname='public' and tablename like 'community_%'))
);
