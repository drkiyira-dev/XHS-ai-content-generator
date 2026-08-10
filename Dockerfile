FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY pyproject.toml ./
COPY backend ./backend

RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install . \
    && /opt/venv/bin/python -m pip check


FROM python:3.12-slim-bookworm AS runtime

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    XDG_CACHE_HOME=/tmp/.cache \
    TMPDIR=/run/xhs/uploads \
    UPLOAD_DIR=/run/xhs/uploads \
    DATABASE_ENABLED=false \
    SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1 \
    VISION_MODEL_NAME=Qwen/Qwen3-VL-8B-Instruct \
    OCR_MODEL_NAME=PaddlePaddle/PaddleOCR-VL-1.5

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 \
        --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin app \
    && install -d -o 10001 -g 10001 -m 0700 /run/xhs/uploads

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY backend ./backend

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import http.client,json; connection=http.client.HTTPConnection('127.0.0.1',8000,timeout=2); connection.request('GET','/api/health'); response=connection.getresponse(); payload=json.loads(response.read()); raise SystemExit(0 if response.status==200 and payload=={'status':'ok'} else 1)"]

STOPSIGNAL SIGTERM

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host=0.0.0.0", "--port=8000", "--workers=1", "--no-server-header"]
