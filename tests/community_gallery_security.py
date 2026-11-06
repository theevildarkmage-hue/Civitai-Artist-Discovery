"""Exact production handler transport security cases; all provider data synthetic."""
import test_safety,subprocess
subprocess.run(['node','--experimental-transform-types',str(test_safety.ROOT/'tests/community_gallery_security.mjs')],check=True)
