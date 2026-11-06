"""A clean tip must not conceal credentials or personal paths in older commits."""
import test_safety
import importlib.util
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('history_audit',test_safety.ROOT/'scripts/audit-history.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
with tempfile.TemporaryDirectory(prefix='cad3-history-check-') as temporary:
    root=Path(temporary)
    def git(*args):
        return subprocess.check_output(['git','-C',str(root),'-c','user.name=Synthetic',
            '-c','user.email=synthetic@example.invalid',*args],stderr=subprocess.STDOUT)
    git('init','-q')
    private=root/'data-test-private';private.mkdir()
    secret='ghp_'+'x'*30
    (private/'secret.txt').write_text(secret)
    git('add','.')
    git('commit','-qm','Synthetic fixture '+('C:'+'\\Users\\ExampleUser\\private'))
    (private/'secret.txt').unlink();(root/'safe.py').write_text('print("synthetic")')
    git('add','-A');git('commit','-qm','Remove synthetic secret at tip')
    with patch.object(module,'ROOT',root): result=module.audit('HEAD')
    assert result['commits']==result['commitObjectsScanned']==2
    assert {x['kind'] for x in result['findings']}=={'credential pattern','personal absolute path'}
    assert len(result['findings'])==2
    assert result['historicalArtifactsRequiringReview'][0]['path']=='data-test-private/secret.txt'
    assert secret not in str(result)
print('PASS: older blobs and commit messages audited despite a clean source tip; values withheld')
