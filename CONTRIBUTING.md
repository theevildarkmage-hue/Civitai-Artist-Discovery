# Contributing

Thanks for helping improve Civitai Artist Discovery. The project is an active alpha, so
small, focused changes are easier to review and less likely to disrupt working behavior.

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
