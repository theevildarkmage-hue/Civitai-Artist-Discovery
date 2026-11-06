param([switch]$RealVerification, [switch]$Offline, [switch]$NoBrowser, [int]$Port = 8907,
      [string]$DataDirectory, [switch]$UseMetadataSample)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$directoryPin = Join-Path $projectRoot '.data-directory-3.0.json'
if (-not $DataDirectory -and (Test-Path -LiteralPath $directoryPin)) {
  $DataDirectory = (Get-Content -LiteralPath $directoryPin -Raw | ConvertFrom-Json).directory
}
$devDataRoot = if ($DataDirectory) { [IO.Path]::GetFullPath((Join-Path $projectRoot $DataDirectory)) } else { Join-Path $projectRoot 'data-dev-3.0' }
if (-not $devDataRoot.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Development data must stay within this separate 3.0 workspace.' }
if ($UseMetadataSample -and ($RealVerification -or $Offline)) { throw 'Metadata fixture samples require explicit fixture mode.' }
$env:CIVITAI_HISTORY_DATA_DIR = $devDataRoot
$env:CAD_DEVELOPMENT = '1'
$env:CAD_COMMUNITY_TESTING = '0'
Remove-Item Env:\CAD_COMMUNITY_SAMPLE -ErrorAction SilentlyContinue
if ($UseMetadataSample -or (-not $RealVerification -and -not $Offline -and (Test-Path -LiteralPath (Join-Path $devDataRoot 'sample.json')))) {
  $sampleFile = Join-Path $devDataRoot 'sample.json'
  if (-not (Test-Path -LiteralPath $sampleFile -PathType Leaf)) { throw 'Extract the isolated metadata sample first.' }
  $env:CAD_COMMUNITY_SAMPLE = $sampleFile
}
$env:CAD_COMMUNITY_MODE = if ($Offline) { 'offline' } elseif ($RealVerification) { 'local' } else { 'fixture' }
Set-Location -LiteralPath $projectRoot
$launchArgs = @('scripts/dev-launcher.py', '--port', "$Port", '--data-directory', $devDataRoot)
if ($NoBrowser) { $launchArgs += '--no-browser' }
$localPython = Join-Path $projectRoot '.venv-3.0\Scripts\python.exe'
$python = if (Test-Path -LiteralPath $localPython) { $localPython } else { (Get-Command python -ErrorAction Stop).Source }
if ($python -eq $localPython) {
  $privateSite = Join-Path $projectRoot '.venv-3.0\Lib\site-packages'
  $env:PYTHONPATH = (@($privateSite, $env:PYTHONPATH) | Where-Object { $_ }) -join [IO.Path]::PathSeparator
  $python = (& $localPython -c 'import sys; print(sys._base_executable)').Trim()
}
& $python @launchArgs
exit $LASTEXITCODE
