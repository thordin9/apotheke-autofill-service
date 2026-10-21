# QA: service #31, Deploy step keys test (N-4)

- **PR:** apotheke-autofill-service #31, `test(deploy): pin the Deploy step's keys (N-4)`.
  - Branch `devops/deploy-step-keys` into `main`. Not a draft, mergeable `clean`.
- **Final head:** `b24d55e0fedfa47f6e25f234273b84e7a244822e`, two commits on top of `main` (`f95bb3e`).
- **First reviewed head:** `afc1c3601c2bd28ac6ea6b96d66bbbadb010b840`. Sections 1–6 cover it, and the Delta section
  covers `afc1c36` → `b24d55e`.
- **Follow-up to:** N-4 from the #29 recheck. Deploy `if: always()` (W9) passed the tests. The #28/#29 follow-up list
  also had Deploy `continue-on-error: true` (W14).
- **Date:** 2026-10-21. The first review ran 09:51–10:01 EEST, and the delta recheck ran 10:02–10:08 EEST.
- **Scope:** static and local QA only. No live Worker or Cloudflare calls, no credentials read, 0 Neurons.
- **Verdict: PASS, FINAL.** No high or medium findings.
  - L-1 and I-1 from the first review were folded into the PR and are closed at `b24d55e`.
  - I-2 and I-3 are info notes only.

## Plan

1. Run `compare_commits main...afc1c36` and `diff -r` the two tarballs.
   - Expect only `tests/test_worker_deploy.py` and `docs/cloudflare-workers-deploy.md` to change, with nothing under
     `worker/**` or `.github/**`.
   - Read the `push.paths` filter in `deploy-worker.yml`, and check that no other workflow deploys.
2. Run the full pytest, `ruff check`, `ruff format --check` and gitleaks on the head tarball.
3. Mutate a copy of `deploy-worker.yml` and run `tests/test_worker_deploy.py` against the head test and against the
   `main` test.
4. Compare the new doc sentence with the new assertion.
5. Check CI on the head through the API.
6. Public safety: scan the added lines and the diff for account ids, tokens, hostnames, IPs and Worker URLs.

Method:
- Codeload tarballs for the head and for `main`. `git get-tar-commit-id` reports `afc1c3601c2bd28ac6ea6b96d66bbbadb010b840`
  and `f95bb3e64bdcef04d1e2811e5e749d5f2531f27b`.
- A Python 3.13 venv from `requirements-dev.txt` (pytest 8.4, ruff 0.17.1), and gitleaks 8.

## Results (first review, `afc1c36`)

### 1. Delta and deploy trigger: PASS

| Commit | Message | Files |
|---|---|---|
| `afc1c36` (parent `f95bb3e`) | test(deploy): pin the Deploy step's keys (N-4) | `docs/cloudflare-workers-deploy.md` +3/−1, `tests/test_worker_deploy.py` +2/−0 |

- The compare shows 1 commit ahead, 0 behind, 2 files and +5/−1. `diff -r` of the two tarballs shows the same 2 files.
- Nothing changes under `worker/**`, `.github/**`, `app/**` or `deploy/**`.
- **The merge won't deploy:**
  - `deploy-worker.yml` triggers only on `push` to `main` with `paths: worker/**, .github/workflows/deploy-worker.yml`,
    and on manual `workflow_dispatch`. Neither changed path matches.
  - `ci.yml` only runs `npx wrangler deploy --dry-run`.
  - No workflow uses `workflow_run` or reads the Cloudflare secrets except `deploy-worker.yml`.
  - The merge commit will run CI only.

The change itself:

```python
deploy = next(step for step in job["steps"] if step.get("name") == "Deploy")
# No if:, continue-on-error or other keys, so a failed deploy always fails the job.
assert set(deploy) == {"name", "env", "run"}
```

This is in `test_deploy_workflow_installs_with_ci_and_checks_wrangler`, before the existing `env` key-set assertion.

### 2. pytest, ruff, gitleaks: PASS

- **pytest:** the full suite gives 566 passed on the head, and 566 on `main`. The count is the same because the change
  adds an assertion, not a test.
  - The one warning is a Starlette deprecation warning about `httpx`, from the test client. It also appears on `main`.
