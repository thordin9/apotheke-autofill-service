# apotheke-autofill-service

Swappable HTTP service that suggests item field values (name, category, tags,
description) from a photo, for [Apotheke](https://github.com/thordin9/apotheke).

Contract: [docs/api-v1.md](docs/api-v1.md) · [openapi.yaml](openapi.yaml)

Hardware CI on the Jetson (run by hand, not a required check): [docs/hardware-ci.md](docs/hardware-ci.md)

## Quickstart

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --port 8080
```

```bash
curl -s localhost:8080/healthz
curl -s localhost:8080/v1/info
curl -s -F image=@photo.jpg \
     -F 'context={"categories":["Tools","Kitchen"],"tags":["bosch","red"],"locale":"en"}' \
     localhost:8080/v1/suggest
# with auth enabled (AUTOFILL_TOKEN=secret):
curl -s -H 'Authorization: Bearer secret' localhost:8080/v1/info
```

## Configuration (environment variables)

All variables are plain env vars and work with `uvicorn` directly; Docker is not required.

| Variable | Default | Meaning |
|---|---|---|
| `BACKEND` | `echo` | `echo`, `ollama`, `clip`. Unknown values fail at startup. |
| `AUTOFILL_TOKEN` | *(unset)* | If set, every route except `/healthz` needs `Authorization: Bearer <token>` (else 401). |
| `MAX_IMAGE_BYTES` | `10485760` (10 MiB) | Larger images get 413. |
| `BACKEND_TIMEOUT_S` | `25` | Backend call limit; exceeded → 504. |
| `ECHO_MODE` | `ok` | Echo only: `ok`, `error` (503), `malformed` (200 with invalid JSON body), `slow` (hangs → 504 after `BACKEND_TIMEOUT_S`). |
| `ECHO_DELAY_MS` | `0` | Echo only: delay before answering. |

`ECHO_*` are read on every request. Example: `ECHO_MODE=slow BACKEND_TIMEOUT_S=2 uvicorn app.main:app --port 8080`.

### Backends

| `BACKEND` | Status |
|---|---|
| `echo` | Implemented. Deterministic: category = first `context.categories` entry (else `Misc`), tags = first 3 `context.tags` (else `echo`, `sample`), name/description derived from image hash, type and size. |
| `ollama` | Placeholder. Service starts and `/healthz` is 200, but `/v1/info` and `/v1/suggest` return **503** `backend_unavailable`. |
| `clip` | Placeholder, same as `ollama`. |

Placeholders return 503 rather than failing at startup so Docker health checks
and Apotheke's "test connection" both show a clear "not implemented" message.

HEIC/HEIF uploads are accepted only if the optional `pillow-heif` package is
installed; otherwise they get 415. Clients should send JPEG at about 1024 px.

Add a backend: subclass `app.backends.base.Backend` (`async info()`,
`async suggest(image_bytes, mime, context)`) and register it in `app/backends/__init__.py`.

## Development

```bash
ruff check . && ruff format --check . && pytest
```
