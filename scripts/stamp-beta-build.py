"""Bind a local build to unchanged reviewed inputs; no publication or app launch."""
import argparse,hashlib,json,ssl,subprocess,sys
from importlib.metadata import version
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def inputs():
    paths=[ROOT/x for x in ('server.py','requirements.txt','requirements-dev.txt','LICENSE','THIRD_PARTY_NOTICES.md')]
    for folder,pattern in [('discovery','*.py'),('scripts','*.py'),('scripts','*.ps1'),('static','**/*'),('supabase/functions','**/*.ts'),('supabase/beta-migrations','*.sql'),('docs','3.0-beta*.md')]:
        paths.extend(p for p in (ROOT/folder).glob(pattern) if p.is_file())
    records={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))}
    return hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['start','finish']);p.add_argument('--record',type=Path,required=True);p.add_argument('--package',type=Path);a=p.parse_args()
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip()
    dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip())
    state={'sourceCommit':commit,'sourceDirty':dirty,'inputFingerprint':inputs(),'python':sys.version.split()[0],'openssl':ssl.OPENSSL_VERSION,
      'dependencies':{k:version(k) for k in ('pyinstaller','pyinstaller-hooks-contrib','pillow','pystray')}}
    if a.action=='start':
        a.record.parent.mkdir(parents=True,exist_ok=True);a.record.write_text(json.dumps(state,indent=2))
    else:
        if not a.package:p.error('Package required')
        if json.loads(a.record.read_text())!=state:p.error('Build inputs/toolchain changed during build; discard candidate')
        exe=a.package/'CivitaiDiscoveryBeta.exe'
        if not exe.is_file():p.error('Expected beta executable')
        state['executableSha256']=hashlib.sha256(exe.read_bytes()).hexdigest()
        (a.package/'build-provenance.json').write_text(json.dumps(state,indent=2))
    print(json.dumps({'recorded':True,'sourceCommit':commit,'dirty':dirty,'action':a.action}))

if __name__=='__main__':main()
