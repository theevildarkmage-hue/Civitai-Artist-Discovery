"""Generate the Windows version resource embedded in the executable.

An executable with no product name, company, or description looks anonymous in Properties
and gives heuristic scanners nothing to identify it by. Generated from the version in
server.py so the two cannot drift.
"""

import re
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from discovery.build_config import validate,CONFIG
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--configuration',type=Path)
parser.add_argument('--output',type=Path,default=ROOT/'build/version_info.txt')
args=parser.parse_args()
OUT=args.output
config=validate(json.loads(args.configuration.read_text(encoding='utf-8'))) if args.configuration else CONFIG
version=config['version']
application='CivitaiDiscoveryBeta' if config['channel']=='beta' else 'CivitaiArtistDiscovery'
name='Civitai Discovery 3.0'+(' Beta' if config['channel']=='beta' else '')

# Windows wants four integers; the pre-release suffix lives in the string fields.
numeric = [int(part) for part in re.findall(r"\d+", version)[:3]] + [0]
while len(numeric) < 4:
    numeric.append(0)

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({numeric[0]}, {numeric[1]}, {numeric[2]}, {numeric[3]}),
    prodvers=({numeric[0]}, {numeric[1]}, {numeric[2]}, {numeric[3]}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'theevildarkmage126'),
        StringStruct('FileDescription', 'Browse Civitai artists one day at a time'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', '{application}'),
        StringStruct('LegalCopyright', 'Copyright (c) 2026 theevildarkmage126. MIT licensed.'),
        StringStruct('OriginalFilename', '{application}.exe'),
        StringStruct('ProductName', '{name}'),
        StringStruct('ProductVersion', '{version}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")
print(f"wrote {OUT.name} ({name} {version})")
