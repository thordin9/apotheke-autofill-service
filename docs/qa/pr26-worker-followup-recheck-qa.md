# QA recheck: PR #26 Worker follow-ups at 21ab9b0 (stub-only)

- PR: #26 (draft). Head `21ab9b0359d4d414bd79819e01d626e36a7d69ee`. Previous head `5f2189805aaa4b88d6ef2851f23a02f3f2476a46`. Base main `c7b6d55efb0d398899089e68a3e087c96d1556d0`.
- Date: 2026-10-20, about 01:40–01:45 EEST.
- Scope: an addendum to `docs/qa/pr26-worker-followup-stub-qa.md`. The rules are the same:
  - No deploy, no `wrangler dev` and no live Worker calls.
  - No Cloudflare credentials in the environment.
  - Vitest runs with `remoteBindings: false` and `AUTOFILL_REQUIRE_AI_STUB=1`.
- Since `5f21898`, the change covers 10 files, +394/−62:
  - `worker/src/ai.ts`, `worker/src/multipart.ts`, two Worker test files and two pytest files.
  - CHANGELOG, `docs/api-v1.md` and `docs/cloudflare-workers-deploy.md`.
  - The stub QA doc, committed.
- `wrangler.toml`, the workflows and `.gitleaks.toml` are unchanged.

## 1. Plan

| # | Check |
|---|---|
| R1 | CI check runs on the head, waiting until all are complete. Local Vitest and pytest. gitleaks over the diff vs main |
| R2 | Size and sha256 of the stub QA doc and the live QA doc |
| R3 | M-5 with QA's own probes: errors thrown as workerd `InferenceUpstreamError` (code only in the message, with and without the name prefix, no `errors` array). Status, Retry-After, day exhausted, negatives, and no read of `lastRequestInternalStatusCode` |
| R4 | L-5 CHANGELOG. L-6: the same 15 config mutations. L-7 `indexOf` scan: parser parity on bodies captured from PHP curl `CURLStringFile`, curl `-F` and httpx, the edge-case set, a differential fuzz against the old parser, a cost probe, and the limit probes at the shipped defaults |
| R5 | Re-run every #25 and #26 probe. Hygiene on the full diff vs main |

## 2. Results

### R1: PASS

- CI on `21ab9b0`: all 5 checks passed (`gitleaks`, `worker`, `lint-test`, `deploy-scripts`, `image`), 01:38–01:42 EEST.
- Local: Vitest **47 passed** (7 files) and pytest **560 passed**.
- gitleaks over main..head (2 commits): 0 leaks with the repo config and 0 with the default rules.

### R2: PASS

- `docs/qa/pr26-worker-followup-stub-qa.md`: 14199 bytes, sha256 `59b399ff701fbe4d34bd4c6b0dc9bc57104d49d11c06b5917e2ea5a234c57118`, byte-identical to QA's copy. A new pytest pins it.
- `docs/qa/feature11-worker-live-qa.md`: 12395 bytes, sha256 `3be8bc9b55402d564535aa179dcc5934daeed82b871587eca3adbece8a59b7b8`, unchanged.

### R3 M-5: PASS

`ai.ts` adds the anchored rule `/^(?:InferenceUpstreamError:\s*)?(3036|3040|3006):\s/`. It's applied to each object's `message` after the structured fields. No file under `src/` reads `lastRequestInternalStatusCode`; the name appears only in a comment.

QA probe at 22:30Z (5400 s to 00:00 UTC). The stub AI's `lastRequestInternalStatusCode` getter counts reads.

| Thrown error | Status | Retry-After | Message | Day exhausted | Getter reads |
|---|---|---|---|---|---|
| `3036: …` | 503 | 5400 | daily capacity is exhausted | yes (`ready` false) | 0 |
| `InferenceUpstreamError: 3036: …` | 503 | 5400 | daily capacity is exhausted | yes | 0 |
| `3040: …` | 503 | 30 | the model is out of capacity | no (`ready` true) | 0 |
| `InferenceUpstreamError: 3040: …` | 503 | 30 | the model is out of capacity | no | 0 |
| `3006: …` | 413 | – | image exceeds 983040 bytes | no | 0 |
| `InferenceUpstreamError: 3006: …` | 413 | – | image exceeds 983040 bytes | no | 0 |

