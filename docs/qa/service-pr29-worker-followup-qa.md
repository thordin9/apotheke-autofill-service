# QA: service #29, Worker follow-up (deploy guard, constant read, Jetson N-4)

- **PR:** apotheke-autofill-service #29, `cursor/worker-deploy-guard` into `main` (draft).
- **Head:** `b821efbd49643042a4247baa572c6d60980a278b` (re-checked before writing this doc).
- **Base:** `c69e9c3bf80916ab0d71b8a690e551a6e5a1ea12` (#27 merge).
- **Date:** 2026-10-20, 12:00–12:20 EEST.
- **Scope:** static and local-runtime QA only. No live Worker calls, no Cloudflare credentials read, 0 Neurons.
- **Verdict: PASS.** No H or M findings. One L (a documented `MAX_UPLOAD_BYTES` change would now also fail the deploy guard), three N, five I.

## Plan

1. Commits and per-commit files against `main`. The QA doc commit holds only `docs/qa/pr27-live-check-qa.md`, byte-identical to the QA copy (4543 B, sha256 `3c575d02…6db1`).
2. Deploy workflow. The guard runs before `wrangler deploy`, fails the job when the check fails, has no secrets in its env, and can't be skipped by `if:` or `continue-on-error`. Cloudflare credentials stay scoped to the Deploy step. Triggers, `environment: production`, permissions and concurrency are unchanged. actionlint.
3. Constant read. Mutation tests on a copy of `worker/`, each run of `local-info-check.sh` offline (loopback-only network namespace, Cloudflare env vars unset): 786432, changed formats, removed or renamed, Worker reporting a different value, the B-1 export regression, plus edge cases. Check for leftover processes and the worst-case runtime against the step's 3-minute timeout.
4. `worker/src` diff is comment-only, and the built bundle is byte-identical to `main`.
5. N-4. `jetson.yml` is dispatch-only, the removed step is gone, the README text matches, and `tests/test_jetson_legacy.py` passes.
6. vitest, typecheck, wrangler dry run, pytest, ruff, shellcheck, deploy-script tests (foreground), relative-link check, gitleaks on the delta, and a scan of added lines for hostnames, IPs and tokens. Production hostname counts per file are compared with `main`.
7. CI on the head, plus the Deploy Worker effect of merging.

Method: codeload tarballs for `c69e9c3`, `f1ab6ae` and `b821efb`, each verified with `git get-tar-commit-id`. Node 22.23.3 and wrangler 4.151.2 from the lockfile. Python 3.13 venv from `requirements-dev.txt`.

## Results

### 1. Commits and files: PASS

| Commit | Message | Files |
|---|---|---|
| `f1ab6ae` | ci: run the local runtime check before wrangler deploy | `deploy-worker.yml`, `jetson.yml`, `CHANGELOG.md`, `README.md`, `docs/api-v1.md`, `docs/cloudflare-workers-deploy.md`, `tests/test_jetson_legacy.py`, `tests/test_worker_deploy.py`, `worker/scripts/local-info-check.sh`, `worker/src/multipart.ts`, `worker/test/budget.test.ts` (11) |
| `b821efb` | docs(qa): add the #27 live check addendum | `docs/qa/pr27-live-check-qa.md` only |

The total is 12 files, +114/−24, which matches the PR. The QA doc at `b821efb` is 4543 B with sha256 `3c575d027a45fbf50a58a4b0fe0c9fa25279b6209e46e1d5ea06f0eb4a636db1`. `cmp` against the QA copy shows it's identical.

### 2. Deploy workflow: PASS

- The step order is: checkout, setup-node, Install, Test, Check Wrangler version, **Local runtime info check**, Deploy, Smoke.
- The guard step has only `name`, `timeout-minutes: 3` and `run: bash scripts/local-info-check.sh`. It has no `if:`, no `continue-on-error` and no `env`. Deploy has the default `success()` condition, so a failed or timed-out guard skips Deploy and Smoke.
- No secrets reach the guard. There is no workflow-level or job-level `env`. `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` are only in the Deploy step's `env`, and the script also unsets both.
- Unchanged against `main`, by YAML compare: triggers (push to `main` on `worker/**` and the workflow file, plus `workflow_dispatch`), job `if:`, `environment: production`, `runs-on`, `defaults`, `permissions: contents: read`, concurrency, and every other step.
- The guard runs the same command as the CI `worker` job.
- actionlint 1.7.7 is clean on `deploy-worker.yml` and `jetson.yml`. Its only output is the existing `runner-label` warning for `[self-hosted, jetson]` in `jetson-hardware.yml`, which `main` has too.

### 3. Constant read and mutation tests: PASS

The script reads the value with `sed -nE 's/^export const ADVERTISED_MAX_IMAGE_BYTES = ([0-9]+);.*/\1/p'` and then requires `^[0-9]+$`. Every mutant was run offline on a copy of the head's `worker/`.

| Mutant | Exit | Output | Time |
|---|---|---|---|
| M0 unchanged | 0 | exports Budget, default; 524288 | 2.4 s |
| M1 constant 786432 | 0 | `/v1/info max_image_bytes 786432` | 2.4 s |
| M2 `524_288` | 1 | could not read ADVERTISED_MAX_IMAGE_BYTES | 9 ms |
| M3 `512 * 1024` | 1 | could not read | 9 ms |
| M3b `: number = 524288` | 1 | could not read | 10 ms |
| M3c `524288 as const` | 1 | could not read | 10 ms |
| M3d `0x80000` | 1 | could not read | 9 ms |
| M3e CRLF line endings | 0 | 524288, read correctly | 2.4 s |
| M4 renamed everywhere | 1 | could not read | 10 ms |
| M4b moved to another file and re-exported | 1 | could not read | 10 ms |
| M5 Worker reports 600000 | 1 | `max_image_bytes 600000, expected 524288` | 2.3 s |
| M6 B-1 (`export const QA_MUTANT` in index.ts) | 1 | `unexpected bundle exports` | 1.0 s |
| M7 constant 1048576 (above the image cap) | 1 | `max_image_bytes 983040, expected 1048576` | 2.3 s |
| M8 `MAX_UPLOAD_BYTES = "524288"` in wrangler.toml | 1 | `max_image_bytes 458752, expected 524288` (see L-1) | 2.3 s |
| M9 commented duplicate `// export const … = 999;` | 0 | 524288 (comment ignored) | 2.5 s |
| M10 second matching line inside a block comment | 1 | could not read (two values) | 9 ms |
| M11 `/healthz` hangs | 1 | runtime cancels each request; not ready | 31.8 s |
| M12 `/healthz` returns 503 | 1 | did not answer /healthz within 30s | 31.5 s |
| M13 port 8787 already taken | 1 | address in use; wrangler exits; loop stops early | 4 s |

- No mutant passes silently. Every format change, removal or rename fails within 10 ms, before any build, and never falls back to a default.
- **Leftover processes:** no `workerd` or `wrangler dev` processes were left after any run (checked 0–4 s after exit).
- **Worst-case runtime:** the slowest run measured was 31.8 s. The bound is about 110 s: the dry-run build, 30 × (2 s curl + 1 s sleep), then two 5 s curls. Both are within the 3-minute step timeout, and an overrun fails the step, which skips Deploy.

### 4. worker/src and bundle: PASS

- `diff -r worker/src` lists only `multipart.ts`, and its change is the JSDoc above the constant. `ADVERTISED_MAX_IMAGE_BYTES = 524288` is unchanged.
- The dry-run bundle `index.js` is byte-identical to `main`: sha256 `ede7562f1cbdfb92432fa632e91d39cafa03660963e357bfb2776b01a4068989` on both. The dry-run output (51.61 KiB, same bindings) is identical too, so cpuTime is unaffected.

### 5. Jetson N-4: PASS

- `jetson.yml` triggers only on `workflow_dispatch`. The "Hosted runners do not build this image" step and the three `if: github.event_name == 'workflow_dispatch'` guards are gone. Job-level `continue-on-error: true` and `timeout-minutes: 120` are kept.
- README lines 187–190 now say the workflow only runs by hand, attempts the arm64 build and is `continue-on-error`. Outside the QA docs, the repo has no remaining "Path-triggered" or file-change trigger text.
- `tests/test_jetson_legacy.py` and `tests/test_worker_deploy.py` pass (11 tests). The new asserts cover both the README text and `github.event_name` in `jetson.yml`.

### 6. Local suites and scans: PASS

| Check | main `c69e9c3` | head `b821efb` |
|---|---|---|
| vitest | 52 passed | 52 passed |
| `npm run typecheck` | 0 | 0 |
| wrangler dry run | OK | OK, identical output |
| `local-info-check.sh` (offline) | pass, 524288 | pass, 524288 |
| pytest (`BACKEND=echo`, offline) | 564 passed | **566 passed** (+2 new tests) |
| ruff check / format --check | — | clean / 61 files formatted |
| shellcheck (CI set, plus `local-info-check.sh`) | — | clean |
| deploy-script tests (foreground, 214 s) | — | passed=282 failed=0 |
| relative links (129) and anchors | 0 broken | 0 broken |

- **gitleaks 8.30.1 on the delta:** no leaks found in the 2 commits (`--log-opts HEAD~2..HEAD`, repo `.gitleaks.toml`, redacted). The whole-tree scan reports the same 3 findings as the base snapshot, so nothing new.
- **Added lines:** no hostnames, IPs or tokens. The only matches are a commit sha, the #27 Worker version ID and the placeholder `https://autofill.example`, all three in the live-check doc.
- **Production hostname counts:** identical per file between `main` and head (10 files, 19 matching lines).

### 7. CI and deploy effect: PASS

- CI run 39397456681 on `b821efb` is 5/5 green: lint-test, worker, gitleaks, deploy-scripts and image, completed by 12:03:47 EEST.
- Each job has 2 platform annotations: the Node 20 deprecation warning for `checkout@v4` and `setup-node@v4`, and the ubuntu-latest migration notice.
- Deploy Worker hasn't run for this branch. Its last run is 39395496438 on `c69e9c3`.
- Merging touches `deploy-worker.yml`, so it re-runs Deploy Worker. That run first runs the new guard. Because the bundle is byte-identical, this gives a new version ID on the same code. Smoke makes 2 unauthenticated requests, which cost 0 Neurons.

## Findings

- **L-1: The guard expects the constant even when `MAX_UPLOAD_BYTES` is lowered.**
  - **Problem:** The script compares `/v1/info` with the raw constant. The Worker reports `min(constant, MAX_UPLOAD_BYTES − 65536)`. `wrangler.toml` says its vars exist "so a deploy can lower the cap without a code change", and the docs describe lowering `MAX_UPLOAD_BYTES` below 589824. In M8, `MAX_UPLOAD_BYTES = "524288"` fails the check: the Worker reported 458752 against an expected 524288.
  - **Impact:** CI already failed this way with the old hard-coded 524288. The guard now also blocks Deploy Worker. It fails loudly and in the safe direction, but it blocks a documented operation.
  - **Fix:** Compute `expected = min(constant, MAX_UPLOAD_BYTES − 65536)` from `wrangler.toml` `[vars]`, or say in the docs and the script comment that lowering the cap means changing the check too. This can go in a follow-up and doesn't block the merge.
- **N-1: The new test doesn't pin the guard's skip conditions.**
  - `test_deploy_runs_local_runtime_check_before_deploy` checks the order, the `run` command and that the step has no `env`. It would still pass if someone added `if:` or `continue-on-error: true` to the step, or `env`/`continue-on-error` to the job.
  - Suggested asserts: `set(check) == {"name", "timeout-minutes", "run"}`, plus no `env` and no `continue-on-error` on the job.
- **N-2: The constant must stay a plain integer literal.**
  - The parse accepts only `export const ADVERTISED_MAX_IMAGE_BYTES = <digits>;` at column 0. Any other form fails with "could not read" (M2–M4b, M10). A value above 983040 fails on the comparison (M7). Both are safe failures.
  - The comment in `multipart.ts` could add "keep it a plain integer literal, at most 983040", so a later change doesn't surprise anyone.
- **N-3: README wording.**
  - Line 188 says the workflow "is legacy and disabled. It only runs by hand". While it's disabled in the Actions settings, it can't run by hand either, so "if re-enabled, it only runs by hand" would be accurate.
  - The paragraph also has a short wrapped line ("…is `continue-on-error`, so a").
- **I-1:** The bundle is byte-identical to `main`, so cpuTime is unaffected and the #27 cpuTime watch item is unchanged.
- **I-2:** Merging re-runs Deploy Worker on unchanged code. DevOps's post-deploy version check covers it. The Budget Durable Object state carries over.
- **I-3:** When wrangler exits early, for example because the port is taken (M13, 4 s), the script still prints "did not answer /healthz within 30s". The message is misleading but predates this PR, and the exit code is correct.
- **I-4:** `jetson.yml` still uses tag-pinned actions (`checkout@v4`, `setup-qemu@v3`, `setup-buildx@v3`, `build-push@v6`), unlike the SHA pins in `deploy-worker.yml`. This predates the PR, and the workflow is dispatch-only and disabled.
- **I-5:** The actionlint runner-label warning on `jetson-hardware.yml`, the CI Node 20 annotations and the 3 whole-tree gitleaks findings are all the same on `main`. None of them come from this PR.

## Verdict

**PASS at `b821efbd49643042a4247baa572c6d60980a278b`.**

- The guard can't be skipped, sees no credentials and fails the deploy on every regression tested, including B-1.
- The constant read fails loudly rather than defaulting.
- The Worker bundle is byte-identical.
- N-4 is done, and the QA doc commit matches byte for byte.

QA has no objection to marking it ready and merging pinned to `b821efb` once the PM's re-scan is clean. L-1 and N-1 to N-3 can go to a later follow-up.
