# Build a release, package it, then print the hash that lets testers verify it.
# Folder build by default; -OneFile produces the single-executable form instead.
param([switch]$OneFile, [int]$KeepBackups = 3)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

# Identifies a build by its contents rather than by when it was written. A re-run after a
# failed package step presents the same dist as the run before it, and a timestamp cannot
# tell those apart.
function Get-BuildFingerprint([string]$path) {
  $files = Get-ChildItem $path -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne '.fingerprint' } | Sort-Object FullName
  if (-not $files) { return $null }
  $lines = foreach ($f in $files) {
    $relative = $f.FullName.Substring($path.Length).TrimStart('\')
    "$relative $((Get-FileHash $f.FullName -Algorithm SHA256).Hash)"
  }
  $writer = New-Object System.IO.StringWriter
  $lines | ForEach-Object { $writer.WriteLine($_) }
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($writer.ToString())
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try { return [System.BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLower() }
  finally { $sha.Dispose() }
}

Push-Location $root
try {
  $version = (Select-String -Path 'server.py' -Pattern '^APP_VERSION\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
  Write-Host "Building CivitaiArtistDiscovery $version"

  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $dist = Join-Path $root 'dist'
  $backupRoot = Join-Path $root 'backups'
  $existing = Get-ChildItem $dist -ErrorAction SilentlyContinue
  if ($existing) {
    $fingerprint = Get-BuildFingerprint $dist
    $newest = Get-ChildItem $backupRoot -Directory -Filter 'before-release-*' -ErrorAction SilentlyContinue |
      Sort-Object Name | Select-Object -Last 1
    $previous = if ($newest) { Get-Content (Join-Path $newest.FullName '.fingerprint') -ErrorAction SilentlyContinue } else { $null }

    if ($previous -and $previous -eq $fingerprint) {
      Write-Host "Previous build is already backed up in $($newest.Name); not copying it again"
    } else {
      $backup = Join-Path $backupRoot "before-release-$stamp"
      New-Item -ItemType Directory -Force $backup | Out-Null
      Copy-Item (Join-Path $dist '*') $backup -Recurse -Force
      $fingerprint | Out-File (Join-Path $backup '.fingerprint') -Encoding utf8
      Write-Host "Previous build backed up to $backup"
    }

    # PyInstaller only overwrites what it produces, so switching between -OneFile and the
    # folder build leaves the other form behind and both end up shipped and backed up. The
    # copy above is what makes clearing this safe.
    Remove-Item (Join-Path $dist '*') -Recurse -Force
  }

  # Keep the backup directory from growing without bound; the copies are ~11-33 MB each.
  Get-ChildItem $backupRoot -Directory -Filter 'before-release-*' -ErrorAction SilentlyContinue |
    Sort-Object Name | Select-Object -SkipLast $KeepBackups |
    ForEach-Object { Remove-Item $_.FullName -Recurse -Force; Write-Host "Pruned old backup $($_.Name)" }

  if ($OneFile) { & (Join-Path $PSScriptRoot 'build_exe.ps1') -OneFile }
  else { & (Join-Path $PSScriptRoot 'build_exe.ps1') }

  if ($OneFile) {
    $artifact = Join-Path $root 'dist\CivitaiArtistDiscovery.exe'
    if (-not (Test-Path $artifact)) { throw 'Build did not produce an executable' }
  } else {
    $folder = Join-Path $root 'dist\CivitaiArtistDiscovery'
    if (-not (Test-Path $folder)) { throw 'Build did not produce an application folder' }
    $artifact = Join-Path $root "dist\CivitaiArtistDiscovery-$version.zip"
    if (Test-Path $artifact) { Remove-Item $artifact -Force }
    # Freshly written files are often still held briefly by the on-access scanner, so a
    # first attempt can fail on a file that is perfectly fine a second later.
    $packed = $false
    foreach ($attempt in 1..6) {
      try { Compress-Archive -Path $folder -DestinationPath $artifact -ErrorAction Stop; $packed = $true; break }
      catch {
        if ($attempt -eq 6) { throw }
        Write-Host "  packaging retry $attempt (files still locked)"
        Start-Sleep -Seconds 3
        if (Test-Path $artifact) { Remove-Item $artifact -Force -ErrorAction SilentlyContinue }
      }
    }
    if (-not $packed) { throw 'Could not package the application folder' }
  }

  $hash = (Get-FileHash $artifact -Algorithm SHA256).Hash.ToLower()
  $size = (Get-Item $artifact).Length
  $notes = "$artifact.sha256.txt"
  "$hash  $(Split-Path $artifact -Leaf)" | Out-File $notes -Encoding utf8
  Write-Host ''
  Write-Host "Version : $version"
  Write-Host "Artifact: $(Split-Path $artifact -Leaf)"
  Write-Host "Size    : $([math]::Round($size / 1MB, 1)) MB"
  Write-Host "SHA-256 : $hash"
  Write-Host "Written : $notes"
  Write-Host ''
  Write-Host 'Publish the SHA-256 next to the download so testers can verify it with:'
  Write-Host "  Get-FileHash .\$(Split-Path $artifact -Leaf) -Algorithm SHA256"
} finally {
  Pop-Location
}
