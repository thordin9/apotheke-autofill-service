# QA recheck: service PR #28, Jetson legacy docs (fix delta)

- Previous QA PASS: `a1d9e968be7898b78d9a296a3d3746a81b509984` ([service-pr28-jetson-legacy-qa.md](service-pr28-jetson-legacy-qa.md))
- Rechecked head: `8e93636bc6fb3d3f972e3a90d3934ddd78a3ca5f` (sha confirmed from the codeload tarball with
  `git get-tar-commit-id`). Base `main` is `d0e959a9620b39d173eebfd1cc5b55c467248fc8`.
- Date: 2026-10-20 (EEST). Docs, workflow and test changes only. No live calls, no board access.

## 1. Plan

1. List the commits after `a1d9e96` and diff each tarball against the one before it.
2. The QA doc commit must add only `docs/qa/service-pr28-jetson-legacy-qa.md`, byte-identical
   (sha256 `8e117139…e18c1`, 8534 B).
3. Check each fix: L-1, L-2 (with a mutation test), L-3 (the Runner paragraph), N-1, N-2, N-3.
4. `worker/**` must match `main`.
5. Run `pytest` (expect 564), ruff, the deploy harness, actionlint and PyYAML, and a link check. Run gitleaks and an
   IP/host/email grep on the delta. Check CI on the head.

## 2. Results

### Commits after `a1d9e96`

| Commit | Files | Content |
|---|---|---|
| `312566c` docs(qa): add QA doc for the Jetson legacy docs PR | `docs/qa/service-pr28-jetson-legacy-qa.md` (new) only | QA doc: sha256 `8e11713952c11f19a421d8735f873690e388abcbc38eae206497e890f47e18c1`, 8534 B, identical to QA's file. **PASS** |
| `52c4a8a` docs: address QA findings on the Jetson legacy docs | `README.md`, `docs/benchmarks.md`, `docs/deploy-jetson.md`, `.github/workflows/jetson.yml`, `tests/test_jetson_legacy.py` | L-1, L-2, N-1, N-2, N-3, first Runner paragraph |
| `8e93636` docs: document why the Jetson runner is kept and how it is guarded | `docs/deploy-jetson.md` only, and only the 2-line Runner paragraph (now 4 lines) | L-3 |

### Checks

- **`worker/**`** is identical to `main` `d0e959a`, so merging doesn't deploy. `deploy/` is unchanged since `a1d9e96`.
  **PASS**
- **Tests:** `pytest` **564 passed** (563 + the second case of the parametrized test). `deploy/jetson/tests/run-tests.sh`
  passed=282 failed=0. `ruff check` and `ruff format --check` are clean (58 files). **PASS**
- **Mutation:** QA added a blank line and then `pull_request_target:` inside `on:`, which PyYAML reads as
  `[workflow_dispatch, pull_request_target]`. `test_jetson_workflows_are_dispatch_only` now **fails** for
  `jetson-hardware.yml` and for `jetson.yml`. **PASS**
- **YAML:** PyYAML reads the triggers as `ci` push+pull_request, `deploy-worker` push+dispatch, and both Jetson
  workflows `workflow_dispatch` only. actionlint reports only the custom `jetson` runner label, as on `main`. **PASS**
- **Links:** 113 relative links and anchors in README, `deploy-jetson.md`, `hardware-ci.md`, `benchmarks.md` and
  CHANGELOG, 0 broken. **PASS**
- **Secrets and hosts:** gitleaks on the added lines, the new QA doc and the test found no leaks. There are no IPs,
  URLs, emails, tokens, account ids or runner hostnames, and the production Worker hostname doesn't appear. **PASS**
- **CI:** 5/5 on `8e93636` (run 39392459932: `lint-test`, `gitleaks`, `worker`, `deploy-scripts`, `image`; done
  11:22:17 EEST). **PASS**

### Findings

| Id | Status at `8e93636` | Evidence |
|---|---|---|
| L-1 README wording | **Fixed** | README:108-109: "the autofill service no longer runs on the project's board … Ollama and the idle self-hosted runner stay on the board" |
| L-2 test misses triggers | **Fixed** | `yaml.safe_load`, parametrized over both workflows, `assert list(triggers) == ["workflow_dispatch"]`. The mutation fails as it should |
| L-3 runner kept | **Fixed (docs)** | `deploy-jetson.md` Runner paragraph: kept "for the board's Ollama work". "The repo requires approval before workflows run for all outside contributors' fork PRs". "No workflow may ever target this runner from `pull_request_target` or from any fork-triggered event". "Deregistering it is the owner's call" |
| L-3 repo setting | **Reported by DevOps, not verified by QA** | DevOps reports the fork-PR approval setting is `all_external_contributors`. QA has no read-only tool for that setting (cursor-github has none, and the REST endpoint needs admin auth), so it wasn't checked |
| N-1 jetson.yml dispatch-only | **Fixed, one stale line** | `on:` is `workflow_dispatch` only, with a legacy comment, and the header comment is updated. **Open (nit, N-4):** README:184-186 still says jetson.yml "starts on `workflow_dispatch` and when `Dockerfile.jetson` or `requirements-jetson.txt` changes. Path-triggered runs only record that reason." The step guarded by `if: github.event_name != 'workflow_dispatch'` can no longer run (info) |
| N-2 benchmarks note | **Fixed** | `docs/benchmarks.md` "Reproduce" opens with a legacy note linking `deploy-jetson.md#decommissioned-2026-10-20`, and the test now checks it |
| N-3 shred line | **Fixed** | "env files and the auth header file shredded first" |

No H, M or L findings remain open.

## 3. Verdict

**PASS at `8e93636bc6fb3d3f972e3a90d3934ddd78a3ca5f`.** The QA doc is byte-identical in its own commit. L-1, L-2, L-3
(docs), N-2 and N-3 are fixed. N-1 is fixed apart from the stale README:184-186 sentence (N-4, a nit; it can go in
a follow-up). `worker/**` is unchanged, tests are 564/282, and CI is 5/5. The fork-approval setting is as reported by
DevOps and not verified by QA.
