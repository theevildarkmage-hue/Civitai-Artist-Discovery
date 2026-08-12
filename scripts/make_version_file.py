"""Generate the Windows version resource embedded in the executable.

An executable with no product name, company, or description looks anonymous in Properties
and gives heuristic scanners nothing to identify it by. Generated from the version in
server.py so the two cannot drift.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "version_info.txt"

source = (ROOT / "server.py").read_text(encoding="utf-8")
version = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', source, re.M).group(1)
name = re.search(r'^APP_NAME\s*=\s*"([^"]+)"', source, re.M).group(1)

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
        StringStruct('InternalName', 'CivitaiArtistDiscovery'),
        StringStruct('LegalCopyright', 'Copyright (c) 2026 theevildarkmage126. MIT licensed.'),
        StringStruct('OriginalFilename', 'CivitaiArtistDiscovery.exe'),
        StringStruct('ProductName', '{name}'),
        StringStruct('ProductVersion', '{version}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")
print(f"wrote {OUT} ({name} {version})")
