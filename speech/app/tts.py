import io
import os
import threading
import wave
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from piper import PiperVoice


MODEL_PATH = Path(
    os.getenv("TTS_MODEL_PATH", "/models/fa_IR-mana-medium.onnx")
)
CONFIG_PATH = Path(
    os.getenv("TTS_CONFIG_PATH", "/models/fa_IR-mana-medium.onnx.json")
)

router = APIRouter()

_voice = None
_voice_lock = threading.Lock()


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def get_voice():
    global _voice

    if _voice is None:
        with _voice_lock:
            if _voice is None:
                if not MODEL_PATH.is_file():
                    raise RuntimeError("Persian TTS model not found")

                if not CONFIG_PATH.is_file():
                    raise RuntimeError("Persian TTS config not found")

                _voice = PiperVoice.load(
                    MODEL_PATH,
                    config_path=CONFIG_PATH,
                )

    return _voice


def is_voice_loaded() -> bool:
    return _voice is not None


@router.get("/v1/audio/speech/health")
def speech_health():
    return {
        "status": "alive",
        "engine": "piper",
        "model": MODEL_PATH.name,
        "model_loaded": _voice is not None,
    }


@router.post("/v1/audio/speech")
def generate_speech(payload: SpeechRequest):
    text = payload.text.strip()

    if not text:
        raise HTTPException(status_code=422, detail="متن خالی است")

    try:
        voice = get_voice()

        output = io.BytesIO()

        with wave.open(output, "wb") as wav_file:
            voice.synthesize_wav(text, wav_file)

        audio_data = output.getvalue()

        if not audio_data:
            raise RuntimeError("Empty audio generated")

        return Response(
            content=audio_data,
            media_type="audio/wav",
            headers={
                "Content-Disposition": 'inline; filename="forushyar.wav"',
                "X-Audio-Format": "wav",
            },
        )

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"تولید صدای فارسی ناموفق بود: {type(exc).__name__}",
        ) from exc
