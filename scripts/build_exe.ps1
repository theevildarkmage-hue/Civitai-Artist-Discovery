# Builds the portable folder application. A self-extracting one-file build would write its
# runtime to the system temporary folder, contrary to the portable-only storage model.
param([switch]$OneFile)

$ErrorActionPreference = 'Stop'
if ($OneFile) { throw 'Portable releases use the folder build; -OneFile is no longer supported.' }
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
  python -B scripts/make_icon.py
  if ($LASTEXITCODE -ne 0) { throw "Icon generation exited with code $LASTEXITCODE" }
  python -B scripts/make_version_file.py
  if ($LASTEXITCODE -ne 0) { throw "Version resource generation exited with code $LASTEXITCODE" }

  # --noupx is explicit rather than assumed: a machine with UPX installed would otherwise
  # pack the binary, and packed executables are a well-known heuristic trigger.
  python -m PyInstaller --noconfirm --clean --onedir --windowed --noupx `
    --name CivitaiArtistDiscovery `
    --icon "static/app.ico" `
    --version-file "build/version_info.txt" `
    --add-data "static;static" `
    server.py
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller exited with code $LASTEXITCODE" }

  $package = Join-Path $root 'dist\CivitaiArtistDiscovery'
  $licenses = Join-Path $package 'licenses'
  New-Item -ItemType Directory -Force $licenses | Out-Null
  $pystraySite = python -c "import pathlib, pystray; print(pathlib.Path(pystray.__file__).parent.parent)"
  $pystrayInfo = Get-ChildItem $pystraySite -Directory -Filter 'pystray-*.dist-info' | Select-Object -First 1
  Copy-Item (Join-Path $pystrayInfo.FullName 'COPYING') (Join-Path $licenses 'pystray-COPYING.txt')
  Copy-Item (Join-Path $pystrayInfo.FullName 'COPYING.LGPL') (Join-Path $licenses 'pystray-COPYING.LGPL.txt')
  $pillowSite = python -c "import pathlib, PIL; print(pathlib.Path(PIL.__file__).parent.parent)"
  $pillowInfo = Get-ChildItem $pillowSite -Directory -Filter 'pillow-*.dist-info' | Select-Object -First 1
  Copy-Item (Join-Path $pillowInfo.FullName 'licenses\LICENSE') (Join-Path $licenses 'Pillow-LICENSE.txt')
  Copy-Item (Join-Path $root 'THIRD_PARTY_NOTICES.md') $package
  Write-Host "Built: $package\CivitaiArtistDiscovery.exe"
} finally {
  Pop-Location
}
