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
| `OLLAMA_URL` | `http://ollama:11434` | Ollama only: base URL of the Ollama server. |
| `OLLAMA_MODEL` | `moondream` | Ollama only: vision model. `qwen2-vl-2b` (or another Ollama vision tag) is an option: better multilingual output, slower. Pull it first: `ollama pull <model>`. |
| `OLLAMA_WARMUP` | `1` | Ollama only: load the model in the background at startup. Until loaded, `/v1/info` has `"ready": false` and `/v1/suggest` returns 503 + `Retry-After`. `0` skips warm-up. |
| `OLLAMA_QUEUE_SIZE` | `1` | Ollama only: requests allowed to wait behind the one in flight; more get 503 `backend_unavailable` + `Retry-After`. |
| `OLLAMA_RETRY_AFTER_S` | `5` | Ollama only: `Retry-After` value for warming-up/busy 503s. |
| `OLLAMA_NUM_PREDICT` | `256` | Ollama only: most tokens the model may generate per request. Caps runaway output (small models sometimes pad `format=json` answers with whitespace until the timeout). `0` removes the cap. |
| `OLLAMA_TEMPERATURE` | `0` | Ollama only: sampling temperature. |
| `OLLAMA_KEEP_ALIVE` | `30m` | Ollama only: how long Ollama keeps the model loaded after a request (duration, or seconds; `-1` = forever). |
| `OLLAMA_JSON_FORMAT` | `1` | Ollama only: send `format: "json"` to constrain output to JSON. `0` lets the model answer freely; the service still extracts the first JSON object. |
| `OLLAMA_LOG_RAW` | `0` | Ollama only: `1` logs every raw model answer at DEBUG. Answers that cannot be used are always logged at WARNING. Logged text is cut to 2 KB; the image and the request token are never logged. |

`ECHO_*` are read on every request. Example: `ECHO_MODE=slow BACKEND_TIMEOUT_S=2 uvicorn app.main:app --port 8080`.

### Backends

| `BACKEND` | Status |
|---|---|
| `echo` | Implemented. Deterministic: category = first `context.categories` entry (else `Misc`), tags = first 3 `context.tags` (else `echo`, `sample`), name/description derived from image hash, type and size. |
| `ollama` | Implemented. Sends the image to Ollama `/api/chat` (`format: json`, temperature 0) with a prompt that asks for the Suggestion JSON and lists `context.categories`/`context.tags` (exact spelling preferred) and `context.locale`. The answer is extracted (tolerates code fences/prose), validated and clamped: max 10 unique tags of ≤40 chars, name ≤120, category ≤80, description ≤500, confidence in [0,1]; existing category/tag spelling is restored case-insensitively. Unusable output → 503 `backend_error`; unreachable → 503 `backend_unavailable`; slower than `BACKEND_TIMEOUT_S` → 504. |
| `clip` | Placeholder. Service starts and `/healthz` is 200, but `/v1/info` and `/v1/suggest` return **503** `backend_unavailable`. |

Example: `BACKEND=ollama OLLAMA_URL=http://localhost:11434 uvicorn app.main:app --port 8080`.

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
