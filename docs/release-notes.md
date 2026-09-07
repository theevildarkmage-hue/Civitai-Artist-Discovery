# Civitai Artist Discovery 2.0.0

Version 2.0 is a full interface and interaction refresh focused on making a very large
daily Civitai feed feel fast, understandable, and centered on creators rather than rows
of disconnected images.

## A new gallery experience

- Image-first creator cards now share one visual and interaction system across Daily
  Gallery and Time Machine.
- Each artist stays in one card with an image carousel, visible navigation feedback,
  reaction totals, follower information, Follow, details, and direct Civitai links.
- Card menus can save the current image to a Civitai collection or add the artist to the
  account's Civitai Hidden Users list. Civitai Content Controls remain the source of truth.
- Recommendation, familiarity, and Emerging badges live together at the top of the image.

## Simpler navigation and filtering

- A shared command bar keeps Daily Gallery, Time Machine, and My Profile in one consistent
  shell.
- Calendar navigation replaces the older day-by-day controls and marks saved and partially
  collected days, with Morning, Evening, and All day choices in the same panel.
- Content and generation-model filters are combined into one compact panel with search,
  active filter chips, and a safe-default reset.
- Settings contains card size, viewed-card dimming, frequent-poster filtering, automatic
  day collection, update checks, and local profile-data management without duplicated UI.

## Personal discovery

- For You combines reaction taste with a cached fingerprint of the account's public work
  and explains why a creator is being recommended.
- Emerging uses the same personal ranking while limiting the pool to creators with fewer
  than 1,000 followers.
- My Profile has a redesigned hierarchy for reaction mix, distinctive tags, model signals,
  favorite creators, and creators worth following.
- Time Machine uses the shared cards while walking through the oldest work of creators the
  account follows.

## Faster and clearer loading

- The first page is smaller, previews are sized responsively, and artwork begins loading
  only as cards approach the viewport.
- Cached tag decisions and an indexed hidden-tag lookup reduce repeat work on large
  archives without weakening content filtering.
- Skeletons, retry actions, carousel progress, cancellation, and empty states replace
  unexplained blank cards or controls that appear unresponsive.
- Opening an image at full size clears the previous artwork immediately and shows a
  loading state, instead of leaving the last image on screen until the new one arrives.
- Gallery depth, scroll position, date/window/view, models, and card size survive an
  ordinary refresh within the browser session.

## Updating

Packaged versions 1.0.0 through 1.0.4 can install 2.0.0 through the normal in-app update
dialog. The updater verifies the GitHub-provided SHA-256 digest, preserves the portable
`data/` folder, rolls back a failed replacement, and restarts automatically.

The exact release asset is `CivitaiArtistDiscovery-2.0.0.zip`. Its SHA-256 is
`0783c3f1f78f0262cb60a0cb32bba17f21d6ccef03eb1ad3a761c188436c9ee8`; it is also recorded in the
accompanying checksum file and verified automatically by the app.

The Hide Artist action needs Civitai's profile-settings permission. Existing users should
sign out and back in once after updating if they want to use it; saved galleries and local
profile analysis are not removed.

Windows 10 and 11 remain the packaged and routinely tested platforms. The package is
unsigned, so Windows SmartScreen or managed-device policy may warn or block it.

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
