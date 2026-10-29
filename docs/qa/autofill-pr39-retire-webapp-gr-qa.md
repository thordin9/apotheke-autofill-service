# QA: apotheke-autofill-service PR #39, retire webapp.gr

- PR: #39 `docs/changelog-1-3-0-fix` -> `main`, same repo (not cross-repo)
- Head reviewed: `3bc14922920db83de31c8e0cb171a18094d5a788`
- Base: `main` `4b74bd6` (includes #38 at `82cfb02`), 0 behind
- Commits: `c8ad53e` (L-1020 / N-1021), `3bc1492` (retire webapp.gr)
- CI: 5/5 green at the head
- Reviewed: 2026-10-29 (EET), light gate and delta read

## Checklist

| Check | Result |
|---|---|
| Head unchanged, same-repo, 0 behind, 2 commits | OK |
| Full diff read (6 files) | OK |
| No change to `worker/src`, secrets, token handling or `.github` | OK (diffs empty) |
| `wrangler.toml` parsed and compared section by section | OK, identical apart from `routes` and `env.paid.routes` |
| Routes, both envs | OK: only webapp.gr removed; `.com` then `.eu`, each `custom_domain = true` |
| Paid env name, vars, caps, CPU, TIER unchanged | OK (`apotheke-autofill`; 30000 paid, 6000 free; `cpu_ms` 500; `TIER=paid`) |
| `wrangler deploy --dry-run` default and `--env paid`, no credentials | OK, both exit 0; bindings and bundle byte-identical to `main` |
| Worker test suite (vitest) | OK, 9 files, 84 tests (route count 2, hash pin updated) |
| `tests/test_worker_deploy.py` | OK, 15 passed (`RETIRED_DOMAIN` asserted absent) |
| Deploy workflow | OK, unchanged |
| L-1020 | Closed: every released section (`[2.0.0]` onward, including `[1.3.0]`) is byte-identical to `74eeae65` |
| New CHANGELOG bullet | OK, under `[Unreleased]` / Changed, links #39 |
| N-1021 wrapping | Mostly fixed, see N-1040 |
| README / deploy doc | OK: retirement stated once in each; `.com` primary, `.eu` legacy alias |
| gitleaks 8.30.1 (checksum pinned) on `main..head` | OK, no leaks |
| Live check | One unauthenticated `GET /v1/info` each on `.com` and `.eu`: both answer with a JSON 401. No token and no photo sent |

Wrangler 4.151.2 was run under Node 22 from a scratch copy. No Cloudflare
credential env vars or files were used.

## Remaining webapp.gr references

- **This repo at the PR head:** README and the deploy doc (the retirement
  note), `tests/test_worker_deploy.py` (`RETIRED_DOMAIN`, intended), the
  `paid-tier.test.ts` comment, the CHANGELOG (history and the #39 bullet), and
  two older `docs/qa` reviews (history). Nothing in `worker/` config or
  `worker/src`, and nothing in CI.
- **apotheke `main` (`5f63656`) and `feature/ci4` (`f1b4535`):** none, from a
  fresh scratch clone. Neither branch hardcodes any autofill hostname, so the
  base URL is runtime configuration only.

## Findings

### H
None.

### M
None.

### L
None.

### N

- **N-1040, wrapping not fully done.** Besides the allowed table row, three
  new prose lines are still over 80 columns: README (92) and the deploy doc
  (94, 88). Cosmetic only.
- **N-1041, `[Unreleased]` reads two ways.** The #38 bullet still says
  webapp.gr "stays legacy", and the #39 bullet just above it removes that
  host. Both will ship in the same release. A short "superseded by #39" note
  on the #38 bullet would help readers.
- **N-1042, client cut-over.** Merging deploys `--env paid` and detaches
  webapp.gr. No repo code points at it, but any deployed Apotheke instance
  whose configured base URL is still webapp.gr will stop reaching the
  Worker. Checking that configuration before merge is an ops step, not a
  code issue.

## Verdict

**PASS** (N-1040 to N-1042). Only the webapp.gr route goes, in both envs.
`.com` and `.eu` stay. Config apart from routes, bindings and the built
bundle match `main`. L-1020 is closed.
