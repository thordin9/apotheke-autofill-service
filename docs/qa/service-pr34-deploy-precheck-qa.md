# QA: service PR #34, Deploy Worker tier pre-check (#33 follow-ups)

- PR: thordin9/apotheke-autofill-service #34 (draft)
- Head: `c682438797b899065b948f1a3d6abea92e19b2f5`
- Base: `main` at `5345aa6` (PR #33 merged)
- Scope: delta only. This PR closes the findings from the #33 QA report
  (`docs/qa/service-pr33-deploy-env-qa.md`): L-1 (tier check only after the
  upload), N-1 (env name pattern), N-2 (Free plan wording), N-3 (no trap
  test) and N-4 (line wraps).
- Verdict: **pass**. No high or medium findings. Two low and two nit
  findings below; none blocks the merge.

## Test plan

1. Compare `5345aa6...c682438`: files changed, `worker/src` untouched,
   linear history, no surprise files.
2. Extract the Deploy step's `run:` script from the workflow and run it with
   a fake `npx` that logs its argv and prints a configurable `TIER` line,
   for each value of `WORKER_DEPLOY_ENV`. Each case runs in its own
   `mktemp -d` with `TMPDIR` pointed at it. Check that the dry run comes
   first with the same `--env`, that the TIER grep works both ways on the
   dry-run output before the real deploy is called, that a failing dry run
   exits non-zero through `pipefail`, that the post-upload check still
   works, that the trap removes both temp files, and that invalid names fail
   without echoing the value or calling Wrangler.
3. Run the pinned Wrangler (`4.151.2`, `worker/package.json` and the
   lockfile) with `deploy --dry-run`, with and without `--env paid`, without
   credentials. Then run the extracted step with the real dry run and a stub
   in place of the upload.
4. Secrets: `AUTOFILL_TOKEN`, `secret put`, `set -x`, artifact upload, and
   where the Cloudflare secrets appear.
5. Docs: rollback note, N-2 wording, N-4 wraps, CHANGELOG, "What you need".
6. Tests: pytest and vitest at the head, actionlint and shellcheck, and a
   mutation check of the new parts of the step.
7. CI run 39848682668, per job.
8. gitleaks 8.30.1 on the five commit patches and the four changed files.

## Results

### 1. Delta

`5345aa6...c682438` is 5 ahead and 0 behind. Each commit has one parent and
the chain is linear:

| Commit | Parent | Subject |
|---|---|---|
| `d85f011` | `5345aa6` | ci(deploy): dry-run the tier check before upload |
| `e304ae6` | `d85f011` | ci(deploy): reject env names that start with a hyphen |
| `c91ca80` | `e304ae6` | docs(deploy): say Free rejects limits.cpu_ms |
| `556451a` | `c91ca80` | ci(deploy): require the temp log trap |
| `c682438` | `556451a` | docs(deploy): reflow wraps and note the follow-ups |

A recursive diff of the two trees shows exactly four changed files:
`.github/workflows/deploy-worker.yml` (+24/-3), `CHANGELOG.md` (+12/-5),
`docs/cloudflare-workers-deploy.md` (+22/-16) and
`tests/test_worker_deploy.py` (+80/-14). There are no added or removed files.
`worker/` is unchanged, so there is no runtime or CPU-time impact. Only
`ci:` and `docs:` prefixes are used, so no release is cut. The PR is open,
draft, and mergeable (`clean`).

### 2. Deploy step against a fake Wrangler

The argv column is what the fake `npx` logged, in order. The "left" column
counts temp files left in the case directory after the step exits. During
every Wrangler call, the fake saw exactly two `tmp.*` files in `TMPDIR`
(`$dry` and `$log`).

| Case | `WORKER_DEPLOY_ENV` | Dry-run TIER | Upload TIER | Exit | argv | Left |
|---|---|---|---|---|---|---|
| unset | unset | free | free | 0 | `deploy --dry-run`; `deploy` | 0 |
| empty | `""` | free | free | 0 | `deploy --dry-run`; `deploy` | 0 |
| empty, no TIER | `""` | none | none | 0 | `deploy --dry-run`; `deploy` | 0 |
| paid | `paid` | paid | paid | 0 | `deploy --dry-run --env paid`; `deploy --env paid` | 0 |
| paid, dry run missing TIER | `paid` | none | paid | 1 | `deploy --dry-run --env paid` only | 0 |
| paid, dry run shows free | `paid` | free | paid | 1 | `deploy --dry-run --env paid` only | 0 |
| free, dry run shows paid | `""` | paid | none | 1 | `deploy --dry-run` only | 0 |
| paid, dry run exits 1 | `paid` | paid | paid | 1 | `deploy --dry-run --env paid` only | 0 |
| free, dry run exits 7 | `""` | none | none | 7 | `deploy --dry-run` only | 0 |
| paid, upload missing TIER | `paid` | paid | none | 1 | dry run, then `deploy --env paid` | 0 |
| free, upload shows paid | `""` | free | paid | 1 | dry run, then `deploy` | 0 |
| paid, upload exits 3 | `paid` | paid | paid | 3 | dry run, then `deploy --env paid` | 0 |
| other valid name | `staging` | none | none | 0 | `deploy --dry-run --env staging`; `deploy --env staging` | 0 |

- The dry run always comes first and uses the same `--env` selection as the
  upload.
- The pre-check works both ways. With `paid`, a dry run without
  `TIER ("paid")` stops the job before the upload is called. With the free
  command, a dry run showing `TIER ("paid")` stops it before the upload. A
  free dry run with no TIER line, or `TIER ("free")`, passes.
- A failing dry run exits with Wrangler's code through `pipefail` (1 and 7
  above), although `tee` succeeds, and the upload is never called.
