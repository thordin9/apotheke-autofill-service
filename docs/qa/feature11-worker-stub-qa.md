# Feature 11 (Cloudflare Worker): stub QA for #25

QA on 2026-10-20, 00:15–00:35 EEST. Box only. Fresh clone, Node 22.23.3, `@cloudflare/vitest-plugin` 1.4.5 with a stubbed `env.AI`, gitleaks 8.28.0, Python pytest. Every Vitest and Wrangler run had loopback-only networking (a network namespace with no route out) and no Cloudflare credentials in the environment. **No live Cloudflare calls, no deploy, no `wrangler login`, 0 Neurons.** The Jetson was not touched.

Heads reviewed:
- **`a2f0ea0`**, the requested head, reviewed in full.
- **`b9745f4`**, the current head. It is `a2f0ea0` plus one commit with DevOps's nits. Everything was re-run on it, and **the verdict below is for `b9745f4`**.
- Base: main `e150d3f`.

## Verdict for #25 at b9745f4

**PASS with follow-ups.** There are no High findings. The contract mostly holds:
- Fail-closed auth with digest compare.
- The 3 MiB body cap, checked from both the header and the bytes read.
- Media type from magic bytes.
- One repair, with prose rejected.
- A SQLite Durable Object cap that reserves first, survives a clock step backwards, and fails closed.
- Error mapping without upstream text.
- A rate limit applied after auth.
- Clean logs.
- A main-only, SHA-pinned deploy workflow with step-scoped secrets.
- A clean gitleaks history, with the M9 bypasses now caught.

Four Medium items (M-1 to M-4) should be fixed in a follow-up. M-3 should be measured in the live check before the 3 MiB cap is relied on.

## 1. Test plan

| ID | Area | What was checked |
|---|---|---|
| T1 | CI and tests | Check runs on both heads. Worker `npm ci`, `npm run typecheck`, `vitest run`, `wrangler deploy --dry-run`, all offline. Python `pytest tests` |
| T2 | Auth | `/healthz` open. SHA-256 on both sides, then `timingSafeEqual`. Missing, wrong, prefix, scheme and query-string tokens give 401. A missing or empty `AUTOFILL_TOKEN` gives 503 on `/v1/*` and on `/healthz` |
| T3 | Upload cap | `/v1/info` `max_image_bytes`. A body exactly at 3145728 and one byte over. Bad `Content-Length`. A declared length over the cap. A streamed body with no length, at and over the cap. A declared length smaller than the real stream |
| T4 | Media type | 415 from magic bytes (GIF, HEIC, PDF, text declared as JPEG). JPEG, PNG and WebP accepted. The data-URI MIME follows the magic bytes |
| T5 | Model I/O | Gemma 4 26B, `enable_thinking: false`, `json_schema`. Exactly one repair. Prose after the repair rejected (old L7). `backend_used` and `model_used` |
| T6 | Daily cap | Durable Object reserve before the call and settle after. Store failure on reserve and on settle. 40-way concurrency race. Clock stepping backwards. Reset at UTC midnight (not Athens). Exact Retry-After. A cap of 0. Test-clock header ignored in production mode. `/v1/info` used and left |
| T7 | Workers AI errors | 3036 (structured and message-only), 3040, 3006, 5035/403, 3007/408, 3042/404, 5007/400. No upstream text in responses |
| T8 | Rate limit, base64, logs | `[[ratelimits]]` after auth; key; 429 and Retry-After; a limiter that throws. Native `toBase64()`. Sentinel scan of every console method |
| T9 | `wrangler.toml` | Custom domain, `workers_dev`/`preview_urls` false, `new_sqlite_classes = ["Budget"]`, no `account_id`, no KV, observability. **Mutation test**: 24 one-line drifts, each checked against the config test |
| T10 | `deploy-worker.yml` | Triggers, environment, permissions, concurrency, runner, SHA pins (checked against upstream tags), pinned Wrangler, `npm ci`, Vitest, deploy from `worker/`, secret handling, `AUTOFILL_TOKEN` never touched, smoke step behaviour before and after the secret is set. Stub guard |
| T11 | gitleaks | Allowlist scoping; full history; planted-then-deleted token; the earlier bypasses (second bearer on one curl line, glued placeholder) plus the path-scoped allowlist; `scripts/gitleaks-allowlist-fixture.sh`. Throwaway branches in clones with no remote |
| T12 | Docs and repo | `docs/cloudflare-workers-deploy.md` (manual deploy, workflow, fork secrets, `<your-domain>`), Jetson marked legacy, known issues H2/M5/M6/L6/L7, `CF_*_FILE` paths under `/var/lib/autofill` plus the mount test. Repo scan for the Jetson hostname, private IPs, 32-hex ids and tokens |
| T13 | Free tier | Against ITAdvisor's free-tier notes: 10 ms CPU, memory, body size. Local workerd timings for 3 MiB |

