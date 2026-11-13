# QA: apotheke-autofill-service #41, per-installation monthly autofill budget (E5), full gate

- PR: https://github.com/thordin9/apotheke-autofill-service/pull/41 (Coder), `feat/installation-autofill-budget` into `main`, same repo. Operator console E5, service side.
- Reviewed head: `3d716fed0bc44e760e83f1d2883789c7bb2b8192` on main `73f28d7bdf2afe92e39d00ce0eaf3c9e44f1ade8`. 0 behind, 2 ahead, mergeable `clean`. Re-checked at the end of the review: unchanged.
- Commits: `3af14dff` feat(worker): per-installation monthly autofill budget (54 chars), `3d716fed` docs(changelog): add the installation autofill budget entry (59). PR title 54. All Conventional, all at most 72.
- CI on the head: CI 43453070880 succeeded, 5/5 check runs (lint-test, gitleaks, worker, deploy-scripts, image). The repo has no required status checks (branch protection lists none, and the ruleset only blocks deletion and force pushes) and no CodeQL analysis. Deploy Worker runs only on main.
- Gate: full. Diff read, the CI steps run locally, 10 probes, 19 mutants, a security review and a secret scan.
- Reviewed: 2026-11-13, 18:30 to 18:45 (UTC+2). Reviewer: Apotheke QA 2

## Checklist

- [x] Numstat `main...head` matches the claim: 8 files, +226/-4 (CHANGELOG 8, docs/api-v1.md 29, openapi.yaml +10/-1, budget.ts +32/-1, index.ts +25/-2, installation.ts 34 new, time.ts 9, installation-budget.test.ts 79 new).
- [x] No config change: `worker/wrangler.toml`, `worker/package*.json` and `.github/` are byte-identical to main. `wrangler deploy --dry-run` (default and `--env paid`) lists the same bindings as main (BUDGET, AI, RATE_LIMITER, 3 vars). No new `env.` reference and no new secret. Wrangler ran with no Cloudflare credentials in the environment.
- [x] Run as CI does (Node 22.23.3, `npm ci`): `npm run typecheck` (`wrangler types` and `tsc --noEmit`) passes, vitest 89/89 (main 84, plus 5 new), both dry runs and `scripts/local-info-check.sh` pass. Python: `ruff check` and `ruff format --check` are clean, and `pytest -q` (BACKEND=echo, FALLBACK_BACKEND=none) passes 574.
- [x] Auth comes first: `route()` calls `authorize()` (bearer, constant-time) before `suggest()` (`index.ts:285`). Unauthenticated or wrong-token requests with the headers get 401, with no model call and no count (probe). The id and limits are parsed before the body is read, and the count happens after the rate limiter and the body checks: a bad image is 415 and uncounted, and a rate-limited 429 is uncounted.
- [x] Id: at most 63 characters of `^[a-z0-9]+(-[a-z0-9]+)*$`, the same rule as apotheke `edge/src/installation.ts:18` for `do_name`. The DO name is `installation:<id>`, so it can't collide with `global`. 64 characters, `a--b`, `-a`, `Upper`, `a:b`, `a_b`, `../x`, non-ASCII and empty all get 400. The regex can't backtrack.
- [x] Limits: `^\d{1,15}$` after trim. `+5`, `5.0`, `1e3`, `-0`, `0x10`, `5 5`, empty, 16 digits and a duplicate header (`2, 999`) all get 400. `0005` is read as 5. Outer whitespace is already stripped by Headers.
- [x] Boundaries: hard 10 gives ok×7, a warning on calls 8 to 10, then 429. Hard 1 warns on call 1. Hard 0 is always 429, never counted and never calls the model. Hard 7 (soft 5.6) warns from call 6. An explicit soft overrides the default, and a soft above hard never warns.
- [x] Month and Retry-After: the key is `YYYY-MM` in UTC and only moves forward. Dec 31 23:59:59.999 to Jan 1 00:00 starts fresh. Retry-After is 1 at 2026-12-31T23:59:59.5Z, 2678400 at 2026-12-01T00:00Z and 1296000 at 2028-02-15 (leap year), all exact.
- [x] Atomicity: `reserveMonthly` reads and writes with no `await` in between, so DO input gates serialize it. 10 parallel calls at hard 5 give exactly 5×200 and 5×429, and the count is 5.
- [x] Storage: one row (`id = 1`) per installation, overwritten each month, so old months leave nothing behind. See N-2193 for the number of DOs.
- [x] No header: only `getByName("global")` is called (spy probe), and the limit headers are ignored. No module-level mutable state. A DO failure is a generic 503 `backend_unavailable` with no internals. 400 messages name only the header.
- [x] Mutants, 19 (each restored, tree clean afterwards): 13 killed, 6 survived.
  - Killed: hard `>=`→`>`, soft `>=`→`>`, 80%→90%, month key as the year, id validation removed, limit validation removed, model called despite the limit, Retry-After until midnight, Retry-After month+2, no rollover, soft header ignored, warning dropped, one DO shared by all installations.
  - 2 equivalent: the month key in local time (workerd runs in UTC) and the `trim()` removed (Headers strip whitespace).
  - 4 gaps (N-2192): an `await` between read and write, the no-header path calling a DO, refusals counted, and the 63-character cap removed. The QA probes kill the first three.
