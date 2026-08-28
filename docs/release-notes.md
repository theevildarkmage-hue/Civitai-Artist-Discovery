# Civitai Artist Discovery 1.0.1

Version 1.0.1 is a collection-reliability hotfix for the first stable release.

## Fixed

- PG and PG-13 are now collected as independent feeds. Keeping each high-volume stream
  below Civitai's public cursor traversal window lets the locator reach older dates that
  failed immediately when both levels shared one cursor.
- The date locator retries an unexpected empty HTTP 200 response three times. Each empty
  response is recorded in the rotating `data/api-failures.jsonl` journal, including its
  safe request context and response excerpt.
- Cursor seeking has an explicit 50,000-result ceiling. A feed that ends or stops
  advancing now exits predictably instead of returning a known-empty cursor or expanding
  offsets without a bound.
- When the selected date is outside Civitai's currently accessible public history, the
  app says **Date unavailable from Civitai**, explains that the device and connection are
  not the cause, and keeps both day navigation and a later retry available.

The original report was captured as HTTP 503 with `Retry-After: 2` and the Civitai body
`Image search is temporarily overloaded — please retry.` The API then returned a
successful but empty locator page before the requested boundary. Version 1.0.1 handles
the overload and the premature empty-page condition separately.

## Updating

Version 1.0.0 can install this release from its in-app update dialog. The update remains
user-approved, verifies GitHub's SHA-256 asset digest, preserves the portable `data/`
folder, rolls back a failed replacement, and restarts automatically.

The exact release asset is `CivitaiArtistDiscovery-1.0.1.zip`. Its SHA-256 is
`d9425fb005ef609036195f1fa52cbd96d8c44b47921d5858cef1a7785fac9fc1`; it is also
recorded in the accompanying checksum file and verified automatically by the app.

Windows 10 and 11 remain the packaged and routinely tested platforms. The package is
unsigned, so Windows SmartScreen or managed-device policy may warn or block it.
