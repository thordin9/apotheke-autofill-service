# QA: apotheke-autofill-service #45, installation budget purge and charged-only refunds (A1: N-2301, N-2193)

- PR: https://github.com/thordin9/apotheke-autofill-service/pull/45 (Coder), into `main`. A1 of the hosted autofill follow-ups: closes N-2301 (refund keyed on `timing.neurons`) and N-2193 (no purge cleanup for an installation's budget).
- Reviewed head: `387983a0e4e4ae9b069b6139fbcc2b959b183a55`. 0 behind main `3faec1e9`, 4 ahead, mergeable `clean`, no merge commits. Full gate at `1feddfad`; since then `387983a0` ("fix(worker): address review notes on budget purge", 3 files), read in full.
- Commits: `7428eb9` fix(worker): keep the month once the daily budget admitted a call (65 chars), `a097829` feat(worker): purge one installation's budget via HostedAutofill (64) and `1feddfa` docs(changelog): note installation budget purge and refund rule (63). All are Conventional and at most 72.
- CI on the head: CI 44772590081 succeeded, 5/5 (earlier, on `1feddfad`: 44769933435) (lint-test, gitleaks, worker, deploy-scripts, image). The repo has no required checks and no CodeQL. Deploy Worker runs only on main.
- Gate: full, security-focused. Diff read, the Worker CI steps run locally, 18 mutants, workerd probes of the purge path on both entrypoints, docs and a secret scan.
- Reviewed 2026-11-20, 18:11 to 18:20 (UTC+2). Reviewer: Apotheke QA 2

## Checklist

