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
Liveness only, no backend call, no auth. → `200 {"status":"ok"}`

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
| `backend` | no | Adapter id (`echo`, `ollama`, …) |
| `max_image_bytes` | no | Upload limit; clients may skip the call for larger images |
| `ready` | no | `false` while the backend is still loading its model (e.g. Ollama warm-up); `/v1/suggest` then answers `503` with `Retry-After`. Also `false` when Ollama has since unloaded the model: the service then reloads it in the background, and `/v1/suggest` still works but its first answer is slower. Absent or `true` means ready. |
| `avg_latency_ms` | no | *Added in v1.1, optional; clients MUST tolerate its absence.* Non-negative integer: the **default model's** typical time in milliseconds for one `/v1/suggest` generation on the loaded model, model load time excluded. Ollama reports a moving average of recent requests (70% previous, 30% latest), so the field is **absent until the first suggestion has finished** after a service start. Echo reports `ECHO_DELAY_MS` when it is above 0. Meant for estimated progress bars: fill by elapsed time against this value, hold below 100% until the answer arrives, and fall back to an indeterminate spinner when absent. It is not a deadline: cold starts (model load), queueing and the plain-text retry can take longer. |
| `models` | no | *Added in v1.2, optional; absent unless the server sets `ALLOWED_MODELS`.* The models a client may pick with `context.model`: the default model first, then the other allowlisted models the backend has installed (Ollama: `GET /api/tags`, cached 30 s; if that fails before it ever answered, only the default is listed). Each entry: `id` (string, required; the value to send as `context.model`), `default` (boolean, required; exactly one entry is `true`), `loaded` (boolean, optional; whether the model is in memory now, absent when unknown), `avg_latency_ms` (integer, optional; like the top-level field, for this model, absent until measured). |
| `image_sizes` | no | *Added in v1.2, optional; absent unless the server sets `IMAGE_SIZES`.* Long-edge presets in pixels the client may resize photos to before upload, ascending: `[{"long_edge":448,"recommended":false},…]`, exactly one `recommended: true`. The service only advertises them; it does not resize, and there is no per-request size or `max_pixels` setting. |

A service with `ALLOWED_MODELS=moondream` and `IMAGE_SIZES=448,672*,1024`
(v1.2) also lists the models and image sizes a client may choose:

```json
{"name":"ollama-qwen2.5vl-jetson:3b","version":"1","capabilities":["name","category","tags","description"],
 "backend":"ollama","max_image_bytes":10485760,"ready":true,"avg_latency_ms":13540,
 "models":[{"id":"qwen2.5vl-jetson:3b","default":true,"loaded":true,"avg_latency_ms":13540},
           {"id":"moondream","default":false,"loaded":false,"avg_latency_ms":4300}],
 "image_sizes":[{"long_edge":448,"recommended":false},{"long_edge":672,"recommended":true},
                {"long_edge":1024,"recommended":false}]}
```

With models configured, `ready` is `true` when the default **or any other
allowlisted** model is loaded: a model a client picked is not evicted by
reloading the default.

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
 "description":{"value":"Red Bosch 18V drill","confidence":0.7}}
```

`model_used` (v1.2, optional string) names the model that produced the answer.
Old clients ignore it.

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
  backend_unavailable` ("model '…' is loading") with `Retry-After` (at least
  15 s) at once, and the model loads in the background. With Qwen on the Jetson
  that means a fast 503 at 25 s and a wait at 45 s.
- A request for another model than the one **in flight** must also wait for
  the requests ahead of it: it is refused at once (`503`, "a model switch would
  time out") when their slowest recent times plus the new model's load and
  answer exceed 90% of `BACKEND_TIMEOUT_S`.
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
| 503 | `backend_unavailable` | backend not implemented, unreachable, still warming up, reloading a model it had unloaded, or loading the model a client picked with `context.model`, when loading plus answering would not fit in the timeout (otherwise the request waits for the load), or busy (queue full, or a queued request would not finish within the timeout). Warming-up, reloading and busy responses carry `Retry-After: <seconds>` |
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
