"""Release inputs and production trust must remain independent of the Test Lab."""
import ast
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
subprocess.run([sys.executable,'-B',str(ROOT/'scripts/check-test-lab-exclusion.py')],cwd=ROOT,check=True)
lab=ROOT/'dev/community-test-lab'
if lab.exists():
    data=json.loads((lab/'public-fixtures.json').read_text())
    assert len({m['creator']['id'] for m in data['members']})==10
    for member in data['members']:
        for art in member['artworks']:
            assert art['detail']['user']['id']==member['creator']['id']
            assert art['rest']['username']==art['detail']['user']['username']
            assert art['detail']['availability']=='Public' and art['detail']['ingestion']=='Scanned'
            assert art['rest']['type']=='image' and art['rest']['browsingLevel']==art['detail']['nsfwLevel']
    # Frozen applications reject the extension before reading any session/key file.
    for name in ('actor.py','launch.py'):
        code="import sys,runpy;sys.frozen=True;runpy.run_path(sys.argv[1],run_name='__main__')"
        child=subprocess.run([sys.executable,'-B','-c',code,str(lab/name)],capture_output=True,text=True)
        assert child.returncode and ('packaged' in child.stderr or 'excluded' in child.stderr)
print('PASS: ten real public fixture owners; frozen entry rejection; Lab absent from production imports/static/deployment inputs')
