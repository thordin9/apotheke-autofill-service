# QA: service PR #35, Deploy Worker pre-check follow-ups (#34 QA)

- PR: thordin9/apotheke-autofill-service #35 (draft), by Coder. Title:
  `ci(deploy): close the PR #34 dry-run QA follow-ups`
- Head: `2cf89f7c8601c459c79cabbb0666c1bbb7f9cf60` (the pinned head)
- Base: `main` at `102e1b9` (PR #34 merged)
- Scope: delta only. This PR closes the findings from the #34 QA report
  (`docs/qa/service-pr34-deploy-precheck-qa.md`): L-1 (no behaviour test
  that the pre-check stops the job), L-2 (the pre-check and the post-upload
  check print the same error), N-1 (the rollback sentence does not say where
  to run it) and N-2 (the TIER line shows up twice in the log).
- Verdict: **pass**. No high, medium or low findings. Two nits below; neither
  blocks the merge. DevOps's deploy check is still needed (see Summary).

## Test plan

1. Compare `102e1b9...2cf89f7`: files changed, `worker/` untouched, linear
   history, no surprise files. CI on the head.
2. Extract the Deploy step's `run:` script from the workflow and run it with
   a fake `npx` that logs its argv and prints a configurable `TIER` line and
   exit code for the dry run and the upload, for each value of
   `WORKER_DEPLOY_ENV`. Each case runs in its own `mktemp -d` with `TMPDIR`
   pointed at it. Check the dry-run and post-upload messages (L-2), how
   often the TIER line appears in the log (N-2), that a mismatch still stops
   the job before the upload, and that the trap removes both temp files.
   Run the base script on the same cases for contrast.
3. L-1: read the new test, then mutation-check it: make each of the four
   TIER checks fail open (`exit 1` replaced by `true`) and change the
   messages, the `-q` flags, the grep file and `--env`.
4. Docs (N-1) and CHANGELOG.
5. pytest at the head and the base, `ruff check`, `ruff format --check`,
   actionlint on the workflow, and `bash -n` and shellcheck on the
   extracted step.
6. gitleaks 8.x on the PR diff (default rules and the repo
   `.gitleaks.toml`), and a grep for tokens, account ids, URLs and
   hostnames.

No deploy was run and nothing called Cloudflare. The real Wrangler was not
started; the fake `npx` stands in for it.

## Results

### 1. Delta

`102e1b9...2cf89f7` is 2 ahead and 0 behind. Each commit has one parent and
the chain is linear:

| Commit | Parent | Subject |
|---|---|---|
| `365a942` | `102e1b9` | ci(deploy): catch a paid dry-run that misses the tier |
| `2cf89f7` | `365a942` | docs(deploy): say where to run wrangler rollback |

A recursive diff of the two trees shows exactly three changed files:
`.github/workflows/deploy-worker.yml` (+6/-6),
`docs/cloudflare-workers-deploy.md` (+7/-7) and
`tests/test_worker_deploy.py` (+45/-5). There are no added or removed files.
`worker/` is unchanged, so there is no runtime or CPU-time impact. Only
`ci:` and `docs:` prefixes are used, so no release is cut. The PR is open,
**draft**, and mergeable (`clean`).

CI run 40036154041 (CI, `pull_request`, attempt 1, head `2cf89f7`)
completed with `success`. All 5 jobs succeeded: gitleaks, lint-test, worker,
deploy-scripts, image (01:50 to 01:54 EEST, 24 Oct 2026).

### 2. Deploy step against a fake Wrangler

The argv column is what the fake `npx` logged, in order. "TIER lines" counts
TIER lines on the step's stdout. "Left" counts temp files left in the case
directory after the step exits.

| Case | `WORKER_DEPLOY_ENV` | Dry-run TIER | Upload TIER | Exit | argv | stderr | TIER lines | Left |
|---|---|---|---|---|---|---|---|---|
| unset | unset | free | free | 0 | `deploy --dry-run`; `deploy` | none | 2 | 0 |
| empty | `""` | free | free | 0 | `deploy --dry-run`; `deploy` | none | 2 | 0 |
| empty, no TIER | `""` | none | none | 0 | `deploy --dry-run`; `deploy` | none | 0 | 0 |
| paid | `paid` | paid | paid | 0 | `deploy --dry-run --env paid`; `deploy --env paid` | none | **2** (base: 4) | 0 |
| paid, dry run missing TIER | `paid` | none | paid | 1 | dry run only | `dry-run output is missing a TIER binding of "paid"` | 0 | 0 |
| paid, dry run shows free | `paid` | free | paid | 1 | dry run only | `dry-run output is missing …` (base: `deploy output is missing …`) | 1 | 0 |
| free, dry run shows paid | `""` | paid | none | 1 | dry run only | `free dry-run output shows a TIER binding of "paid"` | 1 | 0 |
| paid, dry run exits 1 | `paid` | paid | paid | 1 | dry run only | none | 1 | 0 |
| free, dry run exits 7 | `""` | none | none | 7 | dry run only | none | 0 | 0 |
| paid, upload missing TIER | `paid` | paid | none | 1 | dry run, then upload | `deploy output is missing a TIER binding of "paid"` | 1 | 0 |
| free, upload shows paid | `""` | free | paid | 1 | dry run, then upload | `free deploy output shows a TIER binding of "paid"` | 2 | 0 |
| paid, upload exits 3 | `paid` | paid | paid | 3 | dry run, then upload | none | 2 | 0 |
| other valid name | `staging` | none | none | 0 | `--env staging` for both | none | 0 | 0 |
| invalid `--dry-run` | `--dry-run` | – | – | 1 | none | `WORKER_DEPLOY_ENV must match ^[a-z0-9][a-z0-9_-]*$` | 0 | 0 |
| invalid `PAID` | `PAID` | – | – | 1 | none | same | 0 | 0 |

- **L-2 is fixed.** The two pre-check failures now say `dry-run output …`,
  and the two post-upload failures still say `deploy output …`. An operator
  can tell from the message alone whether anything was uploaded.
- **N-2 is fixed.** With `grep -q` the TIER line appears once per Wrangler
  call (from `tee`), not twice. A passing paid run shows 2 lines instead of
  4. The greps read files, not pipes, so `-q` exiting early is safe under
  `pipefail`.
- Everything #34 verified still holds: the dry run comes first with the same
  `--env`, a mismatch stops the job before the upload, a failing Wrangler
  exits with its code through `pipefail`, invalid names fail before any
  Wrangler call without echoing the value, and the trap removes both temp
  files in every case.

### 3. L-1: the new behaviour test

`test_paid_dry_run_missing_tier_stops_before_upload` extracts the Deploy
`run:` script from the workflow YAML and runs it in bash with a fake `npx`
on `PATH`, `WORKER_DEPLOY_ENV=paid`, and `TMPDIR`/`HOME` in pytest's
`tmp_path`. The fake logs its argv and prints `env.TIER ("free")`. The test
asserts a non-zero exit, an argv log of exactly
`wrangler deploy --dry-run --env paid` (so the upload never ran), the
`dry-run output is missing …` message on stderr and not the post-upload
one, and that each of the four messages sits on the right side of the
upload line. It uses no network and no Wrangler. **L-1 is fixed**: the #34
surviving mutant is now killed.

Mutation check. Each mutation was applied to a scratch copy of the
workflow, then `tests/test_worker_deploy.py`, `tests/test_docs.py` and
`tests/test_worker_followups.py` were run:

| Mutation | Result |
|---|---|
| Paid pre-check `exit 1` replaced by `true` (the #34 L-1 mutant) | **killed** (the new test) |
| Free pre-check `exit 1` replaced by `true` | **survived** (N-1) |
| Paid post-upload `exit 1` replaced by `true` | **survived** (N-1) |
| Free post-upload `exit 1` replaced by `true` | **survived** (N-1) |
| Paid pre-check message back to `deploy output` | killed |
| Free pre-check message back to `deploy output` | killed |
| `-q` dropped on the paid dry-run grep | killed |
| `-q` dropped on the free post-upload grep | killed |
| Paid pre-check reads `$log` instead of `$dry` | killed |
| Paid pre-check grep inverted | killed (2 tests) |
| Paid dry run drops `--env` | killed (3 tests) |

### 4. Docs

- **N-1 is fixed in the deploy doc.** The post-upload sentence now says to
  roll back with `npx wrangler rollback` from `worker/`, with the scoped
  token, then redeploy with the right env. The rest of that paragraph is
  only reflowed (same words, checked line by line).
- The CHANGELOG is not changed. Its `[Unreleased]` #34 entry still says
  "roll back with `npx wrangler rollback`" without `from worker/`, and
  there is no line for the new messages or `grep -q`. The PR body says so
  (N-2 below).
- No doc quotes the old pre-check messages, so nothing else needed an
  update. The optional "nothing was uploaded" wording from #34 L-2 was not
  added; the `dry-run output` prefix makes it clear enough.

### 5. Tests and lint

- pytest at the head: **571 passed** (1 Starlette deprecation warning, as
  before). At the base: 570 passed. The one extra test is the new one.
- `ruff check .` passes and `ruff format --check .` reports 70 files
  formatted.
- actionlint on `deploy-worker.yml` is clean. On the whole directory it
  reports only the existing `jetson` self-hosted label in
  `jetson-hardware.yml`, which this PR does not touch.
- `bash -n` and shellcheck on the extracted Deploy script are clean.
- vitest was not rerun because `worker/` is byte-identical to the base; the
  CI `worker` job is green.

### 6. Secrets

- The step still has no `AUTOFILL_TOKEN` (header comment only), no
  `secret put`, no `set -x`, no `echo` and no artifact upload. The new test
  passes a minimal environment with no Cloudflare variables.
- gitleaks on the PR diff (6,594 B), with the default rules and with the
  repo `.gitleaks.toml`: **no leaks**. The added lines have no tokens,
  account ids, URLs or hostnames.

## Findings

### N-1: three of the four TIER checks still fail open under mutation

The new test covers the paid pre-check, which is what #34 L-1 asked for.
Replacing `exit 1` with `true` in the free pre-check or in either
post-upload check still passes every test. Behaviour is correct today
(section 2). Suggested fix: parametrize the new test over the four
directions (paid and free, dry run and upload), each asserting the exit
code, the argv log and the message. Non-blocking.

### N-2: the CHANGELOG was not updated

The `[Unreleased]` entry from #34 still has the rollback sentence without
`from worker/`, and there is no CI line for the `dry-run output` messages
or the single TIER line. Suggested fix: add `from worker/, with the scoped
token` to that sentence and one short line for the new messages. It is
cosmetic, and the PR body names it.

## Summary

#34's L-1, L-2 and N-2 are fixed, and N-1 is fixed in the deploy doc.
QA OK to merge pinned to `2cf89f7`.

DevOps's deploy check is still needed. The PR body asks DevOps to review
the workflow, and merging changes `.github/workflows/deploy-worker.yml`,
which is on the Deploy Worker path filter, so the merge runs a real
deploy. `worker/` is unchanged, so it redeploys the same code. With
`WORKER_DEPLOY_ENV=paid`, that run should pass, show `TIER ("paid")` once
from the dry run and once from the upload, and then `GET /v1/info` on
`https://autofill.example` (the real hostname is in `vars.WORKER_URL`)
should report `tier: paid`.
