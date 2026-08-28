# Civitai page copy for 1.0.0

Canonical model: https://civitai.red/models/2829529/civitai-artist-discovery

## Model description

### Civitai Artist Discovery — Version 1.0

Civitai Artist Discovery is a free, local, artist-first Windows app for exploring Civitai
Red one day at a time. Instead of another image-first popularity feed, it groups a day's
artwork into one card per creator and helps you discover the people behind the work.

Build Morning, Evening, or a full day; browse For You, Popular, New to You, Followed
First, and Emerging First; filter by model and content level; and react or follow without
losing your place. Your archive, preferences, discovery profile, credentials, and logs
stay in the portable `data` folder on your computer.

#### New in Version 1.0

- One-click, user-approved updates inside the app
- Installed version shown beside the app name
- Release notes and verified download progress before installation
- SHA-256 verification, portable-data preservation, rollback, and automatic restart
- Resumable, boundary-verified Morning, Evening, and full-day collection
- Clear terminal recovery when Civitai remains unavailable
- A rotating diagnostic journal for future API status codes and redacted response details
- Local visual-hash coverage and duplicate metrics without extra API calls
- Personalized artist discovery, Gallery preferences, Content Controls, and My Profile

Version 1.0 begins the stable 1.x line. Going forward, 1.x will focus on compatible bug
fixes and reliability work; a larger interface revamp is planned separately for Version
2. Windows 10 and 11 are the supported packaged platforms.

This is an independent community project and is not affiliated with, endorsed by, or
sponsored by Civitai. The portable Windows package is unsigned, so Windows SmartScreen or
managed-device policy may show a warning.

Source, issue tracker, documentation, and release checksums:
https://github.com/theevildarkmage-hue/Civitai-Artist-Discovery

## Version name

`Red-1.0.0`

## Version notes

Civitai Artist Discovery 1.0.0 is the first stable release. Its headline feature is
secure in-app updating: the app shows its installed version, presents release notes,
downloads only after approval, verifies GitHub's SHA-256 digest, preserves the portable
`data` folder, rolls back a failed replacement, and restarts automatically.

Long gallery builds now use boundary-verified, resumable collection and bounded outage
recovery. Failed Civitai API responses are recorded going forward in a size-limited,
rotating diagnostic journal with status codes, safe headers, redacted request context,
and truncated response excerpts. This release also retains listing visual hashes for
request-free duplicate metrics and includes the complete artist-first discovery,
personalization, Content Controls, Gallery preferences, and profile experience.

Manual beta upgrade: close the old app, extract the entire new portable folder, and move
the old folder's `data` directory into the new `CivitaiArtistDiscovery` folder before
first launch. Keep a backup until the new build opens successfully.

SHA-256: `e568244e3883c61d840eb010402e4c40a0ef0283b19e035da4b7b6f4576ec604`
