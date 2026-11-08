"""Install durable version-controlled identity hooks without altering working history."""
import argparse,hashlib,json,os,shutil,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from git_identity import OWNER_LOGIN,OWNER_EMAIL,require_private_policy
ROOT=Path(__file__).resolve().parents[1]
HOOKS=('pre-commit','prepare-commit-msg','commit-msg','pre-push','pre-merge-commit','pre-rebase','applypatch-msg','pre-applypatch','post-commit')
FILES=tuple('.githooks/'+n for n in HOOKS)+('scripts/check-git-identity.py','scripts/git_identity.py','discovery/__init__.py','discovery/privacy_patterns.py')
def install(bundle_root=None,owner=False):
    require_private_policy()
    common=Path(subprocess.check_output(['git','rev-parse','--git-common-dir'],encoding='utf-8').strip()).resolve()
    repo_id=hashlib.sha256(str(common).casefold().encode()).hexdigest()[:20]
    payload={name:(ROOT/name).read_bytes() for name in FILES}
    for name in FILES:
        if name.startswith('.githooks/'):payload[name]=payload[name].replace(b'\r\n',b'\n')
    content=hashlib.sha256()
    for name in FILES:content.update(name.encode());content.update(payload[name])
    version=content.hexdigest()
    base=(Path(bundle_root) if bundle_root else Path(os.environ.get('LOCALAPPDATA') or Path.home()/'.local/share')/'DiscoveryGitIdentityGuard').resolve()
    if base==ROOT or ROOT in base.parents or base==common or common in base.parents:raise RuntimeError('Hook bundle must be outside working source and Git metadata')
    target=base/repo_id/version
    for name in FILES:
        source=ROOT/name;dest=target/name
        if dest.exists():
            if dest.read_bytes()!=payload[name]:raise RuntimeError('Installed hook bundle integrity mismatch')
        else:
            dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(payload[name])
        if name.startswith('.githooks/'):dest.chmod(0o755)
    manifest={'format':1,'version':version,'files':{n:hashlib.sha256((target/n).read_bytes()).hexdigest() for n in FILES}}
    (target/'manifest.json').write_text(json.dumps(manifest,indent=2))
    if owner:
        for key,value in (('user.name',OWNER_LOGIN),('user.email',OWNER_EMAIL),('user.useConfigOnly','true'),('identityGuard.ownerMode','true')):subprocess.run(['git','config','--local',key,value],check=True)
    subprocess.run(['git','config','--local','core.hooksPath',(target/'.githooks').as_posix()],check=True)
    subprocess.run(['git','config','--local','identityGuard.bundleVersion',version],check=True)
    private=os.environ.get('DISCOVERY_IDENTITY_POLICY_FILE')
    if private:subprocess.run(['git','config','--local','identityGuard.policyPath',str(Path(private).resolve())],check=True)
    print('Durable identity hooks installed; branch checkout cannot downgrade this bundle.')
    return target
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle-root',type=Path);p.add_argument('--owner',action='store_true');a=p.parse_args();install(a.bundle_root,a.owner)