- After a 3036, the next request is 503 with Retry-After 5400, and the model is called once in total. A 3040 followed by a success gives 503 and then 200.
- No upstream text reaches the body.

Negatives return null both as an `InferenceUpstreamError` and as a plain `Error`:
- "network reset after 3036 ms" and "failed after 3006 ms"
- "upstream 3040: busy" and " 3036: leading space"
- "3036 without colon" and "3036:no-space"
- "Error: 3036: other prefix" and "x InferenceUpstreamError: 3040: mid"
- "30360: five digits" and "13040: x"

A request whose error says "…after 3036 ms" gets 503 with no Retry-After, and the day is not exhausted. Structured `errors:[{code:3040}]` still maps.

### R4: PASS

- **L-5:**
  - There is one `## [Unreleased]` (`CHANGELOG.md:6`); the old second section was merged into it.
  - "Upstream text is not returned. A Durable Object error fails closed." is now its own bullet in `[2.0.0]` (`:100`), no longer under the `.npmrc` bullet (`:98`).
  - `test_changelog_moves_v3_lean_note_and_records_the_cap` locks both.
- **L-6:** 15 of 15 mutations are caught.
  - pytest catches 13, including smoke accepting `503` and Test moved after Deploy.
  - Vitest catches the other 2 (budget var 3 failures, ratelimit removed 1 failure).
- **L-7:** `findBytes` now finds the first byte with `Uint8Array.prototype.indexOf`, and checks the rest of the delimiter only at those positions.
  - The PHP curl `CURLStringFile`, curl `-F` and httpx bodies parse with the image byte-identical and the context intact.
  - All 20 edge cases give the same result as at `5f21898`.
  - Fuzz: 400 bodies heavy in CR, LF, `-` and boundary letters give 0 differences between the old and new parser.
  - Cost in workerd (30-iteration mean, 983040-byte image): the parser takes 0.47 ms, down from 2.0 ms at `5f21898`. The old `formData()` path takes 2.7 ms. At 300 KB the parser takes 0.17 ms.
- **Limits** at the shipped defaults:
  - An image of exactly 983040 bytes gives 200. 983041 gives 413 "image exceeds 983040 bytes", with no AI call.
  - A body of 1048576 bytes gives 413 on the image cap. A body of 1048577 bytes gives 413 "image exceeds 1048576 bytes". Both hold with Content-Length and chunked, with no AI call.

### R5: PASS

- The #25 probes give 26 passed and 5 failed. All 5 failures are the intended changes: the three 3 MiB cap tests, the `it.fails` for M-1 (now fixed), and the 3040 midnight Retry-After (now 30). The #25 probe "3036 (message-only, as the binding throws it)", which failed at `5f21898`, passes again.
- The #26 probes give 11 of 11 passed. The binding-shape 3036, 3040 and 3006 tests that failed at `5f21898` now pass. Routes, `backend_used` and the log line (`*_ms` numeric, no token or image) are unchanged.
- Hygiene, across 1195 added lines vs main:
  - No IPv4, no 32-hex values and no hostname.
  - The only 40/64-hex values are known commit and document hashes.
  - Domains are the existing custom domain, github.com and `workers.dev` mentions in the QA docs.
- `wrangler.toml` still has `workers_dev = false`, `preview_urls = false` and the custom-domain route.

## 3. Findings

- M-5, L-5, L-6 and L-7 are closed. No regression was found.
- **N-2 (nit, docs):** the docs say the message rule applies to an "`InferenceUpstreamError` message". The code doesn't check `err.name`, so any error or object whose message starts with `3036: `, `3040: ` or `3006: ` matches. That's harmless because the rule is anchored and needs the colon and a space.
- Still open from the stub QA:
  - M-3/F-1: re-measure `cpuTime` after deploy.
  - N-1: the parser is stricter than `formData()` on a preamble, LF-only line endings and a duplicate image part. Real clients are unaffected.

## 4. Verdict

**PASS. Ready to merge once the Apotheke release is tagged.**

- CI is green, the local counts match (Vitest 47, pytest 560) and gitleaks is clean.
- Both QA docs are byte-identical.
- M-5 is fixed and verified with errors in the real binding's shape. L-5, L-6 and L-7 are fixed. Nothing regressed.
- The PR stays a draft until the Apotheke release is tagged. After the merge deploys it, QA re-measures `cpuTime`.
