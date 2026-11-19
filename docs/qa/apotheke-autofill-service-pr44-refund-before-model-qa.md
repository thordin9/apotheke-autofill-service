# QA: apotheke-autofill-service #44, refund the month only when no model call returned (L-2295, N-2296, N-2297)

- PR: https://github.com/thordin9/apotheke-autofill-service/pull/44 (Coder). Follow-up to #43. Gated as the delta `f1155052...58d8f28d` (#43's final head to this head).
- Reviewed head: `f73f1ce8ef477dbeef2436e9e4ffb4c9eb7e0664`, a plain merge of main `29ce7d38` (the #43 squash) into `58d8f28dc239a37d6e4dacca650eff207825a3ac`, where the content was gated as the delta from #43's head `f1155052`. Merge-only read: parents are exactly `[58d8f28d, 29ce7d38]`. The `29ce7d38` tree equals the `f1155052` tree, and the head tree equals the `58d8f28d` tree. So resolving the declared conflicts (CHANGELOG, `api-v1.md`, `index.ts`, the test) to the branch side adds nothing beyond #44's own delta. main..head numstat is 4 files, +58/−7, identical to `f1155052..58d8f28d`. CHANGELOG released sections are byte-identical to main. 0 behind, 6 ahead, `mergeable_state` clean.
- Commits: `0a8e21d` fix(worker): refund the month only when no model call returned (62 chars) and `58d8f28` docs(changelog): note the no-model-call refund rule (#44) (57). Both are Conventional.
- CI on `f73f1ce8`: CI 44452512734 succeeded, 5/5 (lint-test, gitleaks, worker, deploy-scripts, image). It was also green on `58d8f28d` (44451716830). The repo has no required checks and no CodeQL.
- Gate: full on the delta. Diff read, CI steps run locally on Node 22.23.3, 9 mutants, a failure-path review and a secret scan.
- Reviewed 2026-11-19, 05:34 to 05:45 (UTC+2). Reviewer: Apotheke QA 2

## Checklist

- [x] Scope: 4 files, +58/−7 vs `f1155052` (`index.ts` +3/−2, the test +44/−2, CHANGELOG +5, `api-v1.md` +6/−3). `budget.ts`, `wrangler.toml`, `package*.json` and `.github/` are unchanged.
- [x] As CI (Node 22.23.3, `npm ci`): typecheck passes, vitest 101/101 (98 + 3 new; the head tree is identical), both dry runs and `local-info-check.sh` pass.
- [x] L-2295: the only code change is `refundMonth` returning early when `timing.aiCalls > 0`. `aiCalls` goes up right after `AI.run` returns, so a parse failure, a failed repair after one answer, an error code inside a returned answer, or a `settle` failure after an answer all keep the count. A gate refusal (`budgetError`), a test-mode refusal and an `AI.run` that throws (model error, 3036, 3040, 3006, an HTTP error) are still refunded. No other refund path changed. The public path still returns early (no installation), and its behaviour and DO use are unchanged.
- [x] Tests: the L-2191 test now expects 429 after a two-call parse failure (`parse.calls` is 2). The model-reject and seven daily-budget-503 cases still refund. The new N-2296 tests cover exactly one refund with 2 counted, a throwing refund that returns the original `daily capacity is exhausted` 503 with `retry-after` and keeps the count, and a refund at the reservation time on 2025-01-31T23:59:59.999Z.
- [x] Mutants, 9 (tree clean afterwards), 8 killed:
  - My earlier survivors: double refund without the flag, refund by 2, a refund error rethrown, and refund at `Date.now()`.
  - The inverse (refund only when `aiCalls === 0` is false), no `aiCalls` check, a `timing.neurons > 0` rule (the model-reject test pins the PM's rule), and no refund at all.
  - Survives: `aiCalls > 1` (N-2299).
- [x] Docs: `api-v1.md` now says what's refunded (global-budget refusal, model error), what keeps the count (a failure after the model answered, success) and that the refund is best effort (abort or refund failure). This matches the code.
- [x] CHANGELOG: a separate `### Fixed` bullet links the full #44 URL. Released sections are byte-identical to `f1155052` and to main. See N-2300.
- [x] Secret scan: gitleaks 8.30.1 with the repo config on the 2 commits found 0 leaks. Added lines matching secret patterns: 0.

## Findings

- **L-2295 closed:** a failure after a model call returned keeps the month. **N-2296 closed:** all four gaps are pinned. **N-2297 closed.**
- **L-2298 (new, open):** `aiCalls` counts only calls that returned, so an `AI.run` that throws is refunded even though the daily budget was charged the estimate (30 neurons, settled at estimate in `attempt()`). The image check is magic bytes only (`sniffMime`), so a tenant can probably make `AI.run` throw on purpose: an `FF D8 FF` header with a corrupt body, or extreme dimensions within the byte cap. Each throw would then use shared daily budget without using the month. I didn't verify this against live Workers AI. It's bounded by 10/min per installation and the daily budget, but that is enough to drain it: free 6000 is 200 throws (about 20 minutes), paid 30000 is 1000 (about 100 minutes). One tenant could then deny autofill to all tenants every day, the same impact as L-2295. Limits are off and the binding is absent, so it's Low. Stricter fix: refund only when nothing was charged to the daily budget (`timing.neurons === 0`, i.e. only gate or test-mode refusals). That changes the PM's `aiCalls === 0` rule and the model-reject test.
- **N-2299:** Test gap. No case has exactly one returned call followed by a failure (e.g. first reply `"nope"`, then the repair rejects, or an error code inside a returned answer). `aiCalls > 1` survives.
- **N-2300:** In [Unreleased], #43's bullet still says a parse failure is refunded, which contradicts #44's bullet. Amend #43's bullet (drop "or parse fails") in the catch-up, or fold #44 into it.

0 High, 0 Medium, 1 Low and 2 Nit open (next free id: 2301).

## Verdict

**PASS** at `f73f1ce8ef477dbeef2436e9e4ffb4c9eb7e0664`, 0 behind main `29ce7d38`, CI 44452512734 green. Highs and Mediums block; none are open. Settle L-2298 (stricter rule or explicit acceptance) before the edge `AUTOFILL` binding is added. N-2299 and N-2300 can follow. This doc lands byte-exact as the last commit (`docs/qa/apotheke-autofill-service-pr44-refund-before-model-qa.md`), then the hash check and CI on that head. Squash as "fix(worker): refund the month only when no model call returned" (62 characters, 68 with " (#44)").
