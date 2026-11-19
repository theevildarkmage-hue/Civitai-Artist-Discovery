# Docker deployment

The container supports one local user through a browser on the Docker host. It
publishes only `127.0.0.1:8765`, with both the UI and OAuth callback on that port.
Open **http://localhost:8765** (the exact hostname matters). This keeps the built-in
Civitai application's registered callback. A remote browser, reverse proxy,
arbitrary port mapping, and LAN/public hosting are not supported by this configuration.

## Prepare a host bind mount

Install Docker with Compose. On native Linux, choose the numeric UID/GID that
should own the files; normally these are `id -u` and `id -g`. Root IDs are rejected.
The root entrypoint updates one `discovery` account, then `exec gosu discovery`
starts Python. There is no sudo or root parent process left running.

Build the image and prepare directories (replace these example paths):

```bash
docker build -t discovery-container:local .
export WANTED_DIR=`pwd`/discovery-container
mkdir -p $WANTED_DIR/discovery-data $WANTED_DIR/discovery-secrets
chmod 700 $WANTED_DIR/discovery-secrets
# Generate a key directly to a host file, without printing it.
(umask 077; docker run --rm --entrypoint python discovery-container:local \
  -c 'from cryptography.fernet import Fernet; import sys; sys.stdout.buffer.write(Fernet.generate_key())' \
  > $WANTED_DIR/discovery-secrets/credential_key)
export DISCOVERY_DATA_DIR=$WANTED_DIR/discovery-data
export DISCOVERY_KEY_FILE=$WANTED_DIR/discovery-secrets/credential_key
export WANTED_UID=$(id -u)
export WANTED_GID=$(id -g)
docker compose up --build -d
```

The data directory and key file must already exist. Compose refuses to create
missing bind sources. The key must be readable by the mapped identity inside the
container; on Linux prepare its ownership for the selected UID/GID and mode `0400`
or `0600`. Do not regenerate a key for an existing installation. Docker Desktop
and rootless Docker translate ownership differently; verify access on those systems.

Data may be on an external disk. SQLite requires reliable locking and durability;
SMB/NFS storage is not validated. Prefer a local filesystem. Do not share one data
directory between running instances or reuse a Development/Windows profile.

An empty directory is initialized automatically. Existing data is never recursively
chowned by default. If it is not writable, correct the dedicated directory's host
permissions. Explicit `FIX_DATA_OWNERSHIP=true docker compose up -d --force-recreate`
repairs ownership within `/data`, refuses nested mounts, and does not follow
symlinks. With the restricted capability set, directories must be traversable by
initialization for recursive repair; restrictive existing permissions may require
host-side ownership preparation instead. This changes host ownership too. Unset the option after repair. UID
collisions with unrelated image accounts are rejected; existing non-root groups
can be reused. The default umask is `0027`.

## Sign in and preserve credentials

Click **Sign in**, then **Continue to Civitai sign-in**. Authorization opens in a
new host-browser tab. The original tab polls completion. Community reconnect uses
the same link. PKCE and one-time state validation stay server-side; tokens never
reach browser JavaScript. Desktop launches retain their existing OAuth listener
and operating-system credential storage.

Container tokens are encrypted in `/data/oauth_tokens.fernet` using the separate
read-only Fernet key mount. No desktop keyring or plaintext fallback is used.
Startup refuses a missing/invalid key. An incorrect key cannot decrypt credentials;
restore the original key or sign out and authorize again. Encryption protects the
data backup without its key; a compromised running application can read its own
key and credentials.

Back up the data and key separately. Stop the container before copying the entire
data directory so SQLite databases and WAL files form a consistent snapshot.
Restore both to recover the saved authorization. No migration from DPAPI or desktop
Secret Service credentials is performed: sign in afresh in the container.

## Operations and security

```bash
docker compose logs --tail 100
docker compose ps
docker compose stop
docker compose up --build -d
```

Use image replacement for upgrades and `docker compose restart discovery` for
restarts. In-app process restart is rejected in container mode. Close app stops
the server; Compose has no automatic restart policy so it stays stopped until
started explicitly. SIGTERM cancels background work and checkpoints history.

Initialization runs as container root with only CHOWN, SETUID and SETGID
capabilities, then drops to the mapped user. `no-new-privileges` remains enabled.
Health probes also run through gosu as the mapped user.
Account mapping needs a writable container root filesystem; application code is
root-owned. Only `/data` and the application home are prepared for application
writes. The host Docker socket and host desktop D-Bus are never mounted.
Host/Origin guards accept the canonical local endpoint, and the OAuth callback is
the sole cross-site navigation exception, protected by pending state validation.
OAuth callback query strings are excluded from access logs.

## Verification boundaries

See the engineering completion report for the tests run for this change. Automated
OAuth tests use synthetic credentials and callbacks; real Civitai login, Production
enrollment, and remote-service behavior require an interactive owner test.

Focused checks (browser checks require the development dependencies and Chromium):

```bash
python tests/container_runtime.py
python tests/container_docker.py discovery-container:local
python tests/container_browser.py --stable
```

Run the browser check inside an isolated test container: it needs its own port
8765 and uses synthetic token exchange. The Docker lifecycle check creates and
removes its own containers and temporary bind directories; it does not use an
existing installation. The full desktop/native credential regression commands
remain documented in the Linux source guide.
