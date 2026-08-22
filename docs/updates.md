# Application updates

Packaged Windows builds can check this project's public GitHub releases and install a
newer portable build without moving or recreating the user's local archive. Source
checkouts can report releases, but one-click replacement is intentionally limited to the
packaged application.

## User experience

Automatic checks are enabled by default and happen at most once every 24 hours. The app
uses GitHub's public release API, so a GitHub account and access token are not required.
The preference can be disabled under **My Profile**, and **Check now** remains available
there when checks are enabled.

When a newer beta or stable version exists, an **Update** button appears in the header.
It opens a dialog containing the release name, plain-text changelog, download size, and a
link to the GitHub release. Nothing is downloaded or installed until the user approves
it. Download and verification progress remain visible in the dialog.

Installation waits if a gallery build, profile synchronization, follower scan, or tag
preparation job is active. After the conflict ends, **Install and restart** closes the
local server, applies the update, and reopens the application. A success or recovery
message is shown after restart.

## Verification and portable storage

The updater accepts only a non-draft release newer than the running version and only the
exact asset `CivitaiArtistDiscovery-<version>.zip` hosted by this repository. It requires
the SHA-256 digest GitHub records for the uploaded asset, calculates the downloaded
archive's digest locally, and refuses a mismatch. Archive paths, symbolic links, file
count, download size, and expanded size are validated before extraction.

Temporary files are placed under `data/update/`, keeping the portable-only storage model.
The replacement helper copies packaged application entries but never copies or replaces
`data/`; unrelated files a user placed beside the executable are also left alone. The
old packaged entries are backed up immediately before replacement. If any copy fails,
the helper restores them and records the failure. Once the new version starts
successfully, the archive, staging folder, and rollback copy are removed; the small
result receipt remains only until the UI acknowledges it.

The checker includes prereleases because Civitai Artist Discovery is currently published
as a beta. Invalid versions, drafts, releases without the exact portable ZIP, and assets
without GitHub digest metadata are ignored.

## Publishing a compatible release

1. Set `APP_VERSION` in `server.py` to the intended semantic version and use the matching
   `v<version>` Git tag.
2. Run `scripts/release.ps1`. It builds the Windows folder package and produces
   `CivitaiArtistDiscovery-<version>.zip` plus a local SHA-256 text file.
3. Upload that exact ZIP as an asset on the matching GitHub release. GitHub calculates
   the asset digest exposed by its release API.
4. Publish the release. A draft is never offered to users.

The updater does not silently elevate permissions. Users should extract the application
to a folder their account can write to, as described in the README. Windows remains the
only packaged and routinely tested platform.
