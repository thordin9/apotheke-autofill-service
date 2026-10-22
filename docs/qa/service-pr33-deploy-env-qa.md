# QA: service PR #33, Deploy Worker follows `WORKER_DEPLOY_ENV` (token-free tier check)

- Reviewed head: `8135e37` (`ci(worker): drop tier check from deploy smoke`), parent `28e3fb1`.
- PR head at review time: `840ef65` = `8135e37` + `docs(qa): add the service PR #32 paid live check` (one added file, byte-identical to the QA copy).
- Base: `main` after the #32 merge. The PR adds 3 commits on top of it (`28e3fb1`, `8135e37`, `840ef65`).
- Scope: the `WORKER_DEPLOY_ENV` deploy logic, the token-free `TIER` check, the removal of `AUTOFILL_TOKEN` from the workflow, #32 L-1 and L-3 (docs), and #32 N-2 (the whole-body 413 text).
- Verdict: **PASS.** No High or Medium findings. One Low and four Nits, all follow-ups.

## Test plan

1. **History.** Compare `28e3fb1...8135e37` and `main...840ef65`. Check that the history is linear, that the previously reviewed head is an ancestor, and that the commit adds only the five listed files.
2. **Workflow logic.** Extract the Deploy step `run:` from the YAML and run it under `bash -e` with a fake `npx` that records argv and prints scripted Wrangler output. Cases:
   - the variable unset, empty, `paid`, and a valid non-paid name;
   - bad names: `paid;rm -rf x`, `../x`, `PAID`, `' paid'`, `'paid '`, `'pa id'`, an embedded newline, `$(id)` and `--dry-run`;
   - the grep both ways: paid output with and without `TIER ("paid")`, and free output with and without it;
   - a Wrangler that exits 1 after printing `TIER ("paid")`.
   Then run the same script against the pinned Wrangler forced into `--dry-run`, with no Cloudflare credentials in the environment. Run actionlint and shellcheck.
3. **Secrets.** `AUTOFILL_TOKEN` appears only in comments. The deploy token is not echoed, the tee log is not uploaded, and nothing writes to the step summary or outputs.
4. **Docs.** #32 L-1 (a plain `wrangler deploy` reverts to free), #32 L-3 (the Paid plan in "What you need"), CHANGELOG consistency, and the accuracy of every sentence about the new step.
5. **#32 N-2.** Check the source and tests for the whole-body 413 text in both tiers, and grep the client repo `thordin9/apotheke` (main) for any dependency on the old text.
6. **Tests.** At `8135e37`, run `npm ci` then vitest, and pytest. Mutation-check the new workflow assertions. Check CI.
7. **Gitleaks.** Scan the PR commits.

## Results

### 1. History

