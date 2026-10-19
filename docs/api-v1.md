# Apotheke Autofill API v1

Machine-readable spec: [`../openapi.yaml`](../openapi.yaml) (OpenAPI 3.1). This
document is normative; if the two disagree, file a bug.

## Principles

- **Swappable.** Any backend (echo, Ollama/Moondream, CLIP, cloud) exposes the same three endpoints.
- **Everything is optional.** A `200` with `{}` is valid: "no suggestions".
- **Fail soft.** Clients (Apotheke) MUST treat any non-`200` status, timeout,
  connection error, or body that is not a JSON object matching the schema as
  *no suggestions*, and must never block the upload on this service.
- **Forward compatible.** Clients MUST ignore unknown fields. Changes within v1
  are additive only (new optional fields, new capabilities, new error codes).
  Breaking changes get a new `/v2` prefix; `version` stays `"1"` on `/v1`.

## Authentication

Optional, enforced by **service-wide middleware**, independent of the backend.
If the server has `AUTOFILL_TOKEN` set, every route except `/healthz` (including
unknown routes) requires `Authorization: Bearer <token>`; a missing or wrong
token returns `401` JSON `{"error":{"code":"unauthorized",...}}`. If
`AUTOFILL_TOKEN` is unset or empty, no auth is applied and any header is ignored.

## Endpoints

### `GET /healthz`
No auth. → `200 {"status":"ok"}` when the process can serve.

Ollama reads `GET /api/ps` for this probe. A failed read keeps the last
result: the probe stays `ok` when it has not already seen a degraded
residency, and it stays `degraded` when that was the last successful read.
A down Ollama therefore does not by itself flip a healthy probe. The body
is `{"status":"ok"}` or `{"status":"degraded"}`. When the primary is
degraded and a configured fallback can serve, the body is
`{"status":"degraded","serving":"fallback"}` and the status is still 503
(a deploy check that treats 503 as unhealthy keeps working). Model ids,
`state`, `size_vram` and latency are on `GET /v1/info`, which requires auth
when `AUTOFILL_TOKEN` is set.

`503 {"status":"degraded"}` means a model that is **warm**, or the loaded
production model, is in Ollama's memory with `size_vram` **0**. The runner
has no GPU memory. That is what happens when Ollama starts before CUDA and
falls back to CPU. `size_vram` is the source of truth; a missing `size_vram`
does not degrade the probe. Echo never reports degraded.

### `GET /v1/info`
Used by Apotheke's "Test connection". Requires auth when enabled, so a wrong
token shows up as `401` here.

```json
{"name":"echo","version":"1","capabilities":["name","category","tags","description"],
 "backend":"echo","max_image_bytes":10485760}
```

An Ollama service that has answered a few requests also reports readiness and
its typical generation time:

```json
{"name":"ollama-qwen2.5vl-jetson:3b","version":"1","capabilities":["name","category","tags","description"],
 "backend":"ollama","max_image_bytes":10485760,"ready":true,"avg_latency_ms":13540}
```

