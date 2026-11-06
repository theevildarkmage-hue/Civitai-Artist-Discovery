"""Run the real restart script with mocked OS operations; never stop a live app."""
import test_safety  # Refuse live application endpoints and isolate all test data.

import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if os.name == 'nt':
    harness = r'''
$ErrorActionPreference = 'Stop'
. (Join-Path $env:RESTART_REPOSITORY 'scripts\restart.ps1')
$root = $env:RESTART_FIXTURE
$data = Join-Path $root 'data-dev-3.0'
New-Item -ItemType Directory -Path $data | Out-Null
$script:instanceFile = Join-Path $data 'running-instance.json'
$script:process = [pscustomobject]@{ExecutablePath='C:\Python314\python.exe';CommandLine=('python "' + (Join-Path $root 'server.py') + '" --port 8906')}
if (-not (Test-DiscoveryProcess $script:process $root)) { throw 'Current checkout rejected' }
foreach ($bad in @('C:\Other\server.py', (Join-Path $root 'server.py.evil'), 'C:\Discovery 3.0\server.py')) {
  $p = [pscustomobject]@{ExecutablePath='C:\Python314\python.exe';CommandLine=('python "' + $bad + '"')}
  if (Test-DiscoveryProcess $p $root) { throw 'Unrelated process accepted' }
}
$script:closed=0; $script:started=0; $script:waited=0; $script:owner=123
function Get-CimInstance { param($ClassName,$Filter,$ErrorAction)
  if ($Filter -ne 'ProcessId=123') { throw 'Broad process enumeration' }; return $script:process }
function Get-NetTCPConnection { param($State,$LocalPort,$ErrorAction)
  if ($LocalPort -ne 8906) { throw 'Wrong port' }; return [pscustomobject]@{OwningProcess=$script:owner;LocalAddress='127.0.0.1'} }
function Invoke-RestMethod { param($Uri,$Method,$ContentType,$Body,$TimeoutSec,$MaximumRedirection)
  if ($Uri -like '*/api/app/close') {
    if ($Uri -ne 'http://127.0.0.1:8906/api/app/close' -or $Method -ne 'Post') { throw 'Unsafe shutdown address' }
    $script:closed++; return @{closing=$true}
  }; return @{version='test'} }
function Get-Process { param($Id,$ErrorAction)
  if ($Id -ne 123) { throw 'Wrong PID' }
  $p = [pscustomobject]@{Handle=1}
  $p | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($timeout) $script:waited++; return $true }
  return $p }
function Stop-Process { throw 'Forced shutdown forbidden' }
function Start-Process { param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,[switch]$PassThru)
  if ($WindowStyle -ne 'Hidden' -or $WorkingDirectory -ne $root -or $ArgumentList -notcontains 8906 -or $ArgumentList -notcontains '127.0.0.1') { throw 'Restart configuration lost' }
  $script:started++; @{pid=456;url='http://127.0.0.1:8906'} | ConvertTo-Json | Set-Content -LiteralPath $script:instanceFile
  return [pscustomobject]@{Id=456} }
@{pid=123;url='http://192.168.0.135:8906'} | ConvertTo-Json | Set-Content -LiteralPath $script:instanceFile
$script:owner=999
try { Restart-DiscoverySource -Root $root -NoBrowser; throw 'Wrong listener accepted' }
catch { if ($_.Exception.Message -notlike '*port belongs to another*') { throw } }
if ($script:closed -or $script:started) { throw 'Unsafe mutation' }
$script:owner=123
Restart-DiscoverySource -Root $root -NoBrowser
if ($script:closed -ne 1 -or $script:waited -ne 1 -or $script:started -ne 1) { throw 'Wrong process scope' }
'''
    with tempfile.TemporaryDirectory(prefix='cad3-restart-scope-') as temporary:
        script = Path(temporary) / 'test.ps1'
        script.write_text(harness, encoding='utf-8')
        env = {**os.environ, 'RESTART_REPOSITORY': str(ROOT), 'RESTART_FIXTURE': temporary}
        env.pop('CIVITAI_HISTORY_DATA_DIR', None)
        result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                env=env, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr
print({'restartScope': True, 'noLiveProcessesTouched': True})
