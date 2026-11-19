# QA: apotheke-autofill-service #43, refund failed monthly installation calls (L-2191, N-2192, N-2292)

- PR: https://github.com/thordin9/apotheke-autofill-service/pull/43 (Coder), into `main`. Follows #41 (budget) and #42 (`HostedAutofill`). The edge side is apotheke #327.
- Reviewed head: `b15a37e1948ced05225bd12c1ed44e7abbc65339`. 0 behind main `1c98f674`, 2 ahead, mergeable `clean`.
- Commits: `3f5ab8c` fix(worker): refund failed monthly installation autofill calls (62 chars) and `b15a37e` docs(changelog): link the failed-call refund entry to #43 (57). Both are Conventional.
- CI on the head: CI 44448258847 succeeded, 5/5 (lint-test, gitleaks, worker, deploy-scripts, image). The repo has no required checks and no CodeQL.
- Gate: full. Diff and the whole of `suggest()` read, CI steps run locally, 14 mutants, a failure-path review and a secret scan.
- Reviewed 2026-11-19, 05:18 to 05:30 (UTC+2). Reviewer: Apotheke QA 2

## Checklist

- [x] Scope: 5 files, +152/−15 (`budget.ts` +15, `index.ts` +28/−10, test +93, CHANGELOG +11/−1, `api-v1.md` +5/−4). `wrangler.toml`, `package*.json` and `.github/` are byte-identical to main. Both dry runs list the same bindings as main.
- [x] As CI (Node 22.23.3, `npm ci`): typecheck passes, vitest 98/98, both dry runs and `local-info-check.sh` pass.
- [x] Atomic: `reserveMonthly` and `refundMonthly` use only synchronous `sql.exec`, with no `await` between read and write, so each runs as one step in the DO. `refundMonthly` never goes below 0 and only acts on the stored month equal to the reserve time's month. A rollover between reserve and refund (the row is already on the next month) is a no-op and never touches the new month. It uses the same `now` as the reserve. `x-autofill-now` is read only in test mode (`AUTOFILL_REQUIRE_AI_STUB`, not set in any config).
- [x] Exactly once: `monthReserved` is set only after a successful reserve and cleared before the refund `await`. The only call site is the single `catch` around the model, judge and response. Refused before admission, and not counted: rate limit 429, body, part and context 400/413/415, the reserve 503 and the hard 429 (all thrown before `monthReserved`). Refunded after admission: test-mode refusal, the global gate (`budgetError`, 3036), capacity 3040, 3006 413, model and HTTP errors, a `settle` failure, both parse failures and any exception building the answer. Success paths never refund. A repaired success counts once.
- [x] Not refunded: a client abort, wall-time or CPU kill, or a `refundMonthly` error leaves the count. That fails closed and is accepted (N-2297 for the docs).
- [x] Warning: still `used + 1 >= soft` at reserve, and only added on success. In-flight reservations that are later refunded can at most bring a warning one call early. The public path is unchanged: with no installation, `refundMonth` returns at once and no installation DO is opened (test `:177`).
- [x] Tests: `:110` 7 global-budget 503s at hard 7 leave `used 0`, then success is `used 1`. `:133` covers a model reject, a two-step parse failure and a repaired success. `:152` covers double refund and month boundary at the DO level. `:162`/`:177`/`:192` cover N-2192.
- [x] Mutants, 14 (tree clean afterwards), 8 killed: no refund, no 63-char cap, negative refund, a 50 ms await in reserve, refund on success, cross-month refund, refund only for some errors, and reserve after the first model call. 6 survive:
  - M05 (`await Promise.resolve()` in reserve; vitest's DO runner) and M08 (flag cleared after the await; single sequential call site) are equivalent.
  - M07 (flag removed and refunded twice), M10 (refund by 2), M12 (a refund error rethrown, masking the real error) and M13 (refund with `Date.now()`) are gaps (N-2296).
- [x] CHANGELOG: the new `### Fixed` bullet links the full #43 URL (`:15`), and the #42 bullet now links the full #42 URL (`:65`). Released sections are byte-identical to main. `api-v1.md` says later failures are refunded.
- [x] Secret scan: gitleaks 8.30.1 with the repo config on `main..head`: 0 leaks. Added lines matching secret patterns: 0.

## Findings

- **L-2191 closed** for its stated harm: global-budget, capacity and pre-model failures no longer use up the month.
- **N-2192 closed:** concurrency, no-header spy, refusals not counted and 64 characters are all pinned. **N-2292 closed.**
- **N-2193 still open** (purge cleanup, skipped).
- **L-2295 (new):** the refund also covers failures after the model ran: parse failures after 2 model calls, and errors after a call. Their neurons are charged to the global daily budget (settled at actual), but not to the installation's month. So photo content that makes the model answer outside JSON gives a tenant unlimited model calls that never count. The monthly limit no longer caps a tenant's model spend, and one tenant can drain the shared daily budget. It's bounded by 10/min per installation and the daily budget. Limits are off and the binding is absent, so it's Low. L-2191 asked to refund only when the model was never called. Fix: refund only when `timing.aiCalls === 0`, or count model-called failures. Decide before the `AUTOFILL` binding is added.
- **N-2296:** Test gaps (M07, M10, M12, M13). No hosted test refunds from `used >= 2` or with a second in-flight reservation, has a refund DO that throws (the original error should be returned and the count kept), or covers reserve and refund across a month boundary through `suggest()`.
- **N-2297:** `api-v1.md` says failures "are refunded" without qualification. Say it's best effort: an aborted or killed request, or a refund error, still counts (fails closed).

0 High, 0 Medium, 1 Low and 2 Nit (next free id: 2298).

## Verdict

**PASS** at `b15a37e1948ced05225bd12c1ed44e7abbc65339`, 0 behind main `1c98f674`, CI 44448258847 green. Highs and Mediums block; none are open. L-2295 should be settled (narrow the refund, or accept it explicitly) before hosted autofill is turned on. Squash as "fix(worker): refund failed monthly installation autofill calls" (62 characters, 68 with " (#43)"). Commit this doc byte-exact as the last commit (`docs/qa/apotheke-autofill-service-pr43-budget-refund-qa.md`), then re-read the hash and CI on that head.
