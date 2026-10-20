# QA: PR #26 Worker follow-ups (stub-only)

- PR: #26 "fix: Worker follow-ups after the live QA check" (draft)
- Head: `5f2189805aaa4b88d6ef2851f23a02f3f2476a46`. Base: main `c7b6d55efb0d398899089e68a3e087c96d1556d0`.
- Date: 2026-10-20, about 01:15–01:30 EEST.
- Scope: static review plus local stub tests. Nothing was deployed. The live Worker was not called. No `wrangler deploy` or `wrangler dev`, and no Cloudflare credentials were used. Every Vitest run used `remoteBindings: false` and `AUTOFILL_REQUIRE_AI_STUB=1`, so `env.AI` was always a stub.
- Companion to `docs/qa/feature11-worker-stub-qa.md` and `docs/qa/feature11-worker-live-qa.md` (findings M-1 to M-4, L-1 to L-4, I-4, F-1/F-3, I-8).

## 1. Test plan

| # | Area | Method |
|---|---|---|
| T1 | CI | Check runs on the head sha |
| T2 | Local suites | Worker Vitest and pytest on a fresh tree of the head |
| T3 | Secrets | gitleaks 8.30.1 over the diff head vs main (one-commit repo), with the repo config and with default rules |
| T4 | Live QA doc | Size and sha256 of `docs/qa/feature11-worker-live-qa.md` |
| T5 | Follow-ups | Read the diff for M-1 to M-4, L-1 to L-4, I-4, `ready`, privacy, `*_ms`, 404, CF_*_FILE and Jetson known issues, and find the test for each |
| T6 | Probes | Run the #25 QA probes unchanged against the head, plus new #26 probes (caps at the defaults, chunked, errors shaped like the real binding's, routes without a secret, `backend_used`, log fields, parser cost) |
| T7 | Multipart parser | Feed bodies captured from PHP curl `CURLStringFile` (what Apotheke uses), curl `-F` and httpx, plus edge cases, to `readSuggestParts` |
| T8 | Config drift | Mutate `deploy-worker.yml` and `wrangler.toml` 15 ways and check that pytest or Vitest fails |
| T9 | Hygiene | Added lines: IPv4, 32/40/64-hex, token-like strings, hostnames. `wrangler.toml` flags and route. Workflow diff |

## 2. Results

### T1 CI: PASS

All 5 check runs on `5f21898` passed: `gitleaks`, `worker` (npm ci, typecheck, `npm test`, `wrangler deploy --dry-run`), `lint-test`, `deploy-scripts` and `image`. They ran 01:14–01:17 EEST.

### T2 Local suites: PASS

- Worker Vitest: 7 files, **45 passed**.
- pytest: **559 passed**. The 10 new or changed tests in `tests/test_worker_followups.py` and `tests/test_worker_deploy.py` pass.
- The fresh tree diffs to main as 24 files, +794/−148, the same as the PR.

### T3 gitleaks: PASS

Diff commit: 0 leaks with `.gitleaks.toml` and 0 with the default rules. The `dir` scan's 2 hits are in unchanged files (`.github/workflows/ci.yml` healthz curl, `deploy/jetson/service.env.example` placeholder). They're the known false positives. CI's full-history gitleaks passed.

### T4 Live QA doc: PASS

12395 bytes, sha256 `3be8bc9b55402d564535aa179dcc5934daeed82b871587eca3adbece8a59b7b8`. This matches the pinned copy, and `test_live_qa_report_is_the_pinned_bytes` locks it.

### T5 Follow-ups

