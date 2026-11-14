FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=2 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HOME=/models/huggingface

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 speech \
    && useradd --system --uid 10001 --gid speech --home-dir /app speech \
    && mkdir -p /models \
    && chown -R speech:speech /app /models

COPY requirements.txt ./requirements.txt
ARG PIP_INDEX_URL=https://pypi.org/simple/
RUN python -m pip install \
    --no-cache-dir \
    --retries 5 \
    --timeout 60 \
    --index-url="${PIP_INDEX_URL}" \
    -r requirements.txt

COPY --chown=speech:speech app ./app

USER 10001:10001

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
