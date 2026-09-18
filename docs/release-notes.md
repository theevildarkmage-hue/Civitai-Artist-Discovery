# Civitai Artist Discovery 2.2.0

Civitai's feeds have always mixed videos in with images. This release stops throwing them
away: a video now appears in the same cards and carousels as everything else, in the
daily gallery and in the Time Machine.

## Videos appear alongside images

- Collecting a day keeps the videos Civitai's feed already returned. They cost no extra
  requests — the app was fetching those rows and discarding them. Days collected before
  this release contain no videos until they are rebuilt.
- A video card shows a still frame and plays itself, muted, once at least half of it has
  been on screen for a moment. Scrolling it away stops playback and aborts the download,
  so a fast scroll past thirty cards only ever plays the ones you stop on.
- A ▶/❚❚ control on the card overrides either choice. Cards also pause while the details
  dialog is open, while the tab is in the background, and when the system asks for reduced
  motion — where ▶ still plays on request.
- The details dialog plays the larger file with controls, starting muted.
- Hidden-tag filtering, reactions, follows, and collections work on a video exactly as
  they do on an image.
- The Time Machine shows videos too, including in creators' back catalogues it had
  already collected. Nothing is refetched to make that work.

## Find one creator in the Time Machine

- A search box lists the creators it already tracks, so typing never asks Civitai
  anything. Choosing one shows only their work.
- It opens at the same place the one-card-per-creator walk had reached, and reading here
  moves that place too, so neither view makes you scroll through what you have seen.

## Fixes

- Cards no longer show "Could not verify this image against your Civitai Content
  Controls" in place of artwork when several parts of the gallery load at once. The
  authorization was being renewed by each of them separately, and all but one of those
  renewals failed.
- A card whose content check fails once now tries again before giving up, instead of
  staying blank for the rest of the session.
- Artwork loading above a card no longer counts as you having scrolled past it. Cards
  below what you were reading could be marked as seen — and so buried in later views —
  purely because the page grew above them.
- Each tab keeps its own scroll position. Switching from a Time Machine scrolled far down
  used to open the gallery at the same depth, in the middle of a day you had never seen,
  and reading on from there marked all of it as seen.

## Updating

Packaged versions 1.0.0 through 2.1.0 can install 2.2.0 through the normal in-app update
dialog. The updater verifies the GitHub-provided SHA-256 digest, preserves the portable
`data/` folder, rolls back a failed replacement, and restarts automatically.

The release asset is `CivitaiArtistDiscovery-2.2.0.zip`. Its SHA-256 is recorded here and
in the accompanying checksum file once the asset is built, and is verified automatically
by the app.

Windows 10 and 11 remain the packaged and routinely tested platforms. The package is
unsigned, so Windows SmartScreen or managed-device policy may warn or block it.