| Field | Required | Notes |
|---|---|---|
| `name` | yes | Human-readable service/backend name, e.g. `moondream-jetson` |
| `version` | yes | Always `"1"` |
| `capabilities` | yes | Subset of `name`, `tags`, `category`, `description` |
| `backend` | no | Adapter id of the primary (`echo`, `ollama`, `cloudflare`, …) |
| `backends` | no | *Added in v1.4, optional.* `primary` (`id`, `state` of `ok`, `warming`, `degraded`, `unavailable`) and `fallback` (`id` of `none` or `cloudflare`, `state` of `off` or `ready`, `can_serve`). `can_serve` is true only when a fallback is configured. Absent on servers older than v1.4. |
| `max_image_bytes` | no | Upload limit; clients may skip the call for larger images |
| `ready` | no | `false` while the backend is still loading its model (e.g. Ollama warm-up); `/v1/suggest` then answers `503` with `Retry-After`. Also `false` when Ollama has since unloaded the model: the service then reloads it in the background, and `/v1/suggest` still works but its first answer is slower. Absent or `true` means ready. |
| `avg_latency_ms` | no | *Added in v1.1, optional; clients MUST tolerate its absence.* Non-negative integer: the **default model's** typical time in milliseconds for one `/v1/suggest` generation on the loaded model, model load time excluded. Ollama reports a moving average of recent requests (70% previous, 30% latest), so the field is **absent until the first suggestion has finished** after a service start. Echo reports `ECHO_DELAY_MS` when it is above 0. Meant for estimated progress bars: fill by elapsed time against this value, hold below 100% until the answer arrives, and fall back to an indeterminate spinner when absent. It is not a deadline: cold starts (model load), queueing and the plain-text retry can take longer. |
| `models` | no | *Added in v1.2, optional; absent unless the server sets `ALLOWED_MODELS`.* The models a client may pick with `context.model`: the default model first, then the other allowlisted models the backend has installed (Ollama: `GET /api/tags`, cached 30 s; if that fails before it ever answered, only the default is listed). Each entry: `id` (string, required; the value to send as `context.model`), `default` (boolean, required; exactly one entry is `true`), `loaded` (boolean, optional; whether the model is listed in Ollama `/api/ps` now — resident **or** still loading — absent when unknown), `state` (string, optional, v1.3; `cold`, `loading` or `warm`; see below), `size_vram` (integer, optional, v1.3; bytes from `/api/ps` `size_vram`, the source of truth for GPU residency; `0` means the runner has no GPU memory; absent when the model is not in `/api/ps` or Ollama did not send the field), `avg_latency_ms` (integer, optional; like the top-level field, for this model, absent until measured). |
| `image_sizes` | no | *Added in v1.2, optional; absent unless the server sets `IMAGE_SIZES`.* Long-edge presets in pixels the client may resize photos to before upload, ascending: `[{"long_edge":448,"recommended":false},…]`, exactly one `recommended: true`. Each `IMAGE_SIZES` entry must be ASCII digits with at most one trailing `*` (`^\d+\*?$`); anything else (`672**`, `+448`, `4_48`) fails at startup. The service only advertises them; it does not resize, and there is no per-request size or `max_pixels` setting. |

A service with `ALLOWED_MODELS=moondream` and `IMAGE_SIZES=448,672*,1024`
(v1.2) also lists the models and image sizes a client may choose:

```json
{"name":"ollama-qwen2.5vl-jetson:3b","version":"1","capabilities":["name","category","tags","description"],
 "backend":"ollama","max_image_bytes":10485760,"ready":true,"avg_latency_ms":13540,
 "models":[{"id":"qwen2.5vl-jetson:3b","default":true,"loaded":true,"state":"warm","size_vram":4294967296,"avg_latency_ms":13540},
           {"id":"moondream","default":false,"loaded":false,"state":"cold","avg_latency_ms":4300}],
 "image_sizes":[{"long_edge":448,"recommended":false},{"long_edge":672,"recommended":true},
                {"long_edge":1024,"recommended":false}]}
```

With models configured, `ready` is `true` when the default **or any other
allowlisted** model is loaded: a model a client picked is not evicted by
reloading the default.

**`loaded` is not "ready for a fast answer".** Ollama lists a model in
`/api/ps` as soon as its runner is starting, so `loaded: true` during the
load (finding F4). Clients **must use `state`**, not `loaded`:

| `state` | Meaning |
|---|---|
| `cold` | Not in `/api/ps`. |
| `loading` | In `/api/ps`, and this process has not yet finished a background load or a `/v1/suggest` for this model since it entered `/api/ps`. `loaded` may already be `true` while the runner is still starting. |
| `warm` | A background load of this model finished successfully (startup warm-up or a later reload), or a `/v1/suggest` for it finished successfully, since that residency. |

`state` is absent when `/api/ps` could not be read. A row in `/api/ps` by
itself leaves the model `loading`. Echo starts its default model `warm`
because that model is already resident; a switch stays `loading` until the
suggest that loaded it finishes. `size_vram: 0` on a warm model, or on the
loaded production model, makes `/healthz` **degraded** (see above).

