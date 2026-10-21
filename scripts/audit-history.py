"""Read every blob reachable from a proposed publication ref; never print secrets."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('source_gate', ROOT/'scripts/check-source.py')
gate = importlib.util.module_from_spec(spec); spec.loader.exec_module(gate)


def audit(ref):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(ROOT), *args])
    tip = git('rev-parse', '--verify', ref+'^{commit}').decode().strip()
    entries = git('rev-list', '--objects', tip).decode().splitlines()
    paths = {line.split(' ', 1)[0]: line.split(' ', 1)[1] if ' ' in line else '<commit>' for line in entries}
    # cat-file inspects objects without checking out old source or running old hooks.
    process = subprocess.Popen(['git', '-C', str(ROOT), 'cat-file', '--batch'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    payload, _ = process.communicate(('\n'.join(paths)+'\n').encode())
    if process.returncode: raise RuntimeError('Object inspection failed')
    offset = count = size = commits = 0; findings = []; artifacts = []
    for oid, name in paths.items():
        end = payload.index(b'\n', offset)
        header = payload[offset:end].decode().split(); length = int(header[2])
        content = payload[end+1:end+1+length]; offset = end+length+2
        if header[1] == 'commit': commits += 1
        elif header[1] == 'blob':
            count += 1; size += length
            if gate.forbidden(name): artifacts.append({'object':oid, 'path':name, 'bytes':length})
        else: continue
        for number, line in enumerate(content.decode('utf-8', 'replace').splitlines(), 1):
            if any(re.search(pattern, line) for pattern in gate.PATTERNS):
                findings.append({'object':oid, 'path':name, 'line':number, 'kind':'credential pattern'})
            if re.search(gate.PERSONAL_PATH_PATTERN, line):
                findings.append({'object':oid, 'path':name, 'line':number, 'kind':'personal absolute path'})
    return {'ref':ref, 'tip':tip, 'commits':int(git('rev-list','--count',tip)),
            'blobs':count, 'bytes':size, 'commitObjectsScanned':commits, 'findings':findings,
            'historicalArtifactsRequiringReview':artifacts,
            'limitations':'Pattern scanning cannot certify absence of all personal information; review historical media separately.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='HEAD'); parser.add_argument('--output', type=Path)
    args = parser.parse_args(); result = audit(args.ref)
    output = args.output or Path(tempfile.mkdtemp(prefix='cad3-history-audit-'))/'audit.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('tip','commits','blobs','bytes')}))
    print(f"Findings: {len(result['findings'])}; historical artifacts: {len(result['historicalArtifactsRequiringReview'])}; report: {output}")
    raise SystemExit(bool(result['findings']))