- [x] Docs: `docs/api-v1.md` adds "Per-installation budget" (headers, counting at admission including failures, 429/Retry-After, warning, 400, trust and service-binding note) and the 429 row. `openapi.yaml` adds `warning` and the `autofill_limit` 429. The CHANGELOG bullet is under `[Unreleased]` / Added and links #41. README is unchanged (it doesn't list Worker headers). Placeholders only.
- [x] Secret scan: gitleaks 8.30.1 with the repo config on `main..head` (2 commits) found no leaks. A manual grep of the 226 added lines found 0 secrets (the one hit is the prose "bearer token").
- [x] The `docs/qa/` folder exists in this repo. Commit this doc as `docs/qa/apotheke-autofill-service-pr41-installation-budget-qa.md`.

## Findings

- **L-2190 (open, latent, blocks the wiring PR, not this one):** the installation id and limits are trusted from headers behind the one shared `AUTOFILL_TOKEN`, on the still-public custom-domain routes (`installation.ts:25-33`, `index.ts:137`, `:285`). Any token holder can leave the header out to get no monthly limit, or name another installation with no limit header to burn its month. Once the global daily budget is exhausted, each burned call costs no model call (probe: 3×503, 0 model calls, count 3). Bounded by the per-IP rate limiter. Today no caller sends the header, so nothing is enforced and there's nothing to burn, hence Low. Before the hosted wiring relies on it, the headers must only be honoured from the edge Worker. Options: a separate secret or HMAC over id, limit and month for the hosted path, and the bearer held only by the edge Worker, never by tenant PHP. `api-v1.md:330` states the trust assumption but not this requirement.
- **L-2191 (open):** a call is counted before the global gate and the model (`index.ts:250` before `:264`/`:166`), with no refund. Calls refused by the operator's daily budget (`503`, model never called) and model or parse failures still use up the installation's month, and caller retries add up. This is documented (`api-v1.md`), so it's not a Medium. Fix: refund when the model was never called (`budgetError` and capacity paths), or reserve after `budget.reserve` succeeds.
- **N-2192 (open, tests):** `installation-budget.test.ts` has no concurrency test, no spy on the no-header path, no check that refusals aren't counted, and no 64-character id. Mutants M13, M12, M15 and M7 survive. Fix: add those four cases.
- **N-2193 (open):** one Budget DO per distinct id, never deleted. A token holder can create them freely (tiny: a 1-row `monthly` plus the empty `budget` table from the constructor), and a purged installation's DO stays. Fix: a delete method that apotheke purge calls, or accept it.

Also noted, from Coder: the count starts at admission, wiring is not done (DevOps), and a duplicated header is a 400.

0 High, 0 Medium, 2 Low and 2 Nit open, none blocking.

## Verdict

**PASS** at `3d716fed0bc44e760e83f1d2883789c7bb2b8192`, 0 behind main `73f28d7bdf2afe92e39d00ce0eaf3c9e44f1ade8`, CI 43453070880 green (5/5). Squash as "feat(worker): per-installation monthly autofill budget". L-2190 must be resolved in or before the DevOps wiring PR. L-2191 and the Nits can follow. Once this doc is committed as the last commit, the head changes, so the hash check and CI must be re-read on that commit.
