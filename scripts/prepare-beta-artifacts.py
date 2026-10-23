"""Prepare local portable beta ZIP, identity and checksums; never publishes."""
import argparse,hashlib,json,re,subprocess,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--package',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--preview',action='store_true');a=p.parse_args()
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip()
    dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip())
    if dirty and not a.preview:p.error('Final candidate must come from a clean reviewed commit')
    package=a.package.resolve()
    if package.name!='CivitaiDiscoveryBeta' or not (package/'CivitaiDiscoveryBeta.exe').is_file():p.error('Expected beta-specific package')
    files=[f for f in sorted(package.rglob('*')) if f.is_file()]
    if any(f.suffix in ('.db','.sqlite','.sqlite3','.dpapi','.log') or any(x in f.relative_to(package).parts for x in ('data','reports','tests','dev')) for f in files):p.error('Runtime/test artifacts in package')
    provenance=json.loads((package/'build-provenance.json').read_text())
    if provenance.get('sourceCommit')!=commit or provenance.get('executableSha256')!=hashlib.sha256((package/'CivitaiDiscoveryBeta.exe').read_bytes()).hexdigest():p.error('Build provenance does not match this source/executable')
    if provenance.get('sourceDirty') and not a.preview:p.error('Dirty preview builds cannot be relabeled as final candidates')
    version=re.search(r'^APP_VERSION = "(3\.\d+\.\d+-beta\.[1-9]\d*)"$',(ROOT/'server.py').read_text(encoding='utf-8'),re.M)
    if not version:p.error('A strict beta source version is required')
    version=version.group(1)
    identity={'applicationId':'CivitaiDiscoveryBeta','version':version,'tag':'v'+version,'channel':'beta','sourceCommit':commit,'sourceDirty':dirty,'preview':a.preview}
    (package/'release-identity.json').write_text(json.dumps(identity,indent=2))
    a.output.mkdir(parents=True,exist_ok=True);archive=a.output/('CivitaiDiscoveryBeta-'+version+'.zip')
    epoch=int(subprocess.check_output(['git','show','-s','--format=%ct','HEAD'],cwd=ROOT))
    import datetime
    date=datetime.datetime.fromtimestamp(epoch,datetime.timezone.utc).timetuple()[:6]
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for f in sorted(package.rglob('*')):
            if not f.is_file():continue
            info=zipfile.ZipInfo('CivitaiDiscoveryBeta/'+f.relative_to(package).as_posix(),date_time=date);info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,f.read_bytes())
    sums={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in (archive,package/'CivitaiDiscoveryBeta.exe')}
    (a.output/'SHA256SUMS.txt').write_text(''.join(v+'  '+k+'\n' for k,v in sums.items()))
    (a.output/'release-manifest.json').write_text(json.dumps({**identity,'sha256':sums,'published':False},indent=2))
    print(json.dumps({'prepared':True,'preview':a.preview,'sourceCommit':commit,'sha256':sums}))

if __name__=='__main__':main()
