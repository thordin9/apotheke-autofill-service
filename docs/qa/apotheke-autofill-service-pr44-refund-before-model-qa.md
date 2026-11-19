# QA: apotheke-autofill-service #44, refund the month only when nothing was charged (L-2295, L-2298, N-2296, N-2297, N-2299, N-2300)

- PR: https://github.com/thordin9/apotheke-autofill-service/pull/44 (Coder), into `main`. Follow-up to #43.
- Reviewed head: `ff7d833b41a40093439bae1e10fe662d296af3c7`, two plain commits on `0f01f4d9`. `0f01f4d9` is the docs(qa) commit of the previous issue of this doc (sha256 `40288d8b…`), on the caught-up merge `f73f1ce8`. That merge is a plain merge of main `29ce7d38`, and its tree equals the `58d8f28d` content tree. 0 behind main `29ce7d38`, 9 ahead, `mergeable_state` clean.
- Commits: `4280b1a` fix(worker): refund the month only when no neurons were charged (63 chars) and `ff7d833` docs(changelog): align the refund bullets with the charge rule (62). Both are Conventional.
- CI on `ff7d833b`: CI 44453741091 succeeded, 5/5 (lint-test, gitleaks, worker, deploy-scripts, image). The repo has no required checks and no CodeQL.
- Gate: the content of `58d8f28d` was fully gated earlier. This issue fully re-reads the code delta `0f01f4d9..ff7d833b` (4 files, +31/−17), with CI steps run locally on Node 22.23.3, 17 mutants and a secret scan.
- Reviewed 2026-11-19, 05:34 to 06:00 (UTC+2). Reviewer: Apotheke QA 2

## Checklist

- [x] Scope vs main: `index.ts` +3/−2, the test +57/−3, CHANGELOG +7/−1, `api-v1.md` +7/−3, plus `0f01f4d9`'s copy of this doc. `budget.ts`, `wrangler.toml`, `package*.json` and `.github/` are unchanged from main.
- [x] As CI (Node 22.23.3, `npm ci`): typecheck passes, vitest 102/102, both dry runs and `local-info-check.sh` pass.
- [x] L-2298 rule: `refundMonth` returns early when `timing.neurons > 0`, and nothing else in refund changed. The order in `attempt()`:
  - `budget.reserve` holds the 30-neuron estimate in `reserved` before `AI.run`.
  - On a returned answer, `timing.neurons += actual` after `settle(actual)`.
  - On any throw after the reserve (model error, 3036, 3040, 3006, an HTTP status, an error code in a returned answer), the `catch` runs `settle(estimate)` and then `timing.neurons += 30`.
  - So a throwing run keeps the count. A refusal before any reserve (the global gate's `budgetError`, the test-mode refusal) leaves `neurons` at 0 and is refunded. Success always has neurons > 0, so it can't refund.
  - The public path is unchanged (no installation, so it returns at once).
- [x] Tests: the model-down case (throws after the reserve) now expects 429 on the next call. Parse failure and repaired success still keep the count. N-2299: one answer (21.37), then the repair is refused by a budget of 40 (`21.37 + 30 > 40`), giving 503 `daily capacity is exhausted` with 1 model call and the count at 1. The failure-before-charge tests (seven daily-budget 503s, refund once, refund error, reservation month) still refund.
- [x] Mutants, 17 (tree clean afterwards):
  - 15 killed: `aiCalls > 0` (killed on rerun; a stale first run was discarded), `aiCalls > 1`, `neurons > 30`, `neurons > 21.37`, the inverse, no check, no `neurons += estimate` in the `catch`, refund twice, refund by 2, refund error rethrown, refund at `Date.now()`, no refund, an unguarded refund on success, cross-month refund, negative refund and a 50 ms await in reserve.
  - Equivalent: the guarded refund on success (blocked by `neurons > 0`) and a no-op control.
- [x] Docs: `api-v1.md`, #43's [Unreleased] bullet and #44's bullet all state the nothing-charged rule and that the refund is best effort, and they match the code. The CHANGELOG diff against main is one hunk inside [Unreleased], and released sections are byte-identical to main.
- [x] Secret scan: gitleaks 8.30.1 with the repo config on the 2 commits found 0 leaks. Added lines matching secret patterns: 0.

## Findings

- **Closed:** L-2295, L-2298 (the PM's ruling: no refund once anything is charged), N-2296, N-2297, N-2299 and N-2300.
- **N-2301 (optional hardening):** two leftover paths refund after the model ran.
  - (a) `budget.settle` throws, whether after an answer or in the `catch` after a throw, so `neurons` stays 0.
  - (b) Workers AI reports usage of 0 neurons or 0 tokens.
  - Neither can be driven by a tenant. (a) needs the global Budget DO to fail, which is infrastructure, and then the estimate stays held in `reserved`, so the daily budget fails closed. (b) is platform-reported, and an image prompt always has tokens; missing usage already falls back to the 30 estimate.
  - Worst case is one free retry per such fault. Coder's `charged` flag, set after a successful `budget.reserve`, would close both paths.
- **Still open:** N-2193 (purge cleanup).

0 High, 0 Medium, 0 Low and 1 Nit open (next free id: 2302).

## Verdict

**PASS** at `ff7d833b41a40093439bae1e10fe662d296af3c7`, 0 behind main `29ce7d38`, CI 44453741091 green. Highs and Mediums block; none are open. The next commit after `ff7d833b` must be the docs(qa) commit of this doc (`docs/qa/apotheke-autofill-service-pr44-refund-before-model-qa.md`, byte-exact), replacing `0f01f4d9`'s copy. Then come the hash check and CI on that head. If main moves first, a merge-only read and a reissued doc are needed. Squash as "fix(worker): refund the month only when no neurons were charged" (63 characters, 69 with " (#44)"). The PR title still describes the old model-call rule.
