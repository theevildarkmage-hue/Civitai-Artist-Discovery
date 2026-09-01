# Civitai Artist Discovery 1.0.3

Version 1.0.3 adds a way to reach artwork the daily gallery cannot, and an optional
unattended collector for days that would otherwise expire.

## Time machine

A new tab that walks each creator you follow from their earliest artwork, one card per
creator. Scroll a card away and the next refresh shows that creator's next-oldest image.

This reaches history the daily gallery structurally cannot. Every other part of the app
pages Civitai's global feed, which can only be paged back about two days; asking for one
creator at a time is a short feed that can be entered from its far end. In testing, a
followed creator's earliest artwork was from September 2024 -- eleven months beyond the
oldest day the archive could hold -- and arrived in a single request.

Reading your follows fetches one page per creator, so the progress bar counts creators
rather than images. Most creators are complete after that single page and are never
fetched again. The cards are the gallery's own, so following, reacting, creator details
and the artwork dialog all behave exactly as they do in the daily gallery.

## Collect recent days automatically

Off by default. When switched on, the app collects recently ended days on a 12 or 24 hour
schedule so they are not lost to Civitai's roughly two-day window. It never touches the
current day, skips days already out of reach rather than spending requests to fail on
them, and records every run -- including runs that found nothing to do -- to
`data/capture-log.jsonl` so an unattended failure can be diagnosed afterwards.

Each installation picks its own slot in the interval rather than a shared one. Civitai's
posting volume is close to flat across the day, so there is no quiet hour to aim for;
spreading installations apart is what keeps the app from arriving in a burst.

## Fixed

- Rebuilding a day Civitai could no longer reach left a complete gallery marked
  incomplete with no way back, because the collection it needed could never succeed.
  Reach is now checked before anything is cleared, and the refusal names the oldest day
  that can still be rebuilt.
- A date out of reach now fails in under a second instead of after a full date-location
  sweep, and the build screen shows the reachable boundary before the build starts.
- Opening artwork details asked Civitai without naming a browsing level, so every image
  above the public level returned nothing: no prompt, no resources, and the same request
  re-sent every time the dialog was reopened.
- Locating a date takes about seven requests instead of sixteen, by estimating from the
  feed's known depth rather than searching from the first page.

## Updating

Versions 1.0.0 through 1.0.2 can install this release from the in-app update dialog. The
update remains user-approved, verifies GitHub's SHA-256 asset digest, preserves the
portable `data/` folder, rolls back a failed replacement, and restarts automatically.

The exact release asset is `CivitaiArtistDiscovery-1.0.3.zip`. Its SHA-256 is
`51734e241c8a42bb241dcf360fb76fa5463ff6af898a34ad254082cb95c20b47`; it is also recorded in the
accompanying checksum file and verified automatically by the app.

Windows 10 and 11 remain the packaged and routinely tested platforms. The package is
unsigned, so Windows SmartScreen or managed-device policy may warn or block it.
