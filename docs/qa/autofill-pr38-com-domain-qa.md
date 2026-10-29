# QA: apotheke-autofill-service PR #38, .com custom domain

- PR: #38 `docs/paid-tier-cap-wording` -> `main`, same repo (not cross-repo)
- Head reviewed: `6b921b6c3bb4e4bc3928fa6375f4f597c19892b0`
- Base: `main` `74eeae6` (the #37 merge, which includes the #37 QA doc), 0 behind
- Commits: `06e7f7d`, `0a2b008`, `f1f4c78`, `6b921b6`
- CI: 5/5 green at the head
- Reviewed: 2026-10-29 (EET), light gate

## Checklist

| Check | Result |
|---|---|
| Head unchanged, same-repo, 0 behind, #37 in base | OK |
| Full diff read (7 files) | OK |
| No change to `worker/src`, secrets or token handling | OK (`worker/src` diff empty) |
| `wrangler.toml` parsed and compared section by section | OK, identical apart from `routes` and `env.paid.routes` |
| Paid env name, vars, caps and CPU unchanged | OK (`apotheke-autofill`; 30000 paid, 6000 free; `cpu_ms` 500) |
| Routes, both envs | OK: `.com`, `.eu`, `webapp.gr` in that order, each `custom_domain = true`; no route dropped |
| `wrangler deploy --dry-run` default and `--env paid`, no credentials | OK, both exit 0; bindings and bundle byte-identical to `main` |
| Worker test suite (vitest) | OK, 9 files, 84 tests (route count 3, hash pin updated) |
| `tests/test_worker_deploy.py` | OK, 15 passed (`ROUTES` asserts all three, in order, in both envs) |
| Deploy workflow | OK: only the smoke default URL changed (to `.com`); triggers and `WORKER_DEPLOY_ENV` unchanged |
| L-980 / N-981 (PR #37) | Fixed: 30000 is now "our own daily cap for the paid tier"; wrapped, with backticks |
| README / deploy doc | OK: `.com` primary, `.eu` legacy alias (later 308), `webapp.gr` legacy |
| CHANGELOG | see L-1020 |
| gitleaks 8.30.1 (checksum pinned) on `main..head` | OK, no leaks |
| Live check | One unauthenticated `GET /v1/info` on the `.com` host: it resolves and answers with a JSON 401 (auth required). No token and no photo sent |

Wrangler 4.151.2 was run under Node 22 from a scratch copy. No Cloudflare
credential env vars or files were used.

## Findings

### H
None.

### M
None.

### L

- **L-1020, CHANGELOG duplicate in a released section.** The
  "Primary hostname is `autofill.services.getapotheke.com`" bullet is added
  twice. It appears once under `[Unreleased]` / Changed (correct) and again
  under `[1.3.0] - 2026-10-19` / Changed. That release predates the change,
  so the second copy should be removed. Docs only, does not block.

### N

- **N-1021, line length.** A few new README, deploy doc and CHANGELOG lines
  run to 82-93 columns, where the docs otherwise wrap at about 78. The
  paid-tier paragraph itself is wrapped. Cosmetic only.
- **N-1022, smoke default.** The smoke check now defaults to the `.com`
  host. That host already resolves and answers, so the healthz retry window
  (about 3 min) should not be at risk on the merge deploy. `WORKER_URL`
  still overrides it.

## Verdict

**PASS** (L-1020; N-1021, N-1022). Only routes and the matching
docs/tests change. Config apart from routes, bindings and the built bundle
match `main` for both envs. Merging keeps all three hostnames in both envs,
so the next `--env paid` deploy will not drop `.com`. L-1020 can be fixed in
a follow-up.
