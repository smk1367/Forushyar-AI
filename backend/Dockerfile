# syntax=docker/dockerfile:1

FROM python:3.13-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app

COPY requirements.txt ./requirements.txt

# The build argument may be overridden with a reachable PyPI mirror.
# Fall back when Compose or the CLI passes an empty build argument.
ARG PIP_INDEX_URL=https://pypi.org/simple/

RUN index_url="${PIP_INDEX_URL:-https://pypi.org/simple/}" \
    && python -m pip install --no-cache-dir --retries 5 --timeout 60 \
       --index-url "${index_url}" \
       -r requirements.txt

COPY --chown=app:app app ./app
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app alembic.ini ./alembic.ini

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
