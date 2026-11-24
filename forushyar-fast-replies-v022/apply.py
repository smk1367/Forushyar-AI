#!/usr/bin/env python3
"""Conservative patch: deterministic sales FAQs + faster native Ollama, no PBX changes."""
from pathlib import Path
import os
import re
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
APP = Path.home() / 'forushyar-AI' / 'forushyar-voice-pilot-v02' / 'voice_gateway.py'
BACKUP = APP.with_name('voice_gateway.py.before_fast_replies_v022')
START = 'def answer(transcript: str, history: list[dict[str, str]]) -> str:'
END = '\ndef speak_to_file(text: str, name: str) -> Path:'
OLD_MODEL = 'MODEL = os.getenv("LLM_MODEL", "qwen3.5:4b-q4_K_M")'
NEW_MODEL = 'MODEL = os.getenv("LLM_MODEL", "qwen3:1.7b")'


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True)


def check():
    status = run('docker', 'exec', 'forushyar-sip-test', 'asterisk', '-rx', 'core show channels count').stdout
    print('ASTERISK:', status.strip().replace('\n', ' | '), flush=True)
    matched = re.search(r'(\d+) active channels?', status)
    if not matched or int(matched.group(1)) != 0:
        raise RuntimeError('Active call or unknown channel count; aborting')
    probe = run('docker', 'exec', 'smart-sales-ai-ollama-1', 'ollama', 'list').stdout
    if not re.search(r'^qwen3:1\.7b\s', probe, re.M):
        raise RuntimeError('qwen3:1.7b is missing in Ollama')
    pilot = run('docker', 'inspect', '-f', '{{.State.Running}}', 'forushyar-voice-pilot').stdout.strip()
    if pilot != 'true':
        raise RuntimeError('Voice pilot not running')


def main():
    if not APP.is_file() or BACKUP.exists():
        raise RuntimeError('Source missing or backup exists: refusing to apply twice')
    check()
    old = APP.read_text(encoding='utf-8')
    if old.count(START) != 1 or old.count(END) != 1 or old.count(OLD_MODEL) != 1:
        raise RuntimeError('Unexpected pilot source: no changes made')
    if 'START FORUSHYAR FAST REPLIES' in old:
        raise RuntimeError('Already patched')
    begin = old.index(START)
    finish = old.index(END, begin)
    new = old[:begin] + (HERE / 'replacement.py').read_text(encoding='utf-8') + '\n' + old[finish:]
    new = new.replace(OLD_MODEL, NEW_MODEL, 1)
    compile(new, str(APP), 'exec')
    # Tests run without network and never generate customer data.
    context = {'__name__': 'test_fast_replies'}
    exec(compile(new, str(APP), 'exec'), context)
    cases = [
        ('سلام شما چه محصولاتی دارید؟', 'رابیتس ۹ ستون'),
        ('قیمت امروز ۱۳ ستون چنده؟', 'قیمت روز'),
        ('موجودی انبار دارین؟', 'موجودی'),
        ('فرق ۹ ستون با ۱۳ ستون چیه؟', 'تعداد ستون'),
    ]
    for query, expected in cases:
        actual = context['answer'](query, [])
        assert expected in actual, (query, actual)
    assert context['MODEL'] == 'qwen3:1.7b'
    old_mode = APP.stat().st_mode & 0o777
    BACKUP.write_bytes(APP.read_bytes())
    os.chmod(BACKUP, 0o600)
    try:
        # Docker binds this *file* to /pilot; preserve its inode.
        with APP.open('r+', encoding='utf-8') as f:
            f.seek(0)
            f.write(new)
            f.truncate()
            f.flush()
            os.fsync(f.fileno())
        os.chmod(APP, old_mode)
        run('docker', 'restart', 'forushyar-voice-pilot')
        time.sleep(2)
        run('docker', 'exec', 'forushyar-voice-pilot', 'python', '-B', '/pilot/voice_gateway.py', '--health-check')
    except Exception:
        with APP.open('wb') as f:
            f.write(BACKUP.read_bytes())
        os.chmod(APP, old_mode)
        subprocess.run(['docker', 'restart', 'forushyar-voice-pilot'], capture_output=True, text=True)
        raise
    print('SUCCESS: Fast replies installed; original audio fix preserved.')
    print('MODEL: qwen3:1.7b (only experimental pilot)')
    print('BACKUP:', BACKUP)
    print('NEXT: call extension 300, test product list and pricing')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('STOP:', type(exc).__name__, str(exc))
        sys.exit(1)
