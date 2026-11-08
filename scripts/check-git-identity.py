"""CLI entry point for the shared Git privacy policy."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
try:
    from git_identity import main
except Exception:
    raise SystemExit('Identity guard: REJECTED; policy initialization failed; details withheld') from None
if __name__=="__main__":raise SystemExit(main())
