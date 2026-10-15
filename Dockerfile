# syntax=docker/dockerfile:1
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    RADAR_HOST=0.0.0.0 \
    RADAR_PORT=8080 \
    RADAR_DB_PATH=/data/radar.db

WORKDIR /app

COPY src/ /app/src/

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin radar \
    && mkdir -p /data \
    && chown -R radar:radar /app /data

USER 10001

EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else sys.exit(1)"]

ENTRYPOINT ["python", "-m", "radar"]