- The post-upload check is unchanged and still fails both ways.
- The trap removes both temp files on success, on a pre-check failure, on a
  Wrangler failure, and on a name-validation failure.
- A valid name other than `paid` (here `staging`) gets no TIER check, the
  same as on `main`. This is unchanged and documented.

Invalid names. Every one of these exits 1 before any Wrangler call (empty
argv log), with nothing on stdout and only the fixed line
`WORKER_DEPLOY_ENV must match ^[a-z0-9][a-z0-9_-]*$` on stderr, so the value
is not echoed:

`-x`, `--dry-run`, `paid;rm`, `PAID`, `Paid`, `pa id`, ` paid`, `paid `,
`paid` followed by a newline, `paid`+newline+`x`, `$(id)`, `../paid`,
`_paid`.

`-x` and `--dry-run` are the cases N-1 asked about. The old pattern accepted
them, and the new one rejects them.

### 3. Pinned Wrangler 4.151.2

Run from `worker/` with Node 22, `env -i`, a fresh `HOME` and no Cloudflare
variables:

- `npx wrangler deploy --dry-run` exits 0. Its stdout lists the bindings and
  has no TIER line, so the free pre-check grep does not match.
- `npx wrangler deploy --dry-run --env paid` exits 0. Its stdout includes
  `env.TIER ("paid")   Environment Variable`, and
  `grep -E 'TIER.*"paid"'` matches it. The TIER line is never on stderr.
- The extracted step, with the real dry run and a stub for the upload,
  passes for unset and for `paid`. Argv order is the dry run, then the
  upload, and both temp files are removed.

This is the same output the #33 check relied on, so the pre-check reads the
same line as the post-upload check.

### 4. Secrets

- `AUTOFILL_TOKEN` appears only in the header comment. There is no
  `secret put`, no `set -x` or `xtrace`, no `echo` in the step, and no
  artifact upload or cache of the dry-run or deploy logs. Both logs are
  `mktemp` files removed by the trap.
- `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` are read only in the
  Deploy step's `env:`. The dry run does not need them; it runs with them
  in its environment, which is harmless.

### 5. Docs

- Rollback note (L-1): the deploy doc and the CHANGELOG say that if the
  post-upload TIER check goes red the version is already live, and to roll
  back with `npx wrangler rollback`, then redeploy with the right env.
  `wrangler rollback [version-id]` exists in 4.151.2 and rolls back to the
  previous version when no id is given. `[env.paid]` sets the same Worker
  `name`, so the rollback without `--env` targets the right Worker. The
  existing rollback section already says to run it from `worker/` with the
  scoped token, and that it keeps the secrets. See N-1 below.
- N-2: both "What you need" and the paid section now say that the Free plan
  rejects a deploy that sets `limits.cpu_ms` (error 100328). That matches
  what was observed on #33. "What you need" still says the default
  `wrangler deploy` is for Workers Free and `--env paid` needs Workers Paid,
  which is right.
- N-4: the CHANGELOG's short "Smoke does not" line and the deploy doc's
  short "Any other value must match" line are reflowed. The changed
  paragraphs have no other odd wraps.
- The CHANGELOG has a new `[Unreleased]` CI entry for the pre-check, the
  rollback note, the new pattern and the 100328 wording. The #33 entry uses
  the new pattern, so the CHANGELOG, the deploy doc and the workflow message
  all name the same pattern.

