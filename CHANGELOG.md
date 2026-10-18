# Changelog

All notable changes to this service. The HTTP contract is versioned
separately in [docs/api-v1.md](docs/api-v1.md) (all v1 changes are additive).

## Unreleased

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
