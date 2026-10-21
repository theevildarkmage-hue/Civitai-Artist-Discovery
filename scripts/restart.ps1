# Restart only the instance belonging to this checkout. Never enumerate/kill other apps.
param([int]$Port = 0, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'

function Test-DiscoveryProcess($Process, [string]$Root) {
  if (-not $Process) { return $false }
  $source = [regex]::Escape((Join-Path $Root 'server.py'))
  $sourceArgument = '(?i)(?:^|\s)(?:"' + $source + '"|' + $source + ')(?=\s|$)'
  $python = [System.IO.Path]::GetFileName($Process.ExecutablePath) -in @('python.exe', 'pythonw.exe')
  $package = Join-Path $Root 'CivitaiArtistDiscovery.exe'
  return ($python -and $Process.CommandLine -match $sourceArgument) -or
    ($Process.ExecutablePath -and $Process.ExecutablePath -eq $package)
}

function Restart-DiscoverySource([string]$Root, [int]$Port = 0, [switch]$NoBrowser) {
  $Root = [System.IO.Path]::GetFullPath($Root)
  $pin = Join-Path $Root '.data-directory-3.0.json'
  $dataRoot = if ($env:CIVITAI_HISTORY_DATA_DIR) { $env:CIVITAI_HISTORY_DATA_DIR } elseif (Test-Path -LiteralPath $pin) {
    Join-Path $Root ((Get-Content -LiteralPath $pin -Raw | ConvertFrom-Json).directory)
  } else { Join-Path $Root 'data-dev-3.0' }
  $dataRoot = [System.IO.Path]::GetFullPath($dataRoot)
  if (-not $dataRoot.StartsWith($Root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw '3.0 restart data must remain inside this checkout.'
  }
  $instanceFile = Join-Path $dataRoot 'running-instance.json'
  $localPython = Join-Path $Root '.venv-3.0\Scripts\python.exe'
  $python = if (Test-Path -LiteralPath $localPython) { $localPython } else { (Get-Command python -ErrorAction Stop).Source }
  if ($python -eq $localPython) {
    $privateSite = Join-Path $Root '.venv-3.0\Lib\site-packages'
    $env:PYTHONPATH = (@($privateSite, $env:PYTHONPATH) | Where-Object { $_ }) -join [IO.Path]::PathSeparator
    $python = (& $localPython -c 'import sys; print(sys._base_executable)').Trim()
  }
  $bindAddress = $null
  if (Test-Path -LiteralPath $instanceFile) {
    $instance = Get-Content -LiteralPath $instanceFile -Raw | ConvertFrom-Json
    $instanceId = 0
    if (-not [int]::TryParse([string]$instance.pid, [ref]$instanceId) -or $instanceId -le 0) {
      throw 'Invalid Discovery instance record; no process was stopped.'
    }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$instanceId" -ErrorAction Stop
    if ($process) {
      if (-not (Test-DiscoveryProcess $process $Root)) {
        throw 'The recorded process does not belong to this checkout; no process was stopped.'
      }
      $address = [uri]$instance.url
      if ($address.Scheme -ne 'http' -or $address.Port -le 0 -or $address.UserInfo -or
          $address.AbsolutePath -ne '/' -or $address.Query -or $address.Fragment) {
        throw 'Invalid Discovery instance address; no process was stopped.'
      }
      $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $address.Port -ErrorAction Stop)
      if (-not $listeners.Count -or @($listeners | Where-Object OwningProcess -ne $instanceId).Count) {
        throw 'The recorded port belongs to another process; no process was stopped.'
      }
      if ($Port -eq 0) { $Port = $address.Port }
      $bindAddress = $listeners[0].LocalAddress
      if ([System.IO.Path]::GetFileName($process.ExecutablePath) -eq 'python.exe') { $python = $process.ExecutablePath }
      $waitable = Get-Process -Id $instanceId -ErrorAction Stop
      $null = $waitable.Handle
      $close = "http://127.0.0.1:$($address.Port)/api/app/close"
      Invoke-RestMethod -Uri $close -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 15 -MaximumRedirection 0 | Out-Null
      if (-not $waitable.WaitForExit(30000)) { throw 'Discovery did not close; no replacement was started.' }
    }
  }
  $arguments = @('-B', ('"{0}"' -f (Join-Path $Root 'server.py')))
  if ($Port -gt 0) { $arguments += @('--port', $Port) }
  if ($bindAddress -in @('127.0.0.1', '::1', '0.0.0.0')) { $arguments += @('--host', $bindAddress) }
  if ($NoBrowser) { $arguments += '--no-browser' }
  $started = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $Root -WindowStyle Hidden -PassThru
  $deadline = (Get-Date).AddSeconds(60)
  while ((Get-Date) -lt $deadline) {
    if (Test-Path -LiteralPath $instanceFile) {
      try {
        $ready = Get-Content -LiteralPath $instanceFile -Raw | ConvertFrom-Json
        if ($ready.pid -eq $started.Id) {
          $config = Invoke-RestMethod -Uri "http://127.0.0.1:$(([uri]$ready.url).Port)/api/history/config" -TimeoutSec 10 -MaximumRedirection 0
          Write-Host "Running version $($config.version) at $($ready.url) (PID $($started.Id))"
          return
        }
      } catch { }
    }
    Start-Sleep -Milliseconds 500
  }
  throw 'The new Discovery process did not report ready within 60 seconds.'
}

if ($MyInvocation.InvocationName -ne '.') {
  Restart-DiscoverySource -Root (Split-Path -Parent $PSScriptRoot) -Port $Port -NoBrowser:$NoBrowser
}
