"""GA membership regression on exact Production SQL in owned PostgreSQL only."""
import test_safety
import hashlib,importlib.util,json,os
from pathlib import Path
import unittest

ROOT=test_safety.ROOT
spec=importlib.util.spec_from_file_location('public_enrollment_recovery',ROOT/'scripts/community-recovery.py')
recovery=importlib.util.module_from_spec(spec);spec.loader.exec_module(recovery)
BIN=Path(os.environ.get('CAD_POSTGRES_BIN',Path(os.environ.get('LOCALAPPDATA',''))/'CAD3Tools/postgresql-17.11-5/pgsql/bin'))

@unittest.skipUnless((BIN/('psql.exe' if os.name=='nt' else 'psql')).is_file(),'Owned native PostgreSQL prerequisite unavailable')
class PublicEnrollment(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.cluster=recovery.OwnedPostgres(BIN).__enter__()
  try:
   cls.manifest=recovery.load_manifest(ROOT/'supabase/production/bootstrap-v2.json')
   cls.cluster.bootstrap(cls.manifest)
   snapshot,catalog=cls.cluster.state(cls.manifest);recovery.validate_state(snapshot,catalog,cls.manifest)
  except BaseException:cls.cluster.__exit__(None,None,None);raise
 @classmethod
 def tearDownClass(cls):cls.cluster.__exit__(None,None,None)
 def exchange(self,number,member=91000001,name='Verified'):
  hashes=[hashlib.sha256((str(number)+':'+key).encode()).hexdigest() for key in ('assertion','nonce','session')]
  return "select community_identity_exchange("+','.join(recovery.literal(x) for x in hashes)+','+str(member)+','+recovery.literal(name)+",clock_timestamp()+interval '1 hour');"
 def public(self):return "update community_private.configuration set ready=true;select community_private.owner_access_policy('public','Disposable GA acceptance');"
 def test_membership_exists_before_any_submission_and_login_updates_one_row(self):
  result=self.cluster.sql('begin;'+self.public()+self.exchange(1)+"do $$begin assert(select count(*) from community_members where civitai_id=91000001 and username='Verified')=1;assert not exists(select 1 from community_recommendations);end$$;"+self.exchange(2,name='Updated')+"select count(*)||':'||min(username) from community_members where civitai_id=91000001;select count(*) from community_recommendations;rollback;")
  self.assertIn('1:Updated',result);self.assertTrue(result.endswith('0'))
 def test_public_no_invitation_immediate_access_and_ordinary_quotas(self):
  self.cluster.sql('begin;'+self.public()+self.exchange(3)+"""do $$begin
   assert not exists(select 1 from community_private.invitations);
   assert community_access(91000001)->>'accessMode'='public';
   assert jsonb_array_length(community_recommendation_list(91000001)->'items')=0;
   for i in 1..3 loop perform community_submit(91000001,'Verified',92000000+i,93000000+i,false);end loop;
   begin perform community_submit(91000001,'Verified',92000004,93000004,false);raise exception 'BYPASS';exception when others then assert sqlerrm='MEMBER_QUOTA';end;
  end$$;rollback;""")
 def test_replayed_exchange_cannot_change_membership(self):
  self.cluster.sql('begin;'+self.public()+self.exchange(4)+"do $$begin begin "+self.exchange(4,name='Forged').replace('select community_identity_exchange','perform community_identity_exchange')+"raise exception 'BYPASS';exception when others then assert sqlerrm='AUTH_EXPIRED';end;assert(select username from community_members where civitai_id=91000001)='Verified';end$$;rollback;")
 def test_disabled_and_invite_only_preserve_enrollment_boundaries(self):
  self.cluster.sql('begin;'+self.exchange(5)+"select community_private.owner_access_policy('public','Disabled GA test');"+self.exchange(6)+"do $$begin assert not exists(select 1 from community_members);end$$;update community_private.configuration set ready=true;select community_private.owner_access_policy('invite_only','Restore fixture');"+self.exchange(7)+"do $$begin assert not exists(select 1 from community_members);begin perform community_access(91000001);raise exception 'BYPASS';exception when others then assert sqlerrm='INVITE_ONLY';end;end$$;rollback;")
 def test_membership_does_not_bypass_blocks(self):
  self.cluster.sql('begin;'+self.public()+self.exchange(8)+"insert into community_private.blocks values(91000001,true);do $$begin assert exists(select 1 from community_members where civitai_id=91000001);begin perform community_access(91000001);raise exception 'BYPASS';exception when others then assert sqlerrm='SUBMITTER_BLOCKED';end;end$$;rollback;")
 def test_permissions_and_catalog_remain_sealed(self):
  self.cluster.sql("do $$begin assert not has_function_privilege('anon','community_identity_exchange(text,text,text,bigint,text,timestamptz)','execute');assert not has_function_privilege('authenticated','community_identity_exchange(text,text,text,bigint,text,timestamptz)','execute');assert has_function_privilege('service_role','community_identity_exchange(text,text,text,bigint,text,timestamptz)','execute');assert not has_table_privilege('service_role','community_members','select');end$$;")
  snapshot,catalog=self.cluster.state(self.manifest);recovery.validate_state(snapshot,catalog,self.manifest)

if __name__=='__main__':unittest.main()
