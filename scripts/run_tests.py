"""Run the script-based suite with isolated data and ports, retaining per-test output."""

import argparse
import ast
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import types
import shutil

ROOT = Path(__file__).resolve().parents[1]


def test_interpreter():
    # Windows venv redirectors own a different PID from their server child. Keep the
    # exact-PID safety contract by launching the base interpreter with private packages.
    if os.name == 'nt' and sys.prefix != sys.base_prefix:
        private_site = str(Path(sys.prefix) / 'Lib' / 'site-packages')
        os.environ['PYTHONPATH'] = os.pathsep.join(filter(None, (private_site, os.environ.get('PYTHONPATH'))))
        return sys._base_executable
    return sys.executable


def child(name):
    path = ROOT / "tests" / (name + ".py")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    # Keep the production server (and other projects) on their existing ports.
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "PORT"
            for target in node.targets
        ):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                node.value = ast.copy_location(
                    ast.Constant(sock.getsockname()[1]), node.value
                )
    ast.fix_missing_locations(tree)
    spawned = []
    original = subprocess.Popen

    class TrackedPopen(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            spawned.append(self)

    subprocess.Popen = TrackedPopen
    sys.path[:0] = [str(ROOT / "tests"), str(ROOT)]
    import test_safety  # Install the endpoint/data guard before executing any suite.
    sys.argv = [str(path)]
    module = types.ModuleType('__main__')
    module.__file__ = str(path)
    previous_main = sys.modules['__main__']
    sys.modules['__main__'] = module
    try:
        exec(
            compile(tree, str(path), "exec"),
            module.__dict__,
        )
    finally:
        for process in spawned:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
        subprocess.Popen = original
        sys.modules['__main__'] = previous_main


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child")
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    interpreter = test_interpreter()
    if args.child:
        child(args.child)
        return
    names = args.only or [path.stem for path in sorted((ROOT / "tests").glob("*.py"))
                         if path.stem not in ('test_safety', 'community_harness')]
    output = args.output or Path(tempfile.mkdtemp(prefix='cad3-validation-'))
    output.mkdir(parents=True, exist_ok=True)
    print(f'Validation artifacts: {output.resolve()}',flush=True)
    results = []
    for index, name in enumerate(names, 1):
        with tempfile.TemporaryDirectory(
            prefix="civitai-suite-", ignore_cleanup_errors=True
        ) as data:
            scratch=Path(data)/'tmp';scratch.mkdir()
            profile=scratch/'cad3-default';profile.mkdir()
            artifacts=Path(data)/'artifacts';artifacts.mkdir()
            env = {
                **os.environ,
                "CIVITAI_HISTORY_DATA_DIR": str(profile),
                "TMP": str(scratch), "TEMP": str(scratch), "TMPDIR": str(scratch),
                "CAD_TEST_REPORT_DIR": str(artifacts),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                "PYTHONIOENCODING": "utf-8",
            }
            for key in ('CAD_ALLOW_ISOLATED_DB_TESTS','CAD_TEST_DATABASE_URL',
                        'CAD_ALLOW_ISOLATED_EDGE_TESTS','CAD_TEST_COMMUNITY_URL',
                        'CAD_TEST_CIVITAI_TOKEN','CAD_COMMUNITY_SAMPLE',
                        'CAD_COMMUNITY_TESTING','CAD_TEST_INSTANCE'):
                env.pop(key, None)
            started = time.monotonic()
            with (output / (name + ".log")).open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    [
                        interpreter,
                        "-B",
                        str(Path(__file__).resolve()),
                        "--child",
                        name,
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                try:
                    code = process.wait(timeout=args.timeout)
                    state = "passed" if code == 0 else "failed"
                except subprocess.TimeoutExpired:
                    state = "timeout"
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    else:
                        process.kill()
                    process.wait()
            if state == 'passed' and 'SKIP:' in (output / (name + '.log')).read_text(encoding='utf-8', errors='replace'):
                state = 'skipped'
            result = {
                "name": name,
                "state": state,
                "seconds": round(time.monotonic() - started, 1),
            }
            results.append(result)
            if any(artifacts.iterdir()):
                shutil.copytree(artifacts, output/'artifacts'/name, dirs_exist_ok=True)
            (output / "results.json").write_text(
                json.dumps(results, indent=2), encoding="utf-8"
            )
            print(
                f"{index}/{len(names)} {name}: {state} ({result['seconds']}s)",
                flush=True,
            )
    failed = [row["name"] for row in results if row["state"] not in ('passed','skipped')]
    summary = {
                "total": len(results),
                "passed": sum(row['state']=='passed' for row in results),
                "skipped": sum(row['state']=='skipped' for row in results),
                "failed": failed,
                "artifacts": str(output.resolve()),
            }
    (output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary),flush=True)
    raise SystemExit(bool(failed))


if __name__ == "__main__":
    main()
