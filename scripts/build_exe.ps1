# Builds the portable folder application. A self-extracting one-file build would write its
# runtime to the system temporary folder, contrary to the portable-only storage model.
param([switch]$OneFile, [string]$OutputRoot, [string]$PythonExecutable = 'python')

$ErrorActionPreference = 'Stop'
Set-Alias -Name python -Value $PythonExecutable -Scope Script
if ($OneFile) { throw 'Portable releases use the folder build; -OneFile is no longer supported.' }
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
  python -c "import ssl, sys; crypto = tuple(int(n) for n in ssl.OPENSSL_VERSION.split()[1].split('.')); assert sys.version_info[:2] == (3, 14) and sys.version_info[:3] >= (3, 14, 8) and crypto >= (3, 5, 9), 'Windows releases require Python 3.14.8+ (3.14 series) with OpenSSL 3.5.9+'; print(sys.version.split()[0], ssl.OPENSSL_VERSION)"
  if ($LASTEXITCODE -ne 0) { throw 'Use the reviewed, patched Windows release interpreter.' }
  $buildPaths = @()
  $env:PYTHONHASHSEED = '0'
  $env:SOURCE_DATE_EPOCH = (git show -s --format=%ct HEAD)
  $package = Join-Path $root 'dist\CivitaiDiscoveryBeta'
  if ($OutputRoot) {
    # Keep preview packages, build caches, and generated specs away from release outputs.
    $previewRoot = [System.IO.Path]::GetFullPath($OutputRoot)
    $buildPaths = @('--distpath', (Join-Path $previewRoot 'dist'),
                    '--workpath', (Join-Path $previewRoot 'build'),
                    '--specpath', $previewRoot)
    $package = Join-Path $previewRoot 'dist\CivitaiDiscoveryBeta'
  }
  python -B scripts/make_icon.py
  if ($LASTEXITCODE -ne 0) { throw "Icon generation exited with code $LASTEXITCODE" }
  python -B scripts/make_version_file.py
  if ($LASTEXITCODE -ne 0) { throw "Version resource generation exited with code $LASTEXITCODE" }
  $sourceRecord = Join-Path $root 'build/beta-source.json'
  if ($OutputRoot) { $sourceRecord = Join-Path $previewRoot 'beta-source.json' }
  python -B scripts/stamp-beta-build.py start --record $sourceRecord
  if ($LASTEXITCODE -ne 0) { throw 'Could not record build source' }

  # --noupx is explicit rather than assumed: a machine with UPX installed would otherwise
  # pack the binary, and packed executables are a well-known heuristic trigger.
  python (Join-Path $root 'scripts/check-test-lab-exclusion.py')
  if ($LASTEXITCODE) { throw 'Development Test Lab must be excluded from release inputs' }
  python -m PyInstaller --noconfirm --clean --onedir --windowed --noupx `
    --exclude-module discovery.community_dev --exclude-module discovery.community_fixtures `
    --name CivitaiDiscoveryBeta `
    --icon (Join-Path $root 'static/app.ico') `
    --version-file (Join-Path $root 'build/version_info.txt') `
    --add-data "$(Join-Path $root 'static');static" `
    @buildPaths (Join-Path $root 'server.py')
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller exited with code $LASTEXITCODE" }

  $licenses = Join-Path $package 'licenses'
  New-Item -ItemType Directory -Force $licenses | Out-Null
  $pystraySite = python -c "import pathlib, pystray; print(pathlib.Path(pystray.__file__).parent.parent)"
  $pystrayInfo = Get-ChildItem $pystraySite -Directory -Filter 'pystray-*.dist-info' | Select-Object -First 1
  Copy-Item (Join-Path $pystrayInfo.FullName 'COPYING') (Join-Path $licenses 'pystray-COPYING.txt')
  Copy-Item (Join-Path $pystrayInfo.FullName 'COPYING.LGPL') (Join-Path $licenses 'pystray-COPYING.LGPL.txt')
  $pillowSite = python -c "import pathlib, PIL; print(pathlib.Path(PIL.__file__).parent.parent)"
  $pillowInfo = Get-ChildItem $pillowSite -Directory -Filter 'pillow-*.dist-info' | Select-Object -First 1
  Copy-Item (Join-Path $pillowInfo.FullName 'licenses\LICENSE') (Join-Path $licenses 'Pillow-LICENSE.txt')
  $pythonLicense = python -c "import pathlib, sys; print(pathlib.Path(sys.base_prefix) / 'LICENSE.txt')"
  Copy-Item -LiteralPath $pythonLicense -Destination (Join-Path $licenses 'Python-LICENSE.txt')
  $installerInfo = python -c "from importlib.metadata import distribution; print(distribution('pyinstaller')._path)"
  Copy-Item -LiteralPath (Join-Path $installerInfo 'licenses\COPYING.txt') -Destination (Join-Path $licenses 'PyInstaller-COPYING.txt')
  Copy-Item -LiteralPath (Join-Path $root 'LICENSE') -Destination (Join-Path $package 'LICENSE.txt')
  Copy-Item (Join-Path $root 'THIRD_PARTY_NOTICES.md') $package
  $guides = Join-Path $package 'docs'
  New-Item -ItemType Directory -Force $guides | Out-Null
  foreach ($guide in @('3.0-beta-README.md','3.0-beta-linux.md','3.0-beta-privacy.md','3.0-beta-release-notes.md','3.0-beta-operations.md','3.0-beta-publication-checklist.md')) {
    Copy-Item -LiteralPath (Join-Path $root ('docs/' + $guide)) -Destination $guides
  }
  python -B scripts/stamp-beta-build.py finish --record $sourceRecord --package $package
  if ($LASTEXITCODE -ne 0) { throw 'Build provenance verification failed' }
  Write-Host "Built: $package\CivitaiDiscoveryBeta.exe"
} finally {
  Pop-Location
}
