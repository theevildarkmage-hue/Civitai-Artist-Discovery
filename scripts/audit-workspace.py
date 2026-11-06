"""Read-only cleanup inventory. Does not delete, move, or read credential contents."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def inventory():
    pin=ROOT/'.data-directory-3.0.json'
    selected=(ROOT/json.loads(pin.read_text())['directory']).resolve() if pin.exists() else ROOT/'data-dev-3.0'
    directories=[]
    for path in sorted(ROOT.iterdir()):
        if not path.is_dir() or path.name in ('.git','discovery','static','tests','docs','scripts','supabase','.githooks'):continue
        category=('persistent development data — preserve' if path.resolve() in (selected,ROOT/'data-dev-3.0') else
                  'disposable test sandbox — verify contents/processes before removal' if path.name.startswith(('data-test-','cad3-')) else
                  'private Python environment — preserve' if path.name.startswith('.venv') else
                  'generated reports/logs — review retention' if path.name in ('reports','logs','__pycache__') else
                  'unclassified local directory — preserve pending review')
        size=count=0
        for folder,children,files in os.walk(path,followlinks=False):
            children[:]=[name for name in children if not (Path(folder)/name).is_symlink()]
            for name in files:
                try:size+=(Path(folder)/name).stat().st_size;count+=1
                except OSError:pass
        directories.append({'directory':str(path),'category':category,'files':count,'bytes':size})
    def git(*args):return subprocess.check_output(['git','-C',str(ROOT),*args]).decode().splitlines()
    other_worktrees=[]
    for line in git('worktree','list','--porcelain'):
        if line.startswith('worktree '):
            worktree=Path(line[9:]).resolve()
            if worktree==ROOT:continue
            artifacts=[str(p) for p in worktree.iterdir() if p.is_dir() and
                (p.name.startswith(('data-test-','.venv')) or p.name in ('reports','logs','__pycache__'))]
            other_worktrees.append({'directory':str(worktree),'artifactDirectories':sorted(artifacts),
                'action':'Preserve; test/report directories and worktree removal require separate review.'})
    return {'generatedAt':datetime.now(timezone.utc).isoformat(),'readOnly':True,
        'directories':directories,'otherWorktrees':other_worktrees,'tracked':git('ls-files'),'untrackedSource':git('ls-files','--others','--exclude-standard'),
        'ignored':git('ls-files','--others','--ignored','--exclude-standard'),
        'deletionsPerformed':0,'note':'Names and sizes only; credentials and database contents were not read.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path);args=parser.parse_args()
    output=args.output or Path(tempfile.mkdtemp(prefix='cad3-cleanup-inventory-'))/'inventory.json'
    output.parent.mkdir(parents=True,exist_ok=True);value=inventory();output.write_text(json.dumps(value,indent=2),encoding='utf-8')
    print(f'Cleanup inventory: {output.resolve()}')
    for row in value['directories']:print(f"{Path(row['directory']).name}: {row['category']}; {row['files']} files; {row['bytes']/1048576:.1f} MiB")