| Item | Result | Evidence |
|---|---|---|
| M-1 `backend_used` | **PASS** | `worker/src/validate.ts:578`. OpenAPI enum `[ollama, cloudflare, cloudflare-workers]`. Tests: `suggest.test.ts` (first answer), `test_openapi_allows_cloudflare_workers_backend`. The probe also sees it on a repaired success. The #25 `it.fails` probe now passes |
| M-2 3040 short Retry-After | **FAIL in production (M-5)** | `index.ts:142,157-161,198`: `Retry-After: 30`, "the model is out of capacity", day not marked exhausted. The DO cap and 3036 keep the midnight countdown (test expects 3600 at 23:00Z; probe 5400 at 22:30Z). This works only for the structured stub shape. See M-5 |
| M-3 CPU | **Partial** | See "M-3 CPU" below |
| M-4 gitleaks allowlist | **PASS** | `.gitleaks.toml`: `condition = "AND"`, `commits = ["ba1f8ff4397c568c5b9b5ffdc9f2e680664ca231"]`, path and regexes unchanged. Test `test_gitleaks_qa_allowlist_is_commit_scoped`. CI gitleaks green |
| L-1 code field only | **Implemented, but see M-5** | `ai.ts:83-101` reads only `code`, `internalCode` and `error_code` (with `errors`, `error` and `cause` nesting). The message regex is gone. Test `does not treat a message that merely contains 3036 as quota exhaustion` |
| L-2 config drift | **PASS with L-6** | New `test_wrangler_has_observability_and_no_kv` and `test_deploy_workflow_installs_with_ci_and_checks_wrangler`. Mutations: 13 of 15 caught (pytest 11, Vitest the budget var and ratelimit). Missed: smoke `|| [ "$code" = "503" ]`, and Test moved after Deploy |
| L-3 smoke | **PASS** | `deploy-worker.yml`: `WORKER_URL: ${{ vars.WORKER_URL }}` passed through env (no inline expression), `base="${WORKER_URL:-<custom domain>}"`. Both failures print `(last HTTP code: ${code})`. New "Check Wrangler version" step before Deploy, with no secrets (`package.json` pins exact `4.151.2`, so the substring match works). Test asserts these |
| L-4 caps | **PASS** | `neurons.ts:44` 1048576. `multipart.ts:6-9` `maxImageBytes` = cap − 65536. `/v1/info` 983040. Probe at the shipped defaults: image 983040 → 200. Image 983041 → 413 "image exceeds 983040 bytes", no AI call. Body 1048576 → 413 (image cap). Body 1048577 → 413 "image exceeds 1048576 bytes" with Content-Length **and chunked** (no Content-Length), no AI call. Tests `advertises the image cap…`, `returns 413 for an image part over the image cap…` |
| I-4 CHANGELOG | **FAIL (L-5)** | The line I-4 named, "Upstream text is not returned. A Durable Object error fails closed.", is still stranded under the `.npmrc` bullet (`CHANGELOG.md:54-56`). The PR moved a different line (the v3_lean note, to `[1.3.0]`), which is fine on its own |
| `ready` reserve | **PASS** | `index.ts:89-92` `neuronRemaining >= NEURON_ESTIMATE`. Test `is not ready when fewer than 30 neurons remain and is ready at exactly 30` (29 → false and suggest 503; 30 → true and suggest 200) |
| Privacy, client IP | **PASS** | `docs/cloudflare-workers-deploy.md` Privacy says Workers Logs store `cf-connecting-ip`/`x-real-ip`, and our log line adds none. Also in `docs/api-v1.md` and CHANGELOG |
| `*_ms` fields | **PASS** | `Date.now()` deltas throughout (`index.ts`, `log.ts:28`). Docs say production freezes the clock during CPU and that `cpuTime` is the measure. Tests: `log.test.ts` (no `cpu_ms`, no token), suggest log has numeric `*_ms`. Probe log line has `base64_ms/parse_ms/validate_ms` 0 and `total_ms` 7 |
| Unknown path 404 | **PASS** | `index.ts:255-257`. Routes probe below. Test `returns 404 for an unknown path even when the secret is missing`. `/healthz` with no secret = 503 `degraded`, documented in the troubleshooting table |
| CF_*_FILE | **PASS (unchanged)** | Already fixed on main. `docs/deploy-jetson.md:236-242` uses `/var/lib/autofill`. `tests/test_cf_credential_mount.py` passes. Not touched by this PR |
| Jetson known issues | **PASS** | `docs/deploy-jetson.md:654-683` has H2, M5, M6, L6 and L7. `docs/cloudflare-backend.md:168,186` links them. New test `test_jetson_known_issues_stay_documented`. `deploy/` and both docs are unchanged |

