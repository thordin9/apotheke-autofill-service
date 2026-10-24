# QA: service PR #36, the four-way TIER tests (#35 QA nits)

- PR: thordin9/apotheke-autofill-service #36 (draft), by Coder. Title:
  `test(deploy): close the PR #35 tier-check nits`
- Head: `c9cf0aa329d66aa5ac24f65aaf634d7b5437c438` (the pinned head)
- Base: `main` at `7305ae3` (PR #35 merged, still the head of `main`)
- Scope: delta only. This PR closes the two nits from the #35 QA report
  (`docs/qa/service-pr35-precheck-followups-qa.md`): N-1 (three of the four
  TIER checks still fail open under mutation) and N-2 (the CHANGELOG was not
  updated).
- Verdict: **pass**. No high, medium or low findings, and no nits. Merging
  does **not** start a Worker deploy (see Summary).

## Test plan

1. Compare `7305ae3...c9cf0aa`: files changed, `worker/` and
   `.github/workflows/` untouched, linear history. CI on the head.
2. N-1: read the parametrized test, run it, then mutation-check it: make
   each of the four TIER checks fail open (`exit 1` replaced by `true`), and
   change the exit code, the grep file, the messages, the `--env`, the free
   branch condition and `-q`.
3. N-2: the CHANGELOG entry, word by word against the base.
4. Deploy impact: the Deploy Worker trigger paths against the changed files,
   and what the other workflows do on a push to `main`.
5. pytest at the head and the base, `ruff check`, `ruff format --check`,
   actionlint on the deploy workflow, and `bash -n` and shellcheck on the
   extracted Deploy step.
6. gitleaks 8.30.1 on the PR diff (default rules and the repo
   `.gitleaks.toml`), and a grep for tokens, account ids, URLs and
   hostnames.

No deploy was run and nothing called Cloudflare or the Worker. The real
Wrangler was not started; the test's fake `npx` stands in for it.

## Results

### 1. Delta

`7305ae3...c9cf0aa` is 2 ahead and 0 behind. Each commit has one parent and
the chain is linear:

| Commit | Parent | Subject |
|---|---|---|
| `69deefa` | `7305ae3` | test(deploy): fail closed on all four tier checks |
| `c9cf0aa` | `69deefa` | docs(deploy): note tier messages and where rollback runs |

A recursive diff of the two trees shows exactly two changed files:
`CHANGELOG.md` (+8/-6) and `tests/test_worker_deploy.py` (+66/-14). There
are no added or removed files. `worker/` and
`.github/workflows/deploy-worker.yml` are byte-identical to the base, so
there is no runtime or CPU-time impact. Both subjects match the
Conventional Commits format (49 and 56 characters). The PR is open,
**draft**, and mergeable (`clean`).

CI run 40046583437 (CI, `pull_request`, attempt 1, head `c9cf0aa`)
completed with `success`. All 5 jobs succeeded: lint-test, deploy-scripts,
gitleaks, worker, image (03:45 to 03:49 EEST, 24 Oct 2026).

### 2. N-1: the four-way test

`test_tier_mismatch_fails_the_step` replaces #35's single
`test_paid_dry_run_missing_tier_stops_before_upload` and is parametrized
over four cases. The fake `npx` prints one TIER line for the dry run and
another for the upload, so each case fails at exactly one check and passes
the check before it:

| Case | `WORKER_DEPLOY_ENV` | Dry run prints | Upload prints | Expected argv | Expected stderr |
|---|---|---|---|---|---|
| `paid-dry-run` | `paid` | free | paid | dry run only | `dry-run output is missing a TIER binding of "paid"` |
| `paid-upload` | `paid` | paid | free | dry run, then upload | `deploy output is missing a TIER binding of "paid"` |
| `free-dry-run` | `""` | paid | free | dry run only | `free dry-run output shows a TIER binding of "paid"` |
| `free-upload` | `""` | free | paid | dry run, then upload | `free deploy output shows a TIER binding of "paid"` |

Each case asserts exit code **exactly 1**, the exact argv log (so the
dry-run cases prove the upload never ran), and stderr equal to the one
message. The static asserts from #35 (each message on the right side of the
upload line, no `dry-run output` after it) are kept. It uses `tmp_path` for
`TMPDIR`/`HOME`, a minimal environment with no Cloudflare variables, and no
network. All four cases pass.

