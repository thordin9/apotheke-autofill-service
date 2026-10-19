# Feature 7: Jetson hardware QA (multi-model contract, API v1.2)

Deployment under test: `apotheke-autofill:jetson-517352f` (main 517352f; app code identical to be67956, which adds docs only), container `autofill` on the Jetson **xavier12**, Ollama backend.
Date: 2026-10-18. Phase 1 (single-model allowlist) board window: **13:00:57–13:12:14 EEST**. Phase 2 (two-model allowlist, same image redeployed): **13:13:53–13:22:02 EEST**.

## Deployment configuration (verified from the container env)
| Setting | Value |
|---|---|
| OLLAMA_MODEL | qwen2.5vl-jetson:3b |
| ALLOWED_MODELS | qwen2.5vl-jetson:3b (only the default) |
| IMAGE_SIZES | 448,560*,1024 |
| BACKEND_TIMEOUT_S | 45 |
| OLLAMA_LOG_RAW | 0 |
| OLLAMA_KEEP_ALIVE | -1 (Forever) |

Path: box → WireGuard tunnel → board (30 Mbit/s inbound cap). Client categories: Documents, Electronics, General, Hobby, Kitchen, Tools (direct tests); Apotheke v2.3.2 seed list for the e2e test.

## Test plan
| # | Check | Phase |
|---|---|---|
| T1 | `/v1/info` content + OpenAPI schema validation; auth (401 without token, `/healthz` open) | 1 |
| T2 | Warm `/v1/suggest` at 448 / 560 / 1024 long edge | 1 |
| T3 | `context.model` fallback: unknown, non-allowlisted, empty, wrong case, absent → default; non-string → 400 | 1 |
| T4 | Cold suggest after `keep_alive:0` unload, polling `/v1/info` ~1 s (B3 in-flight guard) | 1 |
| T5 | Idle-unloaded `/v1/info` triggers exactly one reload (B3b), no duplicates while polling | 1 |
| T6 | `avg_latency_ms` excludes model load time | 1 |
| T7 | Concurrency 2 and 3 at once, warm and cold: queue-full 503 Retry-After vs cap (BACKEND_TIMEOUT_S+5 = 50) and Apotheke's single retry (retries if RA ≤ admin timeout, default 50) | 1 |
| T8 | Apotheke v2.3.2 cold-start e2e (fresh empty DATA_DIR, timeout 50) | 1 |
| T9 | Token never in responses or logs; no raw model output in docker logs (OLLAMA_LOG_RAW=0) | 1 |
| T10 | Only the allowlisted model is ever loaded on Ollama | 1 |
| T11 | End state: Qwen loaded, Forever, 100 % GPU; container healthy | 1 |
| T12 | Cold switch to `3b-300k` while polling `/v1/info` ~1 s: ready/loaded during the load, no default reload (B3/B3b), 200 vs 503 + Retry-After | 2 |
| T13 | Retry-After vs measured load + max(2 s, 30 %), cap 50, wherever a 503 occurs; model_used and avg per model | 2 |
| T14 | B1: a switch while a load runs (+ a third request): single pending slot, latest wins, loads strictly serial, never both resident, no OOM | 2 |
| T15 | Switch back to the default; requests use 3b; per-model avg; cold-start accounting | 2 |
| T16 | Apotheke v2.3.2 end to end with a switch | 2 |
| T17 | OOM watch (dmesg/journal), token never in logs; end state 3b loaded, 300k unloaded | 2 |

