# QA recheck: service #29, Worker follow-up (L-1, N-1 to N-3)

- **PR:** apotheke-autofill-service #29, `cursor/worker-deploy-guard` into `main` (draft, mergeable `clean`).
- **Head:** `d9b40b6b3c0951ef1242cfafb6e135e2dc35800f` (re-checked before writing this doc).
- **Previous QA PASS:** `b821efbd49643042a4247baa572c6d60980a278b` (`docs/qa/service-pr29-worker-followup-qa.md`).
- **Delta:** `68a8d3c` (QA doc only), `d9b40b6` (fixes).
- **Date:** 2026-10-20, 12:18–12:30 EEST.
- **Scope:** static and local-runtime QA of the delta only. No live Worker calls, no Cloudflare credentials read, 0 Neurons.
- **Verdict: PASS.** L-1 and N-1 to N-3 are fixed. One new N (a test gap on the Deploy step's condition, not a regression) and five I. Nothing blocks the merge.

## Plan

1. `compare_commits b821efb...d9b40b6` and per-commit files. `worker/src` has comment changes only, and the dry-run `index.js` is byte-identical to `b821efb` and `main`.
2. The QA doc at `d9b40b6` is byte-identical to the QA copy (sha256 `ce95bc21…cb51`). `68a8d3c` touches only that file.
3. L-1. Read `parseMaxUpload`, `maxImageBytes` and the `/v1/info` code, then compare with the script. Run `local-info-check.sh` offline on copies of the head's `worker/` (loopback-only network namespace, Cloudflare env unset). First reproduce DevOps's six rows, then add edge cases: low caps, non-numeric, zero, negative, above 1 MiB, unsafe integers, other TOML spellings, a changed headroom, and a Worker mutant that drops the `min`.
4. N-1. Mutate `deploy-worker.yml` on copies (add `if: false`, `continue-on-error`, `env`, other keys; remove the step; move it after Deploy; `|| true`; rename it; drop the timeout) and confirm `tests/test_worker_deploy.py` fails for each. Confirm the guard still runs before `wrangler deploy`.
5. N-2 and N-3 wording.
6. vitest, typecheck, the targeted pytest, ruff, shellcheck, gitleaks on the delta, a scan of added lines for hosts, IPs and tokens, leftover processes, and CI on the head.

Method: codeload tarballs for `b821efb` and `d9b40b6`, each verified with `git get-tar-commit-id`, and the raw file from raw.githubusercontent. Node 22.23.3, wrangler 4.151.2 from the lockfile (the lockfile is unchanged), and the Python 3.13 venv from `requirements-dev.txt`.

## Results

### 1. Delta and bundle: PASS

| Commit | Message | Files |
|---|---|---|
| `68a8d3c` (parent `b821efb`) | docs(qa): add QA doc for the Worker deploy guard PR | `docs/qa/service-pr29-worker-followup-qa.md` (added) only |
| `d9b40b6` | ci: compute the expected max_image_bytes from the body cap too | `README.md` +2/−2, `tests/test_worker_deploy.py` +4/−1, `worker/scripts/local-info-check.sh` +23/−7, `worker/src/multipart.ts` +2/−1 |

- The compare shows 2 commits ahead and 5 files. A local `diff -r` of the two tarballs shows the same 5 files.
- The PR now has 4 commits, 13 files and +272/−24.
- The only `worker/src` change is the `multipart.ts` doc comment. No code changed.
- The dry-run `index.js` has sha256 `ede7562f1cbdfb92432fa632e91d39cafa03660963e357bfb2776b01a4068989` at `d9b40b6`, at `b821efb` and at `main` (`c69e9c3`). Only `index.js.map` differs, because the comment is in its `sourcesContent` and shifts its line mappings.

### 2. Doc hash: PASS

The raw file at `d9b40b6` is 12872 B with sha256 `ce95bc2187d9bbfb508f08acadd82f137cbdb472bd2480b3ef8b0777ad00cb51`. The tarball copy is identical, and `cmp` against the QA copy shows no difference.

### 3. L-1, expected value: PASS

The Worker computes the value like this:

- `parseMaxUpload(raw)`: undefined, or empty after `trim()`, gives 1048576. A value that isn't `^\d+$` after `trim()` gives 1048576. So does a value that isn't a safe integer or is ≤ 0. Anything else is used as is.
- `maxImageBytes(cap) = max(0, cap − MULTIPART_HEADROOM)`.
- `/v1/info` reports `min(ADVERTISED_MAX_IMAGE_BYTES, maxImageBytes(cap))`.

The script follows the same steps:

- It reads both constants with the strict literal regex and fails if either is missing.
- It takes the first `MAX_UPLOAD_BYTES = "…"` line in `wrangler.toml`.
- A value that isn't `^[0-9]+$`, or is 0, becomes 1048576.
- It then computes `max(0, cap − headroom)` and the `min`, using base-10 arithmetic so leading zeros work.

The local runtime uses the same `wrangler.toml` as `npx wrangler deploy`. Neither the guard nor Deploy passes `--var MAX_UPLOAD_BYTES` or `--env`.

DevOps's table, reproduced (exit code, then the script's output):

