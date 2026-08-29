# Civitai Artist Discovery 1.0.2

Version 1.0.2 makes the app honest about how far back Civitai can be collected, and fails
fast instead of failing late when a date is out of reach.

## Fixed

- A date Civitai can no longer reach now fails in under a second instead of after a full
  date-location sweep. Each unfinished browsing-level feed is asked how far back it
  reaches before any collecting starts -- one single-row request per level, under a
  kilobyte each.
- The failure now names the oldest day that can still be built at the selected coverage,
  and states plainly that days already in the archive are unaffected and stay viewable.
  It previously said only "choose a newer day", which gave no way to know which.
- The build screen shows the reachable boundary before the build button is pressed, via a
  new `GET /api/history/window`. The limit is visible in advance rather than discovered by
  failing.
- The estimated build time now describes the collector that will actually run, instead of
  quoting request counts and page sizes from a different one.

## Why days pass out of reach

Civitai's public API offers no date filter, ignores the timestamp half of its cursor, and
stops cursor traversal near offset 49,000. Because that ceiling counts rows rather than
time, each browsing level reaches a different depth -- PG about five days, XXX about two
-- and a block needs every level it requires, so the most restrictive one decides. The
boundary also moves forward as new artwork is posted.

This is a property of the API rather than a defect, and no documented parameter changes
it. Collecting through the search index behind Civitai's own image browser would lift the
limit, but its Terms of Service (11.4) permit automated access only through interfaces
expressly provided for it and only with the caller's own credentials, so the app does not
use it. Days already collected remain viewable regardless; the limit applies only to new
collection.

## Updating

Version 1.0.0 and 1.0.1 can install this release from the in-app update dialog. The
update remains user-approved, verifies GitHub's SHA-256 asset digest, preserves the
portable `data/` folder, rolls back a failed replacement, and restarts automatically.

The exact release asset is `CivitaiArtistDiscovery-1.0.2.zip`. Its SHA-256 is
`b00584574598fc720c1c7de18e252dab7fa2e14d2234ec8f3101c044c514b520`; it is also recorded in the
accompanying checksum file and verified automatically by the app.

Windows 10 and 11 remain the packaged and routinely tested platforms. The package is
unsigned, so Windows SmartScreen or managed-device policy may warn or block it.