## Results – phase 1 (single-model allowlist)
| # | Result | Evidence |
|---|---|---|
| T1 | **PASS** | 200 in 0.48 s; `ready:true`; `models` = [qwen, default, loaded]; `image_sizes` 448 / **560 recommended** / 1024; schema validation: no errors. `/v1/info` without auth 401, `/healthz` 200. |
| T2 | **PASS** | 18 requests (3 photos × 3 sizes × 2 rounds), all 200, `model_used` = qwen. 448: 7.0–8.2 s; 560: 8.0–8.8 s; 1024: 13.7–14.8 s (end-to-end over the tunnel). |
| T3 | **PASS** | `moondream`, `llava:7b`, `""`, `QWEN2.5VL-JETSON:3B`, exact name, absent → 200 with `model_used` qwen (5.7–8.3 s); nothing else loaded on Ollama. `42`, `["x"]`, `{"id":"x"}`, `true` → 400 `'context.model' must be a string` (~0.46 s). |
| T4 | **PASS** | Unload 13:05:48. `/v1/info` ready:false ~9 s, then true from ~10.5 s; no service-triggered reload during the in-flight cold request. Suggest 200 in 23.6 s (runner start 12.17 s). No 503/504. |
| T5 | **PASS** | Unload 13:06:31. First `/v1/info` → ready:false, exactly **one** reload (`POST /api/generate` 15.42 s, runner 12.85 s); 8 further polls, no duplicate. ready:true at 10.2 s; next suggest 200 in 14.36 s. |
| T6 | **PASS** | After the cold suggest avg moved 5881 → 6785 ms, i.e. a recorded sample of ~8.9 s against 23.6 s wall time: load time excluded. |
| T7 | **PASS** | Warm ×2: 200 / 200 (6.06 s, 14.04 s queued). Warm ×3: 200 / **503 queue full RA=15** / 200; retry after 15 s → 200 (9.21 s). Cold ×2: 200 / 200 (26.68 s, 34.73 s). Cold ×3: 200 / **503 queue full RA=17** / 200; retry after 17 s → 200 (9.54 s). All RA ≪ cap 50 and ≤ Apotheke default 50 → the single client retry succeeds. |
| T8 | **PASS** | Apotheke v2.3.2, `php -S` on loopback, fresh empty `mktemp -d` DATA_DIR, sign-in on, timeout 50. Test connection OK. Cold (after unload 13:10:45): headphones → 200 in 24.1 s, "headphones / Electronics", sensible tags + description. Warm: drill 1024 → "cordless drill / Tools" 14.4 s; lamp 448 → "umbrella / Lighting" 7.4 s. Server stopped afterwards. |
| T9 | **PASS** | Token absent from all recorded responses (asserted per request) and all box logs. `docker logs autofill` since start: 137 lines, all uvicorn access/startup lines, token count 0, no raw model output. |
| T10 | **PASS** | Ollama journal 13:00–13:12: all 5 runner starts load the single blob of `qwen2.5vl-jetson:3b`; no qwen3-vl / moondream / llava loads or requests. 6 `/api/generate` calls = 5 QA unloads + 1 service reload (T5). |
| T11 | **PASS** | 13:12:14 EEST: `qwen2.5vl-jetson:3b 4.2 GB 100% GPU Forever`; container `Up (healthy)`, restarts 0; `/v1/info` ready:true, avg 8900 ms. |

### Findings (phase 1)
- **F1 (model quality, not a service bug):** desk lamp misidentified at every size ("umbrella" 448, "mouse" 560, "blue light" 1024). Drill and headphones correct at all sizes.
- **F2 (info):** during a cold load `/v1/info` reports ready:true ~10 s before the first answer, because Ollama lists the model in `/api/ps` while it is still loading. See F4: the per-model `loaded` flag behaves the same.
- **F3 (expected):** one `/v1/info` 401 in the service log comes from Apotheke probing the service when the URL is saved before the token is set.

## Results – phase 2 (ALLOWED_MODELS = qwen2.5vl-jetson:3b, qwen2.5vl-jetson:3b-300k)
Start state: 3b default + loaded, 3b-300k installed and not loaded, avg reset, container fresh (restarts 0). Board RAM 6.7 GiB.

Measured on this board: `3b-300k` runner start 10.6–11.4 s, resident **3.9 GB** (not ~5.6 GB), cold switch end-to-end 22.8–24.3 s at 560; warm 300k ≈ 8.3–10 s at 560 and 1024 alike (it caps the image at ~300k pixels). 3b: runner start 12.4–13.5 s, warm 7.8–15.5 s depending on size. Minimum MemAvailable observed during switches: 949 MB.

