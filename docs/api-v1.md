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
| `avg_latency_ms` | no | *Added in v1.1, optional; clients MUST tolerate its absence.* Non-negative integer: the backend's typical time in milliseconds for one `/v1/suggest` generation on the loaded model, model load time excluded. Ollama reports a moving average of recent requests (70% previous, 30% latest), so the field is **absent until the first suggestion has finished** after a service start. Echo reports `ECHO_DELAY_MS` when it is above 0. Meant for estimated progress bars: fill by elapsed time against this value, hold below 100% until the answer arrives, and fall back to an indeterminate spinner when absent. It is not a deadline: cold starts (model load), queueing and the plain-text retry can take longer. |

### `POST /v1/suggest`
`multipart/form-data`:

| Part | Required | Notes |
|---|---|---|
| `image` | yes | JPEG, PNG, WebP. HEIC/HEIF only if the server has the optional `pillow-heif` package installed, otherwise `415`. Type is sniffed from **magic bytes**; the part's `Content-Type` is ignored. |
| `context` | no | JSON string: `{"categories":[str],"tags":[str],"locale":"en"}`. All keys optional. |

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

## Errors

All errors are JSON: `{"error":{"code":"...","message":"..."}}`.

| Status | `code` | When |
|---|---|---|
| 400 | `bad_request` | `image` missing/empty, `context` not valid JSON or wrong shape |
| 401 | `unauthorized` | token required and missing/wrong (`WWW-Authenticate: Bearer`) |
| 413 | `payload_too_large` | image bytes > `MAX_IMAGE_BYTES` env (default 10 MiB = 10485760); advertised as `max_image_bytes` in `/v1/info` |
| 415 | `unsupported_media_type` | magic bytes are not JPEG/PNG/WebP (or HEIC/HEIF without `pillow-heif`) |
| 503 | `backend_unavailable` | backend not implemented, unreachable, still warming up, reloading a model it had unloaded when loading plus answering would not fit in the timeout (otherwise the request waits for the load), or busy (queue full, or a queued request would not finish within the timeout). Warming-up, reloading and busy responses carry `Retry-After: <seconds>` |
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