| Mutant | Exit | Reported / message | Time |
|---|---|---|---|
| A default (`"1048576"`, 524288) | 0 | 524288 | 2.4 s |
| B cap `"589823"` | 0 | 524287 | 2.4 s |
| C cap `"524288"` | 0 | 458752 | 2.4 s |
| D cap `""` | 0 | 524288 | 2.4 s |
| E constant 786432 | 0 | 786432 | 2.4 s |
| F constant `512 * 1024` | 1 | could not read ADVERTISED_MAX_IMAGE_BYTES and MULTIPART_HEADROOM | <0.1 s |

Edge cases. "Worker" is the value `/v1/info` returned. "Agree" means the script's expected value equals what the Worker reports.

| Mutant | Exit | Result | Agree |
|---|---|---|---|
| G cap `"65536"` | 0 | 0 | yes |
| G2 cap `"65537"` | 0 | 1 | yes |
| H cap `"1"` | 0 | 0 | yes |
| I cap `"abc"` | 0 | 524288 (fallback) | yes |
| I2 cap `"1e6"` | 0 | 524288 (fallback) | yes |
| J cap `"2000000"` | 0 | 524288 | yes |
| K cap `"0"`, K2 `"000"` | 0 | 524288 (fallback) | yes |
| L cap `"-5"` | 0 | 524288 (fallback) | yes |
| O cap `"99999999999999999999"`, O2 `"9007199254740993"` | 0 | 524288. The Worker falls back to 1048576. In bash the value wraps or stays large, so the `min` still gives 524288 by coincidence. | yes |
| O3 cap `"18446744073709551617"` (2^64+1) | 1 | Worker 524288, script expected 0 (bash wraps to 1) | no, fails closed (I-6) |
| P cap `"0500000"`, P2 `"0589823"` | 0 | 434464, 524287 | yes |
| Q constant 1048576 | 0 | 983040 (the Worker clamps) | yes (I-10) |
| T headroom 131072 + cap `"589823"` | 0 | 458751 | yes |
| U Worker mutant: `/v1/info` drops the `min`, cap `"524288"` | 1 | `max_image_bytes 524288, expected 458752` | the guard catches it |
| V `MAX_UPLOAD_BYTES` line removed | 0 | 524288 (fallback) | yes |
| M cap `" 500000"` (space inside the quotes) | 1 | Worker 434464 (it trims), script 524288 | no, fails closed (I-6) |
| W `'500000'`, X `MAX_UPLOAD_BYTES="500000"` | 1 | Worker 434464, script 524288 | no, fails closed (I-6) |
| N unquoted TOML integer `500000` | 22 | The Worker answers `/v1/info` with 503 `backend_unavailable`; `curl -f` fails | fails closed (I-6) |
| Y `[env.staging.vars]` cap above `[vars]` | 1 | Worker 524288, script 434464 (it takes the first match); wrangler also warns about multiple environments | no, fails closed (I-6) |
| Z CRLF `wrangler.toml`, cap `"500000"` | 0 | 434464 | yes |
| S local `.dev.vars` with `MAX_UPLOAD_BYTES=500000` | 1 | Worker 434464, script 524288 | local only (I-7) |

- Every disagreement fails the guard loudly with both numbers, or with a curl failure. No case passed with a value the Worker doesn't report.
- No `wrangler` or `workerd` processes were left after the runs.
- The worst case is about 3.2 s against the step's 3-minute timeout.

### 4. N-1, guard step keys: PASS

The test now asserts `set(check) == {"name", "timeout-minutes", "run"}` and keeps the `run` and ordering asserts. `tests/test_worker_deploy.py` has 7 tests. All pass on the head.

| Workflow mutant | Result |
|---|---|
| W0 unchanged | 7 passed |
| W1 guard `if: false` | fails (`test_deploy_runs_local_runtime_check_before_deploy`) |
| W2 guard `continue-on-error: true` | fails |
| W3 guard `env:` | fails |
| W4 guard `working-directory:` | fails |
| W5 guard step removed | fails |
| W6 guard moved after Deploy | fails |
| W7 `run: … \|\| true` | fails |
| W8 `timeout-minutes` removed | fails |
| W10 guard renamed | fails |
| W11 `shell: bash {0}` | fails |
| W12 `id: guard` (harmless) | fails. This is intended strictness (I-9). |
| W15 a no-op step named Deploy inserted before the real one | fails (2 other tests) |
| W13 job-level `continue-on-error: true` | passes. This isn't a bypass: later steps still skip after a failed step. |
| W14 Deploy `continue-on-error: true` | passes. This isn't a bypass: it only softens Deploy itself. |
| W9 Deploy `if: always()` | **passes**, so Deploy would run after a failed guard (N-4) |

