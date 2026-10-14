# apotheke-autofill-service

Swappable HTTP service that suggests item field values (name, category, tags,
description) from a photo, for [Apotheke](https://github.com/thordin9/apotheke).

Contract: [docs/api-v1.md](docs/api-v1.md) · [openapi.yaml](openapi.yaml)

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

## Docker (echo, x86)

No GPU. The image runs `uvicorn app.main:app` with `BACKEND=echo`.

```bash
docker compose up --build
curl -s localhost:8080/healthz
curl -s -F image=@photo.jpg \
     -F 'context={"categories":["Tools"],"tags":["bosch"],"locale":"en"}' \
     localhost:8080/v1/suggest
```

Compose publishes `${PORT:-8080}` and passes `AUTOFILL_TOKEN`, `ECHO_MODE`, and `ECHO_DELAY_MS` through from the shell (empty token disables auth). Health checks call `GET /healthz`. That route stays open when `AUTOFILL_TOKEN` is set; `GET /v1/info` returns 401, so it is not a health check.

```bash
AUTOFILL_TOKEN=secret ECHO_MODE=ok docker compose up --build
curl -s localhost:8080/healthz
curl -s -H 'Authorization: Bearer secret' localhost:8080/v1/info
```

`.env` sets `COMPOSE_PROFILES=echo`, so `docker compose up` stays on this stack.

## Docker (Ollama, x86)

Local dev for the Ollama backend. CPU is the default. The app image is the same one as echo; `BACKEND=ollama` selects the backend.

```bash
docker compose --profile ollama up --build
```

That command replaces the echo profile. It starts:

| Service | Role |
|---|---|
| `ollama` | `ollama/ollama:0.40.4` on port 11434. Model files persist in the `ollama-models` volume (`/root/.ollama`). |
| `ollama-init` | One-shot. Pulls `moondream`, then `OLLAMA_MODEL` if it is not `moondream`, then `OLLAMA_EXTRA_MODEL` if set. Exits when the pulls finish. |
| `autofill-ollama` | Autofill on port 8080 with `BACKEND=ollama`, `OLLAMA_HOST=http://ollama:11434`, and `OLLAMA_MODEL` (default `moondream`). |

```bash
OLLAMA_MODEL=moondream docker compose --profile ollama up --build
# optional second model (see verified tags below)
OLLAMA_EXTRA_MODEL=qwen2.5vl:3b docker compose --profile ollama up --build
```

NVIDIA GPU (still the Ollama profile; this file only adds `gpus: all`):

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile ollama up --build
```

Do not enable the `echo` and `ollama` profiles together; both publish port 8080.

The Python `ollama` backend is still a placeholder, so this stack's `/healthz` is 200 while `/v1/info` and `/v1/suggest` return 503 `backend_unavailable`. `OLLAMA_HOST` and `OLLAMA_MODEL` are set for that backend; the process does not read them yet.

### Ollama library tags

Checked against the Ollama library on 2026-10-14:

| Tag | In the library |
|---|---|
| `moondream`, `moondream:latest`, `moondream:1.8b` | Yes. `moondream` is the default pull. |
| `qwen2-vl`, `qwen2-vl:2b` | No. `https://ollama.com/library/qwen2-vl` is 404. |
| `qwen2.5vl:2b` | No. |
| `qwen2.5vl:3b`, `qwen2.5vl:7b`, `qwen2.5vl:32b`, `qwen2.5vl:72b` | Yes. `qwen2.5vl:3b` is the smallest official Qwen vision tag. It is not Qwen2-VL-2B. |

## Jetson (JetPack 5 / L4T r35)

`Dockerfile.jetson` is based on `dustynv/ollama:r35.4.1` (dusty-nv's Jetson
container, not the stock `ollama/ollama` image) and sets `BACKEND=ollama` plus
`OLLAMA_MODEL=moondream` and `OLLAMA_HOST=http://127.0.0.1:11434`.

That base is Ubuntu 20.04 with glibc 2.31. `python3` is 3.8.10, `python3.9` is
3.9.5, `python` is 2.7, and neither `pip` nor `pip3` is installed. This service
needs Python >=3.11, so the image installs CPython 3.11.17 (a standalone build
that only needs glibc 2.17 and already includes pip) and then installs
`requirements-jetson.txt` with `python3.11 -m pip`. Deadsnakes no longer
publishes Ubuntu 20.04 arm64 packages, which is why the image does not
`apt-get install python3.11`.

The `ollama` backend is still a placeholder: the process starts and `/healthz`
is 200, but `/v1/info` and `/v1/suggest` return 503 `backend_unavailable`.
`OLLAMA_HOST` and `OLLAMA_MODEL` are set for the device; the app does not read
them yet.

```bash
docker build -f Dockerfile.jetson -t apotheke-autofill:jetson .
docker run --runtime nvidia --network host \
  -e BACKEND=ollama \
  -e OLLAMA_HOST=http://127.0.0.1:11434 \
  -e OLLAMA_MODEL=moondream \
  -e AUTOFILL_TOKEN= \
  apotheke-autofill:jetson
```

Ollama itself can come from the same base:

```bash
docker run --runtime nvidia --network host \
  -v ~/ollama:/ollama -e OLLAMA_MODELS=/ollama \
  dustynv/ollama:r35.4.1
```

GitHub Actions does not treat the arm64 build as required. The base is about
5.8 GB compressed (~16 GB unpacked); under QEMU that build does not reliably
finish on GitHub-hosted runners (disk and time). `.github/workflows/jetson.yml`
starts on `workflow_dispatch` and when `Dockerfile.jetson` or
`requirements-jetson.txt` changes. Path-triggered runs only record that reason.
`workflow_dispatch` attempts the arm64 build and is `continue-on-error`, so a
failure does not block merging. Build on the Xavier for a result you can run.

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
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Jetson image uses the loopback default. The x86 Ollama profile sets `http://ollama:11434`. Not read by the placeholder `ollama` backend yet. |
| `OLLAMA_MODEL` | `moondream` | Model name passed to the process. Default pull tag in the Ollama profile. Not read by the placeholder backend yet. |
| `OLLAMA_EXTRA_MODEL` | *(unset)* | Ollama profile only. Extra library tag for `ollama-init` to pull, for example `qwen2.5vl:3b`. |

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
