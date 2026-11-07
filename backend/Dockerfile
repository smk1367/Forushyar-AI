
# syntax=docker/dockerfile:1

FROM python:3.13-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app

COPY requirements.txt ./requirements.txt

# Install Python dependencies through internal Nexus
RUN --mount=type=secret,id=nexus_netrc,target=/root/.netrc,required=true \
    pip install --no-cache-dir \
    --index-url http://192.168.88.18:8081/repository/pypi-aliyun/simple/ \
    --trusted-host 192.168.88.18 \
    -r requirements.txt

COPY --chown=app:app app ./app
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app alembic.ini ./alembic.ini

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
