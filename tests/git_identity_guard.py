"""Real Git/hook regression cases; synthetic addresses only, no hosted writes."""
import test_safety
import hashlib,hmac,importlib.util,json,os,re,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('identity_policy',ROOT/'scripts/git_identity.py')
POLICY=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(POLICY)
SYNTHETIC='blocked-fixture@example.invalid'
DIGEST=hashlib.sha256(SYNTHETIC.encode()).hexdigest()

class IdentityGuard(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='cad3-identity-',ignore_cleanup_errors=True);self.addCleanup(self.temp.cleanup)
  self.base=Path(self.temp.name);self.repo=self.base/'repo';self.repo.mkdir();self.guard=self.base/'policy'
  self.env={k:v for k,v in os.environ.items() if not k.startswith(('GIT_AUTHOR_','GIT_COMMITTER_','GIT_CONFIG'))}
  self.env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=str(self.base/'safe.gitconfig'))
  key=bytes(range(32))
  def table(value):
   n=''.join(c for c in value.casefold() if c.isalnum());return {str(len(n)):[hmac.new(key,n.encode(),hashlib.sha256).hexdigest()]}
  private=self.base/'private-policy.json';private.write_text(json.dumps({'key':key.hex(),'names':table('fixturefamily'),'emails':table(SYNTHETIC),'homeAccounts':table('syntheticlocal')}))
  self.env.pop('DISCOVERY_IDENTITY_POLICY',None);self.env['DISCOVERY_IDENTITY_POLICY_FILE']=str(private)
  (self.base/'safe.gitconfig').write_text('[user]\n name = Synthetic Fixture\n email = fixture@example.invalid\n')
  self.run_git('init','-b','main');self.run_git('config','user.name',POLICY.OWNER_LOGIN);self.run_git('config','user.email',POLICY.OWNER_EMAIL)
  (self.repo/'scripts').mkdir();(self.repo/'scripts/check-source.py').write_text('import os,sys\nsys.exit(17 if os.environ.get("FIXTURE_REJECT_SOURCE") else 0)\n')
  (self.repo/'value.txt').write_text('initial');self.run_git('add','.');self.commit('initial');self.initial=self.head()
  (self.guard/'scripts').mkdir(parents=True);shutil.copytree(ROOT/'.githooks',self.guard/'.githooks')
  (self.guard/'discovery').mkdir()
  for name in ('__init__.py','privacy_patterns.py'):shutil.copy2(ROOT/'discovery'/name,self.guard/'discovery'/name)
  privacy=self.guard/'discovery/privacy_patterns.py'
  pin=hashlib.sha256(json.dumps(json.loads(private.read_text()),sort_keys=True,separators=(',',':')).encode()).hexdigest()
  privacy.write_text(re.sub(r"^EXPECTED_POLICY_SHA256=.*$",'EXPECTED_POLICY_SHA256='+repr(pin),privacy.read_text(),flags=re.M))
  for name in ('git_identity.py','check-git-identity.py','install-identity-hooks.py'):shutil.copy2(ROOT/'scripts'/name,self.guard/'scripts'/name)
  code=(self.guard/'scripts/git_identity.py').read_text();code=code.replace('# Tests may add synthetic blocked digests in an isolated copy.',f"BLOCKED_EMAIL_DIGESTS=BLOCKED_EMAIL_DIGESTS|{{'{DIGEST}'}}\nBLOCKED_EMAIL_LENGTHS=BLOCKED_EMAIL_LENGTHS|{{{len(SYNTHETIC)}}}")
  (self.guard/'scripts/git_identity.py').write_text(code)
  self.cli=self.guard/'scripts/check-git-identity.py'
  self.installed=False
 def command(self,args,*,env=None,input=None,ok=True,cwd=None):
  r=subprocess.run(args,cwd=cwd or self.repo,env={**self.env,**(env or {})},input=input,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  if ok and r.returncode:raise AssertionError('Owned fixture command failed; output withheld')
  return r
 def run_git(self,*args,**kw):return self.command(['git',*args],**kw)
 def commit(self,message,**kw):return self.run_git('commit','--allow-empty','-m',message,**kw)
 def head(self):return self.run_git('rev-parse','HEAD').stdout.decode().strip()
 def audit(self,*args,**kw):return self.command([sys.executable,str(self.cli),*args],**kw)
 def install(self):
  self.command([sys.executable,str(self.guard/'scripts/install-identity-hooks.py'),'--owner','--bundle-root',str(self.base/'installed')]);self.installed=True
 def rejected(self,result):
  self.assertNotEqual(result.returncode,0);self.assertNotIn(SYNTHETIC.encode(),result.stdout+result.stderr)
 def unsafe_commit(self,*,author=False,committer=False,message='synthetic blocked metadata'):
  env={}
  if author:env.update(GIT_AUTHOR_NAME='Fixture',GIT_AUTHOR_EMAIL=SYNTHETIC)
  if committer:env.update(GIT_COMMITTER_NAME='Fixture',GIT_COMMITTER_EMAIL=SYNTHETIC)
  self.run_git('-c','core.hooksPath='+str(self.base/'empty-hooks'),'commit','--allow-empty','-m',message,env=env)
  return self.head()
 def test_effective_author_and_committer_are_independent_and_redacted(self):
  for field in ('AUTHOR','COMMITTER'):
   self.rejected(self.audit('--effective',env={f'GIT_{field}_EMAIL':SYNTHETIC},ok=False))
  self.audit('--effective')
 def test_owner_display_name_is_contextual_and_third_party_first_name_allowed(self):
  self.rejected(self.audit('--effective',env={'GIT_AUTHOR_NAME':'ShortOwner'},ok=False))
  self.audit('--effective',env={'GIT_AUTHOR_NAME':'Mark','GIT_AUTHOR_EMAIL':'unrelated@example.invalid'})
 def test_protected_ref_names_are_checked_in_push_and_full_audit(self):
  self.rejected(self.audit('--pre-push',input=f'refs/heads/main {self.head()} refs/heads/Fixturefamily {"0"*40}\n'.encode(),ok=False))
  self.run_git('tag','Fixturefamily');self.rejected(self.audit('--all',ok=False))
 def test_transformed_protected_name_message_is_rejected_without_echo(self):
  p=self.base/'encoded-message';p.write_text('%46%69%78%74%75%72%65%66%61%6d%69%6c%79')
  r=self.audit('--message',str(p),ok=False);self.rejected(r);self.assertNotIn(b'Fixturefamily',r.stdout+r.stderr)
 def test_private_policy_missing_or_malformed_fails_closed(self):
  for extra in ({'DISCOVERY_IDENTITY_POLICY_FILE':str(self.base/'absent')},{'DISCOVERY_IDENTITY_POLICY':'{}','DISCOVERY_IDENTITY_POLICY_FILE':''}):
   self.rejected(self.audit('--effective',env=extra,ok=False))
 def test_private_policy_material_cannot_be_staged_or_configured_inside_source(self):
  source=Path(self.env['DISCOVERY_IDENTITY_POLICY_FILE']);target=self.repo/'policy.json';target.write_bytes(source.read_bytes());self.run_git('add','policy.json')
  self.rejected(self.audit('--staged',ok=False))
  self.rejected(self.audit('--effective',env={'DISCOVERY_IDENTITY_POLICY_FILE':str(target)},ok=False))
 def test_owner_mode_rejects_unapproved_committer_override(self):
  self.install();self.rejected(self.audit('--effective',env={'GIT_COMMITTER_EMAIL':'other-fixture@example.invalid'},ok=False))
 def test_staged_blob_and_filename_are_checked(self):
  for name,body in [('identity.txt',SYNTHETIC),('file-'+SYNTHETIC+'.txt','safe')]:
   p=self.repo/name;p.write_text(body);self.run_git('add',name);self.rejected(self.audit('--staged',ok=False));self.run_git('reset');p.unlink()
 def test_embedded_and_adjacent_email_literals_are_detected(self):
  for content in (SYNTHETIC.upper(), 'prefix@'+SYNTHETIC, 'file-'+SYNTHETIC+'.safetensors'):
   message=self.base/'wrapped-message';message.write_text(content)
   self.rejected(self.audit('--message',str(message),ok=False))
 def test_message_is_checked_without_echo(self):
  p=self.base/'message';p.write_text('Contact '+SYNTHETIC);self.rejected(self.audit('--message',str(p),ok=False))
 def test_actual_commit_metadata_author_and_committer_and_all_reachable(self):
  for role in ('author','committer'):
   self.run_git('checkout','-B','case-'+role,self.initial);tip=self.unsafe_commit(**{role:True})
   self.rejected(self.audit('--tip',tip,'--before',self.initial,ok=False));self.rejected(self.audit('--all',ok=False))
 def test_push_range_accepts_clean_tip_without_rescanning_old_ancestry(self):
  old=self.unsafe_commit(author=True);self.commit('new clean commit');new=self.head()
  self.audit('--tip',new,'--before',old)
  self.rejected(self.audit('--pre-push',input=f'refs/heads/topic {new} refs/heads/topic {"0"*40}\n'.encode(),ok=False))
 def test_reintroduced_history_is_checked(self):
  bad=self.unsafe_commit(author=True);self.run_git('checkout','-B','clean',self.initial);self.commit('clean base');base=self.head()
  self.run_git('merge','--no-ff','-m','merge older branch',bad)
  self.rejected(self.audit('--tip',self.head(),'--before',base,ok=False))
 def test_annotated_tagger_and_tag_message_checked(self):
  self.run_git('tag','-a','bad-tagger','-m','fixture',env={'GIT_COMMITTER_EMAIL':SYNTHETIC})
  value=self.run_git('rev-parse','bad-tagger').stdout.decode().strip();self.rejected(self.audit('--tip',value,ok=False))
  self.run_git('tag','-a','bad-message','-m',SYNTHETIC)
  value=self.run_git('rev-parse','bad-message').stdout.decode().strip();self.rejected(self.audit('--tip',value,ok=False))
 def test_unknown_old_oid_and_malformed_push_fail_closed(self):
  self.rejected(self.audit('--tip',self.head(),'--before','1'*40,ok=False))
  self.rejected(self.audit('--pre-push',input=b'malformed\n',ok=False))
 def test_delete_adds_no_objects(self):
  self.audit('--pre-push',input=f'refs/heads/x {"0"*40} refs/heads/x {self.head()}\n'.encode())
 def test_installer_normalizes_crlf_hooks(self):
  for path in (self.guard/'.githooks').iterdir():path.write_bytes(path.read_bytes().replace(b'\n',b'\r\n'))
  self.install();self.commit('Windows line ending fixture')
  installed=Path(self.run_git('config','core.hooksPath').stdout.decode().strip())
  self.assertNotIn(b'\r', (installed/'pre-commit').read_bytes())
 def test_actual_hooks_normal_commit_and_no_verify(self):
  self.install();self.commit('safe after install')
  for flags in ([],['--no-verify']):
   r=self.run_git('commit',*flags,'--allow-empty','-m','fixture',env={'GIT_AUTHOR_EMAIL':SYNTHETIC},ok=False);self.rejected(r)
 def test_installed_hooks_survive_checkout_of_older_branch(self):
  self.install();hooks=self.run_git('config','core.hooksPath').stdout
  self.run_git('checkout','-b','older',self.initial);self.assertFalse((self.repo/'.githooks').exists())
  self.assertEqual(self.run_git('config','core.hooksPath').stdout,hooks)
  self.rejected(self.commit('blocked',env={'GIT_COMMITTER_EMAIL':SYNTHETIC},ok=False))
 def test_existing_source_gate_is_still_called(self):
  self.install();self.rejected(self.commit('source rejected',env={'FIXTURE_REJECT_SOURCE':'1'},ok=False))
 def test_actual_pre_push_rejects_commits_created_without_hooks(self):
  remote=self.base/'remote.git';self.run_git('init','--bare',str(remote));self.run_git('remote','add','origin',str(remote))
  self.install();self.run_git('push','origin','main')
  self.unsafe_commit(committer=True);self.rejected(self.run_git('push','origin','main',ok=False))
  got=self.command(['git','--git-dir',str(remote),'rev-parse','refs/heads/main']).stdout.decode().strip();self.assertEqual(got,self.initial)
 def test_actual_pre_push_rejects_bad_annotated_tag(self):
  remote=self.base/'remote.git';self.run_git('init','--bare',str(remote));self.run_git('remote','add','origin',str(remote));self.install();self.run_git('push','origin','main')
  self.run_git('tag','-a','blocked','-m','fixture',env={'GIT_COMMITTER_EMAIL':SYNTHETIC})
  self.rejected(self.run_git('push','origin','refs/tags/blocked',ok=False))
 def test_ci_reads_metadata_without_executing_head_policy(self):
  remote=self.base/'remote.git';self.run_git('init','--bare',str(remote));self.run_git('remote','add','origin',str(remote));bad=self.unsafe_commit(author=True)
  self.run_git('push','origin','HEAD:refs/pull/1/head')
  payload={'number':1,'pull_request':{'head':{'sha':bad},'base':{'sha':self.initial}}};event=self.base/'event.json';event.write_text(json.dumps(payload))
  self.rejected(self.audit('--github-event',str(event),'--event-name','pull_request_target',ok=False))
 def test_ci_push_and_moving_head_fail_closed(self):
  remote=self.base/'remote.git';self.run_git('init','--bare',str(remote));self.run_git('remote','add','origin',str(remote));self.run_git('push','origin','main')
  event=self.base/'push.json';event.write_text(json.dumps({'ref':'refs/heads/main','after':self.initial,'before':'0'*40}))
  self.audit('--github-event',str(event),'--event-name','push')
  self.commit('later');event.write_text(json.dumps({'ref':'refs/heads/main','after':self.head(),'before':self.initial}))
  self.rejected(self.audit('--github-event',str(event),'--event-name','push',ok=False))
 def test_release_gate_stops_on_identity_before_runtime_tests(self):
  for name in ('release-test-gate.py','git_identity.py','check-git-identity.py'):
   source=ROOT/'scripts'/name if name=='release-test-gate.py' else self.guard/'scripts'/name
   shutil.copy2(source,self.repo/'scripts'/name)
  self.run_git('add','.');self.run_git('-c','core.hooksPath='+str(self.base/'empty-hooks'),'commit','-m','gate fixture')
  self.unsafe_commit(author=True)
  output=self.base/'gate-refusal'
  result=self.command([sys.executable,str(self.repo/'scripts/release-test-gate.py'),'--output',str(output),'--timeout','10'],ok=False)
  self.rejected(result)
  summary=json.loads((output/'summary.json').read_text())
  self.assertFalse(summary['fullGatePassed']);self.assertEqual(summary['failed'],['git-identity'])
  self.assertFalse((output/'python').exists())
 def test_normal_rebase_rejects_unsafe_authorship_before_replay(self):
  bad=self.unsafe_commit(author=True);self.run_git('branch','topic',bad)
  self.run_git('checkout','-B','newbase',self.initial);self.commit('new upstream')
  self.run_git('checkout','topic');self.install()
  self.rejected(self.run_git('rebase','newbase',ok=False))
 def test_workflow_uses_trusted_policy_and_minimum_permissions(self):
  text=(ROOT/'.github/workflows/git-identity.yml').read_text()
  self.assertIn('pull_request_target:',text);self.assertIn('github.event.repository.default_branch',text)
  self.assertIn('persist-credentials: false',text);self.assertIn('contents: read',text)
  self.assertIn('fetch-depth: 1',text);self.assertIn('fetch-tags: false',text);self.assertNotIn('fetch-depth: 0',text)
  self.assertNotIn('pull_request.head.repo',text)
  self.assertEqual(re.findall(r'secrets\.[A-Za-z0-9_]+',text),['secrets.DISCOVERY_IDENTITY_POLICY'])
  self.assertIn('actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683',text)
if __name__=='__main__':unittest.main(verbosity=2)
