"""Maintained manifest, native Production bootstrap and encrypted recovery tests."""
import test_safety
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=test_safety.ROOT
spec=importlib.util.spec_from_file_location('current_community_recovery',ROOT/'scripts/community-recovery.py')
recovery=importlib.util.module_from_spec(spec);spec.loader.exec_module(recovery)
DEFAULT_BIN=Path(os.environ.get('LOCALAPPDATA',''))/'CAD3Tools/postgresql-17.11-5/pgsql/bin'
BIN=Path(os.environ.get('CAD_POSTGRES_BIN',DEFAULT_BIN))
AVAILABLE=(BIN/('psql.exe' if os.name=='nt' else 'psql')).is_file()

class ManifestTests(unittest.TestCase):
    def test_production_credentials_never_fall_back_to_development_store(self):
        with patch.dict(os.environ,{},clear=True),patch.object(recovery.importlib.util,'spec_from_file_location',side_effect=AssertionError('Development store must not load')):
            with self.assertRaisesRegex(RuntimeError,'explicitly injected'):recovery.secure_token('production')
            with patch.dict(os.environ,{'SUPABASE_ACCESS_TOKEN':'synthetic-explicit-production-token'}):
                self.assertEqual(recovery.secure_token('production'),'synthetic-explicit-production-token')
            with patch.dict(os.environ,{'SUPABASE_ACCESS_TOKEN':'   '}):
                with self.assertRaises(RuntimeError):recovery.secure_token('production')
        with self.assertRaises(ValueError):recovery.secure_token('unknown')
    def test_both_manifests_are_pinned_and_different(self):
        dev=recovery.load_manifest(ROOT/'supabase/recovery/development-v16.json')
        prod=recovery.load_manifest(ROOT/'supabase/production/bootstrap-v1.json')
        self.assertEqual(len(dev['files']),16);self.assertEqual(len(prod['files']),15)
        self.assertEqual(len(dev['tables']),17);self.assertEqual(len(dev['sequences']),4)
        self.assertIsNone(prod['projectRef']);self.assertNotEqual(dev['catalogSha256'],prod['catalogSha256'])
        self.assertFalse(any(f['version'] in ('202610240001','202610270001') for f in prod['files']))
    def test_redirects_and_mutations_not_in_management_transport(self):
        transport=recovery.ReadOnlyManagement('a'*20,'synthetic-not-a-token')
        for path in ('/database/query','/functions/community-v1','/database/password'):
            with self.assertRaises(ValueError):transport.read(path)
        with self.assertRaises(ValueError):recovery.ReadOnlyManagement('https://example.invalid','synthetic')
    def test_manifest_escape_and_missing_pins_rejected(self):
        manifest=json.loads((ROOT/'supabase/recovery/development-v16.json').read_text())
        with tempfile.TemporaryDirectory(prefix='cad3-recovery-manifest-') as folder:
            path=Path(folder)/'bad.json'
            for mutate in ('escape','pin','seal'):
                value=copy.deepcopy(manifest)
                if mutate=='escape':value['files'][0]['path']='../../not-a-source.sql'
                if mutate=='pin':value['files'][0]['sha256Lf']='0'*64
                if mutate=='seal':value['catalogSha256']=''
                path.write_text(json.dumps(value))
                with self.assertRaises((ValueError,RuntimeError)):recovery.load_manifest(path)