Probes: `worker/probes/qa.probe.ts` with its own `worker/probes/vitest.config.ts` (same stub-only setup, uncommitted). Run from `worker/` with `npx vitest run --config probes/vitest.config.ts`. A probe marked `it.fails` reproduces an open finding and starts failing once the finding is fixed.

## 2. Results

### Counts

| Item | a2f0ea0 | b9745f4 |
|---|---|---|
| CI check runs | 5/5 success (gitleaks, worker, lint-test, image, deploy-scripts) | 5/5 success |
| Worker Vitest | 38/38 | 38/38 |
| QA probes | 31: 30 passed, 1 expected fail (M-1) | same |
| `npm run typecheck`, `wrangler deploy --dry-run` | pass (48.12 KiB upload) | pass |
| Python pytest | 551 passed | `test_worker_deploy.py` + mount test: 5 passed (one test added) |
| gitleaks: history / planted-then-deleted / fixture script | exit 0 / exit 1 / exit 0 | exit 0 / exit 1 / exit 0 |

### Results by area

| ID | Result | Notes |
|---|---|---|
| T2 | **PASS** | `!env.AUTOFILL_TOKEN` gives 503 `service is not configured` on `/v1/info` and `/v1/suggest` (also with an empty bearer), and `/healthz` gives 503 `degraded`. Tokens that are missing, wrong, a prefix, `Basic`, `Token`, bare `Bearer` or in the query string get 401 with `WWW-Authenticate: Bearer`. The scheme is case-insensitive. Unequal lengths don't throw, because the inputs are the digests. |
| T3 | **PASS with L-4** | Exactly 3145728 bytes: 200. 3145729: 413, no AI call. Streamed at the cap: 200. Streamed over: 413. A `Content-Length` that isn't a plain integer: 400. Declared over the cap: 413 before the read. A declared length smaller than the stream: not 200, no AI call. `/v1/info` reports 3145728. |
| T4 | **PASS** | GIF, HEIC, PDF and text with a JPEG part type: 415, no AI call. The part type is ignored. The data URI uses the sniffed MIME. |
| T5 | **PASS with M-1** | `@cf/google/gemma-4-26b-a4b-it`, `chat_template_kwargs.enable_thinking=false`, `response_format.type=json_schema`, `temperature` 0, 512 tokens. Three bad replies give 2 AI calls, then 503 `backend_error`. Prose ("Cordless drill", "drill", a refusal) after the repair: 503, and the text isn't echoed. `model_used` is set. **`backend_used` is missing from `/v1/suggest`** (M-1). |
| T6 | **PASS** | During the AI call the DO shows `reserved` 30; after it, `used` = the actual Neurons and `reserved` 0. Reserve throws: 503 and AI not called. Settle throws: 503. 40 parallel requests with room for 5: exactly 5 AI calls, 5×200 and 35×503, with peak concurrency above 1. Clock one day back: counter kept. At 21:00Z (Athens midnight) the cap still holds, with Retry-After 10800. At 23:59:59Z, Retry-After is 1. At 00:00:00Z the day rolls over and `resets_at` is the next 00:00Z. Cap `0`: AI never called. `x-autofill-now` is ignored without the test var. |
| T7 | **PASS with M-2, L-1** | 3036 structured or message-only: 503 + Retry-After = seconds to 00:00 UTC (5400 at 22:30Z), DO marked exhausted, `ready` false. 3006: 413. 5035, 3007, 3042 and 5007: 503 `the model is unavailable`, no upstream text. **3040 also gets Retry-After until midnight** (M-2). |
| T8 | **PASS** | The limiter isn't called for unauthenticated requests or `/v1/info`. A limiter that throws gives 503 with no AI call. Key is `CF-Connecting-IP`; 429 has `Retry-After: 60`. `Uint8Array.prototype.toBase64` is used (falls back to `Buffer`, no `btoa`). Logs are one JSON line per request (status, ms, neurons, model, ai_calls). 0 hits for the token, a wrong-token sentinel, image base64, brand, serial or upstream text across `log`, `info`, `warn`, `error` and `debug`. Observability (logs and traces) is on with sampling 1. |
| T9 | **PASS with L-2** | The config is as required. The mutation test catches 13 of 24 drifts. The config test misses observability off, KV added, the budget var, the upload var and the ratelimit binding; Vitest catches the last three. Workflow misses are listed in L-2. |
| T10 | **PASS with L-3** | Triggers: `push` to main on `worker/**` and the workflow file, plus `workflow_dispatch`, with a job guard `github.ref == refs/heads/main`. No `pull_request` or `pull_request_target`. `environment: production`, `permissions: contents: read`, `cancel-in-progress: false`, `ubuntu-latest`. checkout `11d5960…` and setup-node `49933ea…` match upstream `v4.4.0`. Wrangler 4.151.2 is pinned in the lockfile. Steps run `npm ci` → `npm test` → `npx wrangler deploy` from `worker/`. The Cloudflare secrets are on the deploy step only and never echoed. `AUTOFILL_TOKEN` is never read. `b9745f4` adds `persist-credentials: false`. Smoke: see L-3. Stub guard: a non-stub `env.AI` is refused while `AUTOFILL_REQUIRE_AI_STUB=1` (`test/ai-stub.test.ts`). |
| T11 | **PASS with M-4** | Both commit allowlists use `condition = "AND"` with the two placeholder commits, `^generate-a-random-token>\s` and `Bearer ci-token'$`. In a commit outside those two, a second bearer header with a real-looking value on the same curl line is found, and so are `generate-a-random-token><real>`, a same-line `api_key` and an own-line token. The fixture script exits 0 ("planted tokens ok", "history clean"). **The separate path allowlist for `docs/qa/v1.4-stub-qa.md` isn't commit-scoped, so the second-bearer bypass still works in that file** (M-4). |
| T12 | **PASS** | The deploy doc covers the manual first deploy (`wrangler deploy`, then `secret put`), the workflow, the token permissions, fork secrets in the fork's own `production` environment and `<your-domain>`, and (at `b9745f4`) `wrangler rollback`. Docker and Jetson are marked legacy. H2, M5, M6, L6 and L7 are in `docs/deploy-jetson.md`, with pointers in `docs/cloudflare-backend.md`. `CF_API_TOKEN_FILE` and `CF_ACCOUNT_ID_FILE` are under `/var/lib/autofill`, and `run-service.sh` mounts `/opt/autofill/data:/var/lib/autofill` (the mount test passes). Repo scan: no Jetson hostname, no private IPs (only 127.0.0.1 and 0.0.0.0), no 32-hex ids besides a test fixture, gitleaks clean. |
| T13 | **M-3** | See M-3. |

