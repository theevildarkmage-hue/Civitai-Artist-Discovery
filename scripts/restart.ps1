# Stops whatever copy of the app is running and starts a fresh one from the current
# source. A running copy holds its code in memory, so editing or pulling source changes
# nothing until it is restarted -- which is why a stale process can keep reporting an old
# version number long after the working tree has moved on.
param(
  [int]$Port = 0,          # 0 lets the app pick a free port, as it does normally.
  [switch]$NoBrowser       # Leave the browser alone; useful when a tab is already open.
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$instanceFile = Join-Path $root 'data\running-instance.json'

Write-Host 'Stopping any running copy...' -ForegroundColor Cyan
$stopped = @()
foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                       Where-Object { $_.CommandLine -like '*server.py*' })) {
  $stopped += $process.ProcessId
}
foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name='CivitaiArtistDiscovery.exe'" -ErrorAction SilentlyContinue)) {
  $stopped += $process.ProcessId
}
foreach ($id in $stopped) {
  try { Stop-Process -Id $id -Force -ErrorAction Stop; Write-Host "  stopped process $id" }
  catch { Write-Host "  process $id was already gone" }
}
if (-not $stopped) { Write-Host '  nothing was running' }

# The replacement needs the old copy's port and single-instance lock, which are only
# released once that process has fully exited.
$deadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $deadline) {
  $alive = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
             Where-Object { $_.CommandLine -like '*server.py*' })
  $alive += @(Get-CimInstance Win32_Process -Filter "Name='CivitaiArtistDiscovery.exe'" -ErrorAction SilentlyContinue)
  if (-not $alive) { break }
  Start-Sleep -Milliseconds 300
}

Remove-Item $instanceFile -ErrorAction SilentlyContinue

Write-Host 'Starting from current source...' -ForegroundColor Cyan
# Start-Process joins its argument list with spaces and does no quoting of its own, so a
# path containing spaces -- which this project's own folder has -- arrives at Python split
# into several arguments. Quote it here rather than relying on the caller's folder name.
$arguments = @('-B', ('"{0}"' -f (Join-Path $root 'server.py')))
if ($Port -gt 0)  { $arguments += @('--port', $Port) }
if ($NoBrowser)   { $arguments += '--no-browser' }
Start-Process -FilePath 'python' -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden

# The app records where it ended up, which is the only way to learn a port it chose itself.
$url = $null
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
  if (Test-Path $instanceFile) {
    try { $url = (Get-Content $instanceFile -Raw | ConvertFrom-Json).url } catch { $url = $null }
    if ($url) { break }
  }
  Start-Sleep -Milliseconds 500
}

if (-not $url) {
  Write-Warning 'The app did not report a URL within 60 seconds. It may still be starting.'
  exit 1
}

$version = '(unknown)'
try {
  $version = (Invoke-RestMethod -Uri "$url/api/history/config" -TimeoutSec 15).version
} catch {
  Write-Warning "Started at $url but its version could not be read yet."
}

Write-Host ''
Write-Host "Running version $version at $url" -ForegroundColor Green
