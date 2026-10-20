# Feature 11 (Cloudflare Worker): live QA

QA on 2026-10-20, 00:37–00:45 EEST, against the production custom domain (Worker `apotheke-autofill`, code `b9745f4`, redeployed unchanged from merge `c7b6d55`). Requests ran from the box. The bearer token was read from its 600 file at runtime and placed only in request headers. Workers Logs were read **read-only** through the Cloudflare connector. Nothing was deployed and nothing was posted. The Jetson was not touched.

Companion to `docs/qa/feature11-worker-stub-qa.md` (§4 plan, findings M-1 to M-4, L-1 to L-4).

**Status: final.** **Verdict: PASS with follow-ups.** Auth, the size and type limits, fail-closed behaviour, the daily cap, the label read and log hygiene all pass live. CPU time is over the 10 ms Workers Free limit and is tracked as a follow-up (§5). **Live check total: 4 AI calls, 24.155 Neurons** (the cap probe cost 0).

## 1. Test plan

| ID | Step | Expectation |
|---|---|---|
| P0 | No-token probe (done by DevOps before the secret was set) | Every endpoint 503, 0 AI calls; the old domain and workers.dev don't connect |
| P1 | Deploy run `39328821936` started by the merge | Same `worker/` code, strict smoke step (`/healthz` 200, unauthenticated suggest 401), secret survives |
| P2 | `/healthz`; `/v1/info` with the token | 200; record `max_image_bytes` and the budget |
| P3 | Auth | 401 for no token and a wrong token (`/v1/info` and `/v1/suggest`); record the lowercase `bearer` scheme and a trailing space |
| P4 | Size and type, no AI | 413 at 3145729 bytes and chunked over the cap. A body exactly at the cap with non-image magic is 415, not 413. A PNG name with JPEG part type and text magic is 415. A missing file part is 400. `/v1/info` `used` unchanged after each |
| P5 | Photos (≤ 5 AI calls, repairs included) | 200, valid JSON, Apotheke fields, `model_used` and `backend_used`, Neurons from the `/v1/info` delta |
| P6 | Workers Logs | `cpuTime` and `wallTime` per request, version id, 1102s, log hygiene |
| P7 | Cap check with a low budget var | 503 `daily capacity is exhausted`, `Retry-After` to 00:00 UTC, 0 AI calls, `/v1/info` unchanged; then restore the default vars |

Rate limiter (10 per 60 s, suggest only): not tested on purpose. 9 authenticated suggest requests across about 2.5 min never hit it.

## 2. Results

### P0, P1: deploy and version

- Deployments (from the API, read-only):
  - `fa9e7339`: manual upload, 21:32:25Z. It served the no-token probe with 503s.
  - `3c139786`: secret set, 21:35:11Z. 401s and `/healthz` 200 from here on.
  - **`419e029c`**: run `39328821936`, 21:36:51Z, 100 % of traffic.
- **Every request below was served by version `419e029c`.**
- Run `39328821936`: conclusion **success**. All steps passed, including **Smoke**. Workers Logs show the runner's `GET /healthz` 200 and `POST /v1/suggest` 401 at 21:36:53Z.
- `worker/` at merge `c7b6d55` is byte-identical to `b9745f4`; the merge only added the stub QA doc.
- **`AUTOFILL_TOKEN` survived the redeploy:** authenticated `/v1/info` returns 200, not 503.

### P2–P5: requests

All times are client round-trips. CPU and wall times come from Workers Logs. Neurons are the `/v1/info` `used` delta.

| # | Request | Body bytes | HTTP | Client ms | cpuTime ms | wallTime ms | Neurons |
|---|---|---|---|---|---|---|---|
| 1 | `GET /healthz` | – | 200 `{"status":"ok"}` | 162 | 1 | 1 | 0 |
| 2 | `GET /v1/info` (before) | – | 200 | 52 | 2 | 31 | 0 |
| 3 | `GET /v1/info`, no token | – | 401, `WWW-Authenticate: Bearer` | 88 | 1 | 2 | 0 |
| 4 | `GET /v1/info`, wrong token | – | 401 | 72 | 1 | 2 | 0 |
| 5 | `GET /v1/info`, lowercase `bearer` | – | **200** | 94 | 1 | 26 | 0 |
| 6 | `GET /v1/info`, trailing space (raw TLS) | – | **200** | 157 | – | – | 0 |
| 7 | `POST /v1/suggest`, no token | 136 | 401 | 71 | 0 | 1 | 0 |
| 8 | `POST /v1/suggest`, wrong token | 136 | 401 | 28 | 0 | 1 | 0 |
| 9 | Suggest, `Content-Length` 3145729 | 3145729 | 413 | 190 | 3 | 3 | 0 |
| 10 | Suggest, chunked, over the cap | 3149824 | 413 | 43 | 0 | 0 | 0 |
| 11 | Suggest, exactly 3145728, text magic | 3145728 | 415 (not 413) | 149 | **41** | 89 | 0 |
| 12 | Suggest, `photo.png` as `image/jpeg`, text magic | 151 | 415 | 42 | 1 | 1 | 0 |
| 13 | Suggest, no `image` part | 88 | 400 `missing 'image' file part` | 49 | 1 | 1 | 0 |
| 14 | Photo `sony_cd_player_label.jpg` (95 KB, 1024 px) | 95386 | 200 | 2464 | **10** | 2399 | 6.236 |
| 15 | Photo, 2.9 MiB JPEG (3900×2605, real photo upscaled) | 3041596 | 200 | 3770 | **126** | 3682 | 5.991 |
| 16 | Photo `casio_fx991ex.jpg` (86 KB) | 86240 | 200 | 2297 | **9** | 2260 | 5.991 |
| 17 | Photo `kitchen_knife.jpg` (30 KB) | 29737 | 200 | 1825 | **17** | 1773 | 5.936 |