Routes probe (status by secret):

| Request | Secret unset | Secret "" | Secret set, no auth header |
|---|---|---|---|
| GET /nope, GET /v1/nope | 404 | 404 | 404 |
| GET /v1/info, POST /v1/suggest, GET /v1/suggest | 503 | 503 | 401 |
| GET /healthz | 503 | 503 | 200 |
| POST /healthz | 503 | 503 | 401 |

#### M-3 CPU

What changed:
1. The body cap went from 3 MiB to 1 MiB, so Apotheke skips anything over 983040.
2. A new `worker/src/multipart.ts` scans the buffered body and returns a zero-copy `subarray`. It replaces `Request.formData()` plus `File.arrayBuffer()` (two copies).
3. The schema, image part and thinking flag are built once and reused by the repair.
4. `performance.now` became `Date.now` (no CPU effect).

Base64 is still native `toBase64`. The binding still runs `JSON.stringify` on the whole input once per call (workerd `ai-api` `#generateFetch`).

Local workerd, 983040-byte image, 30-iteration mean: new parser 2.0 ms against old `formData` 2.6–3.9 ms. At 300 KB: 0.6–0.7 ms against 0.6–0.9 ms. Base64 0.3 ms. So the parser saves 1–2 ms at most. The cap is the real lever. By the live run's linear estimate (about 40 ms per MiB plus a 9–17 ms base), a near-cap request would still use about 50 ms `cpuTime`. Nothing in the diff targets the 9–17 ms base (F-1).

Plausible: worst-case cost drops about 3×. It won't bring requests under 10 ms. This needs the post-merge `cpuTime` re-measure (typical photo plus a ~950 KB image).

### T6 Probes

- The #25 probes run unchanged against the head: 25 passed, 6 failed. Five failures are expected changes: the three 3 MiB cap tests, `it.fails` for M-1 (now fixed), and "3040 Retry-After until midnight" (now 30). The sixth is a regression: **"3036 (message-only, as the binding throws it)"** now gets no `Retry-After` (see M-5).
- New #26 probes: 8 passed, 3 failed, and one report test fails on purpose so it prints the notes. The 3 failures are M-5 below.

### T7 Multipart parser

