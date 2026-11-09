# Builds the portable folder application. A self-extracting one-file build would write its
# runtime to the system temporary folder, contrary to the portable-only storage model.
param([switch]$OneFile, [string]$OutputRoot, [string]$PythonExecutable = 'python',
      [ValidateSet('beta','stable')][string]$Channel = 'beta', [string]$Version,
      [ValidateSet('development','production')][string]$Environment = 'development',
      [string]$ProjectRef, [switch]$Preview)

$ErrorActionPreference = 'Stop'
Set-Alias -Name python -Value $PythonExecutable -Scope Script
if ($OneFile) { throw 'Portable releases use the folder build; -OneFile is no longer supported.' }
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
  python -B scripts/check-git-identity.py --all
  if ($LASTEXITCODE -ne 0) { throw 'Personal-identity source audit rejected this build.' }
  python -c "import ssl, sys; crypto = tuple(int(n) for n in ssl.OPENSSL_VERSION.split()[1].split('.')); assert sys.version_info[:2] == (3, 14) and sys.version_info[:3] >= (3, 14, 8) and crypto >= (3, 5, 9), 'Windows releases require Python 3.14.8+ (3.14 series) with OpenSSL 3.5.9+'; print(sys.version.split()[0], ssl.OPENSSL_VERSION)"
  if ($LASTEXITCODE -ne 0) { throw 'Use the reviewed, patched Windows release interpreter.' }
  $buildPaths = @()
  $env:PYTHONHASHSEED = '0'
  $env:SOURCE_DATE_EPOCH = (git show -s --format=%ct HEAD)
  $applicationId = if ($Channel -eq 'beta') { 'CivitaiDiscoveryBeta' } else { 'CivitaiArtistDiscovery' }
  if (-not $Version) { $Version = if ($Channel -eq 'stable') { '3.0.1' } else { '3.0.0-beta.14' } }
  $generatedRoot = Join-Path $root 'build\package-inputs'
  $package = Join-Path $root ('dist\' + $applicationId)
  if ($OutputRoot) {
    # Keep preview packages, build caches, and generated specs away from release outputs.
    $previewRoot = [System.IO.Path]::GetFullPath($OutputRoot)
    $buildPaths = @('--distpath', (Join-Path $previewRoot 'dist'),
                    '--workpath', (Join-Path $previewRoot 'build'),
                    '--specpath', $previewRoot)
    $package = Join-Path $previewRoot ('dist\' + $applicationId)
    $generatedRoot = Join-Path $previewRoot 'package-inputs'
  }
  New-Item -ItemType Directory -Force $generatedRoot | Out-Null
  $configuration = Join-Path $generatedRoot 'build-configuration.json'
  $configArgs = @('--channel',$Channel,'--version',$Version,'--environment',$Environment,'--output',$configuration)
  if ($ProjectRef) { $configArgs += @('--project-ref',$ProjectRef) }
  if ($Preview) { $configArgs += '--preview' }
  python -B scripts/configure-build.py @configArgs
  if ($LASTEXITCODE -ne 0) { throw 'Invalid explicit build target' }
  $staticRoot = Join-Path $generatedRoot 'static'
  if (Test-Path -LiteralPath $staticRoot) { throw 'Generated static inputs already exist. Use a fresh OutputRoot for reproducible builds.' }
  Copy-Item -LiteralPath (Join-Path $root 'static') -Destination $staticRoot -Recurse
  $icon = Join-Path $staticRoot 'app.ico'
  $mark = if ($Channel -eq 'beta') { 'beta' } else { 'spark' }
  python -B scripts/make_icon.py $mark $icon
  if ($LASTEXITCODE -ne 0) { throw "Icon generation exited with code $LASTEXITCODE" }
  Copy-Item -LiteralPath $icon -Destination (Join-Path $staticRoot 'discovery-beta.ico') -Force
  $versionResource = Join-Path $generatedRoot 'version_info.txt'
  python -B scripts/make_version_file.py --configuration $configuration --output $versionResource
  if ($LASTEXITCODE -ne 0) { throw "Version resource generation exited with code $LASTEXITCODE" }
  $sourceRecord = Join-Path $root 'build/beta-source.json'
  if ($OutputRoot) { $sourceRecord = Join-Path $previewRoot 'beta-source.json' }
  python -B scripts/stamp-beta-build.py start --record $sourceRecord --configuration $configuration
  if ($LASTEXITCODE -ne 0) { throw 'Could not record build source' }

  # --noupx is explicit rather than assumed: a machine with UPX installed would otherwise
  # pack the binary, and packed executables are a well-known heuristic trigger.
  python (Join-Path $root 'scripts/check-test-lab-exclusion.py')
  if ($LASTEXITCODE) { throw 'Development Test Lab must be excluded from release inputs' }
  python -B scripts/run-redacted.py -- -m PyInstaller --noconfirm --clean --onedir --windowed --noupx --log-level ERROR `
    --exclude-module discovery.community_dev --exclude-module discovery.community_fixtures `
    --exclude-module discovery.privacy_patterns `
    --name $applicationId `
    --icon $icon `
    --version-file $versionResource `
    --add-data "${staticRoot};static" `
    --add-data "${configuration};discovery" `
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
  foreach ($dependency in @('cryptography','cffi','pycparser')) {
    $dependencyLicense = python -c "from importlib.metadata import distribution; print(distribution('$dependency')._path / 'licenses')"
    Copy-Item -LiteralPath $dependencyLicense -Destination (Join-Path $licenses $dependency) -Recurse
  }
  Copy-Item -LiteralPath (Join-Path $root 'LICENSE') -Destination (Join-Path $package 'LICENSE.txt')
  Copy-Item (Join-Path $root 'THIRD_PARTY_NOTICES.md') $package
  $guides = Join-Path $package 'docs'
  New-Item -ItemType Directory -Force $guides | Out-Null
  foreach ($guide in @('3.0-beta-README.md','3.0-beta-linux.md','3.0-beta-privacy.md','3.0-beta-release-notes.md','3.0-beta-operations.md','3.0-beta-publication-checklist.md','3.0-audit-response-2026-10-24.md','3.0-owner-quota-testing.md','3.0-beta-testing-quota-exceptions.md','3.0-audit-closure-2026-10-24.md','3.0-beta-signing-design.md','3.0-stable-readiness.md','3.0-stable-installation.md','3.0-production-handoff.md','3.0-recovery.md','3.0-production-bootstrap.md','3.0-audit-tracker.md','3.0-supabase-public-release.md')) {
    Copy-Item -LiteralPath (Join-Path $root ('docs/' + $guide)) -Destination $guides
  }
  foreach ($guide in @('3.0-stable-release-notes.md','3.0-changelog.md','3.0-privacy.md','3.0-public-launch.md')) {
    Copy-Item -LiteralPath (Join-Path $root ('docs/' + $guide)) -Destination $guides
  }
  Copy-Item -LiteralPath $configuration -Destination (Join-Path $package 'build-configuration.json')
  $recoveryTools = Join-Path $package 'tools'
  New-Item -ItemType Directory -Force $recoveryTools | Out-Null
  Copy-Item -LiteralPath (Join-Path $root 'scripts/copy-local-data.py') -Destination $recoveryTools
  python -B scripts/stamp-beta-build.py finish --record $sourceRecord --package $package --configuration $configuration
  if ($LASTEXITCODE -ne 0) { throw 'Build provenance verification failed' }
  python -B scripts/check-personal-identity.py --package $package
  if ($LASTEXITCODE -ne 0) { throw 'Personal-identity package audit rejected this build.' }
  Write-Host "Built verified package: $applicationId"
} finally {
  Pop-Location
}