- After rows 2–13, `/v1/info` showed `used` 0 every time. Rows 3–13 had `ai_calls: 0` in the Worker log line.
- The 14 `/v1/info` reads around the photos (cpuTime 0–6 ms) are left out of the table.
- **AI calls: 4, with no repairs. Total: 24.155 Neurons.** One of the 5 allowed calls is unused.
- `/v1/info` after: limit 6000, used 24.155, reserved 0, remaining 5975.845, requests used 4, `resets_at` `2026-10-20T00:00:00.000Z`, `ready` true. `max_image_bytes` 3145728.

**Photo answers:**
- All 4 were valid JSON with `name`, `category`, `description`, `tags` (`{value}` objects, confidence 1), `brand`, `model_number` and `serial_number`.
- `model_used` was `@cf/google/gemma-4-26b-a4b-it`.
- **`backend_used` was absent on every suggestion** (stub finding M-1, confirmed live).

| Photo | Name / category | Brand / model / serial |
|---|---|---|
| Sony | "compact disc player" / Electronics | SONY / CDP-CE525 / 5104890 (all correct) |
| 2.9 MiB | "single board computer" / Electronics | Raspberry Pi / "4 Model B" / – |
| Casio | "scientific calculator" / Electronics | CASIO / fx-991EX / – |
| Knife | "kitchen knives" / Kitchen | GERLACH / – / – |

Neurons per photo are 5.94–6.24, the same as on #22. The 2.9 MiB image cost no more Neurons than the 1024 px one.

### P6: Workers Logs

- Every invocation outcome was `ok`. **No error 1102** and no exceeded-CPU outcome.
- Log lines are the Worker's one JSON line per request (status, neurons, ai_calls, ms fields).
- `base64_ms` and the other `*_ms` fields are always 0 in production. Workers freezes the timer during compute, so they don't measure CPU and only `cpuTime` does (I-8).
- The stored `Authorization` header is masked (`********`). No token, image data or field values are in the logs.
- The request headers in the logs include `cf-connecting-ip` and `x-real-ip` (client IPs), kept for the Workers Logs retention period (I-9).
- Within 4 minutes of go-live the domain got internet scanner traffic (`/.env`, `/config.json`, `/AGENTS.md`, `/wp-json/` and so on). All got 401 with ≤ 2 ms CPU.

## 3. Findings

**F-1 (Medium, PM decision triggered): CPU time.**
- **The 2.9 MiB request used 126 ms CPU** (wall 3682 ms). That is above the PM's 8 ms threshold and the 10 ms Workers Free limit, but it ran without a 1102. The no-AI 3 MiB body (row 11) used 41 ms just for reading and multipart parsing.
- **Every photo request used 9–17 ms, including the 30 KB photo.** So the 2 MiB cap the PM decided on lowers the worst case, but by these numbers it doesn't bring requests under 8 ms:
  - Linear estimate from rows 11 and 15: roughly 40 ms per MiB, plus a 9–17 ms base.
  - That gives about 90 ms at 2 MiB, 50 ms at 1 MiB, and about 20 ms at 256 KB.
- The per-request base is likely the AI binding's request and response handling, and isn't in the Worker's own code paths measured in the stub QA.
- The Workers Free 10 ms limit was not enforced on these requests. I couldn't read which plan the account is on with the connector's permissions (403 on subscriptions).
- Recommendation:
  - Apply the 2 MiB decision (`MAX_UPLOAD_BYTES = "2097152"`, docs and tests).
  - Also consider about 1 MiB. Real Apotheke photos are 30–240 KB.
  - Treat the 9–17 ms base as the real constraint. Watch `cpuTime` p99 and 1102s in Workers Logs, or confirm the plan's CPU limit.

