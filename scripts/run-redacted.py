"""Run a Python build command; redact captured output before any console emission."""
from pathlib import Path
import subprocess,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from discovery.diagnostics import redact_sensitive
args=sys.argv[1:]
if args[:1]==['--']:args=args[1:]
if not args:raise SystemExit('A Python command is required')
try:
    result=subprocess.run([sys.executable,*args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    output=redact_sensitive(result.stdout.decode('utf-8','replace'))
    print(output,end='' if output.endswith('\n') else '\n')
except Exception:
    raise SystemExit('Build command failed; personal details withheld') from None
raise SystemExit(result.returncode)