PHP curl `CURLStringFile` (Apotheke's path), curl `-F` and httpx bodies all parse, with the image byte-identical and the context intact. Edge cases are rejected cleanly with the error envelope:
- truncated body: 400
- non-image: 415 from the first 12 bytes
- empty image: 400
- over the image cap: 413
- missing image, or a file whose name is only `image` in `filename=`: 400
- no boundary: 400

Differences from `formData()` (nit N-1): a preamble before the first boundary is 400, LF-only line endings are 400, a duplicate `image` part is last-wins (was first), a non-file `image` field is accepted (was 400), and a file `context` part is read as text. No real client does any of these.

### T9 Hygiene: PASS

Across 818 added lines: no IPv4, no 32-hex (account-id shape), no tokens. The only 40/64-hex values are the allowlist commit and the live-doc sha. No Jetson hostname. Domains are the existing custom domain, github.com and a `workers.dev` mention in the live doc.

`wrangler.toml` keeps `workers_dev = false`, `preview_urls = false` and the `[[routes]]` custom domain with `custom_domain = true`. The only change is `MAX_UPLOAD_BYTES = "1048576"`.

`deploy-worker.yml` changes only the L-3 smoke and the version-check step. Triggers, permissions, environment, pins and the Deploy step env (`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`) are unchanged.

## 3. Findings

**M-5 (Medium, merge-blocking). Real Workers AI errors are no longer mapped. M-2 doesn't work in production, and 3036/3006 regress.**
- The real binding (workerd `cloudflare/internal/ai-api.ts` `_parseError`, also in the pinned `workerd` 1.20261019.1) throws `InferenceUpstreamError` whose only carrier of the code is the **message**, `"<internalCode>: <description>"`. It has no `code`, `internalCode` or `errors` property. The code goes only to `env.AI.lastRequestInternalStatusCode`.
- L-1 removed the message fallback, so `workersAiCode` returns null for every real binding error. The PR's tests pass because the stubs add `errors: [{ code }]`, which the binding never does.
- Probe with an `InferenceUpstreamError` shaped like the binding's, at 22:30Z:

  | Error | main `c7b6d55` | PR `5f21898` |
  |---|---|---|
  | 3036 | 503, Retry-After 5400, `ready` false | 503 "the service could not complete the request", **no Retry-After**, day **not** exhausted, `ready` true |
  | 3040 | 503, Retry-After 5400 | 503, **no Retry-After** (M-2's 30 s never sent) |
  | 3006 | 413 | **503** |

- Fix: also accept the code from an `Error` message anchored at the start, `/^(?:InferenceUpstreamError:\s*)?(3036|3040|3006):\s/`, optionally only when `err.name === "InferenceUpstreamError"`. Keep the "network reset after 3036 ms" negative test. Add tests that throw exactly `Object.assign(new Error("3040: Capacity temporarily exceeded, please try again."), { name: "InferenceUpstreamError" })` (and 3036, 3006) with no `code`. Don't read `env.AI.lastRequestInternalStatusCode`, because it's shared across concurrent requests.
- QA note: the stub QA's L-1 offered "structured code only" as one of two fixes. That option was wrong for the real binding. The anchored-message option is the right one.

**M-3 / F-1 (open, not blocking). CPU is only partly addressed.** See the M-3 CPU section. Re-measure `cpuTime` after deploy. If near-cap requests give 1102s, consider a 512 KiB cap.

**L-5 (Low). I-4 isn't fixed.** At `CHANGELOG.md:54-56`, "Upstream text is not returned. A Durable Object error fails closed." still sits under the `.npmrc` bullet. Nit: there are now two `## [Unreleased]` headings (`:6` and the older `:161`).

**L-6 (Low). Two L-2 drifts still pass.** The smoke accepting `|| [ "$code" = "503" ]`: the test checks that `'[ "$code" = "401" ]'` is present and `200` is absent, but not that nothing else is accepted. And the Test step moved after Deploy: step order isn't asserted.

**L-7 (Low, perf). The parser scans byte by byte in JS.** `findBytes` costs about 2 ms per MiB. Finding the first byte with `Uint8Array.prototype.indexOf` measured 0.43 ms against 2.04 ms per MiB (node, same V8). It's an easy saving if CPU stays tight.

**N-1 (nit). Parser strictness differs from `formData()`** (see T7). Real clients are unaffected.

**I-9 (Info).** Unknown paths now give 404 before auth, even when the secret is set (they were 401). That's reasonable. The "magic bytes before the rest of the part is accepted" wording is true, but the body is already fully buffered, so it saves only the boundary scan. A 3040 that comes after the reservation still settles the 30-Neuron estimate (unchanged).

## 4. Verdict

**Not ready to merge: changes requested.** CI is green and the local counts match (Vitest 45, pytest 559). gitleaks is clean, the live QA doc is byte-identical and the hygiene checks pass. M-1, M-4, L-3, L-4, the `ready` reserve, privacy, `*_ms`, 404, CF_*_FILE and the Jetson known issues all pass.

**M-5 blocks the merge.** With the real binding's error shape, 3036, 3040 and 3006 all collapse to a generic 503 with no `Retry-After`. That regresses main and makes M-2 ineffective in production.

Also fix L-5 and L-6 in the same pass. After the merge, re-measure `cpuTime` for M-3. The PR stays a draft until the Apotheke release is tagged, as agreed.
