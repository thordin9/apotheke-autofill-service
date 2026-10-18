# Changelog

All notable changes to this service. The HTTP contract is versioned
separately in [docs/api-v1.md](docs/api-v1.md) (all v1 changes are additive).

## Unreleased

### Documentation

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

- **Cold-start `Retry-After` per model (Ollama).** A 503 "model '…' is
  loading" now sends that model's remaining load time: max(15 s, its last
  measured load) minus how long its background load has already run, at
  least 1 s. It was always 15 s (or `OLLAMA_RETRY_AFTER_S` if higher), so a
  client's single retry could land mid-load for a model that loads slower.
  `OLLAMA_RETRY_AFTER_S` no longer applies to cold-start 503s.
- **`/v1/info` no longer evicts a model being switched to (Ollama).** While a
  model switch or background load is in flight, including a request waiting
  for its model to load, `/v1/info` reports `ready: false` if `/api/ps` does
  not list an allowlisted model yet, but no longer queues a reload of the
  default model that would evict the model being loaded.

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
