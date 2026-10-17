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

| Field | Required | Notes |
|---|---|---|
| `name` | yes | Human-readable service/backend name, e.g. `moondream-jetson` |
| `version` | yes | Always `"1"` |
| `capabilities` | yes | Subset of `name`, `tags`, `category`, `description` |
| `backend` | no | Adapter id (`echo`, `ollama`, …) |
| `max_image_bytes` | no | Upload limit; clients may skip the call for larger images |
| `ready` | no | `false` while the backend is still loading its model (e.g. Ollama warm-up); `/v1/suggest` then answers `503` with `Retry-After`. Also `false` when Ollama has since unloaded the model: the service then reloads it in the background, and `/v1/suggest` still works but its first answer is slower. Absent or `true` means ready. |

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
| 503 | `backend_unavailable` | backend not implemented, unreachable, still warming up, or busy (queue full). Warming-up and busy responses carry `Retry-After: <seconds>` |
| 503 | `backend_error` | backend answered but its output was unusable (e.g. model returned invalid/empty JSON, upstream HTTP error) |
| 504 | `backend_timeout` | backend exceeded `BACKEND_TIMEOUT_S` (default 25 s) |

Check order: auth → missing image → size → type → context → backend.
Recommended client timeout: ~30 s (server-side backend timeout is 25 s so the
server answers `504` before the client gives up).
