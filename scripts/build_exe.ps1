# Builds the application. Defaults to a folder build, which antivirus heuristics treat far
# more kindly than a single self-extracting file. Pass -OneFile for the single-exe form.
param([switch]$OneFile)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
  python -B scripts/make_icon.py
  if ($LASTEXITCODE -ne 0) { throw "Icon generation exited with code $LASTEXITCODE" }
  python -B scripts/make_version_file.py
  if ($LASTEXITCODE -ne 0) { throw "Version resource generation exited with code $LASTEXITCODE" }

  $mode = if ($OneFile) { '--onefile' } else { '--onedir' }
  # --noupx is explicit rather than assumed: a machine with UPX installed would otherwise
  # pack the binary, and packed executables are a well-known heuristic trigger.
  python -m PyInstaller --noconfirm --clean $mode --windowed --noupx `
    --name CivitaiArtistDiscovery `
    --icon "static/app.ico" `
    --version-file "build/version_info.txt" `
    --add-data "static;static" `
    server.py
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller exited with code $LASTEXITCODE" }

  if ($OneFile) { Write-Host "Built: $root\dist\CivitaiArtistDiscovery.exe" }
  else { Write-Host "Built: $root\dist\CivitaiArtistDiscovery\CivitaiArtistDiscovery.exe" }
} finally {
  Pop-Location
}
