#!/usr/bin/env python3
from pathlib import Path
import subprocess
ROOT=Path.home()/'forushyar-AI'/'forushyar-voice-pilot-v02'
LIVE=ROOT/'voice_gateway.py'
BACKUP=ROOT/'voice_gateway.py.before_audiofix_v021'
SOUNDS=Path.home()/'.config'/'forushyar-sip-test'/'sounds'
GREETING=SOUNDS/'forushyar-welcome.wav'
GREETING_BACKUP=SOUNDS/'forushyar-welcome.wav.before_audiofix_v021'
if not BACKUP.exists() or not GREETING_BACKUP.exists():
    raise SystemExit('No backup found; no changes made.')
result=subprocess.run(['docker','exec','forushyar-sip-test','asterisk','-rx','core show channels count'],capture_output=True,text=True,check=True)
import re
match=re.search(r'(\d+) active channels?',result.stdout)
if not match or int(match.group(1)) != 0: raise SystemExit('Active call or unknown call count; do not rollback now.')
with LIVE.open('wb') as stream:stream.write(BACKUP.read_bytes())
GREETING.write_bytes(GREETING_BACKUP.read_bytes())
print('Old files restored. Restart ONLY voice pilot: docker restart forushyar-voice-pilot')
