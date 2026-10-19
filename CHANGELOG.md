# Changelog

All notable changes to this service. The HTTP contract is versioned
separately in [docs/api-v1.md](docs/api-v1.md) (all v1 changes are additive).

## [1.4.0] - 2026-10-19

### Added

- **Optional Cloudflare Workers AI backend (API v1.4).** `BACKEND=cloudflare`
  calls the Workers AI REST API directly. `FALLBACK_BACKEND=cloudflare` (default
  `none`) fails over from Ollama on unreachable, degraded GPU residency (warm
  model or the loaded production model at `size_vram` 0), timeout, upstream
  5xx, and busy/queue-full. Cold/loading fails over only when
  `FAILOVER_ON_COLD=true` (default false); Ollama warm-up keeps running and
  traffic returns to Ollama once it is warm. Client 4xx never fails over. The
  Ollama wait plus the Cloudflare call stays under `BACKEND_TIMEOUT_S` + 5 s.
  Provisional default model `@cf/google/gemma-4-26b-a4b-it` (Apache 2.0, free
  plan, vision, `response_format` JSON schema), pending ITAdvisor's accuracy
  comparison. `CF_MODEL` stays configurable. Llama 3.2 Vision and Llama 4 Scout
  are rejected. See [docs/cloudflare-backend.md](docs/cloudflare-backend.md).
- **Contract.** `/v1/suggest` gains optional `backend_used` (`ollama` or
  `cloudflare`). `model_used` is the Cloudflare model id when Cloudflare
  served. `/v1/info` gains `backends` (primary and fallback state, and whether
  the fallback can serve). `/healthz` stays a local probe: `503`
  `{"status":"degraded"}`, or `{"status":"degraded","serving":"fallback"}`
  when the primary is degraded and the fallback can serve. `version` stays
  `"1"`.

### Fixed

- **Health-probe wording.** An Ollama error during `/healthz` keeps the last
  result. It does not always leave the probe at `ok`.
- **Category names.** Newlines in a client category are folded onto one prompt
  line so the name cannot break the prompt.
- **v3_lean latency.** The changelog's v3_lean cost is the measured +0.7 s.

## [1.3.0] - 2026-10-19

### Added

- **Suggest `brand`, `model_number`, `serial_number` (API v1.3).** The Ollama
  prompt and `format` schema are the Feature 9 v3_lean text when the client
  sends no `context.categories`: that prompt matches the winning bench file,
  and the schema caps tags at 3 and the description at 100 characters.
  Client categories replace that bench list in the prompt and the schema enum
  (trimmed, de-duplicated, at most 50). HTTP response clamps stay name ≤120,
  category ≤80, at most 10 tags of ≤40 characters, and description ≤500.
  Successful suggestions include the three strings (trimmed, controls and lone
  surrogates dropped, hard-cut to 128 characters, `""` when missing or
  unreadable). The values are never logged, including with `OLLAMA_LOG_RAW=1`
  and when the raw answer is truncated or uses spaced keys. GPU-confirmed warm
  medians for the production model `qwen2.5vl-jetson:3b`, 19 photos each:
  base-560 8.58 s, v3_lean-560 9.27 s (+0.69 s); base-1024 13.89 s,
  v3_lean-1024 14.62 s (+0.73 s). Echo exposes them through `ECHO_BRAND`,
  `ECHO_MODEL_NUMBER` and `ECHO_SERIAL_NUMBER` (`json:` prefix for null or a
  nested value).

- **Per-model `state` and `size_vram`.** `models` entries on `GET /v1/info`
  gain `state` (`cold` | `loading` | `warm`). `loaded` still means "listed in
  `/api/ps`" (resident or still loading). `state` is `warm` after a background
  load of that model finishes successfully (startup warm-up or a later reload)
  or after a suggest for it finishes successfully. A model that is only listed
  in `/api/ps` stays `loading`. `size_vram` is the byte count from `/api/ps`,
  the source of truth for GPU residency. `/healthz` stays status-only
  (`{"status":"ok"}` or `503 {"status":"degraded"}`) and does not list models.
  It answers degraded when a warm model or the loaded production model has
  `size_vram` 0, which is what a CPU-only runner looks like after Ollama
  starts before CUDA.

### Fixed

- **In-flight admission counted too little load (F5).** A request queued
  behind one that is itself loading, or behind a background reload, is refused
  with `503` and `Retry-After` when the remaining load would push it past 90%
  of `BACKEND_TIMEOUT_S`, instead of being admitted and later timing out.
