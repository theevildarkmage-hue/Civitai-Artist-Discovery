"""Pure, fail-closed Community catalog models shared by hosted/local verification."""
BASE_TABLES={'public.'+n for n in ('community_members','community_recommendations','community_audit',
    'community_rate_buckets','community_submission_requests')}
PRIVATE_TABLES={'community_private.'+n for n in ('configuration','operators','invitations','blocks',
    'reports','moderator_audit','artwork_reviews')}
BASE_RPC={
 'community_quota':'bigint','community_gallery':'bigint, integer','community_take_rate':'text',
 'community_submit':'bigint, text, bigint, bigint, boolean',
 'community_submit_once':'bigint, text, bigint, bigint, text',
 'community_withdraw':'bigint, bigint','community_moderate':'bigint, text'}
NEW_RPC={'community_access':'bigint','community_report':'bigint, bigint, text',
 'community_review_candidates':'bigint','community_record_review':'bigint, bigint, bigint, uuid, text',
 'community_beta_gallery':'bigint, bigint, integer','community_operator':'text, bigint, text, timestamp with time zone'}
LEGACY_RPC={n+'_legacy':BASE_RPC[n] for n in ('community_quota','community_submit','community_submit_once','community_withdraw')}
RUNTIME_CANDIDATE={'community_quota','community_submit','community_submit_once','community_withdraw',
 'community_take_rate','community_access','community_report','community_review_candidates',
 'community_record_review','community_beta_gallery'}
BASE_VERSIONS={'202610140001','202610150001','202610210001','202610220001'}


def validate_catalog(matrix,model,*,owner='postgres',migrations=None,expected_definitions=None):
    """No inference/auto-detection: requesting candidate always requires its objects."""
    if model not in ('baseline','candidate','release','automatic'):raise ValueError('Explicit catalog model required')
    candidate=model!='baseline';release=model in ('release','automatic');automatic=model=='automatic';checks=[]
    def check(name,value):checks.append({'name':name,'passed':bool(value)})
    def objects(kind):return matrix.get(kind) or []
    def key(row):return row.get('schema','public')+'.'+str(row.get('name'))
    roles={r.get('role'):r for r in objects('roles')}
    required={'anon','authenticated','service_role'}|({'community_operator'} if candidate else set())
    check('required roles present',required<=roles.keys())
    # Candidate group can exist before baseline migration; it must still be safe.
    group=roles.get('community_operator')
    if group:
        check('restricted operator group',not any(group.get(k,True) for k in
            ('login','inherit','superuser','bypassRls','createRole','createDb','replication')) and group.get('memberOf')==[])
    for name,r in roles.items():
        if name in ('anon','authenticated','service_role'):
            check('role authority '+name,not any(r.get(k,True) for k in ('superuser','createRole','createDb','replication'))
                and (name=='service_role' or r.get('bypassRls') is False) and r.get('memberOf')==[])
        elif name!='community_operator':
            check('restricted operator login '+name,r.get('login') is True and not any(r.get(k,True) for k in
                ('inherit','superuser','bypassRls','createRole','createDb','replication')) and r.get('memberOf')==['community_operator'])
    principals=set(roles)
    def access(row,fields,allowed=None):
        entries=row.get('access') or [];names=[a.get('role') for a in entries]
        check('complete ACL '+key(row),set(names)==principals and len(names)==len(set(names)))
        for a in entries:
            for field in fields:
                expected=bool(allowed and allowed(a.get('role'),field))
                check(key(row)+' '+str(a.get('role'))+' '+field,a.get(field) is expected)
    expected_tables=BASE_TABLES|(PRIVATE_TABLES if candidate else set())
    if release:expected_tables|={'community_private.discovery_users'}
    check('exact table inventory',{key(t) for t in objects('relations')}==expected_tables and len(objects('relations'))==len(expected_tables))
    for row in objects('relations'):
        check('table ownership/RLS '+key(row),row.get('kind')=='r' and row.get('owner')==owner and row.get('rlsEnabled') is True)
        if automatic and key(row)=='community_private.discovery_users':
            check('registry stores only six approved fields',row.get('columns')==['civitai_id','username','first_seen','last_seen','app_version','platform'])
        access(row,('select','insert','update','delete','truncate','references','trigger','maintain',
                    'columnSelect','columnInsert','columnUpdate','columnReferences'))
    expected_sequences={'public.community_recommendations_seq_seq','public.community_audit_seq_seq'}
    if candidate:expected_sequences|={'community_private.reports_seq_seq','community_private.moderator_audit_seq_seq'}
    check('exact sequence inventory',{key(s) for s in objects('sequences')}==expected_sequences and len(objects('sequences'))==len(expected_sequences))
    for row in objects('sequences'):
        check('sequence owner '+key(row),row.get('owner')==owner)
        access(row,('usage','select','update'))
    expected={'public.'+name:types for name,types in BASE_RPC.items()}
    if candidate:
        expected.update({'public.'+name:types for name,types in (NEW_RPC|LEGACY_RPC).items()})
        expected['community_private.assert_member']='bigint, boolean'
    if release:expected.update({'public.community_registry_upsert':'bigint, text, text, text' if automatic else 'bigint, text, text, text, text',
                               'community_private.owner_admin':'text, bigint, text, timestamp with time zone'})
    check('exact routine inventory',{key(r) for r in objects('rpcs')}==set(expected) and len(objects('rpcs'))==len(expected))
    for row in objects('rpcs'):
        name=key(row);path=[c.replace(' ','') for c in row.get('configuration') or []]
        check('routine authority/signature '+name,row.get('owner')==owner and row.get('securityDefiner') is (name!='community_private.owner_admin')
              and path==['search_path=public,pg_temp'] and row.get('types')==expected.get(name))
        def permitted(role,field):
            if release and name=='public.community_registry_upsert':return role=='service_role'
            if name=='public.community_operator':return candidate and not release and role=='community_operator'
            return role=='service_role' and name.startswith('public.') and row.get('name') in (RUNTIME_CANDIDATE if candidate else BASE_RPC)
        access(row,('execute',),permitted)
    schemas=objects('schemas')
    check('exact schema presence',{r.get('name') for r in schemas}==({'public','community_private'} if candidate else {'public'}) and len(schemas)==1+int(candidate))
    for row in schemas:
        if row.get('name')=='public':
            check('public schema owner',row.get('owner') in (owner,'pg_database_owner'))
            access(row,('create',))
            for entry in row.get('access') or []:
                if entry.get('role') in ('service_role','community_operator'):check('public runtime/operator usage '+entry['role'],entry.get('usage') is True)
        else:
            check('private schema owner',row.get('owner')==owner)
            access(row,('usage','create'))
    check('no ordinary-client RLS policies',matrix.get('policies')==[])
    if migrations is not None:
        versions=[m.get('version') for m in migrations]
        needed=BASE_VERSIONS|({'202610230001'} if candidate else set())
        if release:needed|={'202610230002','202610230003'}
        if automatic:needed.add('202610230004')
        check('required migration history',needed<=set(versions) and len(versions)==len(set(versions)))
        if not candidate:check('candidate history absent','202610230001' not in versions)
    if expected_definitions is not None:
        actual={key(r):r.get('bodyMd5') for r in objects('rpcs')}
        check('pinned function-body fingerprints',actual==expected_definitions)
    return {'model':model,'passed':all(c['passed'] for c in checks),'checks':checks,
            'errors':[c['name'] for c in checks if not c['passed']]}
