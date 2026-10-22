## Live check results

Post-deploy live check of the paid tier, run by QA on 2026-10-22 between
19:39:10 and 19:40:09 UTC (22:39:10–22:40:09 EEST). The Worker was deployed
with `wrangler deploy --env paid` from `main` at `7eeb998` (Worker version
`42520d5d-954a-42cf-902d-f7737893c9d8`), with no secret step, so the existing
`AUTOFILL_TOKEN` was kept. Target: `https://autofill.example`. The token was read
at runtime into a mode-600 header file, sent only with `curl -H @file`, and
deleted afterwards. No response body or header contained it.

17 requests in total: 1 `/healthz`, 7 `/v1/info`, 9 `POST /v1/suggest`. Only
4 of them reached Workers AI, within the budget of at most 5 photos.

### `/v1/info` (paid)

| Field | Expected | Live |
|---|---|---|
| `tier` | `paid` | `paid` |
| `max_image_bytes` | 1048576 | 1048576 |
| `enforced_image_bytes` | 2031616 | 2031616 |
| `budget.neurons.limit` | 30000 | 30000 |
| `budget.neurons.used` before / after | | 5.845 / 29.291 |
| `budget.resets_at` | next 00:00 UTC | `2026-10-23T00:00:00.000Z` |

`/healthz` returned 200 `{"status":"ok"}`.

### Requests

| # | Check | Input | Status | Latency | Neurons Δ |
|---|---|---|---|---|---|
| 1 | No token | 37,086 B JPEG | 401 `unauthorized`, `WWW-Authenticate: Bearer` | 0.13 s | 0 |
| 2 | Wrong token | 37,086 B JPEG | 401 `unauthorized`, `WWW-Authenticate: Bearer` | 0.10 s | 0 |
| 3 | Image part one byte over the cap | 2,031,617 B (JPEG magic and random bytes) | 413 `image exceeds 2031616 bytes` | 0.37 s | 0 |
| 4 | Unsupported type | 21 B `text/plain` | 415 `unsupported_media_type` | 0.14 s | 0 |
| 5 | Unsupported type | 68 B GIF | 415 `unsupported_media_type` | 0.27 s | 0 |
| 6 | Photo: paracetamol 500 mg pack | 371,116 B JPEG, 1920×1440 | 200 | 2.77 s | 5.800 |
| 7 | Photo: ibuprofen 400 mg pack | 307,681 B JPEG, 1920×1797 | 200 | 3.10 s | 5.964 |
| 8 | Photo: cordless drill (opened) | 1,001,245 B JPEG, 1920×1440 | 200 | 2.20 s | 5.800 |
| 9 | Photo exactly at the cap: single-board computer | 2,031,616 B JPEG, 2400×1602, q94 4:4:4 padded with COM segments | 200 | 2.82 s | 5.882 |

Latency is the client-side `curl` total from the test box. Requests 1–5
together used 0 Neurons: `used` stayed at 5.845 from before request 1 until
after request 5. The four photos used 23.445 Neurons in total, 5.86 on average
(5.80–5.96). `budget.requests.used` went from 1 to 5, so it counts only calls
that reached the AI. The photo at the cap was accepted, which confirms that
exactly `enforced_image_bytes` is allowed, and it used no more Neurons than
the small photos. Workers Logs cpuTime for requests 8 and 9 comes from Coder.

### Suggestion quality

| Photo | name | category | brand | model_number | Verdict |
|---|---|---|---|---|---|
| Paracetamol pack | `medicine box` | Medicine | `Panadol` | empty | Correct category and brand. The name is generic, and 500 mg isn't captured anywhere |
| Ibuprofen pack | `ibuprofen tablets` | Medicine | `AL` | `400` | Correct name and category. The 400 mg strength ended up in `model_number`. `AL` is the product line; the maker is Aliud Pharma |
| Cordless drill | `cordless drill` | Tools | `BOSCH` | empty | Correct |
| Single-board computer | `single board computer` | Electronics | `Raspberry Pi` | `4 Model B` | Correct |

Every field's `confidence` was 1. All four answers came from
`@cf/google/gemma-4-26b-a4b-it` with `backend_used: cloudflare-workers`.
Responses contained only contract fields. There were no stack traces, debug
fields, tokens or budget internals in suggest or error bodies.

### Cap behaviour

The live check did not try to exhaust the 30,000-Neuron daily cap. It
confirmed only that the counter advances by about 6 Neurons per photo and
stays still on 401, 413 and 415. The cap-hit `503` and the fail-closed
behaviour when the counter is unavailable are verified in the stub tests only
(`worker/test/paid-tier.test.ts`).

### Findings

- **L (quality, not a #32 regression):** medicine strength is not captured
  reliably. It is missing on the paracetamol pack and placed in `model_number`
  on the ibuprofen pack. The pack names are generic or partial. This is the
  same model and prompt as free mode.
- **Note:** `confidence` is always 1, so clients can't use it to rank fields.
- **Note:** `budget.neurons.used` is reported with full float precision (for
  example 29.290907859802246). This is cosmetic.
