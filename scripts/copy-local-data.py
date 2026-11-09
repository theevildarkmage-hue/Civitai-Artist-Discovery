"""Manual beta recovery into a NEW empty profile; excludes known credential files.

Run only after closing both installations and backing up the old profile.
This helper is not an application updater or a database merge tool.
Copied settings/history remain private. Arbitrary custom JSON is not a secret scan.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3

ALLOWLIST=('settings.json','following.json','history/history.sqlite3',
           'discovery/taste.sqlite3','discovery/timemachine.sqlite3')

def copy_local_data(source, destination):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if not source.is_dir() or source==destination or source in destination.parents or destination in source.parents:
        raise ValueError('Choose separate existing source and new destination profiles')
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError('Destination must be absent or empty; database merging is unsupported')
    for relative in ALLOWLIST:
        path=source/relative
        if path.exists() and (path.is_symlink() or source not in path.resolve().parents):
            raise ValueError('Linked input files are unsupported')
    destination.mkdir(parents=True,exist_ok=True)
    copied=[]
    for relative in ALLOWLIST:
        path=source/relative
        if not path.is_file():continue
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        if path.suffix=='.sqlite3':
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target)) as dest:
                if src.execute('pragma quick_check').fetchone()[0]!='ok':
                    raise ValueError('Source database failed integrity check; preserve evidence')
                src.backup(dest)
                if dest.execute('pragma quick_check').fetchone()[0]!='ok':
                    raise ValueError('Copied database failed integrity check; preserve evidence')
        else:
            json.loads(path.read_text(encoding='utf-8'))
            shutil.copy2(path,target)
        copied.append(relative)
    return {'copied':copied,'credentialsCopied':False,'sourcePreserved':True,'freshSignInRequired':True}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True,type=Path)
    parser.add_argument('--destination',required=True,type=Path)
    args=parser.parse_args()
    try:result=copy_local_data(args.source,args.destination)
    except Exception:
        raise SystemExit('Copy stopped; source preserved. Review paths/database health; do not overwrite or merge the partial destination.') from None
    print(json.dumps(result))
