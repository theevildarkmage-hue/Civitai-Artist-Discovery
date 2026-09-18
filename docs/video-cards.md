# Video in creator cards

Branch: `feature/video-cards`. Videos appear inside the existing creator cards and
carousel rather than in a separate tab.

## Before shipping

- [x] **Removed the testing boost** (2026-09-18). `VIDEO_CREATORS_FIRST_FOR_TESTING`,
      `videos_first_for_testing`, their call in `/api/history/artists`, and the
      now-unused `HistoryArchive.creator_video_covers` are gone. Views rank as they
      always did, so about 1 in 13 cards opens on a video rather than every card.
- [x] **Removed the stray-dimmed-card diagnostic** (2026-09-18): the `debug` payload on
      `/api/history/seen`, `seen_debug`/`data/seen-debug.jsonl`, and the card dataset
      attributes it needed. It had served its purpose — see "Seen tracking" below.
- [ ] Decide whether the card footer should keep saying "images" for mixed carousels.

## How it works

- **Collection.** Civitai's Newest feed already mixes videos in with images: about 1% of
  PG rows and 11% of XXX rows. The collector used to fetch those rows and throw them away.
  It now keeps them (`ARCHIVED_TYPES`), with no extra requests. Days built before this
  change have no videos until they are rebuilt.
- **Tags.** `tag.getVotableTags` returns tags for a video id when asked with
  `type: "image"`. Civitai rejects `"video"`. Hidden-tag filtering therefore covers
  videos with no changes.
- **Card artwork.** A video card shows a still frame and autoplays, muted, once at least
  half of it has been on screen for 300 ms. Scrolling it away, navigating the carousel, or
  removing the card stops playback and aborts the download. The ▶/❚❚ button pauses a card
  until it leaves the screen. Cards also pause while the details dialog is open, while the
  tab is hidden, and when the user prefers reduced motion (▶ still plays on request).
- **Autoplay cost (Sep 13, Edge).**
  - The first screen streams 3 cards, about 4.5 MB.
  - An idle screen adds nothing once the loops are buffered.
  - A fast flick past about 30 cards starts only the 3 cards it stops on.
  - Reading-pace scrolling costs about 2 MB per video card passed. With the testing boost
    every card is a video, which came to 70–105 MB over 18 seconds. Without it, about 1 in
    13 cards opens on a video (303 of 3,948), so the same scroll would be roughly 7 MB.
- **Details dialog.** Plays the larger file with controls, starting muted.
- **Time Machine.** It keeps its own per-creator store and had always collected the
  videos its listing returned, but stored no type and served every row as an image, so a
  card handed its MP4 to an `<img>` and showed nothing but alt text. `creator_images` now
  carries `type`, set from the listing, and reuses the still and playback transforms
  below. Existing stores are classified by file extension on first open — 2,109 of 90,179
  rows locally — rather than refetching every creator's back catalogue.

## Seen tracking

Two faults surfaced while reviewing this branch. Both dimmed cards the reader never
passed, which is worse than it sounds: a dimmed creator sinks in every later view.

- **Layout shift.** Artwork above a card loading at its real height pulls the card up
  through the top of the viewport, which reads exactly like scrolling past it. A card now
  has to have travelled at least `SEEN_SCROLL_MIN_PX` (24) of *reader* scrolling, measured
  from where it entered, before it counts.
- **Shared scroll position between tabs.** The tabs scroll one page, so opening the
  gallery from a Time Machine scrolled far down landed the reader deep into a day they
  had never seen, and reading on from there dimmed all of it. Each tab now remembers its
  own offset (`viewScroll` in `static/app.js`), restored before seen tracking resumes so
  the observer's first report describes where the reader actually is. Changing the day
  clears the gallery's saved offset, because it belongs to the page being replaced.

## Two failures this branch made visible

Neither was caused by videos; both were made easy to hit by the extra requests and rows.

- **OAuth refresh race.** Civitai rotates refresh tokens, so redeeming one twice returns
  HTTP 400. `get_access_token` refreshed with no lock, so when the gallery fired its
  concurrent requests against an expired token, one thread won and the rest failed —
  surfacing as cards stuck on "Could not verify this image against your Civitai Content
  Controls", scattered rather than contiguous. The refresh now happens under a lock that
  re-reads the stored token before redeeming it.
- **No retry behind that message.** One failed `/api/history/tags` call wrote off every
  card in its batch for the rest of the session. A card now gets one more attempt 1.5s
  later; a failure that persists is still reported.

## CDN measurements (image.civitai.com, Sep 2026)

| Transform | Result |
|---|---|
| `original=true` | The original file, e.g. 30 MB |
| `width=768` | Still an MP4, 8.5 MB. An `<img>` cannot show it, so videos need their own URLs |
| `anim=false,width=N` | JPEG still of about 0.2 MB at any width (used for card and details stills) |
| `transcode=true,width=450,optimized=true` | About 3 MB MP4 (card playback) |
| `transcode=true,width=768,optimized=true` | About 8 MB MP4 (details playback) |

Some videos (about 1 in 9 cards in testing) ignore the transform and redirect to the
original on `blobs-b2.civitai.com`. For those videos, the "still" is a 78 MB MP4 and the
"450 px" file is 27 MB. The browser refuses to put video bytes into an `<img>`, so the still
fails cheaply. The card then shows the video's own first frame (`preload="metadata"`),
which reads the header and first frame only. Playback of those videos streams the larger
file, and it still happens only while someone is watching. `media-src` in the CSP allows
`https://*.civitai.com` for this reason.

## Verified

- `tests/video_cards.py`: collection keeps videos, and the still and playback URLs are
  correct for both cards and the details dialog.
- `tests/time_machine_videos.py`: the same for the Time Machine's own store, images left
  untouched, and the extension backfill for stores written before `type` existed.
- `tests/oauth_refresh_race.py`: eight concurrent callers redeem one refresh token once.
  With the lock stubbed out it reproduces production exactly — seven of eight fail.
- `tests/tag_check_retry.py`: a transient tag-check failure is retried and recovers; a
  persistent one is reported instead of retried forever.
- `tests/view_scroll_position.py`: each tab keeps its own scroll position, and switching
  marks nothing seen.
- Full suite passes.
- Live check in Edge against a copy of real data (Sep 13, 3,254 videos):
  - No playback downloads before a hover.
  - Hover plays after exactly one playback request.
  - Moving to another card stops and releases the first.
  - The details dialog plays and releases on close.
  - Across 74 cards: 0 broken, 8 on the first-frame fallback, and fallback cards play.
