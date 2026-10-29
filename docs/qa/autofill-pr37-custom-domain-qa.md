# QA: apotheke-autofill-service PR #37, custom domain

- PR: #37 `chore/autofill-new-hostname` -> `main`, same repo (not cross-repo)
- Head reviewed: `57467e8bd75384414166c953a26cce3a9c5e5c01`
- Base: `main` `dd71820`, 0 commits behind
- Commits: `e272b99` (custom domain), `57467e8` (CHANGELOG link to #37)
- CI: 5/5 green at the head
- Reviewed: 2026-10-29 (EET), light gate

## Checklist

| Check | Result |
|---|---|
| Head unchanged, same-repo, 0 behind | OK |
| Full diff read (7 files) | OK |
| No change to `worker/src`, secrets, vars, bindings or limits | OK (`worker/src` diff empty) |
| Neuron cap and CPU limits unchanged | OK (free 6000; paid 30000, `cpu_ms` 500) |
| `[env.paid]` changes only in routes | OK |
| `wrangler.toml` parsed and compared section by section | OK, identical apart from `routes` and `env.paid.routes` |
| Paid env keeps the base `name` and existing settings | OK (`apotheke-autofill`; vars, ai, durable_objects, ratelimits, limits, migrations unchanged) |
| Route blocks well-formed | OK: both envs list the new host then the legacy host, each `custom_domain = true` |
| Legacy route kept, none dropped, in both envs | OK |
| `wrangler deploy --dry-run` default and `--env paid`, no credentials | OK, both exit 0; bindings and bundle byte-identical to `main` |
| Worker test suite (vitest) | OK, 9 files, 84 tests |
| `tests/test_worker_deploy.py` | OK, 15 passed |
| Deploy workflow | OK: deploys only on push to `main` (worker paths), env from `WORKER_DEPLOY_ENV` (paid). Only the smoke default URL changed |
| CHANGELOG | OK: under Unreleased / Changed, links #37, accurate |
| README / deploy doc hostnames | OK, see N-982 |
| Deploy doc accuracy | see L-980, N-981 |
| gitleaks 8.30.1 (checksum pinned) on `main..head` | OK, no leaks |
| Live check | One unauthenticated `GET /v1/info` on the new host: answers with a JSON 401 (auth required). No token and no photo sent |

Wrangler 4.151.2 was run under Node 22 from a scratch copy. No Cloudflare
credential env vars or files were used.

## Findings

### H
None.

### M
None.

### L

- **L-980, deploy doc wording.** The new Paid tier line says the 30000
  Neuron budget "matches the Cloudflare paid Workers AI limit". Workers AI
  on Workers Paid has no 30000 daily limit. 30000 is this project's own paid
  cap, and the same doc's Cost section says 30000 is above the account's
  10,000 Neuron free allocation. Suggested wording: "the project's paid-tier
  daily cap". This is docs only, with no effect on config or behaviour, so it
  does not block.

### N

- **N-981, line wrap.** The same Paid tier sentence is a single unwrapped
  line, about 250 characters long. The rest of the doc wraps at about 78
  columns and uses backticks for `[vars]`, `TIER=paid` and
  `AUTOFILL_DAILY_NEURON_BUDGET`. Cosmetic only.
- **N-982, real hostnames in docs.** README and the deploy doc name the
  real hosts, not the `https://autofill.example` placeholder. Both files
  already named the legacy real host on `main` before this PR, and forks are
  still told to use `<your-domain>`. Pre-existing practice, a judgement
  call, not blocking.
- **N-983, smoke default.** The post-deploy smoke now defaults to the new
  host. The host already resolves and answers, so the smoke retry window
  (about 3 min for healthz) should not be at risk. `WORKER_URL` still
  overrides it.

## Verdict

**PASS** (L-980; N-981 to N-983). Only routes and the matching
docs/tests change. Config apart from routes, bindings and the built bundle
match `main` for both envs. The L and N items can be handled in a follow-up.
