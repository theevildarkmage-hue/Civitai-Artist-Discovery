"""Run isolated production-handler authentication and availability regressions."""
import test_safety,subprocess
subprocess.run(['node','--experimental-transform-types',str(test_safety.ROOT/'tests/community_auth_security.mjs')],check=True)
