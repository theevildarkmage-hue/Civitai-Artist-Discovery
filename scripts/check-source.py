"""Reject runtime artifacts and recognizable credentials from tracked/staged source."""
import argparse
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
APPROVED_BINARY={'tests/fixtures/recovery.mp4'}
PERSONAL_PATH_PATTERN=r'(?i)\b[A-Z]:[\\/]+Users[\\/]+[A-Za-z0-9_.-]+[\\/]'
PATTERNS=[r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
          r'\b(?:ghp_|github_pat_|sb_secret_|sbp_|sk-proj-)[A-Za-z0-9_-]{20,}',
          r'\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}']


def forbidden(name):
    path=Path(name);parts=path.parts
    directories={'reports','logs','cache','caches','data','archive','__pycache__','node_modules'}
    if any(p.lower() in directories or p.lower().startswith(('.venv','data-','data_')) for p in parts[:-1]):return True
    if name in APPROVED_BINARY:return False
    if path.name=='.env.example':return name!='supabase/.env.example'
    return (path.name.startswith(('.env','.data-directory')) or
            re.search(r'\.(?:sqlite3(?:-wal|-shm)?|db|dpapi|log|jsonl|pem|key|zip|png|jpe?g|webp|gif|avif|mp4)$',name,re.I) is not None)


def audit(staged=False):
    command=['git','-C',str(ROOT),'diff','--cached','--name-only','--diff-filter=ACMR','-z'] if staged else ['git','-C',str(ROOT),'ls-files','-z']
    names=list(filter(None,subprocess.check_output(command).decode().split('\0')))
    failures=[]
    for name in names:
        if forbidden(name):failures.append(f'{name}: runtime/sensitive artifact path');continue
        if name in APPROVED_BINARY:continue
        if staged:value=subprocess.check_output(['git','-C',str(ROOT),'show',':'+name])
        elif (ROOT/name).is_file():value=(ROOT/name).read_bytes()
        else:continue
        for number,line in enumerate(value.decode('utf-8','replace').splitlines(),1):
            if any(re.search(pattern,line) for pattern in [*PATTERNS, PERSONAL_PATH_PATTERN]):
                failures.append(f'{name}:{number}: sensitive content pattern (value withheld)')
    return names,failures


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--staged',action='store_true');args=parser.parse_args()
    names,failures=audit(args.staged)
    for failure in failures:print(failure)
    print(f'Source gate: {len(names)} files reviewed; {len(failures)} findings. No credential values printed.')
    raise SystemExit(bool(failures))