| Check | Result |
|---|---|
| `28e3fb1...8135e37` | ahead 1, behind 0. Modified: `deploy-worker.yml` (+23/−39), `CHANGELOG.md` (+7/−3), `README.md` (+4/−2), `docs/cloudflare-workers-deploy.md` (+10/−6), `tests/test_worker_deploy.py` (+44/−18) |
| `8135e37...840ef65` | ahead 1, behind 0. Only `docs/qa/service-pr32-paid-live-check.md` added (79 lines). The raw file is 4396 B and `cmp` matches the QA copy |
| `main...840ef65` | ahead 3, behind 1 (the #32 merge commit). The merge base is `7923d61`, the #32 head. GitHub reports the PR mergeable, `clean` |
| Ancestry | `28e3fb1` → `8135e37` → `840ef65`, single parents, linear. The previously reviewed head `28e3fb1` is an ancestor, so nothing was force-pushed past it |

### 2. Workflow logic

The Deploy step does the following, in order:

1. Puts `WORKER_DEPLOY_ENV` in the step `env:` (`${{ vars.WORKER_DEPLOY_ENV }}`). It never appears as `${{ }}` inside `run:`.
2. Validates the value with `[[ =~ ^[a-z0-9_-]+$ ]]` when it is non-empty.
3. Runs `npx wrangler deploy [--env "$WORKER_DEPLOY_ENV"] | tee "$log"` under `set -euo pipefail`. The log is a `mktemp` file removed by `trap ... EXIT`.
4. Greps the log for `TIER.*"paid"`. When the value is `paid`, the step requires a match. When the value is empty, the step forbids one.

Simulation with a fake `npx`:

| Case | Exit | Wrangler argv | Message |
|---|---|---|---|
| unset | 0 | `wrangler deploy` | – |
| empty | 0 | `wrangler deploy` | – |
| `paid` | 0 | `wrangler deploy --env paid` | – |
| `staging` (valid, not paid) | 0 with the fake | `wrangler deploy --env staging` | – (no tier check, see N-1) |
| `paid;rm -rf x`, `../x`, `PAID`, `' paid'`, `'paid '`, `'pa id'`, `paid\nx`, `$(id)` | 1 | not run | `WORKER_DEPLOY_ENV must match ^[a-z0-9_-]+$` (value not printed) |
| `--dry-run` | 0 with the fake | `wrangler deploy --env --dry-run` | – (see N-1) |
| free, output has `env.TIER ("free")` | 0 | `wrangler deploy` | – |
| paid, output lacks a TIER line | 1 | `--env paid` | `deploy output is missing a TIER binding of "paid"` |
| paid, output has `env.TIER ("free")` | 1 | `--env paid` | missing-binding message |
| free, output has `env.TIER ("paid")` | 1 | `wrangler deploy` | `free deploy output shows a TIER binding of "paid"` |
| Wrangler exits 1 after printing `TIER ("paid")` (paid and free) | 1 | runs | Wrangler's own error. `pipefail` fails the step, and the grep never runs |

In every case the temporary log was removed on exit.

**Real Wrangler (4.151.2, the pin, Node 22), forced into `--dry-run`, no credentials:**

| Value | Step exit | Notes |
|---|---|---|
| unset / empty | 0 | Prints the multi-environment warning (no `TIER` in it). The bindings table has no `env.TIER` because the top-level `[vars]` has no `TIER` |
| `paid` | 0 | Prints `env.TIER ("paid")   Environment Variable` on **stdout**, so `tee` captures it |
| `staging` | 1 | Wrangler: `No environment found in configuration with name "staging"` (fails closed) |
| `--env --dry-run` | 1 | Wrangler: `Not enough arguments following: env` (fails closed) |

**False matches on free.** The free command's real output has no `TIER` line, and the warning text has no `TIER`. Only a line containing both `TIER` and a later `"paid"` matches. A free deploy with `env.TIER ("free")` does not match.

**Injection.** The value reaches bash only as an environment variable. It is quoted at the use site and validated first, so command or option injection is not possible.

**Lint.** actionlint 1.7.7 reports nothing for `deploy-worker.yml`. Its only report is the self-hosted label `jetson` in `jetson-hardware.yml`, which is unchanged and pre-existing. shellcheck on the extracted Deploy script is clean.

**Smoke.** Smoke is now only `GET /healthz` 200 (with `"status":"ok"`) and `POST /v1/suggest` without a token, where only 401 counts as success. Its `env:` is just `WORKER_URL`, with no `Authorization` header and no `/v1/info` call.

### 3. Secrets

- **`AUTOFILL_TOKEN`.** It appears only in the header comment ("This file never reads AUTOFILL_TOKEN"). `secrets.AUTOFILL_TOKEN` is gone, and the new test strips comments and asserts the name is absent from code.
- **`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.** They stay in the Deploy step `env:` only. There is no `set -x`, no `echo` or `printf` in the step, no write to the step summary or outputs, and no `upload-artifact`.
- **The tee log.** It holds only Wrangler's stdout, which was already in the job log before this change. It lives in a `mktemp` file, is deleted on exit, and is never uploaded.
- **The grep.** On a match it prints only the binding line, `env.TIER ("paid")`, which is a public var.

### 4. Docs and CHANGELOG

| Item | Result |
|---|---|
| #32 L-1 (plain deploy reverts to free) | **Done.** The deploy doc says "A plain `wrangler deploy` rolls back to free" and that "without it, any `worker/**` merge puts a paid Worker back on free". The README carries the same workflow warning and "`wrangler deploy` is the free tier" |
| #32 L-3 (Paid plan in "What you need") | **Done.** "`wrangler deploy --env paid` needs the Workers Paid plan". The stated reason is slightly imprecise (N-2) |
| The step as described in the deploy doc | Accurate: tee, the `grep -E` pattern, the paid/empty behaviour, the regex fails before Wrangler runs, the value is not printed, the free-command warning, and no `--env=""` |
| Smoke as described in the deploy doc and README | Accurate: `/healthz` 200 and suggest 401 only, no `/v1/info`. Both point to an out-of-CI `GET /v1/info` with the bearer token (the QA live check) and say `AUTOFILL_TOKEN` is not stored in GitHub |
| CHANGELOG | Both entries are under `[Unreleased]`. The CI entry matches the workflow, and the "Changed" entry records the 413 text. One cosmetic wrap (N-4) |
| Timing of the tier check | The docs don't say the check runs after the upload (L-1) |

### 5. #32 N-2: whole-body 413 text

**Source.** `worker/src/http.ts` adds `bodyTooLarge(maxBytes)` → 413 `payload_too_large` `request body exceeds <maxBytes> bytes`. It is used on all three whole-body paths:
- a declared `Content-Length` over the cap;
- buffered bytes over the cap;
- a streamed body without a length that passes the cap.

The image-part cap (`multipart.ts`) and Workers AI 3006 (`index.ts`) still say `image exceeds <enforced cap> bytes`. The Python service's `image exceeds <MAX_IMAGE_BYTES> bytes` is unchanged and correct for its image-only cap.

**Tests.**
- `limits.test.ts`: five assertions are updated to the new text. They cover the declared length, the buffered and streamed paths, and custom caps of 40, 100 and 128 bytes.
- `paid-tier.test.ts`: a new `whole-body 413 message` test runs on free (1048576) and paid (2097152). It sends `Content-Length` = cap + 1 and asserts the exact message, with no body read.

**Docs.** `docs/api-v1.md`, `openapi.yaml` and the deploy doc's error table are all updated, and the two texts are distinguished.

**Clients.** The intent is clear from the CHANGELOG ("A whole-body 413 names the body cap"). `thordin9/apotheke` main does not depend on the old text:
- `src/autofill.php` maps HTTP 413 to its own `too_large` message, `autofill_too_large_message($limit)`.
- `public/assets/app.js` checks only `r.status === 413`.
- The test stub returns its own text, `image too large`.

No client code matches `image exceeds` or `request body exceeds`. Older QA reports in `docs/qa/` quote the old text, which is correct as history.

### 6. Tests and CI

| Run | Result |
|---|---|
| `npm ci` + vitest at `8135e37` | 9 files, **84 passed** |
| pytest at `8135e37` (Python 3.13) | **569 passed** |
| Mutations on the workflow, run against `tests/test_worker_deploy.py` | Each of these fails at least one test: drop `pipefail`, invert the paid grep, `-z` → `-n` on the free branch, `> "$log"` instead of `tee`, grep for `"free"` on the free branch, widen the regex to `A-Z`, re-add `AUTOFILL_TOKEN` to Smoke, use `${{ vars.WORKER_DEPLOY_ENV }}` interpolation. Dropping the `trap` survives (N-3) |
| CI run `39842272085` at `8135e37` | completed, **success**: worker, lint-test, deploy-scripts, gitleaks, image (finished 22:45 EEST) |
| CI run `39842661782` at `840ef65` | completed, **success**: same five jobs |

### 7. Gitleaks

- **Commit patches.** gitleaks 8.x, run with `--no-git` over the patches of all 7 PR commits (`dabcc71`…`840ef65`, about 125 KB). The repo `.gitleaks.toml` and the default rules both report **no leaks**.
- **Head tree.** A scan of the full tree at `8135e37` reports one `generic-api-key` hit, in `deploy/jetson/service.env.example`. That file is unchanged by this PR, and the value is a `<gen…>` placeholder, so the hit is a false positive.
- **CI.** The gitleaks job in CI is green on both heads.

## Findings

| ID | Sev | Finding | Suggestion |
|---|---|---|---|
| L-1 | Low | The `TIER` check runs **after** `wrangler deploy` has uploaded. A paid deploy missing the binding, or a free deploy that has it, is already live when the step goes red, and Smoke is then skipped. The check detects the problem but does not prevent it, and the deploy doc does not say so | Add a sentence that a red tier check means the bad version is live, and point to `npx wrangler rollback`. Optionally gate before upload with the same command plus `--dry-run` and the same grep |
| N-1 | Nit | Only `paid` and empty are tier-checked. A valid other name (`staging`) or a leading-hyphen value (`--dry-run`) passes the regex. Wrangler 4.151.2 rejects both (no such environment; "Not enough arguments following: env"), so the step fails closed today | Say in the doc that only `paid` and empty are checked, or tighten the regex to `^[a-z0-9][a-z0-9_-]*$` |
| N-2 | Nit | L-3 says Paid is needed "because `limits.cpu_ms` is enforced only there". In fact Cloudflare rejects a deploy whose config sets `limits.cpu_ms` on the Free plan (error 100328), so `--env paid` on a Free account fails at deploy time | Reword: "`--env paid` sets `limits.cpu_ms`, which Cloudflare refuses on the Free plan" |
| N-3 | Nit | The workflow tests are static string checks. No test runs the Deploy script, and removing the `trap` cleanup is not caught | Optionally add a pytest that runs the extracted step with a fake `npx`, covering the cases in section 2 |
| N-4 | Nit | Cosmetic wraps: the CHANGELOG CI entry breaks "Smoke does not / call", and the deploy doc has a short "Any other value must match" line | Reflow |

Residual risk: the real (non-dry-run) Wrangler output was not exercised here. In 4.151.2 the dry-run prints the same bindings table on stdout. The first Deploy run after the merge confirms it: the job log should show `env.TIER ("paid")` and the step should be green. After that, the out-of-CI `GET /v1/info` check with the bearer token should report tier `paid`.
