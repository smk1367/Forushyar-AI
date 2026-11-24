Forushyar v0.2.1 — audio diagnostics / limited audio fix

WHAT IT DOES
- Uses a band-limited 48-tap polyphase sinc resampler instead of linear interpolation for 8kHz audio.
- Updates original greeting and the TTS reply conversion in voice_gateway.py.
- Preserves Docker bind-mount inode so the running container will read updated code after restart.
- Creates backup copies of both modified files.
- Does NOT alter PBX settings, SIP passwords, database, port routing, or production Compose stack.

REQUIREMENTS
- Copy all 3 files into ~/forushyar-AI/forushyar-audio-diagnostics-v021 (unzip archive).
- No active test calls; ensure extension 300 is idle.
- Original WAV at ~/forushyar-voice-test.wav must exist.
- Pilot source must be at ~/forushyar-AI/forushyar-voice-pilot-v02/voice_gateway.py.

INSTALL
cd ~/forushyar-AI/forushyar-audio-diagnostics-v021
python3 apply.py
# Only on success:
docker restart forushyar-voice-pilot

CALL TEST
Call extension 300, listen to prerecorded greeting and Qwen-generated reply separately. Rate both for choppiness.

ROLLBACK
cd ~/forushyar-AI/forushyar-audio-diagnostics-v021
python3 rollback.py
docker restart forushyar-voice-pilot

WARNING: This improves resampling, but does not prove that choppiness originated there.
