"""Fail release preflight if removable identity code enters production inputs."""
import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
failures=[]
for path in [ROOT/'server.py',*(ROOT/'discovery').glob('*.py')]:
    tree=ast.parse(path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        names=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
        if any(name=='lab' or 'community_test_lab' in name or name.startswith('dev.') for name in names):failures.append(str(path.relative_to(ROOT)))
for path in (ROOT/'static').rglob('*'):
    if path.is_file() and path.suffix in ('.js','.html','.css') and 'community-test-lab' in path.read_text(encoding='utf-8'):
        failures.append(str(path.relative_to(ROOT)))
build=(ROOT/'scripts/build_exe.ps1').read_text(encoding='utf-8')
data_lines=[line for line in build.splitlines() if '--add-data' in line]
if len(data_lines)!=1 or "'static'" not in data_lines[0] or "'server.py'" not in build:
    failures.append('Unreviewed release data/entry configuration')
for path in (ROOT/'supabase/functions').rglob('*.ts'):
    text=path.read_text(encoding='utf-8')
    if 'community-test-lab' in text or '/tests/' in text or 'local_service' in text:failures.append(str(path.relative_to(ROOT)))
if failures:raise SystemExit('Test Lab production exclusion failed: '+', '.join(failures))
print('PASS: production Python imports, bundled static assets, release data inputs and deployed Edge sources exclude the removable Test Lab')
