#!/usr/bin/env bash
set -euo pipefail
CFG="$HOME/.config/forushyar-sip-test"
IMAGE="ghcr.io/ghcr.io/andrius/asterisk:22.11.0_debian-trixie"
for f in "$CFG/pjsip.conf" "$CFG/extensions.conf" "$CFG/rtp.conf" "$CFG/sounds/forushyar-welcome.wav"; do
  if [[ ! -f "$f" ]]; then echo "Missing file: $f" >&2; exit 1; fi
done
if docker container inspect forushyar-sip-test &>/dev/null; then
  echo 'Container forushyar-sip-test already exists. No changes made.' >&2
  exit 1
fi
if ss -lunH | awk '{print $5}' | grep -Eq ':5062$'; then
  echo 'UDP port 5062 is in use. No changes made.' >&2
  exit 1
fi
if ss -lunH | awk '{print $5}' | grep -Eq ':180(0[0-9]|[1-4][0-9]|50)$'; then
  echo 'Potential RTP port conflict in range 18000-18050. No changes made.' >&2
  exit 1
fi
docker pull "$IMAGE"
docker run -d \
  --name forushyar-sip-test \
  --restart no \
  --network host \
  --security-opt no-new-privileges:true \
  -e "PUID=$(id -u)" -e "PGID=$(id -g)" \
  --mount "type=bind,src=$CFG/pjsip.conf,dst=/etc/asterisk/pjsip.conf" \
  --mount "type=bind,src=$CFG/extensions.conf,dst=/etc/asterisk/extensions.conf" \
  --mount "type=bind,src=$CFG/rtp.conf,dst=/etc/asterisk/rtp.conf" \
  --mount "type=bind,src=$CFG/sounds,dst=/var/lib/asterisk/sounds/custom" \
  "$IMAGE"
echo 'Waiting 5 seconds for SIP registration attempt...'
sleep 5
docker exec forushyar-sip-test asterisk -rx 'pjsip show registrations'
