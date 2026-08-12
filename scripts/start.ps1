$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
& python -B (Join-Path $root 'server.py')
if ($LASTEXITCODE -ne 0) { throw "Civitai Artist Discovery exited with code $LASTEXITCODE" }
