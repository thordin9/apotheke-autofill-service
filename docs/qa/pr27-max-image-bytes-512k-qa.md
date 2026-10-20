# Service PR #27 (advertise `max_image_bytes` 524288): stub and vitest QA

QA on 2026-10-20, on the QA box only:
- **Round 1:** 10:44–11:05 EEST on head `d95f0a5c089197ca1cd3e652a291636520f3c27b`. Result **FAIL** (B-1).
- **Round 2 (recheck):** 11:12–11:36 EEST on head **`167671f970f99df0feba447f733b20e94949c89d`**.
- Branch `cursor/max-image-bytes-512k`, base main `d0e959a9620b39d173eebfd1cc5b55c467248fc8`.

How it was run:
- All trees came from codeload tarballs. Their shas were confirmed with `git get-tar-commit-id`.
- Tools: Node 22.23.3, `@cloudflare/vitest-plugin` 1.4.5 with a stubbed `env.AI` (`remoteBindings: false`, `AUTOFILL_REQUIRE_AI_STUB=1`), Wrangler 4.151.2, workerd 2026-10-19 (from the lockfile), Python 3.12 pytest and ruff, gitleaks.
- Round 2 ran every Worker, Python and script gate in a network namespace with loopback only (an outbound HTTPS probe failed as expected), with no Cloudflare variables in the environment.
- **No live Worker calls, no deploy, no `wrangler login`, 0 Neurons.**

## Verdict

**PASS at `167671f`.**

- B-1, M-1, L-1 and I-2 are fixed and verified, each with a mutation test.
- The suggest path is byte-identical to main.
- The photo set passes the PM's rule at 524288, so the 786432 fallback is not needed.
- What remains is non-blocking: I-1, the real-camera HEIC gap, plus two new Low/Info notes (N-1, N-2).
- CI run 39391653661 on `167671f`: **completed, success**. 5/5 jobs (worker, including the new "Local runtime info check" step, lint-test, gitleaks, deploy-scripts, image). It finished at 11:15:32 EEST.

| Head | Result | Reason |
|---|---|---|
| `d95f0a5` | **FAIL** | B-1: the Worker entry module exported a constant, so workerd refused to start the bundle |
| `167671f` | **PASS** | All findings fixed or accepted as non-blocking |

## Findings and status

| ID | Severity | Finding (round 1 unless noted) | Status at `167671f` |
|---|---|---|---|
| B-1 | Blocker | `export const ADVERTISED_MAX_IMAGE_BYTES` in `worker/src/index.ts`. workerd refused to start the bundle: `Uncaught TypeError: Incorrect type for map entry 'ADVERTISED_MAX_IMAGE_BYTES': the provided value is not of type 'function or ExportedHandler'`. Vitest, typecheck and dry-run were green because none of them boots the bundle | **Fixed.** The constant is in `worker/src/multipart.ts`. The bundle exports only `Budget` and `default`. workerd starts it. The new `test/entry-exports.test.ts` fails when a const or object export is re-added (§3.3) |
| M-1 | Medium | The advertised value ignored `MAX_UPLOAD_BYTES`. A cap below 589824 would advertise more than the Worker enforces | **Fixed.** `/v1/info` reports `Math.min(ADVERTISED_MAX_IMAGE_BYTES, maxImageBytes(maxBytes))`. A committed test covers cap 589823. Verified in workerd at five caps (§3.2). The mutation is killed |
| L-1 | Low | No committed test of the image-part cap at the default caps | **Fixed.** 983040 gives 200 with 1 AI call, and 983041 gives 413 `image exceeds 983040 bytes` with 0 AI calls. The off-by-one mutation is killed |
| I-1 | Info | No real camera HEIC in the photo set (§4) | **Open**, non-blocking. Add a real iPhone HEIC when available |
| I-2 | Info | No CI step boots the built Worker | **Fixed.** `worker/scripts/local-info-check.sh` plus a CI step with a 3-minute timeout. It fails the job on a bad export list, on a Worker that won't start, and on a wrong value. No secrets, no Cloudflare network (§3.4) |
| N-1 | Low (new) | `local-info-check.sh` hard-codes 524288, and `budget.test.ts` asserts it. The documented 786432 fallback is therefore a three-place change (constant, test, script), not "one place" | Open, non-blocking. If 786432 is ever needed, change all three. Or let the script read the expected value from `multipart.ts` |
| N-2 | Info (new, pre-existing) | `MAX_UPLOAD_BYTES` ≤ 65536 advertises `max_image_bytes` 0. That breaks `openapi.yaml` `minimum: 1`, and Apotheke rejects the whole `/v1/info`. main did the same (`maxImageBytes()` floor 0). The config is degenerate anyway, since no image fits | Accepted, no action needed for #27 |

