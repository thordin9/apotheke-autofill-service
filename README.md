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
| `autofill-ollama` | Autofill on port 8080 with `BACKEND=ollama`, `OLLAMA_URL=http://ollama:11434`, and `OLLAMA_MODEL` (default `moondream`). |

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

The app reads `OLLAMA_URL` and `OLLAMA_MODEL`. `OLLAMA_HOST` is only the Ollama server bind address and the `ollama` CLI: the server image defaults it to `0.0.0.0:11434`, and `ollama-init` plus the healthcheck set it so `ollama pull` and `ollama list` reach that server. This branch still ships the placeholder backend, so `/healthz` is 200 while `/v1/info` and `/v1/suggest` return 503 until `feat/ollama-backend` is in the image.

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
container, not the stock `ollama/ollama` image) and sets `BACKEND=ollama`,
`OLLAMA_MODEL=qwen2.5vl-jetson:3b`, `OLLAMA_KEEP_ALIVE=-1` and
`OLLAMA_URL=http://127.0.0.1:11434` (Ollama on the device, not a Compose service
named `ollama`).

### Default model on the Jetson: `qwen2.5vl-jetson:3b`

On 9 product photos at 1024 px (what Apotheke sends), Qwen2.5-VL 3B picked the
right category on 9/9 against Moondream's 4/9 through the service, at about 13.5 s per photo
against 4.3 s. Measurements are in [docs/benchmarks.md](docs/benchmarks.md).

Stock `qwen2.5vl:3b` does not fit a Xavier NX: at load Ollama reserves room for
a 2-megapixel image, which without flash attention (unsupported on this GPU)
needs a 7.3 GiB buffer, and falls back to the CPU. `qwen2.5vl-jetson:3b` is a
local copy with the same weights whose GGUF caps images at about 0.5 MP
(`qwen25vl.vision.max_pixels=501760`), with `num_ctx 4096`. It has to be created
on the board once; the steps are in [docs/benchmarks.md](docs/benchmarks.md).
Moondream still works (`-e OLLAMA_MODEL=moondream`) and is about 3× faster but
much less accurate.

