# Civitai page copy for 2.0.0

## Version 2.0 announcement

Version 2.0 puts artwork first while keeping one card per creator.
Daily Gallery, Time Machine, and My Profile share a consistent layout. Common content
and model choices live in one Filters panel, with active chips and a simple reset;
less-used settings live under one cog. Loading improvements include
smaller first pages, appropriately sized previews, and faster local content checks.
The calendar, card collection actions, Civitai Hidden Users integration, personalized
Emerging view, and visible loading/retry feedback are also new in the stable 2.0 release.

Canonical model: https://civitai.red/models/2829529/civitai-artist-discovery

## Model description

### Civitai Artist Discovery — Version 2.0

Civitai Artist Discovery is a free, local, artist-first Windows app for exploring Civitai
Red one day at a time. Instead of another image-first popularity feed, it groups a day's
artwork into one card per creator and helps you discover the people behind the work.

Build Morning, Evening, or a full day; browse For You, Popular, New to You, Followed
First, and Emerging First; filter by model and content level; and react or follow without
losing your place. Your archive, preferences, discovery profile, credentials, and logs
stay in the portable `data` folder on your computer.

#### New in Version 2.0

- Image-first creator cards shared by Daily Gallery and Time Machine
- A calendar showing saved and partially collected Morning, Evening, and All day galleries
- Combined content/model filters with search, active chips, and safe reset
- A simplified Settings panel for appearance, frequent posters, collection, and local data
- Personalized For You explanations and an under-1,000-follower Emerging view
- Save-to-collection and Civitai Hidden Users actions on every artist card
- A redesigned private taste profile and clearer loading, retry, and empty states
- Faster repeat gallery opening through responsive previews, lazy loading, and cached checks

Packaged 1.x installs can update through the normal in-app updater without moving their
portable data. Windows 10 and 11 are the supported packaged platforms.

This is an independent community project and is not affiliated with, endorsed by, or
sponsored by Civitai. The portable Windows package is unsigned, so Windows SmartScreen or
managed-device policy may show a warning.

Source, issue tracker, documentation, and release checksums:
https://github.com/theevildarkmage-hue/Civitai-Artist-Discovery

## Version name

`Red-2.0.0`

## Version notes

Civitai Artist Discovery 2.0.0 is the current stable release. It introduces a shared,
image-first interface across Daily Gallery and Time Machine, calendar navigation,
streamlined filtering and settings, richer Civitai card actions, personalized Emerging
discovery, and a redesigned local taste profile.

Packaged 1.x installs receive this release through the normal in-app updater. Downloads
remain user-approved and SHA-256 verified; the portable `data/` folder is preserved,
failed replacement rolls back, and the app restarts automatically.

Existing users should sign out and back in once if they want to add artists to Civitai
Hidden Users from a card. Civitai requires a profile-settings permission that older OAuth
sessions do not contain.

SHA-256: `0783c3f1f78f0262cb60a0cb32bba17f21d6ccef03eb1ad3a761c188436c9ee8`
