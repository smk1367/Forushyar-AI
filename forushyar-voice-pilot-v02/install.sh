#!/usr/bin/env bash
# Activates only the isolated SIP test dialplan; no changes to the production Compose stack.
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="$HOME/.config/forushyar-sip-test"
DIALPLAN="$CFG/extensions.conf"
BACKUP="$CFG/extensions.conf.before-voice-pilot-v02"
NETWORK="${FORUSHYAR_DOCKER_NETWORK:-smart-sales-ai_private}"
CONTAINER="forushyar-voice-pilot"

[[ -d "$CFG/sounds" && -f "$DIALPLAN" ]] || { echo 'Missing v0.1 SIP config; do not proceed.' >&2; exit 1; }
docker container inspect forushyar-sip-test >/dev/null 2>&1 || { echo 'SIP test container must be running.' >&2; exit 1; }
docker network inspect "$NETWORK" >/dev/null 2>&1 || { echo "Missing Docker network $NETWORK" >&2; exit 1; }
if docker container inspect "$CONTAINER" &>/dev/null; then echo "$CONTAINER already exists. Use rollback.sh before retrying." >&2; exit 1; fi
if ss -lntH | awk '{print $4}' | grep -Eq ':4573$'; then echo 'TCP 4573 already in use.' >&2; exit 1; fi

# Use the EXACT Python image that currently runs the API; no registry download.
API_IMAGE="$(docker inspect --format '{{.Image}}' smart-sales-ai-api-1)"
[[ -n "$API_IMAGE" ]] || { echo 'Cannot identify existing API image.' >&2; exit 1; }

# Only accept the known test dialplan, never silently replace custom production routing.
python3 - "$DIALPLAN" <<'PY'
from pathlib import Path
import sys
body=Path(sys.argv[1]).read_text()
if 'Playback(custom/forushyar-welcome)' not in body or 'exten => 300,1' not in body or 'AGI(agi://' in body:
    raise SystemExit('Dialplan is not the expected v0.1 pilot; aborting without changes.')
PY

echo 'Starting FastAGI pilot on local TCP 4573 ...'
docker run -d \
  --name "$CONTAINER" \
  --restart no \
  --network "$NETWORK" \
  --publish '127.0.0.1:4573:4573/tcp' \
  --user "$(id -u):$(id -g)" \
  --read-only \
  --tmpfs '/tmp:rw,size=32m,mode=1777' \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount "type=bind,src=$APP_DIR/voice_gateway.py,dst=/pilot/voice_gateway.py,readonly" \
  --mount "type=bind,src=$CFG/sounds,dst=/voice-sounds" \
  --entrypoint python \
  "$API_IMAGE" -B -u /pilot/voice_gateway.py

if ! docker exec "$CONTAINER" python -B /pilot/voice_gateway.py --health-check; then
  echo 'AI services unavailable. Keeping old dialplan unchanged.' >&2
  docker rm -f "$CONTAINER" >/dev/null || true
  exit 1
fi

# Create the one-and-only original backup, then make an atomic dialplan edit.
if [[ -e "$BACKUP" ]]; then
  echo "Existing backup: $BACKUP; aborting to prevent backup overwrite" >&2
  docker rm -f "$CONTAINER" >/dev/null || true
  exit 1
fi
cp -p "$DIALPLAN" "$BACKUP"
chmod 600 "$BACKUP"
python3 - "$DIALPLAN" <<'PY'
from pathlib import Path
import os, sys
p=Path(sys.argv[1]); old=p.read_text()
needle='same => n,Playback(custom/forushyar-welcome)'
if old.count(needle) != 1:
    raise SystemExit('Unexpected Playback count; abort')
new=old.replace(needle, 'same => n,AGI(agi://127.0.0.1:4573/voice)')
# Asterisk has a FILE bind mount: do NOT os.replace(), which changes the inode.
# Truncate and write the same inode so 'dialplan reload' sees the change.
with p.open('r+', encoding='utf-8') as fh:
    fh.seek(0)
    fh.write(new)
    fh.truncate()
os.chmod(p, 0o600)
PY

if ! docker exec forushyar-sip-test asterisk -rx 'dialplan reload'; then
  echo 'Dialplan reload failed. Rolling back...' >&2
  cp -p "$BACKUP" "$DIALPLAN"
  docker exec forushyar-sip-test asterisk -rx 'dialplan reload' || true
  docker rm -f "$CONTAINER" >/dev/null || true
  exit 1
fi

echo '===== VERIFY ACTIVE DIALPLAN ====='
ACTIVE=$(docker exec forushyar-sip-test asterisk -rx 'dialplan show 300@from-ucm')
if ! grep -q 'AGI(agi://127.0.0.1:4573/voice)' <<<"$ACTIVE"; then
  echo 'New dialplan not visible inside Asterisk. Rolling back...' >&2
  cp -p "$BACKUP" "$DIALPLAN"
  docker exec forushyar-sip-test asterisk -rx 'dialplan reload' || true
  docker rm -f "$CONTAINER" >/dev/null || true
  exit 1
fi
echo "$ACTIVE"
echo 'Rollback: bash rollback.sh'