### `POST /v1/suggest`
`multipart/form-data`:

| Part | Required | Notes |
|---|---|---|
| `image` | yes | JPEG, PNG, WebP. HEIC/HEIF only if the server has the optional `pillow-heif` package installed, otherwise `415`. Type is sniffed from **magic bytes**; the part's `Content-Type` is ignored. |
| `context` | no | JSON string: `{"categories":[str],"tags":[str],"locale":"en","model":"moondream"}`. All keys optional. `model` (v1.2) must be a string if present (else `400`). |

Clients SHOULD send a JPEG resized to about 1024 px on the long edge (Apotheke
does this); larger images add latency without improving suggestions.

Backends SHOULD prefer values from `context.categories` / `context.tags` (exact
existing spelling) over inventing new ones, and SHOULD answer in `context.locale`.

Response `200`, every field optional; `confidence` is in `[0,1]` and optional:

```json
{"name":{"value":"Cordless drill","confidence":0.82},
 "category":{"value":"Tools","confidence":0.9},
 "tags":[{"value":"bosch","confidence":0.6}],
 "description":{"value":"Red Bosch 18V drill","confidence":0.7},
 "brand":"Bosch","model_number":"","serial_number":""}
```

`model_used` (v1.2, optional string) names the model that produced the answer.
When Cloudflare served the request it is that Cloudflare model id, not the
Ollama tag. Old clients ignore it.

`backend_used` (v1.4, optional string, `ollama` or `cloudflare`) names which
backend produced the answer. Echo omits it. Old clients ignore it.

`brand`, `model_number` and `serial_number` (v1.3, optional strings) are text
printed on the item and clearly readable in the photo. Each is trimmed,
stripped of C0/C1 controls, lone surrogates and bidi embedding, override and
isolate characters, and hard-cut to 128 characters without ending inside a
combining sequence. Missing or unreadable is `""`, not an omitted
field, on a suggestion that has other fields. They are absent on a `{}` "no
suggestions" body. Old clients ignore them. The service never writes these
values to its logs, including when `OLLAMA_LOG_RAW=1`. Logged text drops
identity values even when the key has spaces (`"Brand Name"`), the JSON is
truncated, or the value is an object. The Ollama prompt and `format` schema
are the Feature 9 **v3_lean** text when the client sends no
`context.categories`: that prompt matches
`bench/results/brandserial-2026-10-19/prompts/v3_lean.txt` once that file's
trailing newline is removed. The schema then asks for at most
3 tags and a description of at most 100 characters, and requires `brand`,
`model_number` and `serial_number` with schema `maxLength` 30. When the client
sends `context.categories`, that list (trimmed, de-duplicated, at most 50)
replaces the bench list in the category line and in the schema enum. v3_lean
is the chosen prompt and schema for brand, model number and serial; accuracy
matches the Feature 9 sweep. GPU-confirmed warm medians for the production
model `qwen2.5vl-jetson:3b`, 19 photos each: base-560 8.58 s, v3_lean-560
9.27 s (+0.69 s); base-1024 13.89 s, v3_lean-1024 14.62 s (+0.73 s). HTTP
clamps on the response stay name ≤120, category ≤80, at most 10 tags of ≤40
characters, and description ≤500. The tighter tag and description caps apply
to the schema sent to Ollama when `OLLAMA_JSON_FORMAT=schema`. A category is
still snapped to `context.categories` when the client sent them.

### Choosing a model (v1.2)

- `context.model` is used when it is the default model, or listed in the
  server's `ALLOWED_MODELS` **and** installed. Names compare like Ollama's
  (`moondream` = `moondream:latest`); `model_used` carries the allowlist spelling.
- Anything else falls back to the default model, with no error: an unknown
  name, an allowlisted model that is not installed, an empty string, or any
  `context.model` when `ALLOWED_MODELS` is unset. `model_used` shows the fallback.
