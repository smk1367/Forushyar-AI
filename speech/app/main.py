import os
import tempfile
import threading
from pathlib import Path

from faster_whisper import WhisperModel
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from app.tts import router as tts_router

MODEL_NAME = os.getenv("MODEL_NAME", "base")
MODEL_DIR = Path(os.getenv("MODEL_DIR", "/models"))
CPU_THREADS = int(os.getenv("CPU_THREADS", "2"))
COMPUTE_TYPE = os.getenv("COMPUTE_TYPE", "int8")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

_model = None
_model_lock = threading.Lock()

app = FastAPI(
    title="Forushyar Speech Service",
    version="0.1.0",
)


def get_model():
    global _model

    if _model is None:
        with _model_lock:
            if _model is None:
                MODEL_DIR.mkdir(parents=True, exist_ok=True)

                _model = WhisperModel(
                    MODEL_NAME,
                    device="cpu",
                    compute_type=COMPUTE_TYPE,
                    cpu_threads=CPU_THREADS,
                    num_workers=1,
                    download_root=str(MODEL_DIR),
                )

    return _model


@app.get("/health")
def health():
    return {
        "status": "alive",
        "model": MODEL_NAME,
        "model_loaded": _model is not None,
        "device": "cpu",
        "compute_type": COMPUTE_TYPE,
    }


@app.post("/v1/audio/transcriptions")
def transcribe_audio(
    audio: UploadFile = File(...),
    language: str = Form(default="fa"),
):
    if language not in {"fa", "auto", "en"}:
        raise HTTPException(
            status_code=422,
            detail="زبان پشتیبانی‌شده: fa یا auto یا en",
        )

    suffix = Path(audio.filename or "audio.wav").suffix.lower()
    if not suffix or len(suffix) > 10:
        suffix = ".wav"

    temp_path = None

    try:
        size = 0

        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=suffix,
            dir="/tmp",
            delete=False,
        ) as temp_file:
            temp_path = temp_file.name

            while True:
                chunk = audio.file.read(1024 * 1024)
                if not chunk:
                    break

                size += len(chunk)

                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="حداکثر حجم فایل صوتی ۲۵ مگابایت است",
                    )

                temp_file.write(chunk)

        if size == 0:
            raise HTTPException(status_code=400, detail="فایل صوتی خالی است")

        try:
            model = get_model()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    "بارگذاری مدل ناموفق بود. ممکن است دانلود مدل "
                    "از Hugging Face در دسترس نباشد. "
                    f"خطا: {type(exc).__name__}"
                ),
            ) from exc

        try:
            segments, info = model.transcribe(
                temp_path,
                language=None if language == "auto" else language,
                task="transcribe",
                beam_size=5,
                temperature=0.0,
                condition_on_previous_text=False,
                vad_filter=False,
            )

            segments = list(segments)
            transcript = " ".join(
                segment.text.strip()
                for segment in segments
                if segment.text.strip()
            ).strip()

        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"پردازش فایل صوتی ناموفق بود: {type(exc).__name__}",
            ) from exc

        return {
            "text": transcript,
            "language": info.language,
            "language_probability": round(info.language_probability, 4),
            "duration_seconds": round(info.duration, 2),
            "model": MODEL_NAME,
        }

    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)

        audio.file.close()


app.include_router(tts_router)
