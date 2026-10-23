"""Catalog adversarial model tests; native rehearsal also mutates real SQL ACLs."""
import test_safety
import copy
import sys
import unittest
sys.path.insert(0,str(test_safety.ROOT))
from discovery.community_catalog import validate_catalog,BASE_TABLES,PRIVATE_TABLES,BASE_RPC,NEW_RPC,LEGACY_RPC,RUNTIME_CANDIDATE,BASE_VERSIONS


def fixture(candidate):
    names=['anon','authenticated','service_role']+(['community_operator'] if candidate else [])
    roles=[dict(role=name,login=False,inherit=name!='community_operator',superuser=False,
        bypassRls=name=='service_role',createRole=False,createDb=False,replication=False,memberOf=[]) for name in names]
    def entries(fields,allowed=lambda name,field:False):return [dict(role=name,**{f:allowed(name,f) for f in fields}) for name in names]
    tables=[]
    for key in BASE_TABLES|(PRIVATE_TABLES if candidate else set()):
        schema,name=key.split('.')
        tables.append(dict(schema=schema,name=name,owner='postgres',kind='r',rlsEnabled=True,
            access=entries(('select','insert','update','delete','truncate','references','trigger','maintain',
                            'columnSelect','columnInsert','columnUpdate','columnReferences'))))
    sequences=[]
    for key in ['public.community_recommendations_seq_seq','public.community_audit_seq_seq']+(
        ['community_private.reports_seq_seq','community_private.moderator_audit_seq_seq'] if candidate else []):
        schema,name=key.split('.');sequences.append(dict(schema=schema,name=name,owner='postgres',access=entries(('select','usage','update'))))
    functions={'public.'+n:t for n,t in BASE_RPC.items()}
    if candidate:
        functions.update({'public.'+n:t for n,t in (NEW_RPC|LEGACY_RPC).items()})
        functions['community_private.assert_member']='bigint, boolean'
    rpcs=[]
    for key,types in functions.items():
        schema,name=key.split('.')
        def permitted(role,field):return (role=='community_operator' if name=='community_operator' else
            role=='service_role' and schema=='public' and name in (RUNTIME_CANDIDATE if candidate else BASE_RPC))
        rpcs.append(dict(schema=schema,name=name,types=types,owner='postgres',securityDefiner=True,
            configuration=['search_path=public, pg_temp'],bodyMd5='fixture-digest',access=entries(('execute',),permitted)))
    return dict(roles=roles,relations=tables,sequences=sequences,rpcs=rpcs,policies=[],
        schemas=[dict(name='public',owner='pg_database_owner',access=entries(('usage','create'),lambda name,field:field=='usage'))]+([dict(name='community_private',owner='postgres',access=entries(('usage','create')))] if candidate else []))


class CatalogTests(unittest.TestCase):
    def test_valid_models(self):
        for model in ('baseline','candidate'):self.assertTrue(validate_catalog(fixture(model=='candidate'),model)['passed'])
    def test_wrong_model_never_passes(self):
        self.assertFalse(validate_catalog(fixture(False),'candidate')['passed'])
        self.assertFalse(validate_catalog(fixture(True),'baseline')['passed'])
    def test_missing_roles_or_acl_fail_closed(self):
        for change in ('roles','access'):
            matrix=fixture(True)
            if change=='roles':matrix['roles']=[]
            else:matrix['relations'][0]['access']=[]
            self.assertFalse(validate_catalog(matrix,'candidate')['passed'])
    def test_runtime_moderation_rejected(self):
        matrix=fixture(True);routine=next(r for r in matrix['rpcs'] if r['name']=='community_operator')
        next(a for a in routine['access'] if a['role']=='service_role')['execute']=True
        self.assertFalse(validate_catalog(matrix,'candidate')['passed'])
    def test_private_grant_and_rls_rejected(self):
        for field in ('select','references','trigger','maintain','columnSelect','columnInsert','columnUpdate','columnReferences'):
            matrix=fixture(True);matrix['relations'][0]['access'][0][field]=True
            self.assertFalse(validate_catalog(matrix,'candidate')['passed'])
        matrix=fixture(True);matrix['relations'][0]['rlsEnabled']=False
        self.assertFalse(validate_catalog(matrix,'candidate')['passed'])
    def test_function_authority(self):
        for field,value in [('owner','service_role'),('securityDefiner',False),('types','text'),('configuration',['search_path=pg_temp'])]:
            matrix=fixture(True);matrix['rpcs'][0][field]=value
            self.assertFalse(validate_catalog(matrix,'candidate')['passed'])
    def test_operator_role_elevation(self):
        for field,value in [('inherit',True),('login',True),('createRole',True),('memberOf',['service_role'])]:
            matrix=fixture(True);next(r for r in matrix['roles'] if r['role']=='community_operator')[field]=value
            self.assertFalse(validate_catalog(matrix,'candidate')['passed'])
    def test_ordinary_role_cannot_join_any_privileged_group(self):
        for role in ('anon','authenticated','service_role'):
            matrix=fixture(True)
            next(r for r in matrix['roles'] if r['role']==role)['memberOf']=['postgres']
            self.assertFalse(validate_catalog(matrix,'candidate')['passed'])

    def test_overloads_and_extra_objects(self):
        matrix=fixture(True);matrix['rpcs'].append(copy.deepcopy(matrix['rpcs'][0]))
        self.assertFalse(validate_catalog(matrix,'candidate')['passed'])
    def test_migration_history(self):
        history=[{'version':v} for v in BASE_VERSIONS]
        self.assertFalse(validate_catalog(fixture(True),'candidate',migrations=history)['passed'])
        self.assertTrue(validate_catalog(fixture(True),'candidate',migrations=history+[{'version':'202610230001'}])['passed'])
    def test_definition_pins(self):
        matrix=fixture(True);pins={r['schema']+'.'+r['name']:r['bodyMd5'] for r in matrix['rpcs']}
        self.assertTrue(validate_catalog(matrix,'candidate',expected_definitions=pins)['passed'])
        matrix['rpcs'][0]['bodyMd5']='unexpected'
        self.assertFalse(validate_catalog(matrix,'candidate',expected_definitions=pins)['passed'])


if __name__=='__main__':unittest.main()
