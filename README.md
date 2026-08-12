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

Version `0.2.0-alpha` is functional but under active development. Data formats, UI
behavior, and Civitai integration details may still change. Windows is the primary and
only routinely tested platform.

The underlying application is Python, SQLite, HTML, CSS, and JavaScript. Running from
source on desktop Linux is experimental: secure OAuth storage is implemented through the
desktop's Secret Service keyring, but Linux has not yet been validated on physical test
systems and is not officially supported. The existing packaging scripts remain
Windows-only.

## Major features

- Builds Morning, Evening, or full-day Civitai Red galleries for completed dates.
- Groups all collected images into one carousel per artist and pages 50 artists at a
  time.
- Provides **For you**, **Popular**, **New to you**, **Followed first**, and
  **Emerging first** views.
- Uses reaction history and image tags locally to personalize discovery and explain why
  a creator ranked highly.
- Shows creator avatars and follower counts and highlights emerging creators under 1,000
  followers.
- Supports Like, Heart, Laugh, and Cry reactions plus follow/unfollow when Civitai grants
  social-write access.
- Imports Civitai Content Controls, including hidden creators, hidden images, hidden tags,
  blocked-by accounts, and category switches.
- Defaults to PG and PG-13. R and X/XXX are explicit opt-ins in the Browsing Level
  selector.
- Filters galleries and carousels by generation model.
- Remembers seen creators and moves previously viewed cards later on a fresh visit without
  reshuffling the active page.
- Restores the selected day, day segment, view, model filter, card size, loaded depth, and
  scroll position within the browser session.
- Stops and resumes long collections from SQLite checkpoints.
- Collects lightweight listing data first; full prompts and generation resources are read
  only when an image's detail view is opened.
- Includes a local **My Profile** dashboard for reaction mix, distinctive tags, favorite
  creators, and creators worth following.

## Requirements

- Windows 10 or 11 is the currently tested environment.
- Python 3.10 or newer to run from source. Development currently uses Python 3.14.
- A modern web browser.
- A Civitai account for the personalized gallery, profile analysis, follows, and
  reactions.
- Internet access to Civitai's public REST API, authenticated site API, and image CDN.
- Port `8765` available during OAuth sign-in. The application itself uses an available
  random loopback port.
- On Linux, a desktop Secret Service provider such as GNOME Keyring, running in the same
  D-Bus session and unlocked.

Windows source runs use only the Python standard library. Linux additionally uses the
pinned `keyring` package to access Secret Service. `requirements-dev.txt` includes the
runtime requirements plus the pinned tools used for browser tests and Windows packaging.

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

No configuration file is required. On first launch the app creates its local data folder
and asks you to connect to Civitai.

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

The server listens only on the local computer and opens the application in your default
browser. Use **Close app** in the page to stop it cleanly; closing the browser tab alone
does not stop the Python process.

## Civitai connection and configuration

Public image collection uses `https://civitai.red/api/v1/images`. The endpoint returns at
most 200 listings per request, so a full all-ratings day can require hundreds of
serialized requests. Collection is deliberately sequential, adaptively paced, retried
with backoff, and checkpointed after every page.

Bulk collection always requests lightweight listings with `withMeta=false`. Full prompts
and generation resources are fetched lazily for the image whose detail dialog is opened.
This keeps a large day practical and avoids downloading full metadata for tens of
thousands of images a user may never inspect.

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

Runtime data is stored outside the source tree at:

```text
%LOCALAPPDATA%\CivitaiArtistDiscovery
```

The experimental Linux source path is `~/.local/share/CivitaiArtistDiscovery`. Linux
OAuth tokens are held by Secret Service rather than in that folder.

It can include:

- `history/history.sqlite3`: daily image and creator listings, checkpoints, and metrics;
- `discovery/taste.sqlite3`: reaction-derived taste analysis, seen state, tags, and
  mirrored Content Controls;
- `oauth_tokens.dpapi`: Windows-only DPAPI-encrypted OAuth credentials;
- `oauth_client.json` and `settings.json`: local configuration;
- creator/follow caches, the single-instance marker, and `error.log`.

These files can contain account identifiers, creator names, reaction history, browsing
preferences, image metadata, and diagnostic paths. Do not attach or commit the data
folder when filing an issue.

For isolated development or testing, redirect all runtime data to another directory:

```powershell
$env:CIVITAI_HISTORY_DATA_DIR = "$env:TEMP\civitai-artist-discovery-dev"
py server.py
```

The app never uploads its local taste analysis. It contacts Civitai to collect listings,
read the connected account, and perform user-requested Civitai actions.

## Content levels and collection time

PG and PG-13 are enabled by default. R and X/XXX require explicit opt-in. Every completed
block records its rating coverage; raising the level requests an upgrade scan, while
lowering it immediately hides higher-rated rows already present in the single database.

Collection time depends heavily on rating level, posting volume, latency, and rate
limiting. One measured all-ratings day contained 82,050 retained images and required 458
collection pages plus 34 boundary-seeking pages, completing in about 67 minutes. The safe
default is substantially smaller, but users should still expect multi-minute imports.

Morning and Evening can be built independently. **Build full day** queues both halves,
skips completed halves, resumes partial checkpoints, and opens the locally merged All Day
gallery when both finish.

## Content Controls limitation

Creator- and image-level exclusions are exact for the data the account returns. Tag-level
filtering can act only on images whose tags have been read. Tags are fetched for card
covers and prepared personalized views, but the app intentionally does not fetch tags or
full metadata for every image in a large day. See [docs/content-controls.md](docs/content-controls.md)
for the detailed behavior and limitation.

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

The script generates the icon and Windows version resource, then creates a PyInstaller
folder build under `dist/`. Release packaging is available through
`scripts/release.ps1`. Generated packages and local release backups are intentionally
excluded from Git.

Alpha packages are unsigned, so Windows SmartScreen or managed-device policy may warn or
block them. Review the source and build locally if preferred.

## Contributing and license

Focused contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). The project is
available under the [MIT License](LICENSE).
