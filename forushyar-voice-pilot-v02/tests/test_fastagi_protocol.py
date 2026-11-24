import io
import os
import socket
import sys
import tempfile
import threading
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voice_gateway as v


def audio_file(path):
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(array('h', [1000] * 1600).tobytes())


class FakeAsteriskTest(unittest.TestCase):
    def test_one_complete_turn_and_temporary_file_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            messages = []
            def fake_transcribe(path):
                self.assertTrue(path.exists())
                return 'سلام'
            def fake_answer(text, history):
                self.assertEqual(text, 'سلام')
                return 'سلام، بفرمایین'
            def fake_speak(text, name):
                target = root / (name + '.wav')
                audio_file(target)
                return target
            with patch.object(v, 'AUDIO_DIR', root), patch.object(v, 'ASTERISK_SOUNDS', str(root)), \
                 patch.object(v, 'MAX_TURNS', 1), \
                 patch.object(v, 'transcribe', fake_transcribe), \
                 patch.object(v, 'answer', fake_answer), \
                 patch.object(v, 'speak_to_file', fake_speak):
                with v.PilotServer(('127.0.0.1', 0), v.PilotHandler) as server:
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    with socket.create_connection(server.server_address, timeout=10) as s:
                        s.settimeout(10)
                        s.sendall(b'agi_extension: 300\nagi_uniqueid: abc\n\n')
                        with s.makefile('rb') as io_reader:
                            while True:
                                line = io_reader.readline()
                                if not line:
                                    break
                                command = line.decode().strip()
                                messages.append(command)
                                if command.startswith('RECORD FILE '):
                                    stem = command.split()[2]
                                    audio_file(Path(stem + '.wav'))
                                    s.sendall(b'200 result=0 (timeout)\n')
                                elif command.startswith('STREAM FILE '):
                                    s.sendall(b'200 result=0 endpos=800\n')
                                else:
                                    self.fail('Unexpected AGI command: ' + command)
                    server.shutdown()
                    thread.join(timeout=5)
            self.assertEqual(len([x for x in messages if x.startswith('RECORD FILE')]), 1)
            self.assertEqual(len([x for x in messages if x.startswith('STREAM FILE')]), 2)
            self.assertEqual(list(root.glob('fv_*')), [])


if __name__ == '__main__':
    unittest.main()
