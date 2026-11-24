#!/usr/bin/env python3
"""Non-destructive audio resampler fix for Forushyar v0.2, backed up and tested."""
from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parent
APP = Path.home() / 'forushyar-AI' / 'forushyar-voice-pilot-v02'
GATEWAY = APP / 'voice_gateway.py'
BACKUP = APP / 'voice_gateway.py.before_audiofix_v021'
SOUNDS = Path.home() / '.config' / 'forushyar-sip-test' / 'sounds'
GREETING = SOUNDS / 'forushyar-welcome.wav'
GREETING_BACKUP = SOUNDS / 'forushyar-welcome.wav.before_audiofix_v021'
SOURCE = Path.home() / 'forushyar-voice-test.wav'


def check_no_active_calls():
    result = subprocess.run(['docker', 'exec', 'forushyar-sip-test', 'asterisk', '-rx', 'core show channels count'], capture_output=True, text=True, check=True)
    print('Asterisk channel status:', result.stdout.strip().replace('\n', '; '))
    import re
    match = re.search(r'(\d+) active channels?', result.stdout)
    if match is None or int(match.group(1)) != 0:
        raise SystemExit('STOP: calls active or count unknown. Run this when extension 300 is idle.')


def main():
    check_no_active_calls()
    if not GATEWAY.is_file() or not GREETING.is_file():
        raise SystemExit('STOP: pilot source or greeting does not exist.')
    if BACKUP.exists() or GREETING_BACKUP.exists():
        raise SystemExit('STOP: audio-fix backup already exists; not applying twice.')
    content = GATEWAY.read_text(encoding='utf-8')
    start = content.find('def convert_wav_to_8k(')
    finish = content.find('def request_json(',start)
    if start < 0 or finish < 0 or content.count('def convert_wav_to_8k(') != 1:
        raise SystemExit('STOP: unexpected pilot source structure.')
    if 'Band-limited PCM16' in content:
        raise SystemExit('STOP: high-quality resampler already applied.')
    updated = content[:start] + (ROOT/'resampler.txt').read_text(encoding='utf-8') + content[finish:]
    compile(updated, str(GATEWAY), 'exec')
    # Test converted audio *before* changing any running code.
    if not SOURCE.is_file():
        raise SystemExit('STOP: original ~/forushyar-voice-test.wav missing; cannot make a controlled A/B test.')
    import tempfile
    with tempfile.TemporaryDirectory() as dirname:
        temp_module = Path(dirname)/'voice_test.py'
        temp_module.write_text(updated, encoding='utf-8')
        spec = importlib.util.spec_from_file_location('voice_test', temp_module)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        temp_wav = Path(dirname)/'sample.wav'
        module.convert_wav_to_8k(SOURCE.read_bytes(), temp_wav)
        with wave.open(str(temp_wav), 'rb') as wav:
            if wav.getframerate() != 8000 or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                raise SystemExit('STOP: resampler format validation failed')
            print('Converted sample duration: %.2f sec' % (wav.getnframes()/wav.getframerate()))
        # Backups created only after all validation passes.
        BACKUP.write_bytes(GATEWAY.read_bytes())
        os.chmod(BACKUP,0o600)
        GREETING_BACKUP.write_bytes(GREETING.read_bytes())
        os.chmod(GREETING_BACKUP,0o600)
        try:
            # The Python source is a Docker bind-mounted *file*. Preserve inode.
            with GATEWAY.open('r+',encoding='utf-8') as stream:
                stream.seek(0)
                stream.write(updated)
                stream.truncate()
            GREETING.write_bytes(temp_wav.read_bytes())
            os.chmod(GREETING, 0o644)
        except Exception:
            with GATEWAY.open('wb') as stream: stream.write(BACKUP.read_bytes())
            GREETING.write_bytes(GREETING_BACKUP.read_bytes())
            raise
    print('Audio fix applied; old source and old greeting were backed up.')
    print('Now restart ONLY pilot: docker restart forushyar-voice-pilot')


if __name__=='__main__':
    main()
