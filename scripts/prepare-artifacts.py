"""Prepare a channel-identified portable ZIP and manifest; never publishes."""
import argparse,datetime,hashlib,json,subprocess,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from discovery.build_config import validate

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--package',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--preview',action='store_true');a=p.parse_args()
    package=a.package.resolve();config=validate(json.loads((package/'build-configuration.json').read_text(encoding='utf-8')))
    application='CivitaiDiscoveryBeta' if config['channel']=='beta' else 'CivitaiArtistDiscovery'
    if package.name!=application:p.error('Package folder must match the channel identity')
    executable=package/(application+'.exe')
    if not executable.is_file():p.error('Channel executable missing')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip())
    preview=a.preview or config['preview']
    if dirty and not preview:p.error('Final candidate requires clean source')
    files=list(package.rglob('*'))
    if any(f.is_symlink() for f in files):p.error('Package symlinks are forbidden')
    if any(f.is_file() and (f.name.lower().startswith('.env') or
            f.name.lower().endswith(('.db','.sqlite','.sqlite3','.db-wal','.db-shm','.sqlite-wal','.sqlite-shm','.sqlite3-wal','.sqlite3-shm','.dpapi','.log')) or
            any(part.lower() in ('data','reports','tests','dev') for part in f.relative_to(package).parts)) for f in files):
        p.error('Runtime/test/secret artifacts in package')
    provenance=json.loads((package/'build-provenance.json').read_text())
    sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
    if provenance.get('sourceCommit')!=commit or provenance.get('executableSha256')!=sha(executable) or provenance.get('buildConfiguration')!=config:p.error('Build provenance/configuration mismatch')
    if provenance.get('sourceDirty') and not preview:p.error('Dirty build cannot be relabeled as final')
    embedded=package/'_internal/discovery/build-configuration.json'
    if not embedded.is_file() or embedded.read_bytes()!=(package/'build-configuration.json').read_bytes():p.error('Embedded configuration mismatch')
    identity={'applicationId':application,'version':config['version'],'tag':'v'+config['version'],'channel':config['channel'],
              'sourceCommit':commit,'sourceDirty':dirty,'preview':preview}
    (package/'release-identity.json').write_text(json.dumps(identity,indent=2))
    a.output.mkdir(parents=True,exist_ok=True);archive=a.output/(application+'-'+config['version']+'.zip')
    if archive.exists():p.error('Refusing to replace an existing artifact; use a fresh output directory')
    epoch=int(subprocess.check_output(['git','show','-s','--format=%ct','HEAD'],cwd=ROOT))
    timestamp=datetime.datetime.fromtimestamp(epoch,datetime.timezone.utc).timetuple()[:6]
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for f in sorted(package.rglob('*')):
            if not f.is_file():continue
            info=zipfile.ZipInfo(application+'/'+f.relative_to(package).as_posix(),date_time=timestamp);info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,f.read_bytes())
    sums={f.name:sha(f) for f in (archive,executable)}
    (a.output/'SHA256SUMS.txt').write_text(''.join(v+'  '+k+'\n' for k,v in sums.items()))
    (a.output/'release-manifest.json').write_text(json.dumps({**identity,'buildConfiguration':config,'sha256':sums,'published':False},indent=2))
    print(json.dumps({'prepared':True,**identity,'sha256':sums}))
if __name__=='__main__':main()
