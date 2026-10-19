# x86 image. Shipped default is Cloudflare with an Ollama fallback.
# CI smoke overrides BACKEND=echo and FALLBACK_BACKEND=none.
FROM python:3.11-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BACKEND=cloudflare \
    FALLBACK_BACKEND=ollama \
    BACKEND_TIMEOUT_S=45 \
    PORT=8080

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && mkdir -p /var/lib/autofill

COPY app ./app

EXPOSE 8080

# /healthz is unauthenticated. /v1/info returns 401 when AUTOFILL_TOKEN is set.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