- **ruff:** `ruff check` passes, and `ruff format --check` reports 65 files already formatted.
- **gitleaks:** no leaks in the PR diff or the two changed files.
  - A `--no-git` scan of the whole tree reports one `generic-api-key` hit in `deploy/jetson/service.env.example`. It is
    the documented `<generate-a-random-token>` placeholder.
  - The same hit appears on `main`. `.gitleaks.toml` allowlists it by commit, so only git-mode scans skip it (I-2).
  - The CI `gitleaks` job (git mode, `--config .gitleaks.toml`) is green.

### 3. Mutations: PASS (two survivors, outside the key check)

Each mutant was applied to a copy of `deploy-worker.yml` in a copy of the head tree. `tests/test_worker_deploy.py`
(7 tests) was then run with the head test, and again with the `main` test as the baseline. The workflow copy was
restored and compared with `cmp` afterwards.

| Mutant on the Deploy step | Head test | `main` test |
|---|---|---|
| M0 unchanged | 7 passed | 7 passed |
| M1 `continue-on-error: true` | **fails** | passes |
| M2 `if: false` | **fails** | passes |
| M3 `if: ${{ false }}` | **fails** | passes |
| M14 `if: always()` (the #29 N-4 case W9) | **fails** | passes |
| M10 `continue-on-error: false` (harmless) | fails (intended strictness) | passes |
| M4 extra `working-directory: worker` | **fails** | passes |
| M5 extra `timeout-minutes: 5` | **fails** | passes |
| M6 extra `id: deploy` (harmless) | fails (intended strictness) | passes |
| M7 `env` removed | fails (2 tests) | fails (2 tests) |
| M8 `run` renamed to `shell` | fails (2 tests) | fails (1 test) |
| M9 `env` renamed to `environment` | fails (2 tests) | fails (2 tests) |
| M15 extra env var | fails | fails |
| M13 step renamed `Deploy worker` | fails (3 tests) | fails (3 tests) |
| M11 `run: npx wrangler deploy \|\| true` | **passes** (L-1) | passes |
| M12 job-level `continue-on-error: true` | **passes** (I-1) | passes |

- Every requested mutant fails on the head, and the unchanged workflow passes:
  - `continue-on-error: true`
  - `if: false`
  - an extra key (`working-directory` or `timeout-minutes`)
  - a missing `env`
  - a renamed key
- M1–M6, M10 and M14 pass with the `main` test, so the new assertion is what catches them. This closes the #29 N-4 case.
- **M11 survives.** `|| true` hides a failed `wrangler deploy`.
  - The Smoke step would then still pass against the Worker that was already live, so the run is green although
    nothing was deployed.
  - The new comment ("a failed deploy always fails the job") overstates the check, because the `run` text is still
    matched with `"npx wrangler deploy" in deploy["run"]`.
  - This existed before the PR and is outside the key check. It is L-1.
- **M12 survives,** but the job still fails. The step and job conclusions are `failure`, and only the run conclusion
  is softened. It is outside the step key check (I-1). The #29 recheck noted the same for the guard step (W13).

### 4. Doc sentence: PASS

`docs/cloudflare-workers-deploy.md` now says:

> Secrets are step environment variables on the deploy step only. That step has only `name`, `env` and `run`, with no
> `if:` or `continue-on-error`, and `tests/test_worker_deploy.py` fails if any other key is added.

- This matches `assert set(deploy) == {"name", "env", "run"}` exactly: the same three keys, and any other key fails.
- The assertion is stricter than the sentence, because it also fails if a key is removed. The sentence doesn't say
  that, but it isn't wrong.
- The two keys it names were checked as M1 and M2.

### 5. CI on the head: PASS

CI run 39557054611 (`pull_request`, `.github/workflows/ci.yml`) on `afc1c36` completed with **success**, 09:42:06–09:45:53 EEST.

| Job | Result | Window (EEST) |
|---|---|---|
| gitleaks | success | 09:42:08–09:42:15 |
| worker | success | 09:42:10–09:42:43 |
| lint-test | success | 09:42:08–09:44:27 |
| image | success | 09:44:29–09:44:49 |
| deploy-scripts | success | 09:42:09–09:45:53 |

5/5 check runs completed. The Deploy Worker workflow doesn't run on pull requests, as expected.

### 6. Public safety: PASS

- The added lines contain no URLs, hostnames, IP addresses, e-mail addresses, account ids or token-like strings.
- The only secret references are the existing `${{ secrets.… }}` names, which are unchanged.
- The diff context (an unchanged line) and existing files on `main` contain the Worker's production custom domain.
  The domain is pinned on purpose by `test_wrangler_toml_is_the_production_route`. The PR doesn't add or change it
  (I-3).
- This doc names no hosts, URLs or ids.

## Delta recheck: `afc1c36` → `b24d55e`

Plan:
- Compare the two heads and `diff -r` the tarballs.
- Run the full pytest, ruff and gitleaks.
- Rerun the whole mutation set, plus `run` variants and job-level `continue-on-error` variants.
- Check the doc sentence against the test, and CI on the new head.

Method: the codeload tarball for `b24d55e`, where `git get-tar-commit-id` reports
`b24d55e0fedfa47f6e25f234273b84e7a244822e`, with the same venv and gitleaks as above.

### D1. Delta and deploy trigger: PASS

| Commit | Message | Files |
|---|---|---|
| `b24d55e` (parent `afc1c36`) | test(deploy): pin the Deploy run and job continue-on-error | `docs/cloudflare-workers-deploy.md` +3/−2, `tests/test_worker_deploy.py` +2/−0 |

- The compare of `afc1c36...b24d55e` shows 1 commit ahead, 0 behind and 2 files. `diff -r` of the two tarballs shows
  the same 2 files.
- The PR as a whole (`main...b24d55e`) shows 2 commits ahead, 0 behind, 2 files and +8/−1.
- Nothing changes under `worker/**` or `.github/**`. `deploy-worker.yml` is byte-identical to `main` (`cmp`), so its
  `push.paths` filter still doesn't match and the merge won't deploy.

The new assertions in `test_deploy_workflow_installs_with_ci_and_checks_wrangler`:

```python
assert set(deploy) == {"name", "env", "run"}
assert deploy["run"].strip() == "npx wrangler deploy"
assert "continue-on-error" not in job
```

### D2. pytest, ruff, gitleaks: PASS

- **pytest:** the full suite gives 566 passed, with the same Starlette warning as before.
- **ruff:** `ruff check` passes, and `ruff format --check` reports 65 files already formatted.
- **gitleaks:** no leaks in the delta diff. A `--no-git` scan of the whole tree shows only the I-2 placeholder, which
  is unchanged.

### D3. Mutations: PASS

The whole set was rerun on a copy of the `b24d55e` tree, with the workflow restored and checked with `cmp` afterwards.

| Mutant | Result at `b24d55e` |
|---|---|
| M0 unchanged | **7 passed** |
| M1 Deploy `continue-on-error: true` | fails |
| M10 Deploy `continue-on-error: false` | fails |
| M2 Deploy `if: false` | fails |
| M3 Deploy `if: ${{ false }}` | fails |
| M14 Deploy `if: always()` | fails |
| M4 extra `working-directory` | fails |
| M5 extra `timeout-minutes` | fails |
| M6 extra `id` | fails |
| M7 `env` removed | fails (2 tests) |
| M8 `run` renamed to `shell` | fails (2 tests) |
| M9 `env` renamed to `environment` | fails (2 tests) |
| M13 step renamed | fails (3 tests) |
| M15 extra env var | fails |
| M11 `run: npx wrangler deploy \|\| true` | **fails** (survived at `afc1c36`) |
| M16 `run: npx wrangler deploy; true` | fails |
| M17 `run: npx wrangler deploy \|\| exit 0` | fails |
| M20 multi-line `run`: deploy, then `true` | fails |
| M21 `run: npx wrangler deploy --dry-run` | fails |
| M22 `run: echo npx wrangler deploy` | fails (2 tests) |
| M26 `run: set -e; npx wrangler deploy` | fails (intended strictness) |
| M12 job-level `continue-on-error: true` | **fails** (survived at `afc1c36`) |
| M23 job-level `continue-on-error: false` | fails (intended strictness) |
| M24 job-level `continue-on-error: ${{ github.event_name == 'workflow_dispatch' }}` | fails |
| M18 `run: "npx wrangler deploy   "` (trailing spaces) | passes, equivalent |
| M19 `run: \|` block with the single line `npx wrangler deploy` | passes, equivalent |
| M25 `run: >` folded block with the single line `npx wrangler deploy` | passes, equivalent |

- Every non-equivalent mutant fails.
- The only mutants that pass are the unmodified file and the three that are the same command after `strip()`:
  trailing spaces, and a single-line `|` or `>` block that ends in a newline. Each runs exactly `npx wrangler deploy`,
  so passing is correct.
- M11 and M12 passed at `afc1c36`. They now fail on the two new assertions.

### D4. Doc sentence: PASS

`docs/cloudflare-workers-deploy.md` now says:

> That step has only `name`, `env` and `run`, with no `if:` or `continue-on-error`, and `run` is exactly
> `npx wrangler deploy`. The job has no `continue-on-error` either. `tests/test_worker_deploy.py` fails if any of
> that changes.

The doc and test match clause for clause:

| Doc clause | Assertion |
|---|---|
| only `name`, `env` and `run` | `set(deploy) == {"name", "env", "run"}` |
| `run` is exactly `npx wrangler deploy` | `deploy["run"].strip() == "npx wrangler deploy"`. Leading and trailing whitespace is ignored, which doesn't change the command. |
| the job has no `continue-on-error` | `"continue-on-error" not in job` |

The code comment ("so a failed deploy always fails the job") is now accurate for the step: `|| true`, `; true` and
`|| exit 0` all fail the test.

### D5. CI on `b24d55e`: PASS

CI run 39559220671 (`pull_request`) completed with **success**, 10:02:12–10:05:54 EEST.

| Job | Result | Window (EEST) |
|---|---|---|
| gitleaks | success | 10:02:15–10:02:21 |
| worker | success | 10:02:15–10:02:37 |
| lint-test | success | 10:02:15–10:04:47 |
| image | success | 10:04:49–10:05:08 |
| deploy-scripts | success | 10:02:15–10:05:53 |

5/5 check runs completed successfully.

### D6. Public safety: PASS

The added lines contain no URLs, hostnames, IP addresses, e-mail addresses, account ids or token-like strings. I-3 is
unchanged.

## Findings

| ID | Sev | Finding | Suggestion |
|---|---|---|---|
| L-1 | Low, **closed** at `b24d55e` | The Deploy `run` is matched as a substring, so `npx wrangler deploy \|\| true` passes every test (M11). A failed deploy would then leave a green run, and Smoke would pass against the Worker that was already live. The new comment says "a failed deploy always fails the job". This existed before the PR and is a follow-up. | Done: `deploy["run"].strip() == "npx wrangler deploy"` is asserted. M11, M16, M17 and M20 now fail. |
| I-1 | Info, **closed** at `b24d55e` | Job-level `continue-on-error: true` (M12) passes. The job still shows `failure`, and only the run conclusion is softened. | Done: `"continue-on-error" not in job` is asserted. M12, M23 and M24 now fail. |
| I-2 | Info | A `--no-git` gitleaks scan flags the documented placeholder in `deploy/jetson/service.env.example`. It is allowlisted by commit, so the git-mode CI scan is clean. It appears on `main` too. | None. |
| I-3 | Info | The production custom domain is in existing files and in unchanged diff context, by design. The PR doesn't change it. | None. |

No high or medium findings, and no open low findings.

## Verdict

**PASS, FINAL** at `b24d55e0fedfa47f6e25f234273b84e7a244822e`.

- #31 changes only `tests/test_worker_deploy.py` and `docs/cloudflare-workers-deploy.md`.
- Nothing under `worker/**` or `.github/**` changes, and the deploy workflow's path filter doesn't match, so merging
  won't deploy.
- The tests now pin the Deploy step's keys, its exact `run` and the job's lack of `continue-on-error`.
  - Every non-equivalent mutant fails.
  - Only the unmodified file and three strip-equivalent `run` spellings pass.
- The doc sentence matches the assertions clause for clause.
- Full pytest, ruff and gitleaks pass, and CI is 5/5 green.
- L-1 and I-1 are closed. I-2 and I-3 are info notes only.