- **A model other than the loaded one is a cold start.** The backend holds one
  model in memory, so switching means loading. The usual cold-start rule
  applies: when that model's load estimate (last measured load, at least 15 s)
  plus its slowest recent answer (last 5, 15 s until measured) fit in 90% of
  `BACKEND_TIMEOUT_S`, the request waits for the load; otherwise it gets `503
  backend_unavailable` ("model '…' is loading") with `Retry-After` at once,
  and the model loads in the background. With Qwen on the Jetson that means a
  fast 503 at 25 s and a wait at 45 s.
- **`Retry-After` on a cold start** is that model's expected remaining load
  time in whole seconds, at least 1. Ollama: its load estimate (the last
  measured load of **that** model, at least 15 s) plus a margin of max(2 s,
  30%), so a load up to 30% slower than the last one is still covered, minus
  how long its background load has already been running, so a retry during
  the load gets the time left (e.g. 20, then 15 when retried 5 s later; 30 for
  a model that last took 22.4 s to load). It is **capped at
  `BACKEND_TIMEOUT_S` + 5 s**, the recommended client timeout (50 at 45 s),
  including the queued-switch value below: a client that only retries when
  `Retry-After` is within its timeout (Apotheke) still retries, and a capped
  retry that lands mid-load is no worse than none. E.g. a 40 s measured load
  at 45 s gives 50, not 52. Echo: the remaining simulated load time.
  A client whose own timeout is set below `BACKEND_TIMEOUT_S` + 5 s (e.g.
  Apotheke at 30 s against a 45 s backend) may get a `Retry-After` above its
  timeout and then won't retry; keep the client timeout at
  `BACKEND_TIMEOUT_S` + 5 s or more.
- **A load requested while another background load runs is queued**, not
  dropped: the backend loads one model at a time (it still holds one model)
  and loads the requested one right after the current load, so a client told
  "model '…' is loading" finds it loading or loaded when it retries. The queue
  has one slot: a later request for a third model replaces the queued one.
  Its `Retry-After` adds what is left of the running load (with its margin)
  to its own estimate and margin.
- Echo (with `ALLOWED_MODELS` set) answers one request at a time like Ollama,
  so concurrent requests for different models follow the queued-switch rule
  above (one may wait, more get `503` "queue full").
- While a model switch or background load is in flight (including a request
  waiting for its model to load), `GET /v1/info` reports `ready: false` if no
  allowlisted model is in memory yet but does **not** reload the default
  model, which would evict the model being loaded. The check runs before and
  after the backend reads `/api/ps`, so a load or request that starts or ends
  during that read also skips the reload. This guard applies **without
  `ALLOWED_MODELS` too**: the background reload of an unloaded default waits
  while a request is in flight (that request's chat call loads the model
  anyway) and happens on a later `/v1/info` once nothing is in flight.
