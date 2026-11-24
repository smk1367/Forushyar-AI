#!/usr/bin/env python3
"""Configure isolated Asterisk SIP extension 300 and a prerecorded test greeting."""
from __future__ import annotations
import getpass
import ipaddress
import os
import re
import socket
import sys
import wave
from array import array
from pathlib import Path
from urllib.parse import urlsplit

HOME = Path.home()
PROJECT = HOME / 'forushyar-AI'
PRIVATE = HOME / '.config' / 'forushyar-sip-test'
RATE = 8000

def pbx_default() -> str:
    p = PROJECT / '.env'
    if not p.is_file():
        return ''
    for line in p.read_text(errors='replace').splitlines():
        if line.startswith('UCM_API_URL='):
            value = line.split('=', 1)[1].strip().strip("'\"")
            try:
                return urlsplit(value).hostname or ''
            except ValueError:
                return ''
    return ''

def ask_ip(prompt: str, default: str='') -> str:
    while True:
        entered = input(f'{prompt}' + (f' [{default}]' if default else '') + ': ').strip() or default
        try:
            parsed = ipaddress.ip_address(entered)
            if parsed.version != 4 or not parsed.is_private:
                raise ValueError()
            return str(parsed)
        except ValueError:
            print('Please enter a private IPv4 address (e.g. 192.168.x.x).')

def ask_port(prompt: str, default: int) -> int:
    while True:
        text = input(f'{prompt} [{default}]: ').strip() or str(default)
        try:
            port = int(text)
            if not (1 <= port <= 65535):
                raise ValueError()
            return port
        except ValueError:
            print('Invalid port.')

def routed_ip(pbx_ip: str, port: int) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((pbx_ip, port))
        return sock.getsockname()[0]

def write_private(name: str, text: str) -> None:
    path = PRIVATE / name
    tmp = PRIVATE / (name + '.tmp')
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        output.write(text)
    os.replace(tmp, path)
    os.chmod(path, 0o600)

def convert_wav(src: Path, dest: Path) -> None:
    with wave.open(str(src), 'rb') as inp:
        nch = inp.getnchannels()
        width = inp.getsampwidth()
        rate = inp.getframerate()
        if nch not in (1, 2) or width != 2 or rate < 8000 or inp.getcomptype() != 'NONE':
            raise ValueError('Expected uncompressed signed-16-bit mono/stereo WAV at >=8kHz')
        frames = inp.readframes(inp.getnframes())
    raw = array('h')
    raw.frombytes(frames)
    if sys.byteorder != 'little':
        raw.byteswap()
    n = len(raw) // nch
    if n < 2:
        raise ValueError('Audio too short')
    result = array('h')
    out_n = round(n * RATE / rate)
    for i in range(out_n):
        pos = i * rate / RATE
        left = min(int(pos), n-1)
        right = min(left+1, n-1)
        frac = min(1.0, pos - left)
        a = sum(raw[left*nch+c] for c in range(nch)) / nch
        b = sum(raw[right*nch+c] for c in range(nch)) / nch
        result.append(max(-32768, min(32767, round(a + (b-a)*frac))))
    if sys.byteorder != 'little':
        result.byteswap()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(result.tobytes())
    os.chmod(dest, 0o644)


def main() -> None:
    audio = HOME / 'forushyar-voice-test.wav'
    if not audio.exists():
        print(f'Missing greeting: {audio}')
        raise SystemExit(2)
    pbx_ip = ask_ip('Grandstream PBX SIP IP', pbx_default())
    pbx_port = ask_port('Grandstream SIP UDP port', 5060)
    server_ip = ask_ip('Local server IP reachable from Grandstream', routed_ip(pbx_ip, pbx_port))
    listen_port = ask_port('Forushyar gateway SIP UDP port', 5062)
    sip_secret = getpass.getpass('SIP password for extension 300 (not web login): ')
    if not re.fullmatch(r'[A-Za-z0-9!@#$%^&*()_+=.\-]{8,128}', sip_secret):
        raise SystemExit('SIP password contains special chars unsafe in Asterisk config; use a strong password without spaces, semicolons, quotes, or slashes.')
    PRIVATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(PRIVATE, 0o700)
    config = f'''[global]
type=global
user_agent=Forushyar-SIP-Playback-Test
endpoint_identifier_order=ip,username,anonymous

[transport-forushyar]
type=transport
protocol=udp
bind={server_ip}:{listen_port}
local_net=192.168.0.0/16

[ucm-300-auth]
type=auth
auth_type=userpass
username=300
password={sip_secret}

[ucm-300-registration]
type=registration
transport=transport-forushyar
outbound_auth=ucm-300-auth
server_uri=sip:{pbx_ip}:{pbx_port}
client_uri=sip:300@{pbx_ip}:{pbx_port}
contact_user=300
retry_interval=30
forbidden_retry_interval=60
expiration=300
line=yes
endpoint=ucm-300-peer

[ucm-300-peer]
type=endpoint
transport=transport-forushyar
context=from-ucm
outbound_auth=ucm-300-auth
aors=ucm-300-aor
disallow=all
allow=alaw,ulaw
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
dtmf_mode=rfc4733

[ucm-300-aor]
type=aor
contact=sip:{pbx_ip}:{pbx_port}

[ucm-300-identify]
type=identify
endpoint=ucm-300-peer
match={pbx_ip}/32
'''
    dialplan = '''[general]
static=yes
writeprotect=yes

[from-ucm]
exten => 300,1,NoOp(Forushyar SIP greeting pilot)
 same => n,Answer()
 same => n,Wait(1)
 same => n,Playback(custom/forushyar-welcome)
 same => n,Hangup()
exten => s,1,Goto(300,1)
'''
    rtp = '''[general]
rtpstart=18000
rtpend=18050
strictrtp=yes
'''
    write_private('pjsip.conf', config)
    write_private('extensions.conf', dialplan)
    write_private('rtp.conf', rtp)
    convert_wav(audio, PRIVATE / 'sounds' / 'forushyar-welcome.wav')
    print('Configuration generated:', PRIVATE)
    print('SIP listen: ', server_ip, listen_port)
    print('PBX: ', pbx_ip, pbx_port)
    print('Audio: 8kHz, mono, signed 16-bit PCM')
    print('SIP secret stored locally only in pjsip.conf (mode 0600).')

if __name__ == '__main__':
    main()
