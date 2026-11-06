"""Explicit approved-tag Linux source update; no downloaded scripts are executed."""
import argparse,json,re,subprocess
from pathlib import Path
REMOTE='https://github.com/theevildarkmage-hue/Civitai-Artist-Discovery'

def update(root,tag,commit,*,fetch=True):
    root=Path(root).resolve()
    if not re.fullmatch(r'v3\.\d+\.\d+-beta\.[1-9]\d*',tag) or not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('Approved beta tag and immutable 40-character commit required')
    def git(*args):return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.PIPE,text=True).strip()
    if git('status','--porcelain'):raise ValueError('Commit or move conflicting local source changes before updating')
    if fetch:
        if git('remote','get-url','origin').removesuffix('.git')!=REMOTE:raise ValueError('Unexpected source remote')
        git('fetch','--no-tags','origin','refs/tags/'+tag+':refs/tags/'+tag)
    if git('rev-parse',tag+'^{commit}')!=commit:raise ValueError('Tag/commit mismatch')
    paths=git('ls-tree','-r','--name-only',commit).splitlines()
    if any(p.startswith(('data/','data-','data_','.env')) or p.endswith(('.sqlite3','.dpapi')) for p in paths):raise ValueError('Source release contains forbidden runtime data')
    previous=git('rev-parse','HEAD')
    # Checkout refuses conflicting untracked files; ignored data is additionally
    # excluded from all approved source trees above. No merge/reset/delete.
    git('checkout','--no-overwrite-ignore','--detach',commit)
    return {'previousCommit':previous,'installedCommit':commit,'tag':tag,'dataPreserved':True,'detached':True}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--checkout',type=Path,required=True);p.add_argument('--tag',required=True);p.add_argument('--commit',required=True)
    a=p.parse_args();print(json.dumps(update(a.checkout,a.tag,a.commit)))

if __name__=='__main__':main()
