#!/usr/bin/env bash
set -euo pipefail
CFG="$HOME/.config/forushyar-sip-test"
DIALPLAN="$CFG/extensions.conf"
BACKUP="$CFG/extensions.conf.before-voice-pilot-v02"
if [[ ! -f "$BACKUP" ]]; then
  echo 'Backup not found; no dialplan change made.' >&2
  exit 1
fi
cp -p "$BACKUP" "$DIALPLAN"
docker exec forushyar-sip-test asterisk -rx 'dialplan reload'
docker rm -f forushyar-voice-pilot >/dev/null 2>&1 || true
echo 'Original greeting-only dialplan restored. SIP extension 300 kept registered.'