The Jetson image sets `BACKEND_TIMEOUT_S=45`. With Qwen a request takes about
13.5 s, and loading the model about 15 s, so a request that finds the model
unloaded (Ollama restarted or evicted it) waits for the load and still answers
within the limit (15 + 13.5 s fits in 90% of 45 s). One request queued behind
another fits as well (2 × 13.5 s). The service decides from the last load time
it measured (never assumed below 15 s) and the slowest of the last 5
answers (15 s until measured), so one fast answer can't let in a request
that then times out: when loading plus answering would not fit, as with Qwen at the
generic 25 s default, it answers 503 with `Retry-After: 15` at once and reloads
the model in the background. A request that would time out in the queue gets
503 with a `Retry-After` based on recent request times. Clients should allow
the backend timeout plus 5 s, so 50 s for the Jetson (Apotheke's maximum is 60 s).

That base is Ubuntu 20.04 with glibc 2.31. `python3` is 3.8.10, `python3.9` is
3.9.5, `python` is 2.7, and neither `pip` nor `pip3` is installed. This service
needs Python >=3.11, so the image installs CPython 3.11.17 (a standalone build
that only needs glibc 2.17 and already includes pip) and then installs
`requirements-jetson.txt` with `python3.11 -m pip`. Deadsnakes no longer
publishes Ubuntu 20.04 arm64 packages, which is why the image does not
`apt-get install python3.11`.


```bash
docker build -f Dockerfile.jetson -t apotheke-autofill:jetson .
docker run --runtime nvidia --network host \
  -e BACKEND=ollama \
  -e OLLAMA_URL=http://127.0.0.1:11434 \
  -e OLLAMA_MODEL=qwen2.5vl-jetson:3b \
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
| `BACKEND_TIMEOUT_S` | `25` (`45` in the Jetson image) | Backend call limit; exceeded → 504. Clients should time out 5 s later (30 s, or 50 s for the Jetson). |
| `ECHO_MODE` | `ok` | Echo only: `ok`, `error` (503), `malformed` (200 with invalid JSON body), `slow` (hangs → 504 after `BACKEND_TIMEOUT_S`). |
| `ECHO_DELAY_MS` | `0` | Echo only: delay before answering. When above 0, `/v1/info` reports it as `avg_latency_ms`, so clients can test progress estimates without a model. |
| `OLLAMA_URL` | `http://ollama:11434` | Base URL the app reads. The x86 Ollama profile sets `http://ollama:11434`. The Jetson image sets `http://127.0.0.1:11434`. |
| `OLLAMA_HOST` | `0.0.0.0:11434` on the server image | Ollama server bind address, and the address the `ollama` CLI uses. Not read by the app. |
| `OLLAMA_MODEL` | `moondream` (`qwen2.5vl-jetson:3b` in the Jetson image) | Model the app requests, and the default tag `ollama-init` pulls. `qwen2.5vl:3b` (or another Ollama vision tag) is an option: better output, slower. Pull it first: `ollama pull <model>`. |
| `OLLAMA_EXTRA_MODEL` | *(unset)* | Ollama profile only. Extra library tag for `ollama-init` to pull, for example `qwen2.5vl:3b`. |
| `OLLAMA_WARMUP` | `1` | Ollama only: load the model in the background at startup. Until loaded, `/v1/info` has `"ready": false` and `/v1/suggest` returns 503 + `Retry-After`. `0` skips warm-up. |
| `OLLAMA_QUEUE_SIZE` | `1` | Ollama only: requests allowed to wait behind the one in flight; more get 503 `backend_unavailable` + `Retry-After`. A request is also refused at once when, judging by the slowest of the last 5 answers, it would time out while queued. |
| `OLLAMA_RETRY_AFTER_S` | `5` | Ollama only: `Retry-After` value for warming-up/busy 503s. |
| `OLLAMA_NUM_PREDICT` | `256` | Ollama only: most tokens the model may generate per request. Caps runaway output (small models sometimes pad `format=json` answers with whitespace until the timeout). `0` removes the cap. |
| `OLLAMA_TEMPERATURE` | `0` | Ollama only: sampling temperature. |
| `OLLAMA_KEEP_ALIVE` | `30m` (`-1` in the Jetson image) | Ollama only: how long Ollama keeps the model loaded after a request (duration, or seconds; `-1` = forever). On a board dedicated to the service use `-1`, so the first request after an idle spell does not wait for the model to load. |
| `OLLAMA_JSON_FORMAT` | `schema` | Ollama only: `schema` (or `1`) sends a JSON schema as `format`, so Ollama only lets the model write JSON of that shape, with the category limited to the request's categories. The prompt shows no JSON template, because small models copy a template back instead of answering. `json` sends `format: "json"`. `0` lets the model answer freely; the service still extracts the first JSON object, or derives fields from plain text. |
| `OLLAMA_PLAIN_RETRY` | `1` | Ollama only: if the answer to the JSON prompt is unusable, ask once more with a short plain-text prompt and derive a low-confidence name and description from the text. It retries when generating took at most 40% of `BACKEND_TIMEOUT_S` (time Ollama spent loading the model does not count) and enough time is left. `0` disables the retry. |
| `OLLAMA_LOG_RAW` | `0` | Ollama only: `1` logs every raw model answer at DEBUG. Answers that cannot be used are always logged at WARNING. Logged text is cut to 2 KB; the image and the request token are never logged. |

`ECHO_*` are read on every request. Example: `ECHO_MODE=slow BACKEND_TIMEOUT_S=2 uvicorn app.main:app --port 8080`.

### Backends

| `BACKEND` | Status |
|---|---|
| `echo` | Implemented. Deterministic: category = first `context.categories` entry (else `Misc`), tags = first 3 `context.tags` (else `echo`, `sample`), name/description derived from image hash, type and size. |
| `ollama` | Implemented. Sends the image to Ollama `/api/chat` (temperature 0) with a JSON schema as `format`, so the answer is always JSON of that shape and, when `context.categories` is given, the category is one of them (an `enum`). The short prompt names the fields in words and shows no JSON template, since small models copy a template back. It asks for a generic object name (no brand or person names) and a description of the object only, offers `context.categories` as "exactly one of", lists `context.tags` as preferred and names `context.locale`. The answer is extracted (tolerates code fences/prose, a JSON object cut off by `OLLAMA_NUM_PREDICT`, renamed keys such as `object`/`caption`/`keywords`, and schema placeholders like `"str"`), validated and clamped: max 10 unique tags of ≤40 chars, name ≤120, category ≤80, description ≤500, confidence in [0,1]; fields nested as `{"value","confidence"}` (in any combination) are unwrapped, and a top-level `confidence` fills in for name/category/description without one. When `context.categories` is given, the category must be one of them: matched case-insensitively, singular/plural (`clothes` → `Clothing`) or by a small English synonym list (`cookware` → `Kitchen`, `laptop` → `Electronics`, `lamp` → `Lighting`) if that target is listed, otherwise dropped. Tags matching `context.tags` the same way take the existing spelling; new tags stay allowed. Tags that are only a prompt word (`material`, `colour`, `brand`, `category`, …) are dropped, and `material: leather` becomes `leather`. A lone common first name ("John") is not used as the name. When the name shares no word with the object phrase that opens the description (`bose` vs "A pair of black headphones…"), that phrase (up to 4 words) becomes the name, at confidence ≤0.5. A description that only repeats the name or category is dropped. If the model ignores the JSON instructions, a low-confidence name/description is derived from its plain-text answer; if the answer is unusable (e.g. `{}`), one retry with a short plain-text prompt is made while time allows (`OLLAMA_PLAIN_RETRY`). Still unusable → 503 `backend_error`, with the raw output logged at WARNING; unreachable → 503 `backend_unavailable`; slower than `BACKEND_TIMEOUT_S` → 504. |
| `clip` | Placeholder. Service starts and `/healthz` is 200, but `/v1/info` and `/v1/suggest` return **503** `backend_unavailable`. |

Example: `BACKEND=ollama OLLAMA_URL=http://localhost:11434 uvicorn app.main:app --port 8080`.

`GET /v1/info` reports `avg_latency_ms` (optional, added in API v1.1) once the
Ollama backend has finished a suggestion: the moving average of recent
generation times in ms, model load excluded. Clients such as Apotheke can fill
an estimated progress bar against it; it is absent after a restart until the
first request completes. See [docs/api-v1.md](docs/api-v1.md).

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