### Delta a2f0ea0..b9745f4

Commit `b9745f4` changes 5 files (+26/−1):
- `persist-credentials: false` on the checkout step.
- A `npx wrangler rollback` section in the deploy doc.
- `worker/.npmrc` (`legacy-peer-deps=true`) **removed**, not commented. `npm ci` resolves cleanly without it.
- A test for those changes.
- CHANGELOG lines.

`worker/src`, `worker/test`, `wrangler.toml` and the lockfile are unchanged. Nothing else changed. One nit: a CHANGELOG line ("Upstream text is not returned…") is now stranded under the `.npmrc` bullet (I-4).

## 3. Findings

### High

None.

### Medium

**M-1. `/v1/suggest` has no `backend_used`.**
- Problem: `publicSuggestion` sets `model_used` but not `backend_used`. Only `/v1/info` has `backend_used`. The docs ("`backend` and `backend_used` are `cloudflare-workers`") and the contract ask for it on suggestions. Also, `openapi.yaml` Suggestion `backend_used` is `enum: [ollama, cloudflare]`, so `cloudflare-workers` wouldn't validate. Apotheke v2.4.1 ignores the field, so nothing breaks today.
- Repro: `probes::validation and repair > success carries backend_used on /v1/suggest` (`it.fails`).
- Fix: set `out.backend_used = "cloudflare-workers"` in `publicSuggestion`, add `cloudflare-workers` to the OpenAPI enum, and add a Vitest assertion.