On the head the guard is still the step just before Deploy. The steps run in this order: checkout, setup-node, Install, Test, Check Wrangler version, Local runtime info check, Deploy, Smoke. Deploy has no `if:`. The `test_local_info_check_reads_the_constant` test also now requires `MULTIPART_HEADROOM` and `wrangler.toml` in the script.

### 5. N-2 and N-3 wording: PASS

- **N-2:** the `multipart.ts` comment now says the script reads the constant from there, "so keep it a plain integer literal, at most 983040 (the enforced image cap at 1 MiB)."
- **N-3:** the README now says `jetson.yml` "is legacy and disabled. If it is ever re-enabled, it only runs by hand (`workflow_dispatch`)…".

### 6. Tests, lint, secrets, CI: PASS

- **vitest:** 52/52 in 8 files. `npm run typecheck` exits 0.
- **pytest:** `tests/test_worker_deploy.py` plus `tests/test_jetson_legacy.py` give 11 passed.
- **ruff:** `ruff check` is clean, and `ruff format --check` reports 62 files already formatted.
- **shellcheck:** clean on `worker/scripts/local-info-check.sh` and on the CI scope (`deploy/jetson/…`). The `bench/*.sh` warnings are pre-existing, outside CI and unchanged.
- **gitleaks 8.30.1 on `b821efb..d9b40b6`:** no leaks. The whole-tree scan has only the 2 known placeholder hits (`ci.yml` and `deploy/jetson/service.env.example`), and neither is in a changed file.
- **Added-line scan** (169 lines): no IPs, hostnames, account ids or tokens. The only 32+ hex strings are commit SHAs and sha256 values in the QA doc. The production hostname count per changed file is unchanged against `b821efb`: README 1, test 1, and 0 in the rest, including both QA docs.
- **CI on `d9b40b6`:** 5/5 success, last one finished 12:21:50 EEST.
  - `worker`, `lint-test`, `deploy-scripts`, `gitleaks` and `image` all passed.
  - The `worker` log shows `local /healthz 200; /v1/info max_image_bytes 524288` from the new script.

## Findings

Previous findings:

- **L-1 fixed.** A documented `MAX_UPLOAD_BYTES` change no longer fails the guard (rows B, C, D and G to Z).
- **N-1 fixed** (§4).
- **N-2 fixed** and **N-3 fixed** (§5).

New:

- **N-4 (test gap, not a regression):** `tests/test_worker_deploy.py` doesn't pin the Deploy step's condition. With `if: always()` (or `!cancelled()`) on Deploy, Deploy runs after a failed guard and every test still passes (W9). Suggestion: `assert set(deploy) == {"name", "env", "run"}`, or `assert "if" not in deploy`. It can go into this PR or a later one, since a reviewer would see such a change in the diff.
- **I-6:** the cases where the script reads the cap differently from `parseMaxUpload` all fail closed. They are:
  - whitespace inside the quotes (the Worker trims)
  - single-quoted or no-space TOML spellings
  - an `[env.*.vars]` cap above `[vars]`
  - values above 2^63
  - an unquoted TOML integer, where the Worker itself answers `/v1/info` with 503. That behaviour is pre-existing and comes from a non-string var.

  None of them can pass a wrong value. No change is needed. If wanted, the script comment could say "quoted string in `[vars]`".
- **I-7:** a developer's local `.dev.vars` with `MAX_UPLOAD_BYTES` overrides the cap in `wrangler dev`, so the check fails locally. CI and Deploy have no `.dev.vars` (it's gitignored).
- **I-8 (pre-existing):** when the constants can't be read, the script exits before `trap cleanup` is set, which leaves an empty `${TMPDIR}/autofill-local-info-$$` directory. `b821efb` behaves the same way.
- **I-9:** the strict key set also rejects harmless keys (`id:`, `shell:`, `working-directory:`). This is by design: whoever adds one updates the test.
- **I-10:** a constant above 983040 passes the guard, and `/v1/info` reports the clamped 983040 (row Q). That matches the Worker. The N-2 comment documents the limit, but nothing enforces it.

## Verdict

**PASS at `d9b40b6b3c0951ef1242cfafb6e135e2dc35800f`.** After merge, DevOps runs the Deploy Worker version check as agreed. Since `worker/**` changed, the merge triggers Deploy Worker, and the built `index.js` equals the deployed `main` bundle.
