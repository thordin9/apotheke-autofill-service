# QA recheck: service #30, L-1 fix and N-1/N-2 docs

- **PR:** apotheke-autofill-service #30, `cursor/suggest-cpu-body-read` into `main` (draft).
- **Head:** `387f5ed49190eb2e7ef173c57ee2c3175eab6371`. **Previous head:** `560b98c92eab57344393038daf1b962d7f56269a`.
- **Date:** 2026-10-20, 13:26–13:34 EEST.
- **Scope:** the delta only, checked statically and with tests. No live calls, no Cloudflare credentials, 0 Neurons.
- **Verdict: PASS.** L-1 is fixed, N-1 and N-2 are fixed, and there are no new findings. The I-1 to I-6 notes in the first doc still apply and don't block the merge.

## Plan

1. Compare `560b98c...387f5ed`: per-commit scope, and the QA doc at the head byte-identical to the QA copy.
2. L-1: re-run the two injected bugs that got past the old tests against the head's tests. Both must fail. The clean head must pass 64/64.
3. The built `index.js` is byte-identical to `560b98c`'s.
4. The N-1/N-2 wording matches what I saw on raw workerd in the first pass.
5. gitleaks on the range, and no new hostnames.
6. CI on `387f5ed`, including the I-2 log line.

Method: codeload tarballs for `2a1a90e` and `387f5ed`, both verified with `git get-tar-commit-id`. Same lockfile as `560b98c`, Node 22.23.3, wrangler 4.151.2, loopback-only network namespace.

## Results

### 1. Scope: PASS
2 commits ahead of `560b98c`, 0 behind. 6 files, +172/−23.

| Commit | Message | Files |
|---|---|---|
| `2a1a90e` | docs(qa): add QA report for service PR 30 | `docs/qa/service-pr30-suggest-cpu-qa.md` only (added) |
| `387f5ed` | test: catch early body reads; clarify Content-Length docs | `worker/test/helpers.ts`, `README.md`, `CHANGELOG.md`, `docs/api-v1.md`, `docs/cloudflare-workers-deploy.md` |

`worker/src` is untouched. The QA doc at the head is 12560 B with sha256 `b7cba854d71cd388ca7ea631ba1608852d57e904c461383066a4f7af66a6749d`. `cmp` against the QA copy shows it's identical.

### 2. L-1: PASS
`unreadStream` now passes `{ highWaterMark: 0 }` as the second argument to `new ReadableStream`, so `pull` runs only when something reads the body. Nothing else in the helper changed.

- Clean head: vitest 64/64. Typecheck clean.
- `request.arrayBuffer()` before `authorize`: **"does not read the body when the bearer token is rejected" now fails**, along with 39 others.
- `body.getReader().read()` before the over-cap check: **"returns 413 for a Content-Length one byte over the cap without reading the body" now fails**, along with 4 others. On `560b98c` none of the no-read tests failed.
- Extra: reading a clone of the body before auth now also fails the 401 no-read test.

The PM's "auth before any body read" and "413 without reading the body" conditions are now each covered by a test that can fail.

### 3. Bundle: PASS
`wrangler deploy --dry-run` `index.js` is byte-identical between `560b98c` and `387f5ed` (sha256 `f3eaa0c8…01dc`).

### 4. N-1 / N-2 wording: PASS
README, CHANGELOG, `docs/api-v1.md` (400 and 413 rows, Worker notes) and `docs/cloudflare-workers-deploy.md` (body-read paragraph, 413 row) now say:
- Over HTTP, workerd rejects a malformed `Content-Length` (`abc`, `-1`, `+N`, comma-joined, differing duplicates, over 2^53) with a plain-text 400 before the Worker runs. That matches my runs, where hex, float and empty values got the same 400.
- Leading zeros, whitespace and identical duplicates are accepted, and chunked framing wins over a length. Matches.
- On a Request built in code, a shorter or longer body, or a length that isn't a single integer, gets the JSON 400 `invalid Content-Length` without buffering. Matches the code and tests.
- Over the network, bytes past the declared length aren't part of the body, and a short body waits for the client, then gets 400 `expected multipart/form-data`. Matches. In my runs the 400 came after the client half-closed.

Nothing in the new wording contradicts the code or the first-pass runtime table.

### 5. Secrets and hosts: PASS
gitleaks 8.30.1 on the 2 commits found nothing. No URLs, IPs or new hostnames in added lines. The production hostname count per doc is unchanged (README 1, api-v1 0, deploy 6, CHANGELOG 2), and the added QA doc has none.

### 6. CI: PASS
Run 39407929381 on `387f5ed` is 5/5 success: gitleaks, worker, lint-test, image and deploy-scripts, the last finishing at 13:30 EEST. The worker job ran 64 tests, and I-2 "Local runtime info check" printed bundle exports `Budget`, `default` and `local /healthz 200; /v1/info max_image_bytes 524288`.

## Findings
None new. L-1, N-1 and N-2 are closed.

## Next
PM re-scan of `387f5ed`, then deploy, DevOps version check, and a QA live check (≤ 3 calls) reporting `cpuTime` for a realistic photo against 10 ms. If this doc is committed, the head moves again, so I'll check the doc's hash at the new head.
