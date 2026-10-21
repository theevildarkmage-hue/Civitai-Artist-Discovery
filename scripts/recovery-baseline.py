"""Non-integrating recovery evidence runner; existing suites own disposable data."""
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports' / 'recovery-2026-10-21'
OUT.mkdir(parents=True, exist_ok=True)


def snapshot(label):
    result = {}
    for directory in ('data-test-community', 'data-dev-3.0'):
        for path in sorted((ROOT / directory).rglob('*.sqlite3')):
            db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=20)
            db.execute('PRAGMA query_only=ON')
            entry = {'quick_check': [r[0] for r in db.execute('PRAGMA quick_check')], 'tables': {}}
            for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
                quoted = '"' + table.replace('"', '""') + '"'
                count = db.execute(f'SELECT count(*) FROM {quoted}').fetchone()[0]
                item = {'count': count}
                if any(word in table for word in ('recommend', 'audit', 'reacted', 'followed', 'images', 'import')):
                    digest = hashlib.sha256()
                    # Sort encoded rows rather than assume any particular primary key.
                    rows = sorted(json.dumps(row, sort_keys=True, default=str) for row in db.execute(f'SELECT * FROM {quoted}'))
                    for row in rows:
                        digest.update(row.encode()); digest.update(b'\n')
                    item['sha256'] = digest.hexdigest()
                entry['tables'][table] = item
            db.close()
            result[str(path.relative_to(ROOT))] = entry
    (OUT / f'integrity-{label}.json').write_text(json.dumps(result, indent=2))
    return result


def run():
    before = snapshot('before')
    prior = json.loads((ROOT/'reports/submission-regression.json').read_text())
    suites = [row['test'] for row in prior]
    suites += ['tests/' + name + '.py' for name in (
        'community_backend', 'community_safety', 'persistence_cache', 'artwork_restoration',
        'submission_contract', 'community_browser', 'community_ten_members',
        'persistence_browser', 'community_stress', 'community_postgres', 'community_production')]
    commands = [[sys.executable, script] for script in dict.fromkeys(suites)]
    commands.append(['node', '--experimental-transform-types', 'tests/community_edge.mjs'])
    results = []
    env = os.environ.copy()
    # Never inherit an opt-in that could direct the recovery baseline to a real service.
    for key in ('CAD_ALLOW_ISOLATED_DB_TESTS', 'CAD_TEST_DATABASE_URL',
                'CAD_ALLOW_PRODUCTION_TESTS', 'CAD_ALLOW_ISOLATED_EDGE_TESTS',
                'CAD_TEST_COMMUNITY_URL', 'CAD_TEST_CIVITAI_TOKEN', 'CAD_COMMUNITY_TESTING', 'CAD_TEST_INSTANCE',
                'CIVITAI_HISTORY_DATA_DIR'):
        env.pop(key, None)
    env['PYTHONIOENCODING'] = 'utf-8'
    for command in commands:
        start = time.monotonic()
        try:
            child = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                   text=True, encoding='utf-8', errors='replace', timeout=300)
            output = child.stdout + child.stderr
            status = 'failed' if child.returncode else ('skipped' if 'SKIP:' in output else 'passed')
            row = {'command': command, 'status': status, 'returncode': child.returncode,
                   'seconds': round(time.monotonic()-start, 2), 'output': output}
        except subprocess.TimeoutExpired:
            row = {'command': command, 'status': 'timeout', 'seconds': 300}
        results.append(row)
        (OUT/'baseline.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
        print(f'{len(results)}/{len(commands)} {row["status"]}: {command[-1]}', flush=True)
    after = snapshot('after')
    (OUT/'integrity-comparison.json').write_text(json.dumps({
        'all_database_snapshots_equal': before == after,
        'changed_databases': [key for key in before if before[key] != after.get(key)]}, indent=2))


if __name__ == '__main__':
    run()
