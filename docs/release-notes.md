# Civitai Artist Discovery 1.0.0

Version 1.0.0 is the first stable release of Civitai Artist Discovery, a local,
artist-first browser that groups a day of Civitai Red artwork by creator and helps people
find artists whose work matches their interests.

## Highlights

- **In-app updates:** packaged builds check the project's GitHub releases daily and show
  the installed version beside the app name. Updates remain user-approved, display their
  release notes and progress, verify GitHub's SHA-256 digest, preserve portable data,
  roll back a failed replacement, and restart automatically.
- **Reliable long collections:** Morning, Evening, and full-day builds are checkpointed
  after every page. Independent rating-level feeds must cross the requested date boundary
  before a gallery is marked complete, preventing an early end-of-feed response from
  silently publishing a partial day.
- **Bounded recovery and useful diagnostics:** temporary Civitai failures are retried with
  adaptive pacing, but a persistent outage now reaches a clear terminal state instead of
  repeating the same retry cycle forever. Future failed API responses are recorded in a
  rotating `data/api-failures.jsonl` journal with status codes, safe headers, request
  context, and redacted response excerpts.
- **Artist-first personalization:** For You, Popular, New to You, Followed First, and
  Emerging First views combine reaction history, public upload fingerprints, follower
  data, and volume-resistant gallery preferences.
- **Local archive insight:** newly collected listings retain Civitai's visual hash so
  duplicate uploads, cross-creator copies, and cross-day copies can be measured locally
  without more API traffic.
- **Portable by design:** databases, settings, encrypted Windows credentials, caches,
  logs, and update working files remain in `data/` beside the application. The app does
  not create Registry settings.

## What stable means

The 1.x line is intended for backward-compatible fixes and reliability improvements.
Existing beta archives, taste data, preferences, and credentials remain supported. A
larger interface redesign is planned as a separate 2.x effort rather than being mixed
into 1.x maintenance.

Windows 10 and 11 are the packaged and routinely tested platforms. Linux source use
remains experimental. The Windows package is unsigned, so SmartScreen or managed-device
policy may warn or block it.

## Upgrading from a beta

Users on a packaged build with the in-app updater can install 1.0.0 from the update
dialog. For a manual upgrade, close the old app, extract the entire new portable folder,
and move the old folder's `data` directory into the new `CivitaiArtistDiscovery` folder
before first launch. Keep a backup of `data` until the new build has opened successfully.

The exact release asset is `CivitaiArtistDiscovery-1.0.0.zip`. Its SHA-256 is
`e568244e3883c61d840eb010402e4c40a0ef0283b19e035da4b7b6f4576ec604`; it is also
published beside the download and verified automatically by the in-app updater.

## Release policy

Stable installations ignore prerelease GitHub releases. Existing prerelease builds can
see both newer previews and the eventual stable release, so beta users can upgrade to
1.0.0 normally without moving stable users onto a preview channel.

Earlier beta milestones and their migration details remain documented in
[beta-release.md](beta-release.md).
