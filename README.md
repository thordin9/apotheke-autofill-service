# apotheke-autofill-service

Swappable HTTP service that suggests item field values (name, category, tags,
description) from a photo, for [Apotheke](https://github.com/thordin9/apotheke).

Contract: [docs/api-v1.md](docs/api-v1.md) · [openapi.yaml](openapi.yaml)

**Recommended deployment:** the Cloudflare Worker in [`worker/`](worker),
served at `https://apotheke-autofill-service.webapp.gr`. A fork sets its own
`<your-domain>` and its own production secrets. Install, the one-time secret,
and the main-only deploy Action are in
[docs/cloudflare-workers-deploy.md](docs/cloudflare-workers-deploy.md).
Apotheke only changes the base URL and the bearer token. The Worker reports
`backend: "cloudflare-workers"` and, at the default body cap,
`max_image_bytes: 524288`. That figure is
`min(ADVERTISED_MAX_IMAGE_BYTES, MAX_UPLOAD_BYTES − 64 KiB)` from
`worker/src/multipart.ts`. The request body cap stays 1 MiB (`1048576`). An
image part is still rejected above 983040 (the body cap minus 64 KiB). The
Python service stays at 10 MiB.

Docker and the Jetson stack below are **legacy self-hosting**. They keep the
Python service, including its 10 MiB upload limit. Hardware CI on the Jetson
(run by hand, not a required check): [docs/hardware-ci.md](docs/hardware-ci.md).
Legacy deploy (scripts in `deploy/jetson/`): [docs/deploy-jetson.md](docs/deploy-jetson.md).

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

No GPU. The image runs `uvicorn app.main:app` with `BACKEND=cloudflare` and `FALLBACK_BACKEND=ollama`.

```bash
docker compose up --build
curl -s localhost:8080/healthz
curl -s -F image=@photo.jpg \
     -F 'context={"categories":["Tools"],"tags":["bosch"],"locale":"en"}' \
     localhost:8080/v1/suggest
```

Compose publishes `${PORT:-8080}` and passes `AUTOFILL_TOKEN`, `BACKEND_TIMEOUT_S`, `ALLOWED_MODELS`, `IMAGE_SIZES` and the `ECHO_*` knobs through from the shell (see [.env.example](.env.example)) (empty token disables auth). Health checks call `GET /healthz`. That route stays open when `AUTOFILL_TOKEN` is set; `GET /v1/info` returns 401, so it is not a health check.

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

## Jetson (JetPack 5 / L4T r35) — legacy self-hosting

> Decommissioned on 2026-10-20: the autofill service no longer runs on the project's board, and both Jetson
> workflows are disabled. Ollama and the idle self-hosted runner stay on the board. See [docs/deploy-jetson.md](docs/deploy-jetson.md#decommissioned-2026-10-20).

The full legacy deployment (Ollama drop-in, model, `service.env`, container, verify, redeploy, rollback,
bandwidth cap, runner, reboot) is in [docs/deploy-jetson.md](docs/deploy-jetson.md).

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
generic 25 s default, it answers 503 at once and reloads the model in the
background, with `Retry-After` set to that model's remaining load time
(max(15 s, its last measured load) plus a margin of max(2 s, 30%), minus the
time the load has already run, at least 1 s and at most `BACKEND_TIMEOUT_S` + 5 s,
the recommended client timeout; 20 s for an unmeasured model). A request that would time out in the queue gets
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
deploy/jetson/build-image.sh origin/main     # prints the tag it built
IMAGE=apotheke-autofill:jetson-<short sha>   # as printed by build-image.sh
docker run --runtime nvidia --network host \
  -e BACKEND=ollama \
  -e OLLAMA_URL=http://127.0.0.1:11434 \
  -e OLLAMA_MODEL=qwen2.5vl-jetson:3b \
  -e AUTOFILL_TOKEN= \
  "$IMAGE"
```

For the long-running service use `deploy/jetson/run-service.sh "$IMAGE"` with an env file instead (see
[docs/deploy-jetson.md](docs/deploy-jetson.md)).

Ollama itself can come from the same base:

```bash
docker run --runtime nvidia --network host \
  -v ~/ollama:/ollama -e OLLAMA_MODELS=/ollama \
  dustynv/ollama:r35.4.1
```

GitHub Actions does not treat the arm64 build as required. The base is about
5.8 GB compressed (~16 GB unpacked); under QEMU that build does not reliably
finish on GitHub-hosted runners (disk and time). `.github/workflows/jetson.yml`
is legacy and disabled. It only runs by hand (`workflow_dispatch`), where it
attempts the arm64 build and is `continue-on-error`, so a
failure does not block merging. Build on the Xavier for a result you can run.

## Configuration (environment variables)

All variables are plain env vars and work with `uvicorn` directly; Docker is not required.

Do not commit `service.env`, key files, or a local env file. `.gitignore` ignores `service.env`, `*.env`, `*.pem`, and `*.key`. The tracked `.env` (Compose profile only) and `*.env.example` stay tracked via `!.env` and `!*.env.example`. CI runs [gitleaks](https://github.com/gitleaks/gitleaks) 8.28.0 over the full history, with the default rules enabled. The download is checksum-pinned. `.gitleaks.toml` allowlists the two historical placeholders only in the commits that added them, and only when whitespace follows the closing angle bracket. The same placeholders quoted in `docs/qa/v1.4-stub-qa.md` are allowlisted only in commit `ba1f8ff4397c568c5b9b5ffdc9f2e680664ca231`.

| Variable | Default | Meaning |
|---|---|---|
| `BACKEND` | `cloudflare` | `echo`, `ollama`, `cloudflare`, `clip`. Unknown values fail at startup. Shipped with `FALLBACK_BACKEND=ollama`. |
| `AUTOFILL_TOKEN` | *(unset)* | If set, every route except `/healthz` needs `Authorization: Bearer <token>` (else 401). |
| `MAX_IMAGE_BYTES` | `10485760` (10 MiB) | Larger images get 413. |
| `BACKEND_TIMEOUT_S` | `45` | Backend call limit; exceeded → 504. Clients should time out 5 s later (50 s). Must be at least `REQUEST_DEADLINE_S` when Cloudflare is primary and Ollama is the fallback. |
| `ECHO_MODE` | `ok` | Echo only: `ok`, `error` (503), `malformed` (200 with invalid JSON body), `slow` (hangs → 504 after `BACKEND_TIMEOUT_S`). |
| `ALLOWED_MODELS` | *(unset)* | Models a client may pick with `context.model`, comma-separated, e.g. `moondream,qwen3-vl:2b`. The backend's own model (`OLLAMA_MODEL`, or `ECHO_MODEL` on echo) is always allowed and is the default, so it need not be listed. Unset: `/v1/info` has no `models` and `context.model` is ignored. Only installed models (Ollama `GET /api/tags`, cached 30 s) are offered and used; anything else falls back to the default. See [Model and image size choice](#model-and-image-size-choice). On the Jetson, set it to the default model only (`qwen2.5vl-jetson:3b`) so `/v1/info` lists `models` and clients can show the model choice; don't add other tags: `qwen3-vl:2b` cannot load there with Ollama 0.15.6 (out of memory), and the lower-`max_pixels` tags add nothing over client-side `IMAGE_SIZES` ([benchmarks](docs/benchmarks.md#recommendation)). |
| `IMAGE_SIZES` | *(unset)* | Long-edge presets (pixels, 64–8192) advertised as `image_sizes` in `/v1/info`, comma-separated, the recommended one marked with a trailing `*`, e.g. `448,672*,1024`. Without a `*` the largest is recommended; more than one `*` or a bad value fails at startup. Each entry must be plain digits with at most one trailing `*` (`^\d+\*?$`), so `672**`, `+448`, `4_48` and `672 *` are rejected. The client resizes; the service does not. Unset: no `image_sizes`. Recommended on the Jetson: `448,560*,1024` (560 px: median 8.3 s instead of 13.9 s at 1024 px with the same accuracy; see [docs/benchmarks.md](docs/benchmarks.md#image-size-sweep-and-qwen3-vl-2b-2026-10-18)). |
| `ECHO_DELAY_MS` | `0` | Echo only: delay before answering. When above 0, `/v1/info` reports it as `avg_latency_ms` (and in every `models` entry), so clients can test progress estimates without a model. It is also the answer time echo's cold-start check assumes. |
| `ECHO_MODEL` | `echo` | Echo only: name of echo's default model (`model_used`, the default `models` entry). Set it to `qwen2.5vl-jetson:3b` to mimic the Jetson. |
| `ECHO_LOAD_MS` | `0` | Echo only: simulated load time when a request switches models (needs `ALLOWED_MODELS`). Echo holds one "loaded" model, starting with the default; a request for another one is a cold start under the same rule as Ollama: it waits `ECHO_LOAD_MS` when `ECHO_LOAD_MS` + `ECHO_DELAY_MS` fit in 90% of `BACKEND_TIMEOUT_S`, otherwise it gets 503 "model '…' is loading" at once with `Retry-After` = the remaining load time in whole seconds, and the load finishes in the background. `0` switches instantly. With `ALLOWED_MODELS` set, echo also handles one request at a time like Ollama: one may wait behind the request in flight, more get 503 "queue full", and a queued request whose wait (`ECHO_DELAY_MS` per request ahead, its own answer, the in-flight request's remaining load, plus `ECHO_LOAD_MS` for a different model) would exceed 90% of `BACKEND_TIMEOUT_S` gets 503 at once. Queue-full and switch-busy `Retry-After` adds `ECHO_LOAD_MS` when the refused request switches models. |
| `ECHO_INSTALLED_MODELS` | *(all of `ALLOWED_MODELS`)* | Echo only: comma-separated models echo pretends are installed, to test the not-installed fallback. Allowlisted models missing here are left out of `models` and fall back to the default. |
| `ECHO_BRAND` | *(unset)* | Echo only: raw `brand` on the suggestion. Unset omits the key (the response still has `""`). `json:` plus JSON sends null, `""`, or `{"value":"..."}`. Any other string is literal, including empty, longer than 128 characters, and unusual characters. |
| `ECHO_MODEL_NUMBER` | *(unset)* | Echo only: same as `ECHO_BRAND`, for `model_number`. |
| `ECHO_SERIAL_NUMBER` | *(unset)* | Echo only: same as `ECHO_BRAND`, for `serial_number`. |
| `OLLAMA_URL` | `http://ollama:11434` | Base URL the app reads. The x86 Ollama profile sets `http://ollama:11434`. The Jetson image sets `http://127.0.0.1:11434`. |
| `OLLAMA_HOST` | `0.0.0.0:11434` on the server image | Ollama server bind address, and the address the `ollama` CLI uses. Not read by the app. |
| `OLLAMA_MODEL` | `moondream` (`qwen2.5vl-jetson:3b` in the Jetson image) | Model the app requests, and the default tag `ollama-init` pulls. `qwen2.5vl:3b` (or another Ollama vision tag) is an option: better output, slower. Pull it first: `ollama pull <model>`. |
| `OLLAMA_EXTRA_MODEL` | *(unset)* | Ollama profile only. Extra library tag for `ollama-init` to pull, for example `qwen2.5vl:3b`. |
| `OLLAMA_WARMUP` | `1` | Ollama only: load the model in the background at startup. Until loaded, `/v1/info` has `"ready": false` and `/v1/suggest` returns 503 + `Retry-After`. `0` skips warm-up. |
| `OLLAMA_QUEUE_SIZE` | `1` | Ollama only: requests allowed to wait behind the one in flight; more get 503 `backend_unavailable` + `Retry-After`. A request is also refused at once when the slowest recent answers, plus any load the in-flight request and a background reload still owe, would not fit in 90% of `BACKEND_TIMEOUT_S`. |
| `OLLAMA_RETRY_AFTER_S` | `5` | Ollama only: `Retry-After` value for warming-up/busy 503s. |
| `OLLAMA_NUM_PREDICT` | `256` | Ollama only: most tokens the model may generate per request. Caps runaway output (small models sometimes pad `format=json` answers with whitespace until the timeout). `0` removes the cap. |
| `OLLAMA_TEMPERATURE` | `0` | Ollama only: sampling temperature. |
| `OLLAMA_KEEP_ALIVE` | `30m` (`-1` in the Jetson image) | Ollama only: how long Ollama keeps the model loaded after a request (duration, or seconds; `-1` = forever). On a board dedicated to the service use `-1`, so the first request after an idle spell does not wait for the model to load. |
| `OLLAMA_JSON_FORMAT` | `schema` | Ollama only: `schema` (or `1`) sends the Feature 9 v3_lean JSON schema as `format`. The category enum is `context.categories` when the client sends them, otherwise the v3_lean list. Tags are at most 3 and the description at most 100 characters, plus `brand`, `model_number`, `serial_number`. The prompt shows no JSON template, because small models copy a template back instead of answering. `json` sends `format: "json"`. `0` lets the model answer freely; the service still extracts the first JSON object, or derives fields from plain text. |
| `OLLAMA_PLAIN_RETRY` | `1` | Ollama only: if the answer to the JSON prompt is unusable, ask once more with a short plain-text prompt and derive a low-confidence name and description from the text. It retries when generating took at most 40% of `BACKEND_TIMEOUT_S` (time Ollama spent loading the model does not count) and enough time is left. `0` disables the retry. |
| `OLLAMA_LOG_RAW` | `0` | Ollama only: `1` logs every raw model answer at DEBUG. Answers that cannot be used are always logged at WARNING. Logged text is cut to 2 KB; the image, the request token, and `brand` / `model_number` / `serial_number` values are never logged. |
| `FALLBACK_BACKEND` | `ollama` | `none`, `ollama`, or `cloudflare`. `ollama` fails over from Cloudflare and requires `BACKEND=cloudflare`. `cloudflare` fails over from Ollama and requires `BACKEND=ollama`. `none` with `BACKEND=ollama` is the local-only mode and makes no Cloudflare call. |
| `FAILOVER_ON_COLD` | `false` | When the fallback is Cloudflare, also fail over on Ollama warm-up or a cold model load. The load keeps running. Default `false`: those requests stay a local 503 + `Retry-After`. |
| `CF_ACCOUNT_ID` | *(unset)* | Cloudflare account id. When it or the token is missing, startup logs one warning and serves from Ollama. Never logged. |
| `CF_API_TOKEN` | *(unset)* | API token scoped to Workers AI. When it is set, the default path sends photos to Cloudflare. Never logged, never returned, never put in an error. |
| `CF_ACCOUNT_ID_FILE` | *(unset)* | File whose contents are the account id when `CF_ACCOUNT_ID` is unset. A missing or unreadable file does not crash startup. |
| `CF_API_TOKEN_FILE` | *(unset)* | File whose contents are the API token when `CF_API_TOKEN` is unset. Same failure rule as `CF_ACCOUNT_ID_FILE`. The Jetson example uses `/var/lib/autofill/cf-api-token` (host file `/opt/autofill/data/cf-api-token`, mode 600). |
| `CF_MODEL` | `@cf/google/gemma-4-26b-a4b-it` | Workers AI model id. Confirmed default: Gemma 4 26B, thinking off, Apache 2.0, free plan. Llama 3.2 Vision and Llama 4 Scout are rejected (case-insensitive). |
| `CF_TIMEOUT_S` | `20` | Per-attempt Cloudflare timeout. Must be a finite number greater than 0. With Ollama as the fallback the effective slice shrinks so the request stays inside `REQUEST_DEADLINE_S`. |
| `CF_DAILY_NEURON_BUDGET` | `6000` | Neurons this process may spend per UTC day. About 930 Gemma 4 photos at 6.4 Neurons. `0` disables Cloudflare. |
| `CF_BUDGET_STATE_FILE` | `/var/lib/autofill/cf-neuron-budget.json` | Persistent counter. Not `/tmp`. A corrupt or unwritable file spends the day. |
| `REQUEST_DEADLINE_S` | `45` | Total deadline when Cloudflare is primary and Ollama is the fallback. Must be under Apotheke's 50 s client timeout and fit in `BACKEND_TIMEOUT_S`. |
| `OLLAMA_WARM_RESERVE_S` | `15` | Seconds held for a warm Ollama fallback. The Jetson warm p95 at 560 px is about 11 s. |
| `OLLAMA_COLD_RESERVE_S` | `35` | Seconds held when the local model is cold or loading (measured cold Qwen 25–32 s). |

`ECHO_*` are read on every request (as is `ALLOWED_MODELS` on echo; the Ollama backend reads it at startup). Example: `ECHO_MODE=slow BACKEND_TIMEOUT_S=2 uvicorn app.main:app --port 8080`.

### Model and image size choice

Added in API v1.2 (see [docs/api-v1.md](docs/api-v1.md#choosing-a-model-v12)); opt-in: without `ALLOWED_MODELS` and `IMAGE_SIZES`, `/v1/info` is unchanged and `/v1/suggest` behaves as before, only adding `model_used`.

- `GET /v1/info` lists `models` (default first, then installed allowlisted models, each with `default`, `loaded`, `state` (`cold` / `loading` / `warm`), `size_vram` when Ollama reports it, and, once measured, its own `avg_latency_ms`) and `image_sizes` (`long_edge` presets with one `recommended`). `loaded` means "listed in `/api/ps`" (resident or still loading). `state` is `warm` after a background load or a suggest for that model finishes successfully; a model only listed in `/api/ps` stays `loading`. `size_vram` is bytes from `/api/ps`; `0` on a warm model or the loaded production model makes `/healthz` answer `503` `{"status":"degraded"}` (Ollama started before CUDA and is on CPU). `/healthz` itself stays `{"status":"ok"}` or `{"status":"degraded"}` and does not list models.
- `POST /v1/suggest` takes `context.model`. Not allowlisted or not installed → the default model. Every answer has `model_used`.
- A model other than the loaded one is a cold start: it waits for the load when that model's load estimate plus slowest recent answer fit in 90% of `BACKEND_TIMEOUT_S`, else 503 + `Retry-After` at once while it loads in the background. With Qwen on the Jetson: fast 503 at 25 s, a wait at 45 s. Timings are kept per model; the top-level `avg_latency_ms` stays the default model's.
- The cold-start 503's `Retry-After` is that model's remaining load time: max(15 s, its last measured load) plus a margin of max(2 s, 30%), minus the time its background load has already run, at least 1 s and at most `BACKEND_TIMEOUT_S` + 5 s (50 at 45 s), so a client that only retries within its timeout still retries.
- A switch that arrives while another background load is running is queued and loaded right after it (one load at a time, one queued slot, latest wins), so the client's retry finds its model loading or loaded.
- A request queued behind one that is itself loading, or behind a background reload, is refused with `503` and `Retry-After` when that remaining load would make it miss 90% of `BACKEND_TIMEOUT_S` (it used to be admitted and then time out). Queue-full and switch-busy `Retry-After` includes the load budget of the model being switched to (estimate plus max(2 s, 30%)) and is capped at `BACKEND_TIMEOUT_S` + 5 s.
- `/v1/info` does not reload the default while a picked allowlisted model is loaded, or while a model switch, background load or request is in flight (checked before and after reading `/api/ps`), so it does not evict the client's choice. The in-flight part applies without `ALLOWED_MODELS` too: the reload of an unloaded default then waits until no request is in flight.

Trying it on echo (no model needed):

```bash
ALLOWED_MODELS=moondream,qwen3-vl:2b ECHO_MODEL=qwen2.5vl-jetson:3b \
ECHO_INSTALLED_MODELS=moondream IMAGE_SIZES='448,672*,1024' \
ECHO_LOAD_MS=15000 ECHO_DELAY_MS=13500 BACKEND_TIMEOUT_S=25 \
uvicorn app.main:app --port 8080
curl -s localhost:8080/v1/info            # models: qwen (default), moondream
curl -s -F image=@photo.jpg -F 'context={"model":"moondream"}' localhost:8080/v1/suggest
# 25 s: 503 "model 'moondream' is loading", Retry-After: 15; again after 15 s: model_used moondream
# BACKEND_TIMEOUT_S=45: the same request waits ~15 s + 13.5 s and answers 200
```

### Backends

| `BACKEND` | Status |
|---|---|
| `echo` | Implemented. Deterministic: category = first `context.categories` entry (else `Misc`), tags = first 3 `context.tags` (else `echo`, `sample`), name/description derived from image hash, type and size. |
| `ollama` | Implemented. Sends the image to Ollama `/api/chat` (temperature 0) with the Feature 9 v3_lean JSON schema as `format` (category enum from `context.categories` when sent, otherwise the v3_lean list; tags at most 3; description at most 100 characters; `brand` / `model_number` / `serial_number`). The short prompt is that v3_lean text: it names the fields in words, shows no JSON template, asks for a generic object name, and interpolates `context.locale` and those categories. With no categories the prompt matches the winning v3_lean bench file. The answer is extracted (tolerates code fences/prose, a JSON object cut off by `OLLAMA_NUM_PREDICT`, renamed keys such as `object`/`caption`/`keywords` and camelCase `modelNumber` / `serialNumber`, and schema placeholders like `"str"`), validated and clamped: max 10 unique tags of ≤40 chars, name ≤120, category ≤80, description ≤500, confidence in [0,1]; `brand`, `model_number` and `serial_number` are trimmed, stripped of controls and lone surrogates, and hard-cut to 128 characters, or `""` when missing or unreadable. Fields nested as `{"value","confidence"}` (in any combination) are unwrapped, and a top-level `confidence` fills in for name/category/description without one. When `context.categories` is given, the category must be one of them: matched case-insensitively, singular/plural (`clothes` → `Clothing`) or by a small English synonym list (`cookware` → `Kitchen`, `laptop` → `Electronics`, `lamp` → `Lighting`) if that target is listed, otherwise dropped. Tags matching `context.tags` the same way take the existing spelling; new tags stay allowed. Tags that are only a prompt word (`material`, `colour`, `brand`, `category`, …) are dropped, and `material: leather` becomes `leather`. A lone common first name ("John") is not used as the name. When the name shares no word with the object phrase that opens the description (`bose` vs "A pair of black headphones…"), that phrase (up to 4 words) becomes the name, at confidence ≤0.5. A description that only repeats the name or category is dropped. If the model ignores the JSON instructions, a low-confidence name/description is derived from its plain-text answer; if the answer is unusable (e.g. `{}`), one retry with a short plain-text prompt is made while time allows (`OLLAMA_PLAIN_RETRY`). Still unusable → 503 `backend_error`, with the raw output logged at WARNING (identity values redacted); unreachable → 503 `backend_unavailable`; slower than `BACKEND_TIMEOUT_S` → 504. |
| `cloudflare` | Implemented. Shipped default, with `FALLBACK_BACKEND=ollama`. Posts the photo to the Workers AI REST API when a token is set. See [docs/cloudflare-backend.md](docs/cloudflare-backend.md). |
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