**M-2. Workers AI 3040 ("Capacity temporarily exceeded, please try again") gets `Retry-After` until 00:00 UTC.**
- Problem: 3040 is transient (Cloudflare errors page, HTTP 429), but the Worker reuses `capacityError`, so the client is told to wait up to 24 h (5400 s in the probe at 22:30Z). It also says "daily capacity is exhausted". The DO isn't marked exhausted, which is correct. Apotheke retries only on a short Retry-After, so users see "busy" for the rest of the day's advice window even though the next request would work.
- Repro: `probes::3040 (temporary capacity) gets Retry-After until midnight`.
- Fix: for 3040, return 503 with a short `Retry-After` (for example 10–60 s) and a distinct message. Keep the until-midnight value for 3036 and the own cap. Update the doc's troubleshooting table.

**M-3. A request near the 3 MiB cap will probably exceed the 10 ms Free CPU limit.**
- Measured: local workerd, wall clock, 1 ms timer resolution, median of 5, for one 3 MiB image:

  | Step | Time |
  |---|---|
  | `toBase64` | 1 ms |
  | `JSON.stringify` of the AI input with the 4 MiB data URI | 6 ms |
  | `formData()` + `arrayBuffer()` | 4 ms |
  | SHA-256 of a token | ~0 ms (not a concern) |