| # | Result | Evidence |
|---|---|---|
| T12 | **PASS** (with F4) | 13:14:53 cold switch 3b → 300k (drill 560): **200 in 23.4 s**, model_used 3b-300k. `/v1/info`: ready:false and both `loaded:false` from +2.1 s to +9.0 s (3b evicted, 300k not yet in `/api/ps`), then **ready:true + 300k `loaded:true` from +10.4 s**, 13 s before the answer. Ollama journal: no `/api/generate` from the service during the switch (no default reload). Unmeasured 300k does **not** get a 503 at 45 s: load estimate 15 s + generate default 15 s = 30 ≤ 40.5, so the wait path applies — the default Retry-After 20 (15 + 30 %, rounded up) is never sent here. |
| T13 | **PASS / 503 path not reachable** | Only 503s observed: queue-full RA=21 (= ⌈avg 3b 10.0 s × 2⌉) while a 3b request ran and a switch waited. No "model loading" or "a model switch would time out" 503 occurred: measured load (≈11–13 s, floored to 15) + slowest generation (300k ≈ 10 s, 3b ≈ 15.5 s) stays ≤ 40.5 s for a cold request; the in-flight switch check came to ≈40 s (admitted). The load-based Retry-After formula is therefore not exercised on hardware at 45 s; covered by mock tests. Per-model avg (load excluded): 3b 8107 → 8258 after a 23.7 s cold switch-back (sample ≈ 8.6 s); 300k 9124 after its first 23.4 s cold switch (sample = generation only). End: 3b 10121, 300k 8623 ms. model_used always matched the request. |
| T14 | **PARTIAL – see F5** | Background-load queue (B1 pending slot / latest wins) is only used when a cold request does not fit 0.9 × 45 s, which never happens with these two models → **not reachable on hardware; covered by mock tests**. Hardware variants run instead: (b) A=3b 1024 in flight, B=300k @+1 s, C=3b @+2 s → A 200 14.4 s, **B 200 36.0 s** (waited + switch), C 503 queue full RA=21. (d) A=300k 1024, B=3b 1024 @+1 s → both 200 (9.9 s, **38.1 s**). (c3c) nothing loaded, info triggered **one** default reload; 300k request sent 2 s later during that reload → **200 in 35.9 s** (Ollama finished the 3b load, then evicted it and loaded 300k); a 3b request 2 s after that was admitted behind it and **timed out: 504 at 45.6 s** (F5). Loads strictly serial in every case: board monitor (`ollama ps` every ~1 s) never showed both models resident; no OOM in dmesg (the only OOM entry is the pre-existing 12:07:27 one, before both QA windows); no Ollama memory errors. |
| T15 | **PASS** | After switching back: no model / `moondream` / exact 3b / an unknown name → 200, model_used 3b (7.8–11.6 s). After the 504 in T14, Ollama aborted the half-done 3b load; the next `/v1/info` saw nothing allowlisted loaded and triggered exactly one default reload (B3b self-heal, 15.7 s). |
| T16 | **PASS (switch driven by another client)** | Apotheke v2.3.2 (and v2.2.0) never send `context.model`, so a client-selected switch is **not possible from Apotheke** – skipped. Instead: fresh empty DATA_DIR, timeout 50, Test connection OK; a direct client switched the service to 300k (200 24.3 s), then Apotheke's default request switched it back: **200 in 23.4 s** "cordless drill / Tools"; next warm 1024 → 200 14.2 s. Local PHP server stopped. |
| T17 | **PASS** | dmesg OOM lines unchanged (10, all from 12:07:27). Token: 0 occurrences in `docker logs` (132 lines, access/startup only, no raw output) and in all box logs/responses. Only blobs of 3b and 3b-300k were loaded; qwen3-vl:2b never requested. **End state 13:22:02 EEST:** `qwen2.5vl-jetson:3b` 4.2 GB, 100% GPU, Forever; 3b-300k not loaded; container healthy, restarts 0; `/v1/info` ready:true. |

### Findings (phase 2)
- **F4 (answer for the room – `loaded` goes true early, like `ready`):** for both the 3b-300k switch and the cold default load, the model's `loaded` flag (and `ready`) turns true ~10 s after the start, while the request is still loading/answering (answers at 23.4 s / 23.7 s). In the gap between eviction and the new model appearing in `/api/ps` (~8 s), **both** models report `loaded:false` and ready:false. Cause: Ollama lists a model in `/api/ps` as soon as its runner is being started. `loaded` therefore means "resident or loading", not "warm". Harmless for Apotheke (it does not use `loaded`), but a client that shows "model ready" from `loaded` would be optimistic by ~10–13 s.
- **F5 (medium, 504):** in-flight admission for a model switch estimates `slowest(current) + slowest(requested) + load(requested)` but ignores that the request in flight is itself paying a load (and any running background reload). With nothing loaded, the default reload running, a 300k request in flight (35.9 s total) and a 3b request queued behind it, the 3b request was admitted and got **504 after 45.6 s**; Ollama aborted its half-done load. Needs mixed-model concurrent clients during a cold phase. Suggested fix: when the in-flight request is a cold/switching request (or a background load runs), add its remaining load budget to `need`, or refuse with 503 + Retry-After.
- **F6 (low):** the queue-full / switch-busy Retry-After is `avg(current) × (waiting+1)` and does not include the load the queued switch will pay; harmless here (observed RA 21 ≪ 50, and Apotheke's single retry would land after the queue drained).
- **F7 (info):** ITAdvisor's figures for 3b-300k (~25 s load, ~5.6 GB) don't match this board: runner start ≈11 s, cold switch ≈23 s end to end, 3.9 GB resident.

### Not reachable on this deployment at BACKEND_TIMEOUT_S=45 (covered by mock tests)
- Cold-start "model loading" 503 + Retry-After (load budget, unmeasured 20, cap 50) and the B1 background-load queue (single pending slot, latest wins, Retry-After including the running load): every cold request with these two models fits 0.9 × 45 s, so the service waits instead of answering 503.
- "a model switch would time out" 503: the estimate came to ≈40 s ≤ 40.5 s in the tightest case.
