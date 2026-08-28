# Contributing

Thanks for helping improve Civitai Artist Discovery. The 1.x line prioritizes stable,
backward-compatible bug fixes, so small, focused changes are easiest to review and least
likely to disrupt working behavior.

## Before submitting a change

1. Open an issue or briefly describe the problem the change solves.
2. Keep unrelated refactors and formatting changes out of the same contribution.
3. Do not commit local archives, databases, OAuth files, logs, cached API responses,
   screenshots containing account data, or generated packages.
4. Install the pinned development tools:

   ```powershell
   py -m pip install -r requirements-dev.txt
   py -m playwright install chromium
   ```

5. Run the existing script-based test suite:

   ```powershell
   Get-ChildItem tests\*.py | ForEach-Object {
     py $_.FullName
     if ($LASTEXITCODE) { throw "Test failed: $($_.Name)" }
   }
   ```

Please describe what you tested and call out any behavior you could not verify. By
contributing, you agree that your contribution is provided under the repository's MIT
license.

## Release compatibility

The portable updater relies on the version in `server.py`, the Git tag, and the ZIP name
matching exactly: `CivitaiArtistDiscovery-<version>.zip`. Always package releases with
`scripts/release.ps1`, upload the resulting ZIP as a GitHub release asset, and retain its
GitHub-provided SHA-256 digest. Do not publish an updater-compatible asset from another
repository or substitute an installer for the portable folder ZIP.