- Problem: that's about 11 ms before body buffering and the binding's own serialization. A repair re-serializes the data URI. ITAdvisor's notes measured 7.1 ms for 2 MB and ~13 ms for 5 MB on the same path. The limit is soft (bursts are tolerated), but frequent near-cap uploads risk error 1102. Real Apotheke photos (40–240 KB) cost under 1 ms, so this only affects large uploads. The PR body says the same ("can approach").
- Fix: either lower `MAX_UPLOAD_BYTES` to 2 MiB (ITAdvisor's recommendation, still 8× headroom), or keep 3 MiB and confirm `cpuTime` in Workers Logs with a near-cap photo in the live check (plan step 5). Document the outcome in "CPU and base64".

**M-4. The path-scoped gitleaks allowlist reopens the M9 bypass for one file.**
- Problem: the second `[[allowlists]]` matches `paths = docs/qa/v1.4-stub-qa.md` AND the two regexes in **any commit**. `curl-auth-header` reports one match per curl command, ending at the first header. So in that file, a curl command with two Authorization headers (the first is the historical CI placeholder, the second a random 40-character bearer value) gives **0 findings** (1 finding without the allowlist).
- Proof: a throwaway branch in a clone with no remote, never pushed; the same line in a new file is found.
- Fix, verified locally (history exit 0; the doc bypass, second bearer, glued placeholder and own-line probes all found; planted proof still exit 1). Scope that entry to the commit that added the quote:

  ```toml
  [[allowlists]]
  description = "QA note quotes the historical curl placeholder."
  condition = "AND"
  commits = ["ba1f8ff4397c568c5b9b5ffdc9f2e680664ca231"]
  paths = ['''docs/qa/v1\.4-stub-qa\.md''']
  regexTarget = "match"
  regexes = ['''^generate-a-random-token>\s''', '''Bearer ci-token'$''']
  ```

  Add a fifth case to `scripts/gitleaks-allowlist-fixture.sh`: the second bearer appended to that file in a new commit.

### Low

- **L-1. Error codes are also matched in free text.** `workersAiCode` falls back to `/\b(3036|3040|3006)\b/` on any `Error.message`. An unrelated error such as "network reset after 3006 ms" becomes a 413 (probe). Worse, any message containing "3036" marks the DO exhausted until midnight. Fix: only accept the structured `code`/`internalCode`, or a message that starts with the code (`^(?:\w+Error:\s*)?(3036|3040|3006)\b`).
- **L-2. The config test misses some drift.**
  - Not failed by the config test: `[observability] enabled = false`, an added `[[kv_namespaces]]`, the budget and upload vars, and the ratelimit binding. The vars and ratelimit are caught by Vitest; observability and KV are not caught at all.
  - Workflow drift that passes everything: `npm ci` → `npm install`, the Test step removed, the secret printed with `printf` (only `echo` is checked), the secret moved to job-level `env`, the smoke step also accepting 503, and a step reading `secrets.AUTOFILL_TOKEN`.
  - Fix: assert `observability.enabled` and `logs.enabled`, no `kv_namespaces`, the `[[ratelimits]]` shape, the exact step order (`npm ci`, `npm test`, `npx wrangler deploy`), secrets only under the Deploy step's `env`, no `secrets.AUTOFILL_TOKEN` anywhere, and no `|| [ "$code" = "503" ]` in the smoke step.
- **L-3. The smoke step.**
  - Strictness: it is correctly strict. Before the secret is set, `/healthz` is 503, so the smoke fails (red) after about 3–7 min, and the deployed Worker stays fail-closed. It doesn't mask a missing secret, and it would fail a first deploy done by the Action. That's why the documented order (manual deploy plus `secret put` first) matters.
  - Gap: the URL is hardcoded to this repo's domain. A fork that follows the doc (change `routes` only) smoke-tests *our* service, so a broken fork deploy can pass. The checks also can't tell the new version from the old one during propagation, and a failure doesn't print the last HTTP code.
  - Fix: take the host from `wrangler.toml` (or a repo variable), print the last code on failure, and optionally compare a version from `wrangler deployments list`. Tell forks to change the smoke host too.
- **L-4. `max_image_bytes` is the whole-body cap.** Apotheke compares the raw image length with `max_image_bytes`. An image a few hundred bytes under 3 MiB passes Apotheke's check and then gets 413 because of multipart overhead. It's harmless with real photos. Fix: report `MAX_UPLOAD_BYTES − 64 KiB` (or cap the image part separately), and document it.

### Info

- **I-1.** `HEAD /healthz` gives 401, because only GET is open. Fine unless a monitor uses HEAD.
- **I-2.** A whitespace-only `AUTOFILL_TOKEN` passes `/healthz` (200), but `/v1/*` is always 401 (fails closed).
- **I-3.** Workers AI 429 without 3036/3040, and 408 timeouts, give 503 without `Retry-After`.
- **I-4.** At `b9745f4`, a CHANGELOG line about upstream text sits under the `.npmrc` bullet. Also, `.npmrc` was removed rather than commented. That's fine, since `npm ci` passes, and the new test covers it.
- **I-5.** The rollback doc says rollback "keeps the secrets". Worker versions snapshot their secrets, so rolling back to the very first version (before `secret put`) gives the fail-closed 503. Rollback is also refused across a Durable Object migration. Worth one sentence each.
- **I-6.** `ci.yml`'s worker job uses tag-pinned `actions/checkout@v4` and `actions/setup-node@v4`. Only the deploy workflow was required to be SHA-pinned.
- **I-7.** The stub guard depends on the test-only var. A test that clears `AUTOFILL_REQUIRE_AI_STUB` and doesn't pass its own `AI` would reach the real binding. With `remoteBindings: false` and no credentials in CI, that fails rather than spending Neurons. A global setup that swaps `env.AI` for a throwing object would make this structural.

## 4. Live-check plan (DevOps deploys exactly b9745f4; at most 5 photos)

0. **Preconditions.** Deploy by hand from `worker/` at `b9745f4`. Don't set the secret yet. Use a scoped token from the deployer's env (never echoed). Have Workers Logs open read-only.
1. **No-token probe first, no AI calls.** `GET /healthz` gives 503 `{"status":"degraded"}`. `GET /v1/info` and `POST /v1/suggest` (with and without a bogus bearer) give 503 `service is not configured`. Logs show `ai_calls: 0`. **0 Neurons.**
2. **Set `AUTOFILL_TOKEN`** (`openssl rand -hex 32 | npx wrangler secret put AUTOFILL_TOKEN`; don't echo it). Then:
   - `/healthz` gives 200.
   - `POST /v1/suggest` with no token, and with a wrong token, gives 401.
   - `/v1/info` with the token gives 200, `max_image_bytes` 3145728, and a budget of limit 6000 / used 0.
3. **413, 415, 400, no AI.** With the token:
   - A 3145729-byte multipart body (JPEG magic plus padding) gives 413.
   - The same body sent chunked gives 413.
   - GIF bytes declared `image/jpeg` give 415.
   - A non-multipart body gives 400.
   - Logs show `ai_calls: 0` for each. **0 Neurons.**
4. **Domains.**
   - `https://autofill.webapp.gr/healthz` does not reach this Worker (no DNS, or not our JSON).
   - `https://apotheke-autofill.<account-subdomain>.workers.dev/healthz` isn't served (`workers_dev = false`).
   - No preview URL exists for the version.
   - Only `apotheke-autofill-service.webapp.gr` answers.
5. **Photos (≤ 5).**
   1. `sony_cd_player_label.jpg`.
   2. `makita_drill_dhp453.jpg`.
   3. A typical ~1024 px Apotheke JPEG.
   4. A JPEG of about 2.9 MiB, for M-3.
   5. A spare (only for a retry).

   For each, record: status, latency, `model_used`, `backend_used` (expected missing, M-1), JSON validity, `neurons` in the log line, and the `/v1/info` used and remaining. Confirm the label read (brand, model, serial) and no 403/5035.
6. **Cap check, no AI.**
   - Temporarily deploy the same code with `--var AUTOFILL_DAILY_NEURON_BUDGET:<current used + 1>`.
   - `POST /v1/suggest` should give 503 `daily capacity is exhausted` with `Retry-After` = seconds to 00:00 UTC (±2 s). Logs show `ai_calls: 0`.
   - `/v1/info` should show `ready: false` and `remaining` 0.
   - Then **redeploy exactly `b9745f4`** with default vars and confirm the limit is 6000 again.
7. **Workers Logs (read-only).**
   - Read `cpuTime` and `wallTime` for every request.
   - Flag any `cpuTime` over 10 ms or a 1102, especially photo 4 (M-3) and any repair.
   - Confirm the log lines contain only the JSON fields: no token, base64, brand or serial.
8. **Merge.** The `deploy-worker.yml` run started by the merge redeploys the same `worker/` code. Its smoke step must be strict: `/healthz` 200 and an unauthenticated `/v1/suggest` 401. Afterwards **you verify** that:
   - the run is green;
   - the deployed version is the merge commit, with `worker/` identical to `b9745f4`;
   - `/healthz` is 200 and no-token is 401;
   - `/v1/info` shows the same `used` as before, because the DO persists.

**Expected Neurons:** steps 1–4, 6 and 8 cost 0. Photos cost about 6–7 Neurons each (Gemma 4, ~434 input tokens and ~80 output, as on #22), so 5 photos come to **≈ 30–35 Neurons**. The worst case, every photo needing the one repair, is about 70. A larger photo may use more image tokens (unverified). The cost comes out of the account's shared 10,000/day pool.
