# ---- build stage: install dependencies into a venv --------------------------
FROM python:3.12-slim AS build
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /venv && /venv/bin/pip install --no-cache-dir -r requirements.txt

# ---- runtime stage -----------------------------------------------------------
FROM python:3.12-slim
LABEL org.opencontainers.image.title="ise-ndg-sync" \
      org.opencontainers.image.description="Catalyst Center -> Cisco ISE Network Device Group sync"

RUN addgroup --system --gid 1001 app && adduser --system --uid 1001 --gid 1001 app \
    && mkdir -p /data && chown app:app /data

COPY --from=build /venv /venv
WORKDIR /app
COPY app ./app
COPY static ./static

ENV PATH="/venv/bin:$PATH" \
    DATA_DIR=/data \
    PORT=8080 \
    LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1

USER app
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,os,sys; \
      sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/healthz', timeout=8).status==200 else 1)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