- A request for another model than the one **in flight** must also wait for
  the requests ahead of it: it is refused at once (`503`, "a model switch would
  time out") when their slowest recent times plus the new model's load and
  answer exceed 90% of `BACKEND_TIMEOUT_S`.
- **That estimate includes load still unpaid (F5).** If the request in flight
  is itself loading or switching, its remaining load is added, and so is any
  background reload still running. Without that, a trailing request was
  admitted and then timed out (`504`). It is refused with `503` and
  `Retry-After` instead.
- **Queue-full and switch-busy `Retry-After` includes the switch load (F6).**
  It is the in-flight model's average generation time times (requests ahead),
  plus the refused request's load budget when that request is for a different
  model: the load estimate (at least 15 s) plus a margin of max(2 s, 30%).
  The value is capped at `BACKEND_TIMEOUT_S` + 5 s, the same cap as a cold
  start, so a client timeout of 50 s still retries a 45 s backend.
- Timings (`avg_latency_ms`, the recent-answer window, the load estimate) are
  kept **per model**. The top-level `avg_latency_ms` stays the default model's.
- Image size is the client's choice from `image_sizes`; the service never
  resizes.

## Errors

All errors are JSON: `{"error":{"code":"...","message":"..."}}`.

| Status | `code` | When |
|---|---|---|
| 400 | `bad_request` | `image` missing/empty, `context` not valid JSON or wrong shape |
| 401 | `unauthorized` | token required and missing/wrong (`WWW-Authenticate: Bearer`) |
| 413 | `payload_too_large` | image bytes > `MAX_IMAGE_BYTES` env (default 10 MiB = 10485760); advertised as `max_image_bytes` in `/v1/info` |
| 415 | `unsupported_media_type` | magic bytes are not JPEG/PNG/WebP (or HEIC/HEIF without `pillow-heif`) |
| 503 | `backend_unavailable` | backend not implemented, unreachable, still warming up, reloading a model it had unloaded, or loading the model a client picked with `context.model`, when loading plus answering would not fit in the timeout (otherwise the request waits for the load), or busy (queue full, or a queued request would not finish within the timeout). Warming-up, reloading, model-loading and busy responses carry `Retry-After: <seconds>` (for a cold start, the model's remaining load time; see [Choosing a model](#choosing-a-model-v12)) |
| 503 | `backend_error` | backend answered but its output was unusable (e.g. model returned invalid/empty JSON, upstream HTTP error) |
| 504 | `backend_timeout` | backend exceeded `BACKEND_TIMEOUT_S` (default 25 s; 45 s in the Jetson image) |

Check order: auth → missing image → size → type → context → backend.
Recommended client timeout: the server's `BACKEND_TIMEOUT_S` plus 5 s, so the
server answers `504` before the client gives up: 30 s for the generic 25 s
default, 50 s for the Jetson image (45 s, sized so a Qwen request can wait for
a model load of about 15 s plus about 13.5 s to answer).

## Changes within v1

All additive; `version` stays `"1"` and clients MUST tolerate the new fields
being absent.

- **v1.1:** optional `avg_latency_ms` in `GET /v1/info`.
- **v1.2:** optional `models` and `image_sizes` in `GET /v1/info`; optional
  `context.model` in `POST /v1/suggest`, answered with optional `model_used`.
  Without `ALLOWED_MODELS` / `IMAGE_SIZES` set, `/v1/info` is unchanged, and
  requests without `context.model` behave as before.
- **v1.4:** optional `backend_used` (`ollama` or `cloudflare`) on
  `POST /v1/suggest`. `model_used` is the Cloudflare model id when Cloudflare
  answered. Optional `backends` on `GET /v1/info` lists the primary and the
  fallback (`can_serve` is false when no fallback is configured). `GET /healthz`
  is unchanged except when the primary is degraded and the fallback can serve:
  the body is `{"status":"degraded","serving":"fallback"}` with HTTP 503.
  `version` stays `"1"`.
- **v1.3:** optional `brand`, `model_number` and `serial_number` strings on
  `POST /v1/suggest` (empty string when unreadable; ignored by older clients,
  including Apotheke 2.4.1). With no `context.categories`, the Ollama prompt
  and schema are Feature 9 v3_lean (accuracy matches that sweep); client
  categories replace the bench list in the prompt and schema enum when sent.
  The schema caps tags at 3 and description at 100 characters. HTTP clamps
  stay name ≤120, category ≤80, 10 tags, description ≤500. GPU-confirmed warm
  medians for `qwen2.5vl-jetson:3b`, 19 photos each: base-560 8.58 s,
  v3_lean-560 9.27 s (+0.69 s); base-1024 13.89 s, v3_lean-1024 14.62 s
  (+0.73 s). Optional per-model `state` (`cold` | `loading` | `warm`) and
  `size_vram` (bytes from Ollama `/api/ps`) on `models` entries in
  `GET /v1/info`. `state` is `warm` after a successful background load or a
  successful suggest. `GET /healthz` answers `503` `{"status":"degraded"}`
  when a warm model or the loaded production model has `size_vram` 0 (Ollama
  started before CUDA and is on CPU) and does not list models.
