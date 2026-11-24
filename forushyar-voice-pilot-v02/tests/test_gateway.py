import io
import sys
import tempfile
import unittest
import wave
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voice_gateway as v


class GatewayTests(unittest.TestCase):
    def test_parse_agi_result(self):
        self.assertEqual(v.parse_agi_result('200 result=0 endpos=800'), 0)
        self.assertEqual(v.parse_agi_result('200 result=-1'), -1)
        self.assertEqual(v.parse_agi_result('200 result=35 (timeout)'), 35)
        with self.assertRaises(RuntimeError):
            v.parse_agi_result('510 Invalid or unknown command')

    def test_wav_conversion(self):
        src = io.BytesIO()
        pcm = array('h', [10000, -10000] * 1600)
        with wave.open(src, 'wb') as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm.tobytes())
        with tempfile.TemporaryDirectory() as dirname:
            dest = Path(dirname) / 'speech.wav'
            v.convert_wav_to_8k(src.getvalue(), dest)
            with wave.open(str(dest), 'rb') as wav:
                self.assertEqual(wav.getframerate(), 8000)
                self.assertEqual(wav.getnchannels(), 1)
                self.assertEqual(wav.getsampwidth(), 2)
                self.assertEqual(wav.getnframes(), 800)

    def test_malformed_agi_result(self):
        with self.assertRaises(RuntimeError):
            v.parse_agi_result('200 result=')


if __name__ == '__main__':
    unittest.main()
