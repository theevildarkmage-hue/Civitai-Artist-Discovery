#!/bin/bash
set -euo pipefail
fail() { echo "Discovery initialization: $*" >&2; exit 1; }
[[ $(id -u) == 0 ]] || fail 'Start the container as root; the entrypoint drops privileges before running Discovery.'
WANTED_UID=${WANTED_UID:-1000}
WANTED_GID=${WANTED_GID:-1000}
for value in "$WANTED_UID" "$WANTED_GID"; do
    [[ $value =~ ^[1-9][0-9]{0,9}$ ]] && (( value < 4294967295 )) || fail 'UID/GID must be positive, non-root numeric IDs below 4294967295.'
done
[[ ${UMASK:-0027} =~ ^0?[0-7]{3}$ ]] || fail 'UMASK must be an octal permission mask.'
umask "${UMASK:-0027}"
[[ ${FIX_DATA_OWNERSHIP:-false} == true || ${FIX_DATA_OWNERSHIP:-false} == false ]] || fail 'FIX_DATA_OWNERSHIP must be true or false.'
[[ ${CIVITAI_HISTORY_DATA_DIR:-/data} == /data ]] || fail 'Container data must be mounted at /data.'
[[ -d /data && ! -L /data ]] || fail 'Mount a dedicated existing data directory at /data.'
existing_user=$(getent passwd "$WANTED_UID" | cut -d: -f1 || true)
[[ -z $existing_user || $existing_user == discovery ]] || fail "UID $WANTED_UID belongs to another account."
existing_group=$(getent group "$WANTED_GID" | cut -d: -f1 || true)
if [[ -z $existing_group ]]; then
    groupmod --gid "$WANTED_GID" discovery
fi
# Prevent usermod from rewriting home contents (which requires FOWNER).
# Account changes and the explicitly bounded directory preparation are separate.
usermod --home /nonexistent discovery
usermod --uid "$WANTED_UID" --gid "$WANTED_GID" --groups '' discovery
usermod --home /home/discovery discovery
chown -h "$WANTED_UID:$WANTED_GID" /home/discovery
# Only initialize an empty mount automatically. Never repair populated host data implicitly.
if [[ ${FIX_DATA_OWNERSHIP:-false} == true ]]; then
    # No symlink traversal. Refuse nested mounts before recursive ownership repair.
    python - <<'PY'
from pathlib import Path
import re
for line in Path('/proc/self/mountinfo').read_text().splitlines():
    mount = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4])
    if mount.startswith('/data/'):
        raise SystemExit('Ownership repair refuses nested mounts under /data')
PY
    find -P /data -xdev -exec chown -h "$WANTED_UID:$WANTED_GID" {} + || fail 'Ownership repair could not traverse existing permissions; prepare ownership on the host.'
elif entries=$(find /data -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null); then
    if [[ -z $entries ]]; then chown -h "$WANTED_UID:$WANTED_GID" /data; fi
fi
export HOME=/home/discovery USER=discovery LOGNAME=discovery
export CIVITAI_HISTORY_DATA_DIR=/data
# Test actual create/delete access after dropping privileges, not root's access.
gosu discovery python - <<'PY'
import tempfile
try:
    with tempfile.NamedTemporaryFile(dir='/data', prefix='.discovery-write-check-'):
        pass
except OSError:
    raise SystemExit('Mapped user cannot write /data. Prepare host permissions or explicitly enable FIX_DATA_OWNERSHIP=true.')
PY
# Selecting the account alone also initializes its supplementary groups.
exec gosu discovery "$@"