- **Queue-full / switch-busy `Retry-After` omitted the switch load (F6).**
  That value now adds the load budget of the model being switched to (estimate
  plus max(2 s, 30%)) and is capped at `BACKEND_TIMEOUT_S` + 5 s.

### Documentation

- **Brand/model/serial prompt variants and small-VLM shortlist (2026-10-19).** `docs/benchmarks.md`
  adds Feature 9 results: four prompt/schema variants on `qwen2.5vl-jetson:3b` at 560 and 1024 px.
  Winner **`v3_lean`** (+0.7 s, fewest invented values). `v2_omit` invents heavily — do not use.
  Small-model pulls: MiniCPM-V4 runs but category accuracy lags; library `gemma3:4b` needs a newer
  Ollama. InternVL3/SmolVLM2 are not inherently CPU-only. The earlier ~80–110 s/photo was the
  18:22–19:28 CPU-only Ollama boot window; after CUDA restore they run on GPU (~19.5 s InternVL,
  ~17 s SmolVLM for a single 560 px request) and still are not allowlisted. Production stays
  on Qwen with `ALLOWED_MODELS=qwen2.5vl-jetson:3b`. New scripts: `bench/brand_variants.py`,
  `bench/brand_sweep.py`, `bench/brand_sweep.sh`, `bench/score_brand.py`,
  `bench/photos/brand_readability.json`. Raw: `bench/results/brandserial-2026-10-19/`,
  `bench/results/modelbench-2026-10-19/`.
  GPU retime after that boot window confirms Feature 9 medians (v3_lean +0.7 s).

