"""Compatibility entry point for the shared beta/stable artifact preparer."""
import runpy
from pathlib import Path
runpy.run_path(str(Path(__file__).with_name('prepare-artifacts.py')),run_name='__main__')