## 1. Test plan

| ID | Area | Check |
|---|---|---|
| T1 | Provenance | Codeload tarballs, `git get-tar-commit-id`, clean `diff -ruN` against main and against the previous head |
| T2 | Single source | `ADVERTISED_MAX_IMAGE_BYTES` = 524288 is defined once and is the only constant feeding `/v1/info` `max_image_bytes` (since round 2, through `min(…, maxImageBytes(MAX_UPLOAD_BYTES))`) |
| T3 | Enforcement | 1048576 body cap gives 413. 983040 image-part cap: 983040 accepted, 983041 gives 413 with no AI call. `suggest()` unchanged from main. Mutation tests |
| T4 | cpuTime | Source and built-bundle diff against main. No new per-request work |
| T5 | Gates | `npm ci`, `npm run typecheck`, `npx vitest run`, `wrangler deploy --dry-run`, `ruff check`, `ruff format --check`, `pytest`, `deploy/jetson/tests/run-tests.sh` |
| T6 | Runtime boot | Dry-run bundle served by local workerd: start, `/healthz`, `/v1/info`, export list. Round 2 adds `MAX_UPLOAD_BYTES` 1048576, 589824, 589823, 524288 and 131072 |
| T7 | Export guard | Mutation: re-add a const export, and separately an object export, to `index.ts`. The test and the CI script must fail |
| T8 | CI step I-2 | Run `local-info-check.sh` offline. Mutants: bad export (static check), Worker that won't start (runtime path, static check removed), wrong value. Timeout, cleanup, secrets, network |
| T9 | Docs | Consistent numbers in all changed docs |
| T10 | Secrets and hostnames | gitleaks on added lines and trees. Grep for tokens, ids, IPs and hosts. Production-hostname counts per file, main vs head |
| T11 | Photo set | Apotheke v2.5.0 analysis-copy builder against a stub `/v1/info` at 524288 (§4) |
| T12 | p50 vs 10 ms | Reuse of the #26 live re-measure, plus the post-deploy plan (§5) |

## 2. Round 1 at `d95f0a5`: summary

**Diff:** 12 files.
- `worker/src/index.ts` added the exported constant, and `info()` returned it.
- `neurons.ts` changed a comment.
- New vitest cases: 524288–1048576 body accepted, 1048577 gives 413.
- `budget.test.ts` asserted 524288.
- The Python doc test expects 524288.
- Wording updated in README, CHANGELOG, `docs/api-v1.md`, `docs/cloudflare-workers-deploy.md`, `openapi.yaml`, `.env.example` and `worker/.dev.vars.example`.

**Results:**
- Single source: pass. Enforcement unchanged: pass. No new per-request work.
- Gates on main → head: vitest 47 → 49, typecheck pass, dry-run pass, ruff pass, pytest 560 → 560, deploy scripts 282.
- gitleaks clean on added lines. Hostname counts unchanged.
- **Runtime boot: FAIL (B-1).** The main bundle started. The `d95f0a5` bundle was refused by workerd. Removing only the extra export name made it start.
- A deploy of `d95f0a5` was expected to fail Cloudflare's upload validation, which uses the same runtime check. Not tried live.

## 3. Round 2 at `167671f`: recheck

### 3.1 Provenance and diff

