#!/usr/bin/env python3
"""Restores only the pilot source, leaving SIP and the audio fix intact."""
from pathlib import Path
import re
import subprocess
import sys

APP = Path.home() / 'forushyar-AI' / 'forushyar-voice-pilot-v02' / 'voice_gateway.py'
BACKUP = APP.with_name('voice_gateway.py.before_fast_replies_v022')

try:
    result = subprocess.run(['docker', 'exec', 'forushyar-sip-test', 'asterisk', '-rx', 'core show channels count'], text=True, capture_output=True, check=True)
    matched = re.search(r'(\d+) active channels?', result.stdout)
    if not matched or int(matched.group(1)) != 0:
        raise RuntimeError('Call active or unknown count: aborting rollback')
    if not APP.is_file() or not BACKUP.is_file():
        raise RuntimeError('Source or backup missing')
    if 'START FORUSHYAR FAST REPLIES V0.2.2' not in APP.read_text(encoding='utf-8'):
        raise RuntimeError('Current code is not the expected v0.2.2: aborting')
    with APP.open('wb') as f:
        f.write(BACKUP.read_bytes())
    subprocess.run(['docker', 'restart', 'forushyar-voice-pilot'], check=True)
    print('RESTORED pilot source to before v0.2.2. Previous audio fix retained.')
except Exception as exc:
    print('STOP:', type(exc).__name__, str(exc))
    sys.exit(1)
