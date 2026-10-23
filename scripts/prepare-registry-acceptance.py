"""Prepare pinned owner-only acceptance and exact baseline rollback; never call Supabase."""
import argparse,hashlib,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASELINE='e5c0872d5adca05f512d9ec1e2a50df8a650aed7'
REF='aibelmhucizupzvvhdvf'
def digest(raw):return hashlib.sha256(raw).hexdigest()
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();out=args.output.resolve()
 if out.is_relative_to(ROOT) and not out.is_relative_to(ROOT/'reports'):parser.error('Use ignored reports or an external output directory')
 if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip():parser.error('Commit validated source before pinning artifacts')
 out.mkdir(parents=True,exist_ok=True)
 source=ROOT/'supabase/functions/community-v1';manifest={'preparedOnly':True,'deploymentApproved':False,'projectRef':REF,'region':'us-west-2','expectedFunctionVersion':19,'expectedMigrations':8,'sourceCommit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'baselineCommit':BASELINE,'artifacts':{}}
 for kind in ('candidate','rollback'):
  folder=out/kind/'supabase/functions/community-v1';folder.mkdir(parents=True,exist_ok=True);pins=[]
  for target,origin in [('index.ts','owner-registry-entry.ts' if kind=='candidate' else 'index.ts'),('handler.ts','handler.ts')]:
   raw=(source/origin).read_text().encode() if kind=='candidate' else subprocess.check_output(['git','show',BASELINE+':supabase/functions/community-v1/'+origin],cwd=ROOT)
   assert b'Deno.env.set(' not in raw and b'Deno.env.delete(' not in raw
   (folder/target).write_bytes(raw);pins.append({'path':'supabase/functions/community-v1/'+target,'sha256':digest(raw)})
  config=b'[functions.community-v1]\nverify_jwt = false\n';(out/kind/'supabase/config.toml').write_bytes(config);pins.append({'path':'supabase/config.toml','sha256':digest(config)})
  manifest['artifacts'][kind]=pins
 manifest['migrations']=[{'path':str(path.relative_to(ROOT)).replace('\\','/'),'sha256Lf':digest(path.read_text().encode())} for path in sorted((ROOT/'supabase/migrations').glob('*.sql'))+sorted((ROOT/'supabase/beta-migrations').glob('*.sql'))]
 assert len(manifest['migrations'])==8
 manifest['configuration']={'unchanged':{'COMMUNITY_BETA_STATE':'disabled','COMMUNITY_ALLOWED_RATINGS':'1,2'},'acceptance':{'DISCOVERY_REGISTRY_STATE':'owner_test','COMMUNITY_TEST_OWNER_ID':'11107642','COMMUNITY_OWNER_TEST_EXPIRES_AT':{'value':'generated only after approval','utcFormat':'YYYY-MM-DDTHH:mm:ssZ','durationMinutes':15,'hardMaximumMinutes':30}},'closure':{'DISCOVERY_REGISTRY_STATE':'disabled then unset to restore default','COMMUNITY_OWNER_TEST_EXPIRES_AT':'2000-01-01T00:00:00Z'},'credentialHandling':'Existing Credential Manager launcher; no credential values in artifact or env files'}
 manifest['approvalScope']=['Deploy community-v1 only','Set only the three acceptance configuration names','Fresh owner login in a new isolated client profile','Create and retain exactly one legitimate owner registry row','Backdate only that row last_seen for controlled eligibility; restore legitimate timestamp after denied probes','No Community recommendation/audit/replay/quota changes','Restore pinned normal source and disabled collection; wait past original hard expiry before claiming closure']
 manifest['closureCaveat']='Environment updates are not proof that existing workers lost their injected configuration. Restore disabled and normal source, wait until the original deadline plus 60 seconds, and verify no registry writes. Do not renew the deadline.'
 (out/'manifest.json').write_text(json.dumps(manifest,indent=2));print(json.dumps({'preparedOnly':True,'sourceCommit':manifest['sourceCommit'],'manifest':str(out/'manifest.json')}))
if __name__=='__main__':main()
