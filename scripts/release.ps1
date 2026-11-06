# Build and package locally; no GitHub publication. Existing outputs are never deleted.
param([switch]$OneFile, [int]$KeepBackups = 3,
      [ValidateSet('beta','stable')][string]$Channel = 'beta', [string]$Version,
      [ValidateSet('development','production')][string]$Environment = 'development',
      [string]$ProjectRef, [switch]$Preview, [string]$OutputRoot,
      [string]$PythonExecutable = 'python')
$ErrorActionPreference = 'Stop'
if ($OneFile) { throw 'Portable releases require the folder layout.' }
$root = Split-Path -Parent $PSScriptRoot
if (-not $OutputRoot) {
  $OutputRoot = Join-Path $root ('build/releases/' + $Channel + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
if (Test-Path -LiteralPath $OutputRoot) { throw 'Use a fresh OutputRoot; previous artifacts are preserved.' }
$arguments = @{OutputRoot=$OutputRoot;PythonExecutable=$PythonExecutable;Channel=$Channel;Environment=$Environment}
if ($Version) { $arguments.Version = $Version }
if ($ProjectRef) { $arguments.ProjectRef = $ProjectRef }
if ($Preview) { $arguments.Preview = $true }
& (Join-Path $PSScriptRoot 'build_exe.ps1') @arguments
$application = if ($Channel -eq 'beta') { 'CivitaiDiscoveryBeta' } else { 'CivitaiArtistDiscovery' }
$package = Join-Path $OutputRoot ('dist/' + $application)
if (-not (Test-Path -LiteralPath (Join-Path $package ($application + '.exe')))) { throw 'Build did not produce the channel executable.' }
$artifactArguments = @('-B',(Join-Path $PSScriptRoot 'prepare-artifacts.py'),'--package',$package,'--output',(Join-Path $OutputRoot 'artifacts'))
if ($Preview) { $artifactArguments += '--preview' }
& $PythonExecutable @artifactArguments
if ($LASTEXITCODE -ne 0) { throw 'Artifact preparation failed.' }
Write-Host ('Local artifacts: ' + (Join-Path $OutputRoot 'artifacts'))