- **Jetson deployment guide (PR #9).** `docs/deploy-jetson.md` documents the production setup on the Jetson
  Xavier NX: platform, Ollama drop-in, the `qwen2.5vl-jetson:3b` model, `service.env`, building
  (`apotheke-autofill:jetson-<sha>`), switching with rollback, verifying, redeploying, rolling back, the 30 Mbit/s
  inbound cap, the self-hosted runner and reboot behaviour. New scripts in `deploy/jetson/`: `build-image.sh`,
  `run-service.sh` (keeps the old container as `autofill-prev` and restores it if the new one fails or the switch
  is interrupted), `service.env.example`, the Ollama drop-in, `dl-cap.sh` and `dl-cap.service`. Deployed on the
  board at `517352f` with `IMAGE_SIZES=448,560*,1024` and
  `ALLOWED_MODELS=qwen2.5vl-jetson:3b`, which lists only the default and turns on `models` in `/v1/info`. `bench/verify.sh` no longer prints
  Ollama's spinner.

- **Image-size sweep and brand/serial benchmark (2026-10-18).** `docs/benchmarks.md`
  gains a section with hardware/software versions, the exact prompt, schema and
  request settings, and results for `qwen2.5vl-jetson:3b` at `max_pixels`
  501760/401408/301056/200704 and at 1024/672/560/448 px uploads on 19 photos
  (10 new ones with brand names, model and serial numbers, with ground truth).
  Recommendation: `IMAGE_SIZES=448,560*,1024` (560 px: median 8.3 s instead of
  13.9 s, same accuracy), `ALLOWED_MODELS` unset. `qwen3-vl:2b` does not fit in
  memory with Ollama 0.15.6 and must not be allowlisted. New scripts:
  `bench/sweep.sh`, `bench/sweep.py`, `bench/score.py`, `bench/make_sets.py`;
  Modelfiles for the extra tags in `bench/qwen2.5vl-jetson/`; raw results in
  `bench/results/sweep-2026-10-18/`.

### Fixed

- **`deploy/jetson/run-service.sh`: refuse a switch when port 8080 is taken; wall-clock `WAIT_S`.** After the old
  container is stopped, the script refuses (and restores it) if `HEALTH_URL` still answers, because something else
  holds the port and its answer would be taken for the new container. On a first deploy it refuses before starting
  anything. The health wait now counts wall-clock time, so a hanging `/healthz` can't stretch `WAIT_S`. The deploy
  scripts' fake-docker test harness is now in `deploy/jetson/tests/` and runs in CI (`deploy-scripts` job).

- **`deploy/jetson/run-service.sh`: validate `WAIT_S`; bind-error hint; test harness cleans up (QA of #14).**
  `WAIT_S` must be a positive whole number of seconds, at most 6 digits; anything else (`abc`, `1.5`, `0`, a number
  too large for shell arithmetic) now exits 1 with a clear message before any container is touched, instead of a
  cryptic shell error mid-switch. When a new container fails and its logs show `address already in use` (a foreign
  process on port 8080 that doesn't answer `HEALTH_URL` with 2xx, so the port check can't see it), the script prints
  a hint to check `ss -ltnp 'sport = :8080'`. `deploy/jetson/tests/run-tests.sh` keeps all scratch dirs under one
  temporary parent removed on exit, also on Ctrl-C, after stopping the running case (it left about 45 dirs in `/tmp`
  per run), and gains 22 cases (65 in total). `docs/deploy-jetson.md`: port-check limits and the hint, `WAIT_S`
  rule, wording, and the harness run time (about 1 min 30 s, not two minutes).

- **Cold-start `Retry-After` per model (Ollama).** A 503 "model '…' is
  loading" now sends that model's remaining load time: max(15 s, its last
  measured load) plus a margin of max(2 s, 30%), so a load up to 30% slower
  than the last still fits, minus how long its background load has already
  run, at least 1 s (20 s for an unmeasured model), and capped at
  `BACKEND_TIMEOUT_S` + 5 s, the recommended client timeout (50 at 45 s), so
  Apotheke, which never retries when `Retry-After` exceeds its timeout,
  still retries. The cap also applies to a switch queued behind another load. It was always 15 s (or `OLLAMA_RETRY_AFTER_S` if higher), so a
  client's single retry could land mid-load for a model that loads slower.
  `OLLAMA_RETRY_AFTER_S` no longer applies to cold-start 503s.
- **`/v1/info` no longer evicts a model being switched to (Ollama).** While a
  model switch or background load is in flight, including a request waiting
  for its model to load, `/v1/info` reports `ready: false` if `/api/ps` does
  not list an allowlisted model yet, but no longer queues a reload of the
  default model that would evict the model being loaded. Work in flight is
  checked before and after the `/api/ps` read, so a load or request that
  finishes during it cannot trigger the reload either. The guard also
  applies without `ALLOWED_MODELS`: the reload of an unloaded default waits
  while a request is in flight.
- **A switch during another background load is queued (Ollama).** The
  requested model used to be dropped when another background load was
  running, although the 503 said it was loading, so the retry paid a second
  cold cycle. It is now loaded right after the running load (one at a time,
  one queued slot, latest request wins), and its `Retry-After` includes the
  rest of the running load.
- **Echo serializes requests with `ALLOWED_MODELS` set.** Like Ollama with
  one parallel slot and a queue of one, concurrent requests for different
  models now follow the queued-switch rule (wait, "queue full", or "a model
  switch would time out"). Single requests, and echo without
  `ALLOWED_MODELS`, are unchanged.
- **Strict `IMAGE_SIZES` parsing.** Each entry must match `^\d+\*?$`;
  `672**`, `+448`, `4_48` and `672 *` now fail at startup instead of being
  accepted.

### Added

- **Model choice (API v1.2).** `ALLOWED_MODELS` lists models a client may pick
  with the new optional `context.model`; the configured model stays the
  default. `/v1/info` gains optional `models` (default first, then installed
  allowlisted models, with `default`, `loaded` and per-model
  `avg_latency_ms`). Unknown or not-installed models fall back to the default.
  Every `/v1/suggest` answer reports the optional `model_used`.
- **Image size presets (API v1.2).** `IMAGE_SIZES` (e.g. `448,672*,1024`, `*` =
  recommended) is advertised as optional `image_sizes` in `/v1/info`. The client
  resizes; the service does not.
- **Echo model simulation.** Echo reads `ALLOWED_MODELS` / `IMAGE_SIZES` too and
  adds `ECHO_MODEL`, `ECHO_LOAD_MS` and `ECHO_INSTALLED_MODELS`, so clients can
  test model switching, cold starts and fallbacks without a model.

### Changed

- A request for a model other than the loaded one counts as a cold start and
  follows the existing fast-503 / wait rule. Admission timings (moving average,
  last-5 window, load estimate) are tracked per model; the top-level
  `avg_latency_ms` stays the default model's.
- With `ALLOWED_MODELS` set, `/v1/info` reports ready and skips reloading the
  default while any allowlisted model is loaded, so a picked model is not
  evicted.
- `context.model`, when present, must be a string (otherwise 400).

### Unchanged

- Without `ALLOWED_MODELS` and `IMAGE_SIZES`, `/v1/info` is identical to
  before, and requests without `context.model` behave as before (they only
  gain `model_used`).
