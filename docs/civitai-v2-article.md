# Civitai Artist Discovery 2.0: a faster, creator-first way to explore

Version 2.0 of Civitai Artist Discovery is here. Since the 1.0 post, the app has grown
well beyond a visual refresh: the gallery, filters, calendar, Time Machine, profile, card
actions, and loading behavior now work as one shared experience.

The app is still built around the same idea: instead of losing artists in an endless
image feed, group a day of Civitai artwork by creator and give every creator one place to
explore their work.

## The new image-first gallery

Artist cards now put the artwork first. Each card contains the artist's images as a
carousel, with reactions, follower information, Follow, image details, and a direct link
back to Civitai. Moving between images now shows immediate progress instead of leaving you
wondering whether the click worked.

The card menu adds two useful Civitai actions: save the image currently on screen to one
of your collections, or add the creator to your Civitai Hidden Users list. The app re-reads
your Civitai Content Controls after a hide, so there is no separate hidden-artist list to
manage locally.

_Screenshot: `release-assets/v2.0.0/gallery.png`_

## Find the day you want

The old previous-day/next-day controls have been replaced with a calendar. Saved days and
partially collected days are visible at a glance, and Morning, Evening, and All day are
selected in the same place. You can jump to the latest saved gallery or rebuild the
selected date without assembling a row of controls every time.

_Screenshot: `release-assets/v2.0.0/calendar.png`_

## Filters without the wall of options

Content levels and generation models now share one compact Filters panel. Model search,
the most useful choices, active filter chips, and Reset make the current state obvious.
Less frequently changed controls moved under the Settings cog: card size, dimming viewed
cards, hiding frequent posters, automatic day collection, update checks, and local profile
data.

_Screenshot: `release-assets/v2.0.0/filters.png`_

## For You and Emerging

For You combines the artwork you react to with a local fingerprint of your public uploads.
Cards explain why they were recommended, such as matching your reactions or being similar
to your work. Emerging uses that same personal ranking while keeping the pool to creators
with fewer than 1,000 followers, making it a discovery view rather than random ordering.

## Time Machine

Time Machine walks through the oldest work of creators you already follow. It now uses the
same image-first cards as the daily gallery, and it remembers progress as you move through
the feed. It is a fun way to see where a familiar creator started and how their work
changed over time.

_Screenshot: `release-assets/v2.0.0/time-machine.png`_

## A clearer profile

My Profile turns your Civitai reaction history into a private, local overview: reaction
mix, distinctive tags, common models, favorite creators, and artists whose work you keep
liking without following. The analysis stays on your computer and is never uploaded by
the app.

_Screenshot: `release-assets/v2.0.0/profile.png`_

## Faster where it matters

Version 2.0 loads a smaller first page, requests responsive previews only as cards approach
the viewport, reuses cached safety decisions, and adds a much faster hidden-tag lookup for
large archives. It also replaces black cards and silent delays with skeletons, progress,
retry actions, and clear empty states. Opening an image at full size now clears the
previous artwork right away and tells you it is loading, so you are never looking at the
last image while wondering whether the new one opened.

Long day collections are still limited by Civitai's public API and remain resumable. The
speed work in 2.0 is focused on opening and filtering galleries you have already saved,
without weakening Content Controls or downloading the day again.

## Updating from 1.x

Packaged 1.x installs can upgrade through the normal in-app updater. It verifies the
download, preserves the portable `data` folder, rolls back if replacement fails, and
restarts the app.

After updating, sign out and back in once if you want to use Hide Artist. Civitai requires
a profile-settings permission for adding creators to Hidden Users, and older sessions do
not contain that permission.

Download, source, release notes, and installation instructions:
https://github.com/theevildarkmage-hue/Civitai-Artist-Discovery/releases/tag/v2.0.0

This is an independent community project and is not affiliated with, endorsed by, or
sponsored by Civitai.