- `git get-tar-commit-id` gives `167671f970f99df0feba447f733b20e94949c89d`.
- Delta from `d95f0a5`: 13 files.
  - `worker/src/index.ts`: import from `./multipart`; `info()` back to destructuring `maxBytes`; `Math.min(…)`.
  - `worker/src/multipart.ts`: the constant and its doc comment.
  - New `worker/test/entry-exports.test.ts`.
  - New tests in `budget.test.ts` (M-1) and `limits.test.ts` (L-1).
  - New `worker/scripts/local-info-check.sh`.
  - `.github/workflows/ci.yml`: one new step in the `worker` job.
  - Doc wording updated for `min(524288, MAX_UPLOAD_BYTES − 65536)`.
- Against main, `worker/src` changes only in `index.ts`, `multipart.ts` and `neurons.ts` (comment).

### 3.2 T2, T3, T6 single source, enforcement, runtime boot: pass

- **Single source.** `rg ADVERTISED_MAX_IMAGE_BYTES worker/src` finds one definition (`multipart.ts`) and one use (`info()` in `index.ts`, inside `Math.min`).
- **`suggest()` is byte-identical to main.** That covers the whole function, 130 lines: `imageCap = maxImageBytes(maxBytes)`, `readLimitedBody`, `readSuggestParts`. `http.ts`, `context.ts`, `log.ts` and `budget.ts` are also identical.

The local workerd run of the round 2 dry-run bundle used text bindings and the `Budget` SQLite Durable Object, with no AI binding:

| `MAX_UPLOAD_BYTES` | Start | `/healthz` | `/v1/info` `max_image_bytes` | Expected `min(524288, cap − 65536)` |
|---|---|---|---|---|
| 1048576 (default) | ok | 200 | **524288** | 524288 |
| 589824 | ok | 200 | 524288 | 524288 |
| 589823 | ok | 200 | **524287** | 524287 |
| 524288 | ok | 200 | **458752** | 458752 |
| 131072 | ok | 200 | 65536 | 65536 |

- No `Uncaught` errors in any runtime log.
- Bundle exports: `Budget`, `default` (`index_default as default`).

**Mutation tests** (vitest, 52 cases on the head):

| Mutant | Killed by |
|---|---|
| `export const QA_MUTANT = 524288` appended to `index.ts` | `entry-exports.test.ts` |
| `export const QA_LIMITS = { max: 524288 }` appended to `index.ts` | `entry-exports.test.ts` |
| `info()` back to `max_image_bytes: ADVERTISED_MAX_IMAGE_BYTES` (M-1 reverted) | `budget.test.ts` "advertises the enforced image cap when MAX_UPLOAD_BYTES is below 589824" |
| `imageCap = maxImageBytes(maxBytes) + 1` in `suggest()` | 4 tests, including the new 983040/983041 case |
| Constant 524288 → 786432 | `budget.test.ts` "reports the Python info fields…" |

### 3.3 T4 cpuTime impact: none on `/v1/suggest`

- **Bundle.** The built bundle differs from main in exactly three lines, all outside `suggest()`:
  - `var ADVERTISED_MAX_IMAGE_BYTES = 524288;`
  - one removed line in `info()` (`const imageCap = maxImageBytes(maxBytes);`)
  - `max_image_bytes: Math.min(ADVERTISED_MAX_IMAGE_BYTES, maxImageBytes(maxBytes))`

  Size went from 52,798 B to 52,848 B (+50 B).
- **`/v1/suggest`:** unchanged code, so unchanged per-request CPU.
- **`/v1/info`:** the same subtraction as main, plus one `Math.min`, which is negligible.

The only cpuTime effect is indirect. Apotheke now steps its analysis copy down to fit 524288 (§4–§5).

### 3.4 T8 the I-2 CI step: pass

