# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY pyproject.toml ./
COPY app ./app
RUN pip install ".[otlp,s3]"

FROM python:3.12-slim AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENTOS_ENV=production \
    AGENTOS_OBJECT_STORAGE_PATH=/data/objects
RUN groupadd --system --gid 10001 agentos && useradd --system --uid 10001 --gid agentos --home /app agentos \
    && mkdir -p /data/objects && chown -R agentos:agentos /data
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --chown=agentos:agentos app ./app
COPY --chown=agentos:agentos migrations ./migrations
COPY --chown=agentos:agentos alembic.ini ./
COPY --chown=agentos:agentos scripts ./scripts
USER agentos
EXPOSE 8000 9100
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"
# API by default; the worker runs the same image with `python -m app.worker`, migrations with `alembic upgrade head`.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
