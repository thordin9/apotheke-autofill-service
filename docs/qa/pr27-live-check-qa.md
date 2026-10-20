# QA addendum: service #27 post-deploy live check (max_image_bytes 524288)

- **Change:** apotheke-autofill-service #27, merged as `c69e9c3bf80916ab0d71b8a690e551a6e5a1ea12` (pinned to `f09f75c`).
- **Worker version:** `616c02bf-01b8-464e-8a91-d347136244b3` at 100 %, replacing `498ba38c`.
- **Base URL in this doc:** `https://autofill.example`.
- **Date:** 2026-10-20, calls 11:47:39–11:47:58 EEST.
- **Verdict: PASS.** All 5 requests behaved as expected. The 401 and 413 probes reached no model, and both suggest responses match `openapi.yaml`. cpuTime for the suggest calls is above the 10 ms soft target, which is the known M-3 item (see Deviations).

## Test plan

Budget: at most 5 Worker requests in total, and at most 3 suggest calls that reach the AI. The bearer token is read from a file into a mode-600 header file at runtime, then shredded. It is never on a command line or in a log.

| # | Request | Expect | AI? |
|---|---|---|---|
| 1 | `GET /v1/info` with token | 200, `max_image_bytes` 524288, `ready` true, budget visible | no |
| 2 | `POST /v1/suggest` without token. Body is multipart with no image part, so it can't reach the AI even if auth broke. | 401 `unauthorized` | no |
| 3 | `POST /v1/suggest` with token, 1,153,434 B body (JPEG magic + padding, over 1 MiB) | 413 `payload_too_large` | no |
| 4 | `POST /v1/suggest` with token. Real bench photo `raspberry_pi4.jpg` built with Apotheke v2.5.0's own `autofill_analysis_jpeg` + `autofill_multipart_body` at 1024 px, q80. JPEG 222,083 B, body 222,468 B, under 524288. This is the largest primary realistic photo in the #27 photo set at 1024 px. | 200, valid per contract | yes |
| 5 | `POST /v1/suggest` with token. The #26 "near" body (1300×1300 q80, body 894,982 B), between 524289 and 983040. Checks that the advertised limit dropped while the hard limit stayed at 1 MiB − 64 KiB, so clients holding a cached 983040 still work. | 200, valid per contract | yes |

After the calls: read-only Cloudflare GraphQL for `workersInvocationsAdaptive` (per-request cpuTime, filtered to the Worker) and `aiInferenceAdaptive` (Neurons). Validate the saved bodies against `openapi.yaml` (sha256 `7d75dbf3…f833`) with the same jsonschema method as `tests/conftest.py`.

## Results

| # | Request | HTTP | Client s | cpuTime ms | wall ms | AI reached | Neurons | Contract |
|---|---|---|---|---|---|---|---|---|
| 1 | info | 200 | 0.12 | 1.8 | 38 | no | 0 | valid |
| 2 | suggest, no token | 401 `unauthorized` | 0.12 | 1.4 | 1.9 | no | 0 | valid |
| 3 | suggest, 1,153,434 B | 413 `image exceeds 1048576 bytes` | 0.11 | 2.5 | 3.2 | no | 0 | valid |
| 4 | suggest, real photo, 222,468 B | 200 | 1.47 | 16.2 | 1285 | yes (1) | 5.864 | valid |
| 5 | suggest, 894,982 B | 200 | 2.23 | 42.0 | 2159 | yes (1) | 5.582 | valid |

- **`/v1/info`:** `max_image_bytes` 524288 (it was 983040 on `498ba38c`) and `ready` true. Budget before the calls was 39.655 / 6000 Neurons, 7 requests.
- **#4 result:** "single board computer", category Electronics, brand "Raspberry Pi", model "4 Model B", confidence 1. The photo answer is correct.
- **#5 result:** a generic "patterned noise" answer, as expected for the synthetic step-down image.
- **cpuTime (GraphQL, version `616c02bf`, only these 5 rows in the window, 0 errors):**
  - All 5 requests: p50 2.5 ms, p99 42.0 ms.
  - Suggest calls that reached the AI: 16.2 ms and 42.0 ms (p50 of two is 29.1 ms).
  - Non-AI requests: 1.4–2.5 ms, all under 10 ms.
- **Neurons:**
  - This check used 11.445 Neurons on 2 AI calls (gemma-4-26b-a4b-it, errorCode 0). The 401 and 413 probes used 0, with no inference rows.
  - Day total is now 51.100 Neurons over 9 AI calls, against a 6000 budget and an 8000 free-plan limit.

## Deviations and notes

- **N-1 (info, M-3 monitoring):**
  - Suggest cpuTime is still above the 10 ms soft target.
  - The 1024 px real photo at 16.2 ms is close to the earlier p50 of 15.1 ms (#26 "mid", 396,906 B).
  - The same 894,982 B body took 42.0 ms, up from 30.8 ms on `498ba38c`.
  - #27 changes only the advertised value in `/v1/info`, not the suggest path. These are single samples, so the rise is treated as noise. It stays under M-3 monitoring until 2026-11-03.
  - App-sized photos (≤ 524288 B) are the ones that matter, and they sit around 15–16 ms.
- **N-2 (info):** the 413 message still names the 1 MiB body cap (`image exceeds 1048576 bytes`), not 983040 or 524288. This is unchanged from before #27 and matches the contract.
- **Known gap I-1 stands:** there is no real camera HEIC in the set.
