# Content Controls

Added 2026-08-05. The app builds its gallery from Civitai's own artwork, so ignoring the
account's Content Controls would put back precisely what the user asked Civitai not to
show them. These are imported and applied by default. The Filters menu has independent local
viewing switches for **Apply my tag filters**, **Exclude hidden and blocked accounts**,
and **Exclude hidden artworks**.

## Local viewing switches

All three switches are on by default, including when upgrading an existing settings file.
Their values persist in `settings.json` as `applyTagFilters`, `applyAccountFilters`, and
`applyImageFilters`.
Turning a switch off pauses that category locally in Daily, Community, and Time Machine views;
turning it back on uses the same imported rules. Reset filters turns all three back on.
These switches never edit Civitai preferences or erase imported rules or archives.

Tag filtering can be paused without disabling account exclusions, and account exclusions
can be paused without disabling hidden tags. The account switch covers hidden creators,
blocked users, and accounts that blocked the viewer. It only affects local filtering of
accessible artwork: it cannot bypass Civitai's access restrictions. The artwork switch controls direct image-hide records. Civitai can create these from
"Hide this image" or a Terms of Service report; its preferences response does not identify
which action created a record. Selected browsing levels and hosted Community access
policy still apply even when all three switches are off.

Civitai also returns implicit image exclusions with a `tagId`. These are retained
separately: personal-tag votes follow the tag switch; rating-tag votes are excluded only
when their rating exceeds the selected browsing ceiling. Moderated tags carrying
`nsfwLevel` are not imported as personal hidden tags.

Implicit image hides with a missing or invalid `tagId`, or a tag absent from the imported
tag metadata, stay excluded as incomplete metadata. Local switches cannot relax these
unclassified hides. A later complete import can resolve their source. Explicitly inactive
hide records are ignored; a known tag marked unhidden is not treated as an unknown source.

Older imports merged direct and implicit hides. Reads continue using the cached mirror
while one background request recovers source information into the existing `sync_state`
table. If Civitai is unavailable, a later read can retry after five minutes. Until recovery
succeeds, image hides whose source is unknown remain excluded even when the artwork
switch is off. This read-only request never edits reports or account preferences.
Fixture mode and isolated test mode never start this automatic recovery, including reads
through Daily and Time Machine routes.

When tag filtering is off, preview-time tag verification is skipped. Details may still
show imported hidden-tag annotations, but those annotations do not remove the image.
When on, unknown tags continue to block previews until verified. Filter changes invalidate
the current gallery and notify other open tabs so they use the saved switches too.

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

Failure to refresh an existing import keeps the cached controls in use. Legacy source
recovery does not block gallery reads or issue another request for every read.

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

`tests/content_control_recovery.py` checks legacy offline reads, concurrent readers,
bounded retries, preserved mirrors, and successful source recovery. The Content Controls
HTTP suite also checks all three switches against Time Machine cards and creator history
without changing saved positions. `tests/community_backend.py` checks exclusion sources
and bounds pair scans for an 8,000-image input rather than relying on a timing threshold.
