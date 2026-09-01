# Civitai Artist Discovery 1.0.4

Version 1.0.4 fixes the Time machine tab, which did not work as described in 1.0.3, and
lets the request pacer recover from a slow patch inside a single collection.

**1.0.3 should not be used.** It is published as a prerelease and is not offered to
existing installations. Everything below was broken in it.

## Time machine

- Scrolling past a card now advances that creator. Progress was only saved when the tab
  was reopened, so reloading the page discarded it and every creator came back showing
  the same artwork -- which defeats the point of walking a history. It is now saved as
  you scroll, and again when the page closes.
- Scrolling registers reliably. The tab used its own rule for deciding a card had been
  seen, and a card taller than half the window often never satisfied it: eight screens of
  scrolling marked three cards. It now uses the same rule as the dimmed cards in the
  daily gallery -- scrolled completely past, after a pause.
- Cards no longer render empty. Artwork carrying a tag hidden on Civitai left a blank
  card that could never advance, because a card holds one image and there was nothing to
  fall back to. Hidden artwork is now skipped before the card is built.
- The grid no longer reshuffles on every refresh, and creators just read sink to the
  bottom rather than leading every visit.
- Scrolling this tab no longer marks those creators as seen in the daily gallery, where
  they were never shown. Existing incorrect entries are not removed automatically; they
  clear themselves as each day ages out.

## Collection pacing

Backing off and recovering are now both proportional. A failure multiplied the interval
by 1.5, so a handful of errors reached the eight-second ceiling, while recovery subtracted
a tenth of a second per ten clean responses -- around 725 requests to come back down. Few
collections run that long, so any Civitai hiccup left the app slow for the rest of the run
and often the next one. Recovery now takes about 75 requests. Backing off is still faster
than recovering, deliberately.

## Updating

Versions 1.0.0 through 1.0.2 can install this release from the in-app update dialog once
it is promoted from prerelease. The update remains user-approved, verifies GitHub's
SHA-256 asset digest, preserves the portable `data/` folder, rolls back a failed
replacement, and restarts automatically.

The exact release asset is `CivitaiArtistDiscovery-1.0.4.zip`. Its SHA-256 is
`cbe711130a2c9e518d4b66427ec627ca48ae4d0c47b012df7afb8f9c7728106b`; it is also recorded in the
accompanying checksum file and verified automatically by the app.

Windows 10 and 11 remain the packaged and routinely tested platforms. The package is
unsigned, so Windows SmartScreen or managed-device policy may warn or block it.
