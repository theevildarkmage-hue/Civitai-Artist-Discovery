# Content Controls

Added 2026-08-05. The app builds its gallery from Civitai's own artwork, so ignoring the
account's Content Controls would put back precisely what the user asked Civitai not to
show them. These are imported and enforced rather than treated as advisory.

## Where the data comes from

One authenticated tRPC call:

```
hiddenPreferences.getHidden  ->  { hiddenTags, hiddenUsers, hiddenImages,
                                   blockedUsers, blockedByUsers, ... }
```

Civitai resolves the category switches server-side before returning. Turning on **Hide
furry** does not come back as a "furry" flag; it comes back as the concrete tags that
category covers. Verified against the author's account: 3 switches on and 17 tag chips
visible in the web UI produced **130 tags**, and `anime` was absent, matching the one
switch left off.

This matters: the app never has to model Civitai's categories, guess their membership, or
track changes to them. Whatever Civitai would enforce is what arrives.

The same call also returns `blockedByUsers` — accounts that have blocked *this* user. They
are treated exactly like hidden creators.

## What is enforced

| Source | Enforcement | Completeness |
| --- | --- | --- |
| `hiddenUsers` | Excluded from every view and from the artist count | Exact |
| `blockedByUsers` | Same | Exact |
| `hiddenImages` | Removed from carousels; never used as a card cover | Exact |
| `hiddenTags` | Verified before a preview is displayed; matching images are removed | Exact for every displayed preview |

Measured on one real day (2026-08-03, 2,966 creators, 27,002 images):

- 39 creators hidden by name
- 354 creators removed because every image of theirs carried a hidden tag
  (285 had posted one image, 68 two, 1 three)
- 432 cards swapped their cover to the creator's newest visible image
- 2,966 → **2,573 creators shown**

A creator is only dropped when *all* their images are hidden. Someone with ten images and
one hidden image keeps their card and loses that image.

## Preview-time tag verification

The archive's collection endpoint (`/api/v1/images`) does not return tags at all. Confirmed
against the live API, its per-item keys include the id, URL, dimensions, `nsfwLevel`,
`browsingLevel`, `baseModel`, and stats, but not tags. Tags therefore require a separate
authenticated request.

The app does not delay a full-day build by fetching tags for every archived image. Instead,
cards entering the viewport queue their image IDs into a short batched request. A preview
URL is assigned only after that result is available. If an image carries a hidden tag, it is
removed and the card advances to another verified image; a creator left with no visible
image is removed. Carousel navigation uses the same gate before painting the next image.

This makes the guarantee match what matters to the user: artwork carrying a hidden tag is
not displayed. It also avoids tens of thousands of tag calls for images the user may never
scroll to. A background preparation sweep still collects tags used by the personalized
ranking, and all results share the same persistent cache.

If tag verification itself fails, the app leaves the preview blank and explains that it
could not verify the image against Content Controls. It does not fail open by showing
unverified artwork.

## Saying what was removed

The gallery summary reads:

> 2,573 artists · 26,682 images · showing 50 · **393 hidden by your Civitai settings**

Showing a smaller number with no explanation reads as missing data; advertising a total
that cannot be reached by scrolling reads as a paging bug. During implementation the count
and the gallery genuinely disagreed — the count subtracted hidden creators but not creators
whose every image was hidden — which the regression test now pins.

## Inspecting tags

The image dialog lists the tags Civitai holds for that image, marking any the account
hides. Its data normally comes from the same cache populated before the preview. Three
states are distinguished, because collapsing them would be misleading:

- tags listed, some marked hidden
- read, and genuinely carries no tags
- **not read yet** — said plainly, never rendered as "no tags"

An image that holds tags is treated as read regardless of the bookkeeping table, so the
two facts cannot contradict each other. If preferences changed after a preview passed its
initial check and the detail response newly marks it hidden, only that image is removed
from its card. The feed, loaded pages, ordering, and scroll position stay intact.

## Refresh

Preferences re-import automatically after each sign-in. `GET /api/discovery/hidden?refresh=1`
re-reads them mid-session. Import **replaces** the stored copy rather than merging, so
unhiding something on Civitai propagates here; a merge would silently make hiding
permanent.

Failure to import never blocks sign-in or the gallery: the filter is a refinement, not the
point of connecting. If the mirror cannot be read, the gallery renders unfiltered rather
than erroring.

## Storage

Three tables in `taste.sqlite3` (`hidden_creators`, `hidden_tags`, `hidden_images`) plus a
`hidden_imported_at` stamp. They live in the taste database rather than the archive because
they are account state, not archived artwork, and must vanish with a reset.

Computing which creators still have a visible image requires a pass over the whole block,
so it is cached and invalidated by the import stamp.

## Tests

`tests/content_controls.py` covers: import and resolution, ignoring rows Civitai marks
`hidden: false`, re-import replacing rather than merging, exclusion from all five views,
`blockedByUsers` treated as hidden, a fully-hidden creator dropped, cover falling back to a
visible image, carousel filtering, the count agreeing with what is shown, and the three tag
states in the image dialog. `tests/hidden_tags_before_preview.py` verifies that hidden-tag
artwork never receives a preview URL, checks batched tag lookup, and confirms that a late
detail result removes only the affected image without reloading the feed.