@unittest.skipUnless(AVAILABLE,'Native PostgreSQL bin directory unavailable; set CAD_POSTGRES_BIN')
class NativeRecoveryTests(unittest.TestCase):
    def test_owned_environment_rejects_libpq_redirects_and_restores_quoted_data(self):
        manifest=recovery.load_manifest(ROOT/'supabase/recovery/development-v16.json')
        hostile={'PGHOSTADDR':'192.0.2.1','PGHOST':'untrusted.invalid','PGPORT':'1',
                 'PGSERVICE':'untrusted-service','PGSERVICEFILE':'untrusted-service-file',
                 'PGSYSCONFDIR':'untrusted-directory','PGDATABASE':'untrusted-db',
                 'PGUSER':'untrusted-user','PGPASSWORD':'synthetic-untrusted-password',
                 'PGOPTIONS':'-c standard_conforming_strings=off',
                 'SUPABASE_ACCESS_TOKEN':'synthetic-hosted-token',
                 'CAD_TEST_CIVITAI_TOKEN':'synthetic-app-token',
                 'CIVITAI_API_KEY':'synthetic-api-key'}
        with patch.dict(os.environ,hostile):
            with recovery.OwnedPostgres(BIN) as cluster:
                owned_pg={'PGHOST','PGPORT','PGUSER','PGDATABASE','PGPASSWORD',
                          'PGCONNECT_TIMEOUT','PGTZ','PGOPTIONS'}
                self.assertEqual({k for k in cluster.env if k.upper().startswith('PG')},owned_pg)
                self.assertFalse(any(k.upper().startswith(('SUPABASE','CAD','CIVITAI')) for k in cluster.env))
                self.assertNotEqual(cluster.env['PGPASSWORD'],hostile['PGPASSWORD'])
                self.assertEqual(cluster.sql("select current_setting('standard_conforming_strings')"),'on')
                self.assertEqual(cluster.sql('select host(inet_server_addr())'),'127.0.0.1')
                self.assertEqual(cluster.sql('select inet_server_port()::text'),cluster.env['PGPORT'])
                cluster.bootstrap(manifest)
                value="O'Brien\\literal\\n'; select pg_sleep(999); --"
                cluster.sql('insert into community_members values(987654321,'+recovery.literal(value)+');')
                snapshot,catalog=cluster.state(manifest)
                recovery.validate_state(snapshot,catalog,manifest)
                cluster.sql('delete from community_members where civitai_id=987654321;')
                cluster.restore(snapshot,manifest)
                restored,restored_catalog=cluster.state(manifest)
                self.assertEqual(recovery.canonical(restored),recovery.canonical(snapshot))
                self.assertEqual(recovery.canonical(restored_catalog),recovery.canonical(catalog))
                self.assertEqual(cluster.sql('select username from community_members where civitai_id=987654321'),value)
    def test_clean_production_security_and_normal_quotas(self):
        manifest=recovery.load_manifest(ROOT/'supabase/production/bootstrap-v1.json')
        with recovery.OwnedPostgres(BIN) as cluster:
            folder=cluster.directory
            cluster.bootstrap(manifest)
            snapshot,catalog=cluster.state(manifest);recovery.validate_state(snapshot,catalog,manifest)
            self.assertEqual(cluster.sql('select count(*) from community_private.moderator_audit'),'0')
            self.assertEqual(cluster.sql('select ready from community_private.configuration where singleton'),'f')
            self.assertEqual(cluster.sql('select count(*) from public.community_members'),'0')
            self.assertFalse(any('11107642' in f or '12594272' in f for f in snapshot['functionDefinitions']))
            for role in ('anon','authenticated','service_role','community_operator'):
                for table in ('community_private.invitations','public.community_identity_sessions','public.community_device_families','public.community_recommendations'):
                    cluster.sql('begin;set local role '+role+';select * from '+table+';rollback;',expect_error='42501')
                cluster.sql("begin;set local role "+role+";select community_private.owner_admin('invite',999999999999,'Synthetic denial','infinity');rollback;",expect_error='42501')
            # A missing invitation does not become public access until explicit owner policy change.
            cluster.sql("select community_access(999999999999)",expect_error='BETA_DISABLED')
            cluster.sql("begin;update community_private.configuration set ready=true;select community_access(999999999999);",expect_error='INVITE_ONLY')
            cluster.sql("update community_private.configuration set ready=true,access_mode='public' where singleton;")
            for member in (11107642,12594272,999999999998):
                for i in range(3):cluster.sql(f"select community_submit_legacy({member},'Synthetic fixture',{member*100+i},{998000000000+member*10+i},false)")
                cluster.sql(f"select community_submit_legacy({member},'Synthetic fixture',{member*100+3},{998000000000+member*10+3},false)",expect_error='MEMBER_QUOTA')
            cluster.sql("select community_submit_legacy(999999999997,'Synthetic fixture',1110764200,998000000000,false)",expect_error='DUPLICATE')
            cluster.sql("select community_submit_legacy(999999999997,'Synthetic fixture',999999999996,999999999997,false)",expect_error='SELF_RECOMMENDATION')
            for i in range(3):cluster.sql(f"select community_submit_legacy({997000000000+i},'Synthetic fixture',{996000000000+i},995000000000,false)")
            cluster.sql("select community_submit_legacy(997000000003,'Synthetic fixture',996000000003,995000000000,false)",expect_error='ARTIST_QUOTA')
            for key in ('catalogSha256','schemaSha256'):
                bad=copy.deepcopy(manifest);bad[key]='0'*64
                with self.assertRaises(RuntimeError):recovery.validate_state(snapshot,catalog,bad)
            for kind in ('grant','rls','rpc','policy'):
                bad=copy.deepcopy(catalog)
                if kind=='grant':bad['relations'][0]['access'][0]['select']=True
                if kind=='rls':bad['relations'][0]['rlsEnabled']=False
                if kind=='rpc':bad['rpcs'][0]['bodyMd5']='0'*32
                if kind=='policy':bad['policies'].append({'name':'bad'})
                with self.assertRaises(RuntimeError):recovery.validate_state(snapshot,bad,manifest)
        self.assertFalse(folder.exists())
    @unittest.skipUnless(os.name=='nt','DPAPI encryption requires Windows; no plaintext fallback')
    def test_encrypted_current_schema_restore_and_adversarial_archives(self):
        manifest=recovery.load_manifest(ROOT/'supabase/recovery/development-v16.json')
        with recovery.OwnedPostgres(BIN) as cluster:
            cluster.bootstrap(manifest);snapshot,catalog=cluster.state(manifest)
        document={'format':1,'projectRef':manifest['projectRef'],'manifestSha256':recovery.digest(manifest),'snapshot':snapshot,'catalog':catalog}
        with tempfile.TemporaryDirectory(prefix='cad3-current-backup-test-') as folder:
            path=Path(folder)/'fixture.dpapi';path.write_bytes(recovery.transform(json.dumps(document).encode()))
            result=recovery.verify_archive(path,manifest,BIN)
            self.assertEqual(result['tablesRestored'],17);self.assertEqual(result['sqlPermissionDenials'],8)
            tampered=bytearray(path.read_bytes());tampered[-1]^=1;path.write_bytes(tampered)
            with self.assertRaises(RuntimeError):recovery.verify_archive(path,manifest,BIN)
            bad=copy.deepcopy(document);bad['projectRef']='b'*20
            path.write_bytes(recovery.transform(json.dumps(bad).encode()))
            with self.assertRaises(RuntimeError):recovery.verify_archive(path,manifest,BIN)
            bad=copy.deepcopy(document);bad['snapshot']['migrations'][-1]['statements']=['select 1;']
            path.write_bytes(recovery.transform(json.dumps(bad).encode()))
            with self.assertRaises(RuntimeError):recovery.verify_archive(path,manifest,BIN)

if __name__=='__main__':unittest.main()