**F-2 (Medium, confirms stub M-1):** `backend_used` is missing on `/v1/suggest`.

**F-3 (Low):** `/v1/info` `ready` and `remaining` don't take the 30-Neuron reservation into account. `reserve` refuses when `used + reserved + 30 > budget`, but `/v1/info` reports `ready: true` whenever `budget − used − reserved > 0`. So in the last 30 Neurons of the day, `/v1/info` says ready while every suggest gets 503. Fix: compute `ready` as `remaining ≥ NEURON_ESTIMATE`, and report that value.

**I-8 (Info):** in production, the `*_ms` fields in the Worker log line are always 0 (Spectre-mitigated timers). Drop them, or say in the docs that `cpuTime` in Workers Logs is the measure.

**I-9 (Info):** Workers Logs keep client IPs in the request headers. Mention this in the privacy section.

**I-10 (Info):** the lowercase `bearer` scheme and a trailing space after the token are both **accepted (200)**. That matches the code (case-insensitive scheme, trimmed token) and RFC 9110. It is not a weakness: the token must still match exactly.

**Process note: token exposure and rotation.** During P3, the HTTP client rejected the deliberately malformed trailing-space header. Its error message echoed that header, so the bearer token appeared once in QA's own tool output. It did not reach this doc, the results file, the repo or the Worker logs; Workers Logs mask `Authorization`. The QA script was changed to never print exception text, and the trailing-space case was re-run over a raw TLS socket. **DevOps rotated `AUTOFILL_TOKEN` after the cap check** (version `492a6c76`, §4), and reports that the old token now gets 401. No token value appears in this document.

## 4. Cap check (P7)

How the Worker refuses (`worker/src/budget.ts` `reserve`, called before each AI call):
- It refuses if the DO is marked exhausted, or the request cap is hit.
- It also refuses if `used + reserved + NEURON_ESTIMATE (30) > AUTOFILL_DAILY_NEURON_BUDGET + 1e-6`.
- On refusal the Worker returns 503 `daily capacity is exhausted` with `Retry-After` = seconds to 00:00 UTC, **before** `env.AI.run`.

With `used` = 24.155 and `reserved` = 0, any budget ≤ 54 refuses with 0 AI calls. QA proposed `24`, so that `/v1/info` would also show `ready: false`.

**Probe** (run by the PM at 21:46:32Z, 00:46:32 EEST, on version `f1f6960e`, a temporary deploy of `c7b6d55` with `AUTOFILL_DAILY_NEURON_BUDGET=24`):

| Check | Result |
|---|---|
| Authed `POST /v1/suggest` with a real 54583-byte PNG | 503 `{"error":{"code":"backend_unavailable","message":"daily capacity is exhausted"}}`, 141 ms |
| `Retry-After` | 8008 s, exactly the time from 21:46:32Z to 00:00:00Z (2 h 13 min 28 s) |
| `/v1/info` before and after | Identical: `ready` false, limit 24, used 24.1545, reserved 0, remaining 0, requests used 4 |
| AI calls, Neurons | 0, 0 |

**P7: PASS.**

**Restore:**
1. DevOps redeployed `c7b6d55` with the default vars as version `e1dd7575`.
2. DevOps then rotated `AUTOFILL_TOKEN`; version `492a6c76` is at 100 %.
3. The PM verified `/healthz` 200 and an authed `/v1/info` with `ready` true, limit 6000, used 24.1545, remaining 5975.85. **The Durable Object counter persisted across all three deploys.**
4. The old token gets 401 (DevOps).

## 5. Verdict and follow-ups

**Verdict: PASS with follow-ups.** P0–P7 pass. CPU time over the 10 ms Workers Free limit is tracked below.

**Neurons:** the whole live check made 4 AI calls for **24.155 Neurons**. The no-AI probes and the cap probe cost 0.

**Follow-ups, as decided by the PM, in this order:**
1. **Apotheke resizes first.** The original upload is kept unchanged. Only a stripped JPEG copy (1600 px, quality 80) is sent to autofill. If the resize fails, autofill is skipped, and the original is never sent.
2. **Worker cap to 1 MiB, plus the CPU overhead work** (F-1: the 9–17 ms base per photo request, the multipart parse, building the data-URI JSON). `MAX_UPLOAD_BYTES` and `max_image_bytes` become 1048576, with docs and tests updated.
3. **QA re-measures `cpuTime`** in Workers Logs after both changes land, with typical photos and a near-cap image, and checks for 1102s.

Also open from the stub QA and this run: M-1/F-2 (`backend_used` on `/v1/suggest`), M-2 (3040 `Retry-After`), M-4 (gitleaks path allowlist), F-3 (`ready` vs the 30-Neuron reservation), L-1 to L-4, and I-8/I-9.