`worker` job step "Local runtime info check" (`timeout-minutes: 3`, `bash scripts/local-info-check.sh`). The script:
1. Unsets `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_KEY`, and sets `WRANGLER_SEND_METRICS=false`.
2. Runs `wrangler deploy --dry-run` into a temp dir and checks the bundle export list is exactly `Budget` and `default`.
3. Starts `wrangler dev --local` on 127.0.0.1:8787 in its own process group, with a throwaway `AUTOFILL_TOKEN` var.
4. Polls `/healthz` up to 30 times, stopping early if the process dies.
5. Checks `/healthz` returns `"status":"ok"` and `/v1/info` returns `max_image_bytes` 524288.
6. Kills the process group and removes the temp dir on exit.

The workflow has `permissions: contents: read`, and the `worker` job references no `secrets.*`.

| Run (no-network namespace, no Cloudflare variables) | Exit | Time | Output |
|---|---|---|---|
| Head as is | 0 | 3 s | `Budget`, `default`, "local /healthz 200; /v1/info max_image_bytes 524288" |
| Mutant: const export in `index.ts` | **1** | 1 s | "unexpected bundle exports" (static check) |
| Mutant: const export, static check removed | **1** | 4 s | wrangler shows workerd's `Uncaught TypeError … 'QA_MUTANT'`, then "local worker did not answer /healthz within 30s". 0 leftover processes |
| Mutant: constant 786432 | **1** | 3 s | `max_image_bytes 786432` |

- **Timeout.** Each probe has `--max-time 2`, and there are at most 30 probes with 1 s sleeps, so the worst case is about 90 s. That is inside the 3-minute step timeout.
- **Network.** Everything passed with no route out, so the step needs no Cloudflare network access.
- **Ports.** It uses fixed ports 8787 and 9239, which is fine on hosted runners.

### 3.5 T5 gates (round 2, offline)

| Gate | main `d0e959a` | `d95f0a5` | `167671f` |
|---|---|---|---|
| `npm ci` | ok | ok | ok |
| `npm run typecheck` | pass | pass | pass |
| `npx vitest run` | 7 files, 47 passed | 7 files, 49 passed | **8 files, 52 passed** |
| `wrangler deploy --dry-run` | pass | pass | pass |
| `local-info-check.sh` | – | – | **pass** |
| `ruff check .` / `ruff format --check .` | pass / 56 formatted | pass / 56 formatted | **pass / 56 formatted** |
| `pytest -q` | 560 passed | 560 passed | **560 passed** |
| `deploy/jetson/tests/run-tests.sh` | – | 282 passed | **282 passed, 0 failed** |

A first harness run started as a background job showed 8 failures, all in the SIGINT cases. A non-interactive shell ignores SIGINT in background jobs, so those cases can't pass that way. The foreground re-run is 282/282, and `deploy/` is byte-identical to main.

### 3.6 T9 docs and T10 secrets: pass

- **Docs.** README, CHANGELOG, `docs/api-v1.md`, `docs/cloudflare-workers-deploy.md`, `openapi.yaml`, `.env.example` and `worker/.dev.vars.example` now say `min(524288, MAX_UPLOAD_BYTES − 65536)`, 524288 at the default, 983040 enforced, 1048576 body cap, constant in `multipart.ts`. They are consistent.
- **`.dev.vars.example`.** It now says `wrangler dev` without `--local` uses the real AI binding, and `--local` keeps remote bindings off.
- **gitleaks.**
  - Added lines in the round 2 delta (213) and against main (281): no leaks.
  - Full-tree `--no-git` scan: the same single pre-existing placeholder hit in `deploy/jetson/service.env.example`. That file is unchanged since main. CI's history scan is green.
- **Added lines grep.** No tokens, account id, e-mail addresses or Jetson host.
  - IPs: only `127.0.0.1` in the local check script.
  - "Bearer": only the test fixture and the script's throwaway local token.
  - The `CLOUDFLARE_*` names appear only in the script's `unset` line.
- **Production hostname.** Per-file counts are identical on main and `167671f`: `CHANGELOG.md` 2, `README.md` 1, `docs/cloudflare-workers-deploy.md` 6, `docs/qa/feature11-worker-stub-qa.md` 1, `tests/test_worker_deploy.py` 1, `wrangler.toml` 1.

## 4. Photo set at 524288 (offline, 0 Neurons)

