FROM node:24.19.0-slim AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-ara \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock ./
RUN pip install -r requirements.lock

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps . \
    && groupadd --system relay \
    && useradd --system --gid relay --no-create-home relay \
    && install -d -o relay -g relay /var/lib/relay/objects

COPY alembic.ini ./
COPY migrations ./migrations
COPY --from=frontend-build /build/frontend/dist ./frontend/dist

USER relay
ENV OCR_TESSERACT_EXECUTABLE=/usr/bin/tesseract \
    OCR_TESSDATA_DIR=/usr/share/tesseract-ocr/5/tessdata
EXPOSE 8000

# Use a configured trusted host even when production excludes localhost.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import json,os,urllib.request; hosts=json.loads(os.environ.get('ALLOWED_HOSTS','[\"localhost\"]')); request=urllib.request.Request('http://127.0.0.1:8000/health', headers={'Host': hosts[0]}); urllib.request.urlopen(request, timeout=3).close()"

CMD ["uvicorn", "relay.presentation.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