- [x] Scope: 7 files, +228/−10: `worker/src/{index,budget,installation}.ts`, `worker/test/installation-budget.test.ts`, the new `worker/test/installation-purge.test.ts`, `docs/api-v1.md` and CHANGELOG. `wrangler.toml`, `package*.json`, `.github/`, `openapi.yaml` and Python are unchanged. Both dry runs list the same bindings as main.
- [x] Refund rule (N-2301): `charged` is a `let` inside `suggest()`, so it is per request; there is no module-level state. It is set synchronously right after `if (!gate.ok) throw`, with no await between the reserve result and the flag. Refunded: test-mode refusal, a reserve that throws, a daily refusal. Kept: settle failure after an answer or in the catch, 0 reported neurons, a model error, a parse failure. A repair attempt refused by the daily budget after an admitted first attempt keeps the count, which is right (one count per suggest). `refundMonth` runs once from the single catch and clears `monthReserved` before its await, so there is no double refund, and it does nothing when the month was never reserved. Nothing between the month reserve and the `try` can throw.
- [x] Purge reachability: the path is matched only when `hosted` is true; on the default export it falls to the existing 404 before `authorize()`. Probed on the public routes with the token: DELETE, GET and POST all 404, and nothing is deleted (test without a token too).
- [x] Purge scoping: the id must pass the shared `isInstallationId` (≤63 of `^[a-z0-9]+(-[a-z0-9]+)*$`) before any storage call, and the DO name is always `installation:<id>`. `global` purges `installation:global` (200, `deleted: 0`) and the global daily snapshot was byte-identical before and after. `%67lobal`, `a%2Fb`, upper case and over-long ids are 400. `/v1/installations/x/budget/`, `//v1/...`, `/V1/...` and `x/../budget` are 404. A query string is ignored (`?y=1` purges `x`), which is harmless on a binding-only path. `primary` is 400 and its count stays.
- [x] Method and errors: GET, POST, PUT, HEAD and OPTIONS are 405 with `Allow: DELETE`. A throwing `purge()` gives 503 `backend_unavailable` with the generic message; the thrown text does not reach the body. The 405 and 404 bodies match the existing routes.
- [x] Count: `purge()` counts `budget` and `monthly` rows (each table only if it exists, so a repeat on a live instance after `deleteAll()` works), then calls `deleteAll()`. For an installation DO that is 0 or 1. After a purge, `reserveMonthly` recreates `monthly` lazily; installation DOs never use the constructor-only `budget` table, so the dropped table is not needed.
- [x] `isInstallationId` refactor: the regex and length are byte-identical to the old inline check in `installationBudget`, so header validation is unchanged (existing tests pass; mutant M17 on the shared helper is killed).
- [x] Run as CI does (Node 22.23.3, `npm ci`): `npm run typecheck` passes, vitest 112/112 (5 green runs; one earlier run on the box had 28 failures under load average ~12 and passed unchanged on rerun, so it was local contention, not the PR), both dry runs pass and `local-info-check.sh` passes. At `387983a0`: typecheck passes and vitest 113/113.
- [x] Mutants: 18 run, 17 killed. Killed: M1 `timing.neurons > 0` restored (2 tests, as claimed), M2 flag never set, M3 flag set before reserve, M4 flag ignored (refund always), M5 refund on settle failure, M6 primary refusal dropped, M7 loose id regex, M8 count after `deleteAll()`, M9 purge of `getByName(id)` without the prefix, M10 `Allow` dropped, M11 path exposed on the public fetch, M13 id validation dropped, M14 `monthly` not counted, M15 only PUT refused, M16 `charged` at module level, M17 64-character ids, M18 `DELETE FROM monthly` in place of `deleteAll()`. Survived at `1feddfad`: M12, the 503 turned into 500 `internal` (N-2451). `git diff --quiet` after each restore.
- [x] Delta `387983a0`: CHANGELOG split into an Added bullet (purge) and a Fixed bullet (charged-only refunds), comment reworded, one new test. M12 re-run at `387983a0`: killed by the new 503 test (restored, `git diff --quiet`).
- [x] Docs: `api-v1.md` matches the code for the refund rule and every purge answer. Released CHANGELOG sections are byte-identical to main, and both #45 bullets carry the full PR link.
- [x] Title and release: "feat(worker): installation budget purge and charged-only refunds" is 64 characters, 70 with " (#45)". The repo has no release automation (no release workflow or config; `[Unreleased]` is curated by hand), so `feat` only labels the squash; it is the right type for a new endpoint.
- [x] Secret scan: gitleaks 8.30.1 with the repo config found 0 leaks on `main..head` and 0 on the full history (CI's mode). No account ids, tokens or hostnames were added.

## Prior findings

- **N-2301 closed.** The refund follows whether the daily budget admitted a call, not the reported neurons.
- **N-2193 closed on the service side**, effective once the edge calls the purge on installation purge.
- Accepted as stated: a lost reserve reply refunds (at most one free call per lost reply), a purge racing an in-flight suggest can leave that call's count, and the binding is the only access control, as for hosted suggest (N-2293).

## New findings

- **L-2450 (closed in `387983a0`):** The CHANGELOG bullet is under [Unreleased] / Fixed, but the purge endpoint is a new capability and `[Unreleased]` already has an `### Added` section (with #41's monthly budget). Split it: the `DELETE /v1/installations/<id>/budget` bullet under Added, and the refund rule under Fixed, each with the #45 link.
- **N-2451 (closed in `387983a0`):** No test covers the purge's 503 (M12 survived). Add one with a `BUDGET` stub whose `purge` throws, asserting 503 `backend_unavailable` and the generic message.
- **N-2452 (closed in `387983a0`):** The comment "The primary installation is never limited and has no purge" is only half true: hosted suggest still accepts `x-apotheke-installation: primary` and counts it (the new purge test relies on that). Reword it to say the purge refuses `primary`.

0 High, 0 Medium, 0 Low and 0 Nit open; L-2450, N-2451 and N-2452 are closed (next free id: 2453).

## Verdict

**PASS** at `387983a0e4e4ae9b069b6139fbcc2b959b183a55`, 0 behind main `3faec1e9`, CI 44772590081 green. Squash as "feat(worker): installation budget purge and charged-only refunds (#45)" (70 characters). Commit this doc byte-exact as the last commit (`docs/qa/apotheke-autofill-service-pr45-budget-refund-purge-qa.md`), then re-read the hash and CI on that head.