**Method:**
- Apotheke v2.5.0 (`87850e3`) `src/autofill.php`, unchanged.
- It fetched a stub `/v1/info` advertising 524288 and cached it.
- `autofill_open_analysis()` made the decision: it compares the **whole multipart body** with the cached limit.
- Two sizes: 1024 px (no `image_sizes`, as today) and 1600 px (`image_sizes` listing 1600).
- Engines: GD only (Imagick disabled), and GD + Imagick 7.1.1 with libheif.

**Photo set:**
- 243 unique files, each labelled.
- **182 realistic:** 65 originals (bench sets, brand candidates, real camera JPEGs up to 5600×4200) plus 117 downscaled copies.
- 61 synthetic: the #28 noise and plasma fixtures with fake EXIF, flat colours, drawings, collages, garbage.

**Results:**
- **0 of 182 realistic photos came back `too_large`, on either engine, at 1024 px or at 1600 px. All fit at q80, the first quality step.**
- Largest realistic body: **226,688 B at 1024 px** and **394,722 B at 1600 px**.
- Every `too_large` came from synthetic noise: `noise.jpg` and `stepdown.jpg` at 1600 px, and with Imagick forced, also at 1024.
- By the PM's rule these do not count. **The 786432 fallback is not triggered.**
- At the default body cap, round 2 still advertises exactly 524288, so these results stand unchanged.

**GD-only installs:**
- They return `unsupported` for HEIC by design (v2.5.0). That is not a size result.

**Gap (I-1): no real camera HEIC exists in the QA set.**
- HEIC files were made from real photos with ImageMagick. All fit at q80, largest 394,722 B at 1600 px.
- HEIC is always encoded by Imagick. With Imagick, high-noise content comes out about 2–2.5× larger than with GD (for example 982,077 vs 396,906 B for `stepdown.jpg` at 1024 px).
- So a real high-ISO 12 MP iPhone HEIC is the one case not yet covered. Add one to the set when available.

## 5. p50 vs the 10 ms soft target

These are the live figures from the #26 re-measure, 2026-10-20 10:21–10:23 EEST, on Worker version `498ba38c` at 100%. Images were built with Apotheke v2.5.0's own code (GD, q80).

| Call | Body bytes | cpuTime | Neurons |
|---|---|---|---|
| suggest, `sony_cd_player_label.jpg` @1024 (real photo) | 91,330 | **10.1 ms** | 6.164 |
| suggest, `stepdown.jpg` @1024 (synthetic noise) | 396,906 | **15.1 ms** | 5.445 |
| suggest, `stepdown.jpg` @1600 path (synthetic noise) | 894,982 | **30.8 ms** | 5.582 |
| suggest 413 probe | 1,048,577 | 1.2 ms | 0 |

- **p50 of the three suggest calls is 15.1 ms, so the 10 ms soft target is not met.**
- The real-photo call was 10.1 ms, just over the target.
- The worst case is now bounded by the advertised 524288. Realistic analysis copies are ≤ 226,688 B at 1024 px.
- **M-3 is accepted with monitoring until 2026-11-03.** The daily watch flags p50 above 15 ms or p99 above 50 ms, and any 1102.
- PR #27 adds no per-request CPU (§3.3), so it cannot move p50 up.
- **Post-deploy:** a live check of 5 calls or fewer on the new version will add a fresh p50 against 10 ms and the Neurons used.

## 6. Gate

- **QA: PASS at `167671f970f99df0feba447f733b20e94949c89d`.** `d95f0a5` was FAIL (B-1), now fixed.
- CI run 39391653661 on `167671f`: completed, success, 5/5 jobs.
- The PM's secret re-scan was done on `d95f0a5`. The round 2 delta is clean in QA's gitleaks, but the PM's re-scan on `167671f` is still the PM's call.
- Merging deploys the Worker. After the deploy: the post-deploy live check (5 calls or fewer), reporting p50 against 10 ms and the Neurons used, plus `/v1/info` `max_image_bytes` 524288 on the live version.
- Non-blocking items still open: I-1 (real HEIC), N-1 (three places for the value).
