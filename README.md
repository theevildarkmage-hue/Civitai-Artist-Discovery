# Civitai Artist Discovery

Civitai Artist Discovery is a local, artist-first browser for finding creators on
[Civitai](https://civitai.red). Instead of presenting another image-first popularity
feed, it groups a calendar day's artwork by creator and can order those creators using
the artwork you have reacted to.

The project's purpose is to make artist discovery on Civitai easier: surface creators
whose work matches your interests, show emerging artists, and make it simple to explore
their images, react, and follow without losing your place in a very large daily feed.

This is an independent community project. It is not affiliated with, endorsed by, or
sponsored by Civitai.

## Project status

Version `1.0.1` is the current stable release. The 1.x line focuses on compatibility and
bug fixes; a broader interface redesign is planned separately for 2.x. Windows is the
primary and only routinely tested platform.

See the [1.0 release notes](docs/release-notes.md) for the release highlights.

The underlying application is Python, SQLite, HTML, CSS, and JavaScript. Running from
source on desktop Linux is experimental: secure OAuth storage is implemented through the
desktop's Secret Service keyring, but Linux has not yet been validated on physical test
systems and is not officially supported. The existing packaging scripts remain
Windows-only.

## Major features

- Builds Morning, Evening, or full-day Civitai Red galleries for completed dates.
- Groups all collected images into one carousel per artist and pages 50 artists at a
  time.
- Offers Large, Medium, and Small preview cards whose image, header, controls, and
  typography scale together.
- Provides **For you**, **Popular**, **New to you**, **Followed first**, and
  **Emerging first** views.
- Ranks **Popular** by the sum of reactions across every visible image each artist posted
  that day, and opens each card on that artist's most-reacted image.
- Uses reaction history plus a cached fingerprint of the account's public upload history
  to personalize discovery and explain why a creator ranked highly. The initial upload
  scan is paginated once; later refreshes stop at known images and store only new uploads.
- Treats an upload-history tag as strong only when it recurs and is at least 50% more common
  than in the local Civitai comparison sample, preventing generic and one-off tags from
  driving recommendations.
- Blends two creators similar to the user's uploaded work, one familiar favorite, and one
  emerging match in **For you**. Reaction-taste matches fill any unavailable lane.
- Clearly marks **For you** as preliminary while the background tag analysis is still
  preparing a gallery, shows its progress, and refreshes the ranking automatically when
  the tag-backed personalization is ready.
- Shows creator avatars and follower counts and highlights emerging creators under 1,000
  followers.
- Collects secondary display controls under **Gallery preferences**: viewed-card dimming,
  an optional 50/100/200-image daily limit, and Balanced, Strict, or original ordering
  for **Emerging first**. These preferences re-filter saved data without another download.
- Supports Like, Heart, Laugh, and Cry reactions plus follow/unfollow when Civitai grants
  social-write access.
- Imports Civitai Content Controls, including hidden creators, hidden images, hidden tags,
  blocked-by accounts, and category switches.
- Defaults to PG and PG-13. PG, PG-13, R, X, and XXX can be displayed independently;
  mature levels are explicit opt-ins.
- Filters galleries and carousels by generation model.
- Remembers seen creators and moves previously viewed cards later on a fresh visit without
  reshuffling the active page. The visual dimming can be disabled independently without
  clearing or weakening that discovery history.
- Restores the selected day, day segment, view, model filter, card size, loaded depth, and
  scroll position within the browser session.
- Stops and resumes long collections from SQLite checkpoints.
- Collects lightweight listing data first; full prompts and generation resources are read
  only when an image's detail view is opened.
- Retains Civitai's listing-provided visual hash for newly collected images, allowing
  duplicate and cross-creator repost rates to be measured locally without extra API calls.
- Retries the original artwork when a generated Civitai CDN preview is unavailable.
- Includes a local **My Profile** dashboard for the creative fingerprint, dominant model
  signals, reaction mix, distinctive tags, favorite creators, and creators worth following.
  Worth Following starts after reactions to 10 distinct images from an unfollowed artist;
  gallery hearts use a lighter 5-image threshold and also disappear once the artist is followed.
- Refreshes My Profile automatically when its last successful Civitai read is more than
  24 hours old, deferring while a daily gallery is being built. Known tags and upload
  fingerprint pages are reused; the reacted-image listing is still reconciled for accuracy.
- Keeps application-managed databases, configuration, caches, credentials, and logs in
  a portable `data/` folder beside the executable.
- Shows a Windows notification-area icon with **Open** and **Exit** while the local server
  is running.
- Checks the project's GitHub releases once per day and offers an in-app, user-approved
  update with release notes, verified download progress, portable-data preservation,
  rollback on installation failure, and automatic restart. Automatic checks can be
  disabled in **My Profile**.

## Requirements

- Windows 10 or 11 is the currently tested environment.
- Python 3.10 or newer to run from source. Development currently uses Python 3.14.
- A modern web browser.
- A Civitai account for the personalized gallery, profile analysis, follows, and
  reactions.
- Internet access to Civitai's public REST API, authenticated site API, and image CDN.
- Internet access to GitHub's public release API and release downloads when update
  checking or installation is enabled; no GitHub account or token is required.
- Port `8765` available during OAuth sign-in. The application itself uses an available
  random loopback port.
- On Linux, a desktop Secret Service provider such as GNOME Keyring, running in the same
  D-Bus session and unlocked.

Windows uses the conditionally installed Pillow and pystray packages for its notification-
area icon. Linux does not install or import the Windows tray integration; it additionally
uses the pinned `keyring` package to access Secret Service. `requirements-dev.txt` includes
the runtime requirements plus the pinned tools used for browser tests and packaging.

## Installation from source

```powershell
git clone https://github.com/theevildarkmage-hue/Civitai-Artist-Discovery.git
cd Civitai-Artist-Discovery
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
py -m pip install -r requirements-dev.txt
py -m playwright install chromium
```

Playwright and Chromium are required only for the browser test suite. PyInstaller and
Pillow are required only for packaging. On Windows, the runtime requirements file does
not install any package. On Linux, it installs `keyring` and its Secret Service support.

No configuration file is required. On first launch the app creates `data/` beside the
executable or source checkout and asks you to connect to Civitai. Extract packaged builds
to a user-writable folder before launching them.

## Launching

From PowerShell:

```powershell
.\scripts\start.ps1
```

Or directly:

```powershell
py server.py
```

Experimental Linux source launch:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python server.py
```

The executable is a local Python/SQLite server; the browser tab is its interface rather
than a standalone webpage. The server listens only on the local computer and opens the
application in your default browser. On Windows, its notification-area icon remains
visible while it runs and provides **Open** and **Exit**. Use **Close app** in the page or
**Exit** in that menu to stop it cleanly; closing the browser tab alone does not stop the
Python process.

## Civitai connection and configuration

Public image collection uses `https://civitai.red/api/v1/images`, the public API Civitai
expressly provides for automated access. The endpoint returns at most 200 listings per
request, so a full all-ratings day can require hundreds of serialized requests.
Collection is deliberately sequential, adaptively paced, retried with backoff, and
checkpointed after every page. The app collects each required Civitai browsing-level feed
separately, combines duplicate image IDs locally, and marks a block ready only after
every feed has crossed the requested time boundary. If Civitai stops a feed early, the
block remains unfinished with saved progress instead of publishing a truncated gallery.

Bulk collection always requests lightweight listings with `withMeta=false`. Full prompts
and generation resources are fetched lazily for the image whose detail dialog is opened.
This keeps a large day practical and avoids downloading full metadata for tens of
thousands of images a user may never inspect.

### How far back a day can be built

Civitai's public API caps cursor traversal at roughly offset 49,000 and offers no date
filter, so the reachable history is bounded by row count rather than by time. A
high-volume browsing level therefore reaches back only about two days while a quiet one
reaches five, and because a block requires every level it needs, the most restrictive one
decides. `HistoryArchive.feed_floor` probes each required level with a single one-row
request before collecting, and `history_window` reports the binding floor and the oldest
day still buildable; `GET /api/history/window` exposes it to the build screen. Days
already collected stay viewable regardless -- the limit applies only to new collection.

### Why not Civitai's search service

The site's own image browser is backed by a search index that accepts an exact date range
and returns 1,000 listings per request, which would make collection roughly seven times
faster. The app does not use it. Civitai's Terms of Service (11.4) permit automated access
only through "interfaces we expressly provide for automated access, such as our public API
or official Model Context Protocol (MCP) server, in each case accessed with your own valid
credentials." That index is neither expressly provided for automated access nor reachable
with the user's own credentials -- it is reached with the search key Civitai's frontend
ships to browsers. Speed is not a reason to use a credential that was not issued to you.

`discovery/search.py` and `CIVITAI_HISTORY_BACKEND=search` remain in the tree for local
diagnosis of the v1 collector only. They are not a supported configuration, are off by
default, and should not be enabled in a distributed build.

Sign-in uses OAuth 2.0 Authorization Code with PKCE. The source contains a public OAuth
client identifier, not a client secret. Developers can use their own public Civitai OAuth
application by setting:

```powershell
$env:CIVITAI_OAUTH_CLIENT_ID = "your-public-client-id"
py server.py
```

Register `http://localhost:8765/oauth/callback` exactly as the redirect URI. A client ID
can also be entered through the first-launch UI. Access and refresh tokens are protected
with Windows DPAPI on Windows or stored in the desktop Secret Service keyring on Linux.
The app deliberately refuses plaintext or unavailable Linux keyring backends. Tokens are
never sent to browser JavaScript or written to application logs.

The authenticated integration reads reaction history, follows, creator profiles, tags,
and the account's Content Controls. Follow and reaction writes occur only when the user
clicks the corresponding control. Civitai endpoints and response shapes are external
dependencies and may change independently of this project.

## Local data and privacy

All application-managed runtime data is stored portably at:

```text
<application folder>\data
```

An upgrade from an earlier build moves `%LOCALAPPDATA%\CivitaiArtistDiscovery` into this
folder when possible. If permissions prevent the move, the older location remains active
for that launch so existing archives and credentials are not hidden. Linux OAuth tokens
remain in Secret Service rather than `data/`, because the app refuses to store them as
portable plaintext.

It can include:

- `history/history.sqlite3`: daily image and creator listings, visual hashes, checkpoints,
  and collection metrics;
- `discovery/taste.sqlite3`: reaction-derived taste analysis, seen state, tags, and
  mirrored Content Controls;
- `oauth_tokens.dpapi`: Windows-only DPAPI-encrypted OAuth credentials;
- `oauth_client.json` and `settings.json`: local configuration, including Gallery preferences;
- creator/follow caches, the single-instance marker, and `error.log`;
- `api-failures.jsonl`: a size-limited, rotating journal of failed Civitai API status
  codes, safe response headers, request context, and truncated response excerpts. Common
  credential-shaped fields are redacted before the entry is written.
- `update/`: the cached daily release check and, only while an approved update is being
  prepared, its verified archive, staging folder, rollback copy, and result receipt.

The application does not create Registry settings. These files can contain account
identifiers, creator names, reaction history, browsing preferences, image metadata, API
response excerpts, and diagnostic paths. Do not attach or commit the data folder when
filing an issue; review and share only the specific diagnostic entries that are needed.

For isolated development or testing, redirect all runtime data to another directory:

```powershell
$env:CIVITAI_HISTORY_DATA_DIR = "$env:TEMP\civitai-artist-discovery-dev"
py server.py
```

The app never uploads its local taste analysis. It contacts Civitai to collect listings,
read the connected account, and perform user-requested Civitai actions.

## Content levels and collection time

PG and PG-13 are enabled by default. R, X, and XXX require explicit opt-in. Each image's
individual browsing level is stored with its lightweight listing, so any non-empty
combination can be displayed locally—for example, only PG-13 or only R. Collection uses
non-overlapping browsing-level feeds: safe coverage uses PG/PG-13; through-R coverage
adds R; and all-ratings coverage also collects X and XXX independently. Results are
deduplicated by image ID. Every completed block records that coverage; selecting a level
above it requests an upgrade scan, while changing the visible levels within existing
coverage filters the local database immediately.

Collection time depends heavily on rating level, posting volume, latency, and rate
limiting. The fixed all-ratings benchmark contains 82,050 retained images and 458
collection pages. The boundary-verified collector budgets up to 170 date-seeking requests
across five feeds and two halves, producing an estimated range of roughly 52 to 77 minutes.
Preserved
PG/PG-13 measurements required 69–84 collection pages for a half day and 140–147 for a
full day, so even a clean safe half is a multi-minute operation.

The build screen uses fixed capacity benchmarks rather than learning from runtime rows,
which may describe partial or resumed work. The low end applies the known five-second API
cadence; the high end applies the delayed request cycle from the hour-long all-ratings
run. Coverage and time-range selections choose the corresponding fixed image and request
counts, while a half already complete at the requested coverage is omitted.

Each block records wall-clock time, seek/collection page counts, transferred and decoded response bytes,
date-seeking and organization time, deliberate API pacing time, response/read time, retry
wait time, separate rate-limit/service/network retry counts, and the final adaptive pacing
interval. These measurements distinguish Civitai request cadence from local processing and
raw transfer volume; they contain no artwork files or personal credentials.

Morning and Evening can be built independently. The build screen reports each half as
ready, unfinished with saved progress, or not built. **Full day** queues both halves,
skips halves already complete at the requested coverage, resumes partial checkpoints, and
opens the locally merged All Day gallery when both finish. If only one half exists, it
remains viewable and the gallery offers **Complete this day** rather than hiding it behind
the collection screen. Collection coverage and later viewing filters are presented as
separate concepts; viewing filters never redownload an already-covered level.

## Content Controls

Creator- and image-level exclusions are applied from the account data Civitai returns.
Because the daily listing feed does not include tags, the app verifies tags in small
batches as artwork approaches the screen and before assigning its preview URL. An image
matching a hidden Civitai tag is removed without being displayed; the same check is made
before moving through a creator's carousel. Opening image details does not rebuild or
reset the feed. See [docs/content-controls.md](docs/content-controls.md) for the detailed
behavior and API tradeoff.

## Testing

Tests are existing standalone Python scripts rather than a separate test framework. They
use temporary data directories and synthetic records.

```powershell
Get-ChildItem .\tests\*.py | ForEach-Object {
  py $_.FullName
  if ($LASTEXITCODE) { throw "Test failed: $($_.Name)" }
}
```

Some tests start local servers and Chromium. If a run is interrupted, check for a leftover
Python test process before running the suite again.

## Building the Windows package

```powershell
.\scripts\build_exe.ps1
```

The script generates the icon and Windows version resource, then creates a portable
PyInstaller folder build under `dist/`; its `data/` folder is created on first launch.
Release packaging is available through
`scripts/release.ps1`. Generated packages and local release backups are intentionally
excluded from Git.

The packaged app's updater expects the release asset name
`CivitaiArtistDiscovery-<version>.zip`, matching the version in `server.py`, and requires
the SHA-256 digest GitHub records for the uploaded asset. Draft releases, missing or
renamed packages, unsigned digest metadata, and downloads outside this repository are
ignored. See [docs/updates.md](docs/updates.md) for the complete update and recovery flow.

One-file builds are intentionally disabled: they extract their runtime into the Windows
temporary folder and therefore do not meet this project's portable-only behavior. Windows
packages include the tray libraries' license texts and [third-party notices](THIRD_PARTY_NOTICES.md).

Windows packages are unsigned, so Windows SmartScreen or managed-device policy may warn or
block them. Review the source and build locally if preferred.

## Contributing and license

Focused contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). The project is
available under the [MIT License](LICENSE).