Mutation check. Each mutation was applied to a scratch copy of the
workflow, then `tests/test_worker_deploy.py`, `tests/test_docs.py` and
`tests/test_worker_followups.py` were run (40 tests):

| Mutation | Result |
|---|---|
| Paid pre-check `exit 1` replaced by `true` | **killed** (`paid-dry-run` only) |
| Free pre-check `exit 1` replaced by `true` | **killed** (`free-dry-run` only) |
| Paid post-upload `exit 1` replaced by `true` | **killed** (`paid-upload` only) |
| Free post-upload `exit 1` replaced by `true` | **killed** (`free-upload` only) |
| Paid post-upload `exit 1` replaced by `exit 2` | killed (`paid-upload`) |
| Paid post-upload grep reads `$dry` instead of `$log` | killed (2 tests) |
| Free post-upload grep reads `$dry` instead of `$log` | killed (2 tests) |
| Free pre-check grep reads `$log` instead of `$dry` | killed (2 tests) |
| Paid pre-check message back to `deploy output` | killed (4 cases) |
| Free post-upload message changed to `free dry-run output` | killed (4 cases) |
| Paid dry run drops `--env` | killed (4 tests) |
| Free post-upload branch never taken (`elif false`) | killed (2 tests) |
| `-q` dropped on the free post-upload grep | killed |

**N-1 is fixed.** The three #35 survivors are now killed, and each
fail-open mutant fails exactly the case for its check, which matches the
PR body's own mutation notes. A passing paid or free run is still covered
implicitly: the two upload cases have to pass the pre-check first.

### 3. N-2: CHANGELOG

The `[Unreleased]` / `### CI` entry "Deploy Worker dry-runs the tier check
before upload" changes only by these additions (word diff against the
base; the rest is reflowed, same words):

- "`grep -q` prints that line once."
- "A **dry-run** mismatch **says `dry-run output` and** fails the job
  before any upload."
- "The post-upload check stays **and says `deploy output`**."
- "roll back with `npx wrangler rollback` **from `worker/`, with the scoped
  token,** and redeploy with the right env."

**N-2 is fixed.** The wording matches the deploy doc and the step's
messages. The service has no release automation, so the entry stays under
`[Unreleased]`.

### 4. Deploy impact

The Deploy Worker workflow runs on a push to `main` only for `worker/**`
and `.github/workflows/deploy-worker.yml` (plus manual dispatch). #36
changes neither, so **merging does not start a deploy**, unlike #35, which
changed the workflow file. On the merge push, only `ci.yml` runs
(lint-test, deploy-scripts, gitleaks, worker, image); its `image` job
builds and smoke-tests the image locally and pushes nothing. The Jetson
workflows are manual only. No DevOps deploy check is needed for this
merge; the Worker keeps the version DevOps verified after #35.

### 5. Tests and lint

- pytest at the head: **574 passed** (1 Starlette deprecation warning, as
  before). At the base: 571 passed. The 3 extra tests are the one #35 test
  becoming four cases.
- `ruff check .` passes and `ruff format --check .` reports 71 files
  already formatted.
- actionlint on `deploy-worker.yml` is clean. `bash -n` and shellcheck
  0.10.0 on the extracted Deploy step are clean (the step is unchanged).

### 6. Secrets

- gitleaks on the PR diff (6,051 B), with the default rules and with the
  repo `.gitleaks.toml`: **no leaks**.
- The added lines have no tokens, account ids, URLs, IPs or hostnames. The
  fake `npx` prints only `env.TIER (...)` lines.

## Findings

None. #35's N-1 and N-2 are both fixed, and no new issue came up.

## Summary

QA OK to merge pinned to `c9cf0aa`. The merge runs CI only and does not
deploy the Worker, so DevOps does not need to repeat the deploy check. If
a Deploy Worker run does show up for the merge commit, that would be
unexpected and worth a look; otherwise `GET /v1/info` on
`https://autofill.example` (the real hostname is in `vars.WORKER_URL`)
should still report `tier: paid` from the #35 deploy.
