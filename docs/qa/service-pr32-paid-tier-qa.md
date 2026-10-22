# QA: service #32, opt-in paid tier behind `TIER=paid`

- **PR:** apotheke-autofill-service #32 into `main` (draft).
- **Head:** `31dacf5d18672d0068502d461786cd526bdd0654`. **Base:** `39b2b58702062fcb8289d37af5d2b112e0b307c2` (#31 merge).
- **Commits:**
  - `dabcc71` feat(worker): 16 files, +660/−42.
  - `d048e34` test: `paid-tier.test.ts` only, +8/−2. The near-cap byte comparison uses `Buffer.equals` instead of boxing 2 MB into arrays.
  - `31dacf5` docs: `docs/cloudflare-workers-deploy.md` only, +10/−2. An existing deploy runs only `wrangler deploy --env paid`, and the token step is for first-time setup.
- **Date:** 2026-10-22, 22:12–22:40 (UTC+3).
- **Scope:** stub pass only.
  - Local workerd through `@cloudflare/vitest-plugin` with a stub AI. No Cloudflare credentials, 0 Neurons.
  - The live checks come after DevOps's `--env paid` deploy (see "Live check (pending deploy)").
- **CI:**
  - Run 39838859709 at `31dacf5`: **5/5 jobs green** on attempt 1 (worker, lint-test, gitleaks, deploy-scripts, image), finished 22:21 (UTC+3).
  - Run 39838520321 at `d048e34`: 5/5 green. DevOps confirmed worker 82/82, and that the paid dry run shows the expected bindings.
  - Run 39837678574 at `dabcc71`: red. The worker job had 81/82, with the near-cap test timing out at vitest's 5 s. `d048e34` fixed it (see I-1).
- **Verdict: PASS.** No High or Medium findings.
  - Free mode is unchanged apart from the two appended `/v1/info` fields.
  - Only the exact string `paid` turns the tier on. The caps, auth, budget and fail-closed paths behave the same in both modes.
  - The resolved paid config is complete.
  - Two Lows are open and don't block merge. One Low (DevOps, the token rotation in the deploy doc) was fixed in `31dacf5`.

## Plan

1. **Delta.**
   - Commits and files against `39b2b58`. Each follow-up commit diffed against its parent.
   - The near-cap test checked for exact byte equality, with a byte-flip mutant.
2. **Free mode, base against head.** One QA-only vitest file run unchanged on both trees, recording status, headers (minus `date`) and body for 15 requests:
   - `/healthz` and `/v1/info`
   - 401 missing and 401 wrong on info and suggest
   - suggest 200 with JPEG and PNG
   - 415 (GIF magic), 400 (`text/plain`)
   - 413 (image at cap+1, whole body at cap+1)
   - 404, `DELETE` (405 path)
   - 503 with `AUTOFILL_TOKEN` empty and unset
3. **Tier parsing.** `/v1/info` under `TIER` unset, `""`, `PAID`, `Paid`, `" paid"`, `"paid "`, `free`, `x;paid`, NBSP+`paid`, `"paid\n"` and `paid`. Also mixed configs: `TIER=paid` with a 1 MiB body cap, and free with a 2 MiB body cap.
4. **413.**
   - An image part of exactly `enforced_image_bytes`, then +1, in each mode, and a whole body of `MAX_UPLOAD_BYTES`+1.
   - `enforced = MAX_UPLOAD_BYTES − 65536` checked in code against `/v1/info`.
5. **Auth.** 401 and the 503 for a missing or empty `AUTOFILL_TOKEN`, in both modes.
6. **Budget.**
   - Var defaults.
   - The cap-hit error in both modes.
   - A Durable Object that throws.
   - 20 and 40 concurrent suggests against small budgets, with the stub reporting 6, 30 and 60 Neurons per call.
7. **wrangler 4.151.2.**
   - `wrangler deploy --dry-run` and `--dry-run --env paid` with `--outdir`, plus `unstable_readConfig` for both environments.
   - Base top-level config compared with head top-level. Bundle sha compared between the two environments.
8. **CPU and memory near the paid cap.** Stage timings of the near-cap test, and three whole-Worker calls with a 2,030,592-byte image against a 100 KB one, in local workerd.
9. **Static checks.** vitest, typecheck, ruff, docs and OpenAPI coverage, a grep of added lines for hosts, URLs and tokens, gitleaks on the range and on `--all`, and the CI result.

## Results

### 1. Delta: PASS

- `d048e34` touches only `worker/test/paid-tier.test.ts`. `sameBytes()` wraps both views as `Buffer`s over their own offset and length and calls `.equals()`, which compares length and every byte.
- A mutant that flips the last byte before base64 for inputs over 1 MB fails the near-cap test, so the assertion is still exact.
- The paid-tier file now takes 398 ms locally, down from about 4.1 s.
- `31dacf5` touches only the deploy doc.
- `worker/` at `31dacf5` is identical to `d048e34`.

### 2. Free mode is unchanged: PASS

All 15 recorded responses are identical between base and head (status, headers and body), except `/v1/info`:
- On `/v1/info`, every base key keeps its value and its order.
- The only additions are `enforced_image_bytes: 983040` and `tier: "free"`, appended at the end.
- Its headers are identical.

| Request | Status (base = head) |
|---|---|
| `GET /healthz` | 200 `{"status":"ok"}` |
| `GET /v1/info` | 200, plus 2 appended fields |
| info/suggest without a token, or with a wrong one | 401 `unauthorized`, `WWW-Authenticate: Bearer` |
| suggest JPEG, PNG (stub) | 200 |
| suggest GIF magic | 415 `unsupported_media_type` |
| suggest `text/plain` | 400 `expected multipart/form-data` |
| image part 983041 | 413 `image exceeds 983040 bytes` |
| body 1048577 (`Content-Length`) | 413 `image exceeds 1048576 bytes` |
| `/nope` | 404 |
| `DELETE /v1/info` | 401 (auth runs before the method check) |
| `AUTOFILL_TOKEN` empty / unset | 503 `service is not configured` |

The 404 code (`bad_request`) and the whole-body 413 text ("image exceeds <body cap>") are unchanged from base (N-2).

### 3. Tier parsing: PASS

| `TIER` | `tier` | `max_image_bytes` | `enforced_image_bytes` | Neuron limit |
|---|---|---|---|---|
| unset, `""`, `PAID`, `Paid`, `" paid"`, `"paid "`, `free`, `x;paid`, NBSP+`paid`, `"paid\n"` | free | 524288 | 983040 | 6000 |
| `paid` (other vars at their free values) | paid | 983040 | 983040 | 6000 |
| `paid` + `MAX_UPLOAD_BYTES=1048576` | paid | 983040 | 983040 | 6000 |
| unset + `MAX_UPLOAD_BYTES=2097152` | free | 524288 | 2031616 | 6000 |
| `[env.paid]` vars | paid | 1048576 | 2031616 | 30000 |

- `resolveTier()` is one strict comparison (`raw === "paid"`), with no trim or case folding.
- `max_image_bytes = min(advertised(tier), enforced)`, so it can never exceed the enforced cap, even with a half-applied config.

### 4. 413: PASS

| Mode | Image = enforced | Image = enforced + 1 | Whole body = cap + 1 |
|---|---|---|---|
| free | 200 | 413 `image exceeds 983040 bytes` | 413 |
| paid | 200 | 413 `image exceeds 2031616 bytes` | 413 |

- `maxImageBytes(bodyCap) = max(0, bodyCap − MULTIPART_HEADROOM)`, with `MULTIPART_HEADROOM = 65536`. `/v1/info` reports exactly that number.
- The rejected requests make no AI call.

### 5. Auth: PASS

In both modes, a missing or wrong bearer gets 401 with `WWW-Authenticate: Bearer`. An unset or empty `AUTOFILL_TOKEN` gets 503 `backend_unavailable` on `/v1/info` and `/v1/suggest`. The PR's `auth on both tiers` test asserts the same, and auth code is not in the diff.

### 6. Budget: PASS

| Check | Result |
|---|---|
| defaults | `parseNeuronBudget` falls back to 6000. Free sets `"6000"`, and `[env.paid]` sets `"30000"`. A paid deploy missing the var would fall back to 6000, the safe side |
| cap hit | free and paid return the same 503 `daily capacity is exhausted`, with `Retry-After: 3600` (PR test, re-run) |
| Durable Object throws | suggest 503 `the service could not complete the request` with 0 AI calls, and `/v1/info` 503, in both modes |
| 20 concurrent, budget 90, 30 per call | 3 × 200, 17 × 503, 3 AI calls, used 90, reserved 0, in both modes |
| 40 concurrent, budget 300, stub 6 / 30 / 60 per call | 10 admitted each time. Used 60 / 300 / 600 |

- `reserve` is serialized in the Durable Object and admits only while `used + reserved + 30 ≤ budget`.
- Overshoot is therefore bounded by in-flight calls × (actual − 30), and only exists when a call costs more than the 30-Neuron estimate.
- Live photos have cost about 6 Neurons per call, and image size has not changed that. The 2.9 MiB photo in `feature11-worker-live-qa.md` cost the same as a 1024 px one.
- `budget.ts` is not in the diff.

### 7. wrangler: PASS

- **Dry runs:** `wrangler deploy --dry-run` and `--dry-run --env paid` both exit 0. The bundle sha is the same for both (`145bd110…`): the code is identical and only the config differs.
- **Unknown env:** `--env nope` exits 1.
- **Warning:** the plain command now prints Wrangler's "multiple environments, no target environment specified" warning. It still publishes the top-level config (N-3).

Resolved config (`unstable_readConfig`), top-level at head against `[env.paid]`:

| Key | Top-level (identical to base) | `[env.paid]` |
|---|---|---|
| `name` | `apotheke-autofill` | `apotheke-autofill` |
| `main`, `compatibility_date`, `compatibility_flags` | `src/index.ts`, `2026-10-19`, `nodejs_compat` | inherited, same |
| `workers_dev` / `preview_urls` | false / false | false / false |
| `routes` | 1, `custom_domain = true` | the same route, `custom_domain = true` |
| `vars` | NEURON_BUDGET 6000, REQUEST_CAP "", MAX_UPLOAD 1048576 | 30000, "", 2097152, TIER paid |
| `ai` | `AI` | `AI` |
| `durable_objects` | `BUDGET` → `Budget` | `BUDGET` → `Budget` |
| `migrations` | `v1`, `new_sqlite_classes = ["Budget"]` | the same tag, so the Budget state carries over |
| `ratelimits` | `RATE_LIMITER`, 1001, 10/60 s | the same |
| `limits` | none | `cpu_ms = 500` |
| `observability` | logs and traces at 1.0 | inherited, same |
| `account_id`, KV | none | none |

- **Vars:** no var is missing from `[env.paid]`. The only differences are the three intended overrides plus `TIER`.
- **Top-level file:** `worker/wrangler.toml` above `[env.paid]` is byte-identical to base (sha256 `9eefa11c…`, pinned by the PR's test).
- **DevOps (credited):** their config review agrees. It's the same name, the vars, AI, the Durable Object, the rate limits and routes are repeated, and the `v1` tag is unchanged. Plain `wrangler deploy` is the rollback to free.

### 8. CPU and memory near the paid cap: PASS (stub), live measurement pending

Stage timings in local workerd (`performance.now()`, 1 ms granularity). The first rows are from the original near-cap test at `dabcc71`:

| Stage | ms |
|---|---|
| build a 2,030,592-byte JPEG with `"Q".repeat` | 3 |
| build the multipart body | 1 |
| `readSuggestParts` (a view, not a copy) | 1 |
| `bytesToBase64` (native `toBase64`) | 1 |
| `expect(Array.from(a)).toEqual(Array.from(b))` | 580 |
| `expect(u8).toEqual(u8)` on 2 MB | 3128 |
| whole Worker call, 2 MB image, paid (3 runs) | 20, 13, 13 |
| whole Worker call, 100 KB image (2 runs) | 12, 3 |

- The 5 s timeout came from vitest's deep equality over 2 million boxed elements. It was not the Worker's base64 path.
- A near-cap call costs about 10 ms more wall time than a 100 KB call locally. That is consistent with the doc's statement that it can exceed the free 10 ms target, and far below 500 ms.
- These are local wall times, not Cloudflare `cpuTime`. The real number is a live-check item.
- **Memory:** the image is a view of the single body buffer (asserted). Base64 of 2,031,616 bytes is 2,708,824 characters. Body plus base64 is under 8 MB as Latin-1 and under 16 MB as UTF-16, against a 128 MB isolate.

### 9. Static checks: PASS

| Check | Result |
|---|---|
| vitest (node 22 container) | base 64/64. `dabcc71` and `31dacf5` 82/82 |
| `npm run typecheck` | OK |
| `ruff check .` | all checks passed |
| pytest | not re-run here. CI `lint-test` runs `ruff`, `ruff format --check` and `pytest -q` |
| docs | `api-v1.md`, `openapi.yaml`, README, `.env.example`, `.dev.vars.example` and the deploy doc describe both tiers. The two new fields are optional, Worker-only and appended |
| added lines | the only URL is `https://autofill.example/v1/info` (a test). The one route line in `[env.paid]` repeats the top-level route already in base. No account id, token, IP or new hostname |
| gitleaks 8.30.1, git mode, `39b2b58..31dacf5` (3 commits), repo config | no leaks |
| gitleaks, git mode, `--all`, repo config | no leaks |
| gitleaks, dir mode | 2 hits, both in files this PR doesn't touch: the `<…>` placeholder in `deploy/jetson/service.env.example` and a `ci.yml` test header. The path-scoped allowlist doesn't apply in dir mode. CI's `gitleaks detect` passes |
| `local-info-check.sh` | now also asserts `tier: "free"`, `enforced_image_bytes`, and no `recommended_image_bytes` (CI step) |

## Findings

| ID | Severity | Status | Finding |
|---|---|---|---|
| L-1 | Low | open | the deploy Action silently reverts a paid deploy to free |
| L-2 | Low (DevOps) | fixed in `31dacf5` | the deploy doc's paid block also rotated `AUTOFILL_TOKEN` |
| L-3 | Low | open | the "What you need" list names only the Workers Free plan |
| N-1 | Nit | open | no blank line before `[env.paid]` (dropped on purpose) |
| N-2 | Nit | open | the whole-body 413 message names the body cap as "image" |
| N-3 | Nit | open | plain `wrangler deploy` now prints the multiple-environments warning |
| N-4 | Nit | open | `local-info-check.sh` checks only the free tier at runtime |
| I-1 | Info | fixed in `d048e34` | the near-cap test's timeout was test overhead, not Worker CPU |

**L-1: the deploy Action reverts paid to free on the next Worker change (Low, open).**
- `deploy-worker.yml` runs exactly `npx wrangler deploy` on every push to `main` that touches `worker/**`. `tests/test_worker_deploy.py` pins that command.
- After DevOps's manual `--env paid` deploy, the next merged Worker change republishes the top-level config. The Worker goes back to the free vars: 1 MiB body, 6000 Neurons, no `cpu_ms`.
- Clients that re-read `/v1/info` adapt to 524288. A client that cached 1048576 would get 413 for images between 983041 and 1048576 bytes.
- The doc says the Action stays free, and that a plain deploy rolls back to free. It doesn't say that the Action itself will undo a paid deploy.
- Fix: one sentence in the deploy doc and the README ("a later push to `main` under `worker/` redeploys free; re-run `wrangler deploy --env paid` after it, or pause the workflow"). Alternatively, a workflow input or repository variable that selects `--env paid`. That would need a design change and a test update.

**L-2: the token rotation in the deploy doc (Low, found by DevOps, fixed).**
- At `dabcc71`, the paid code block ran `openssl rand -hex 32 | npx wrangler secret put AUTOFILL_TOKEN` right after `wrangler deploy --env paid`. On an existing deploy, that rotates the token and locks out connected clients.
- `31dacf5` keeps only `npx wrangler deploy --env paid` for an existing deploy. It says the secret carries over because it's the same Worker, that the secret step is for first-time setup only, and that a plain `wrangler deploy` rolls back to free. Verified.

**L-3: plan prerequisite (Low, open).**
- "What you need" still lists only "a Cloudflare account on the Workers Free plan".
- The paid block needs the Workers Paid plan for `limits.cpu_ms`, and Workers AI is billed past the 10,000-Neuron allocation at the 30000 cap. Both facts appear only further down, under Cost and the `[env.paid]` notes.
- Fix: add one bullet under "What you need" for the paid tier.

**N-1:** no blank line before `[env.paid]` in `wrangler.toml`. This is cosmetic and was dropped on purpose, and the PR's test pins the top-level bytes.

**N-2:** the whole-body 413 says `image exceeds <MAX_UPLOAD_BYTES> bytes` (2097152 on paid), while `/v1/info` reports 2031616 as the image cap. This is unchanged from base, but the gap is wider on paid. Consider "request body exceeds …".

**N-3:** plain `wrangler deploy` now prints Wrangler's "multiple environments, no target environment specified" warning, which the doc mentions. `--env=""` would make the free deploy explicit, but the Action is pinned to the bare command by design.

**N-4:** `local-info-check.sh` exercises only the free tier at runtime. The paid path is covered by vitest and the paid dry run, but not by a `workerd` boot with `--env paid`.

**I-1:** the timeout in CI 39837678574 came from boxing 2 MB into arrays for `toEqual`: 580 ms plus 3128 ms locally, more on a CI runner. The Worker's base64 path takes about 1 ms. Fixed in `d048e34`.

## Live check (pending deploy)

Run after DevOps deploys with `npx wrangler deploy --env paid` from `worker/` at the merged commit. Use the existing token from a file (no secret put, no rotation) against https://autofill.example (substitute the real Worker URL locally, never in a doc). Spend at most 5 photos.

1. **`/healthz` and `/v1/info`.**
   - `/healthz` returns 200.
   - `/v1/info` returns `tier: "paid"`, `max_image_bytes: 1048576`, `enforced_image_bytes: 2031616` and Neuron `limit: 30000`, with `ready: true`.
   - Record `used` and `requests` before the calls.
2. **Auth and limits, 0 Neurons.**
   - suggest with no token and with a wrong token: 401.
   - Image part 2031617 bytes: 413 `image exceeds 2031616 bytes`, with no inference row.
   - A GIF or text part: 415. A body over 2097152 with `Content-Length`: 413.
3. **Photos, at most 5 AI calls including repairs.**
   - One small (about 100 KB), one at about 512 KB, one at about 1 MiB, and one near the cap (about 1.9–2.0 MiB, under 2031616).
   - Each must return 200 with valid JSON and the Gemma 4 26B model in `model_used`.
4. **CPU time against the 10 ms soft target and the 500 ms limit, with Coder.**
   - Take each request's `cpuTime` and `wallTime` from `wrangler tail --env paid` or Workers observability (GraphQL `workersInvocationsAdaptive`, filtered to the Worker).
   - Report the near-2 MiB upload's `cpuTime` against 10 ms and 500 ms.
   - Compare it with the small photo and with the 126 ms that a 2.9 MiB photo measured earlier. Flag anything over 250 ms (half the ceiling).
5. **Neurons and the cap.**
   - Take the `/v1/info` `used` delta and `aiInferenceAdaptive`. Expect about 6 Neurons per photo, under 40 total.
   - Confirm the cap path without spending Neurons, by reading `budget.neurons.limit` = 30000. Don't exhaust it live.
   - Afterwards, confirm that a plain `wrangler deploy` is the documented rollback (don't run it unless DevOps rolls back), and that the token was not changed.