### 6. Tests

- At the head: pytest 570 passed, vitest 84 passed (9 files), matching what
  Coder reported. actionlint and shellcheck (on the extracted step) are
  clean.
- N-3 is closed: `test_deploy_trap_deletes_both_temp_logs` checks that the
  only trap is `trap 'rm -f "$dry" "$log"' EXIT`, that it is set after both
  `mktemp` calls and before Wrangler, and runs the trap line in bash to check
  that both files are gone.

Mutation check. Each mutation was applied to a scratch copy of the workflow,
then `tests/test_worker_deploy.py`, `tests/test_docs.py` and
`tests/test_worker_followups.py` were run:

| Mutation | Result |
|---|---|
| Remove the dry run and its TIER check | killed (3 tests) |
| Keep the dry run, remove its TIER check | killed |
| Invert the paid dry-run grep | killed |
| Invert the free dry-run grep | killed |
| Dry-run grep reads `$log` instead of `$dry` | killed |
| Paid dry run drops `--env` | killed (2 tests) |
| Dry run tees to `$log` | killed (3 tests) |
| `\|\| true` after the free dry run | killed (2 tests) |
| `set -eu` without `pipefail` | killed |
| Drop the trap | killed |
| Trap removes only `$log` | killed |
| Trap on `ERR` instead of `EXIT` | killed |
| Pattern and message back to `^[a-z0-9_-]+$` | killed |
| Only the bash pattern loosened | killed |
| Pre-check `exit 1` replaced by `true` | **survived** (L-1) |

The surviving mutation is not new. Replacing the post-upload check's
`exit 1` with `true` also passes all tests on this head.

### 7. CI

Run 39848682668 (CI, `pull_request`, head `c682438`) completed with
`success`. All 5 jobs succeeded: gitleaks, lint-test, worker,
deploy-scripts, image (23:25 to 23:28 EEST, 22 Oct 2026). The 5 check runs
on the head commit all succeeded.

### 8. gitleaks

gitleaks 8.30.1 found no leaks in the five commit patches (about 22 KB) or
in the four changed files at the head. The patches contain no tokens or
account ids.

## Findings

### L-1: tests do not prove the pre-check stops the job

The tests check the step as text: command order, grep patterns and trap
line. If the pre-check's `exit 1` is replaced by `true`, the step prints the
error and then uploads anyway, and all tests still pass. The same gap exists
for the post-upload check. Behaviour is correct today (section 2). Suggested
fix: one pytest that extracts the Deploy `run:` script and runs it with a
fake `npx` on `PATH` that logs argv, as in section 2. It would assert that a
paid dry run without `TIER ("paid")` exits non-zero and never calls the
upload. Non-blocking.

### L-2: the two checks print the same error

The pre-check and the post-upload check print the same lines
(`deploy output is missing a TIER binding of "paid"` and
`free deploy output shows a TIER binding of "paid"`). The new rollback note
depends on which one fired: a red pre-check means nothing was uploaded, and
a red post-upload check means the version is live and needs a rollback.
Today an operator can only tell from whether upload output appears above
the error. Suggested fix: say `dry-run output ...` in the pre-check
messages (DevOps's nit), and optionally add "nothing was uploaded" to them.
Non-blocking.

### N-1: the new rollback sentences don't say where to run it

The new sentence in the deploy doc and the CHANGELOG says "roll back with
`npx wrangler rollback`" without "from `worker/`, with the scoped token".
The rollback section further down says both. A pointer to that section, or
"from `worker/`", would make the note stand alone.

### N-2: the TIER line shows up twice in the log

The greps have no `-q`, so on a pass the matching TIER line is printed again
after the `tee` copy. That was already true of the post-upload check, and
now the pre-check does it as well. It's cosmetic. `grep -q` would keep the
log to one copy each.

## Summary

L-1 from #33 is fixed: a wrong tier now stops the job before anything is
uploaded, the post-upload check remains as a backstop, and the docs give the
rollback step. N-1 to N-4 from #33 are fixed. QA OK to merge pinned to
`c682438`. Merging touches `deploy-worker.yml`, so it triggers a deploy.
With `WORKER_DEPLOY_ENV=paid`, that run is the first live test of the
pre-check. It should show `TIER ("paid")` from the dry run and again from
the upload, and `GET /v1/info` on `https://autofill.example` (the real
hostname is in `vars.WORKER_URL`) should then report `tier: paid`.
