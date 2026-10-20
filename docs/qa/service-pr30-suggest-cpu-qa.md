# QA: service #30, suggest CPU (buffered body read, cached token digest)

- **PR:** apotheke-autofill-service #30, `cursor/suggest-cpu-body-read` into `main` (draft).
- **Head:** `560b98c92eab57344393038daf1b962d7f56269a`. **Base:** `175ae76775d70e55daf95d1f661e17c42049b79b` (#29 merge).
- **Commits:** `fa4a19e` (body read: `http.ts`, `helpers.ts`, `limits.test.ts`), `560b98c` (token digest: `auth.ts`, `auth.test.ts`, 4 docs). 9 files, +416/−19.
- **Date:** 2026-10-20, 12:52–13:22 EEST.
- **Scope:** stub pass only. Local runtime with a stub AI, no live Worker calls, no Cloudflare credentials, 0 Neurons. The live cpuTime check comes after deploy.
- **Verdict: PASS, with L-1 to fix before merge.** The Worker code does what the PR says and meets all four PM conditions on the runtime. One test gap (L-1): the two "body not read" assertions can't fail, so the auth-first ordering has no working test. The fix is one line in a test helper and doesn't change the built Worker. Two N, six I.

## Plan

1. Commits and files against `175ae76`. Tarballs verified with `git get-tar-commit-id`.
2. Code review: auth before any body read, over-cap `Content-Length` rejected before the read, missing length still streams with the abort, cache keyed on the secret value, `timingSafeEqual` on two 32-byte digests, and no timing path that depends on the presented token.
3. Mutation tests on a copy of `worker/` to check that the new tests catch each way the change could break.
4. Local runtime, base and head side by side. Raw `workerd serve` (from the lockfile) with the dry-run bundle behind a QA-only wrapper that injects a stub AI and an allow-all rate limiter. A dummy token in the scratch config only. Loopback-only network namespace. Hand-built HTTP/1.1 requests over raw sockets:
   - `Content-Length` at the cap (1048576) and cap+1, sent and withheld. Non-numeric, negative, `+N`, leading or trailing space, leading zeros, > 2^53, hex, float, empty, comma-joined, duplicate headers (same and different), CL plus chunked, CL 0 with a body, chunked within and over the cap.
   - Body longer than declared, shorter than declared (then half-close), and 2 MiB sent with CL 1000.
   - 401 (wrong or missing token) and 413 (declared over cap) answered with the body withheld.
   - Image part at 524288, 983040 and 983041. 415, 400 non-multipart, GET routes, 404, 405.
   - Empty and missing `AUTOFILL_TOKEN` on both builds, and a restart with a rotated token.
   - Repeated under `wrangler dev --local` to see what its dev proxy changes.
5. Contract diff of every status and body, base against head. Bundle diff of `wrangler deploy --dry-run` output.
6. Before/after handler timing, 220 KB-class photo, ~400 KB and ~900 KB noise images. 8 warmups plus 50 runs, 3 rounds, fresh isolate per round.
7. vitest, typecheck, dry run, ruff, shellcheck (CI scope), gitleaks on the range, scan of added lines for hosts and tokens, doc accuracy, cpuTime note, CI and the I-2 log.

## Results

### 1. Commits and files: PASS
Per-commit files match the PR (above). `worker/src` changes only `auth.ts` (`digestServerToken`, `tokensMatch`) and `http.ts` (`readLimitedBody`).

### 2. Code review: PASS
- **Ordering:** `route` calls `authorize` before `suggest`. Inside `suggest`, the rate limiter and `limits()` run, then `readLimitedBody`. A missing or empty secret is 503 and a bad token is 401, both before any body access.
- **Known length:** `/^\d+$/`, then `Number(declared) > maxBytes` gives 413 before `arrayBuffer()`. After the read: more than the cap is 413, and a byte count that doesn't match is 400 `invalid Content-Length`. An `arrayBuffer()` failure is 400 `expected multipart/form-data`, the same mapping as the stream path.
- **No length:** unchanged reader loop, `cancel()` and 413 once past the cap.
- **Cache:** one module-level pair `(cachedToken, cachedDigest)`, written together after the digest resolves, so concurrent calls with different secrets can't mix a token with another token's digest. The presented token is always hashed, with no early return. The cache-hit check compares the server secret with itself, never with client input, so a hit or miss reveals nothing about the presented token. `timingSafeEqual` always gets two 32-byte SHA-256 digests.

### 3. Mutation tests: PASS except L-1
64/64 pass on the head. Each mutant was run on the full suite (`mutants.txt`).

| Mutant | Caught by |
|---|---|
| Drop post-read cap check | over-cap body with small CL |
| Drop length-mismatch check | longer, shorter, CL 0 |
| Mismatch only when shorter | longer, CL 0 |
| Always stream (old path) | at-cap buffered, longer, shorter, CL 0 |
| Buffer with no CL | chunked/stream-cap tests (pool crash) |
| Non-numeric CL to streaming | non-numeric, negative, comma/dup |
| Cache ignores key | rotation, cache-count |
| No cache | cache-count |
| Plain compare instead of `timingSafeEqual` | cache-count, missing-token compare |
| Empty secret not 503 | fails-closed tests |
| `arrayBuffer()` before the over-cap check | over-cap test (via the `arrayBuffer` spy only) |
| **`request.arrayBuffer()` before `authorize`** | **not** by "does not read the body when the bearer token is rejected" (other tests fail only through side effects) |
| **`body.getReader().read()` before the over-cap check** | **nothing** among the no-read tests (4 other tests fail) |

Cause (probe, `probe-unreadstream.txt`): `unreadStream` uses a default `ReadableStream`, high-water mark 1, so `pull` runs once at construction and enqueues the whole body. The count is 1 before the request, and a full read leaves it at 1. `tracked.reads()` can never change. With `new ReadableStream({...}, { highWaterMark: 0 })` the suite stays 64/64, and both mutants fail their no-read tests (`mutants-fixedhelper.txt`).

Extra QA tests (scratch, not in the PR) pass: A→B→A rotation in one isolate, 200 concurrent checks alternating between two secrets, prefix and case variants are 401, and an empty secret after a cached one is 503.

### 4. Local runtime (raw workerd 2026-10-19): PASS
Base and head give the same status and body on all 32 socket cases (`outwd/compare-probe.txt`). Which layer decides:

| Case | Result (both builds) | Layer |
|---|---|---|
| CL = 1048576, real body | 200 | Worker, buffered on head |
| CL = 1048577, body sent / **withheld** | 413 `image exceeds 1048576 bytes`, **sent before any body byte** | Worker, no read |
| Wrong or missing token, body **withheld** | 401, **sent before any body byte** | Worker, no read |
| CL within cap, body withheld | waits (needs the body) | — |
| `abc`, `-1`, `+N`, `0x10`, `N.0`, empty, `N, N`, `N, M`, > 2^53, duplicate different | 400 text/plain `invalid content-length parsed` | workerd HTTP parser, Worker never runs |
| ` N` / `N ` (OWS), `000N`, duplicate identical | accepted, 200 | workerd normalises |
| CL + `Transfer-Encoding: chunked` | chunked wins, CL dropped, 200 | workerd |
| CL 0 with a body | 400 `expected multipart/form-data`, body bytes not read | framing |
| Chunked within cap / 2 MiB over cap | 200 / 413 at 1048576 (stream abort) | Worker stream path |
| Body longer than CL (+100) | first CL bytes only, 200 | framing |
| CL 1000 with 2 MiB sent | 400 `expected multipart/form-data`, 1000 bytes read | framing |
| Body shorter than CL by 100 | no response until the client half-closes (4 s here), then 400 `expected multipart/form-data` | runtime waits, Worker maps the read error |
| Image 524288 / 983040 / 983041 | 200 / 200 / 413 `image exceeds 983040 bytes` | Worker |
| Text image, JSON body | 415 / 400 unchanged | Worker |
| Empty or missing `AUTOFILL_TOKEN` | `/healthz` 503 `degraded`, `/v1/info` and `/v1/suggest` 503 `service is not configured`, `/nope` 404 | Worker |
| Restart with a rotated token | old 401, new 200 | Worker |

Under `wrangler dev --local` the dev proxy holds requests until the body arrives, so withheld-body cases time out on both builds there. A short body after half-close becomes a dev-proxy 500 page. Those are artefacts of wrangler dev, which is why raw workerd is the evidence. No processes were left over.

### 5. Contract and bundle: PASS
GET routes (`/healthz`, `/v1/info` shape with `max_image_bytes` 524288, 401 with `WWW-Authenticate: Bearer`, 404, 405) are byte-identical, base against head. Every 400, 401, 413, 415 and 503 code and body seen on the wire is unchanged. `bundle.diff` (52 lines) touches only `digestServerToken`/`tokensMatch` and `readLimitedBody`.

### 6. Timing: PASS (direction and size match the PR)
Raw workerd, stub AI, `performance.now()` around `worker.fetch` (1 ms steps), median of 3 rounds:

| Image (multipart body) | 175ae76 p50 / mean | 560b98c p50 / mean | Mean saved |
|---|---|---|---|
| photo 240257 B (240389) | 2 / 2.18 ms | 1 / 1.33 ms | 0.85 ms |
| noise 412908 B (413040) | 3 / 3.15 ms | 1 / 1.45 ms | 1.71 ms |
| noise 900223 B (900355) | 4 / 4.64 ms | 2 / 2.00 ms | 2.64 ms |

The savings match the PR's body-read deltas (0.80 / 1.32 / 2.54 ms). Absolute times here are lower than the PR's 6/7/9 → 5/5/5 because this harness has no wrangler proxy and a cheaper stub. Under `wrangler dev` the p50 went from 3/4/6 to 3/3/4 ms. None of this is Workers Logs `cpuTime`, and the PR description says so.

### 7. Checks: PASS
- **Local:** vitest 64/64 (base 52, so 12 new). Typecheck clean. Dry run OK.
- **Linters:** ruff 0.17.1 check and format clean. shellcheck on the CI scope plus `local-info-check.sh` clean, as on base.
- **Secrets and hosts:** gitleaks 8.30.1 on the 2 commits found nothing. The only host in added lines is `autofill.example` in tests. The production hostname count in the 4 docs is unchanged (9).
- **CI run 39403711409 on `560b98c`:** 5/5 success (lint-test, gitleaks, worker, deploy-scripts, image). The worker job ran 64 tests and the dry run. I-2 "Local runtime info check" printed bundle exports `Budget`, `default` and `local /healthz 200; /v1/info max_image_bytes 524288`.

## Findings

- **L-1, tests:** the no-read assertions are vacuous. `unreadStream` counts `pull` calls on a default stream that pulls once at construction, so `tracked.reads()` never changes. The 401 test guards nothing, and the PM's "auth stays before any body read" has no working test. The over-cap 413 test is guarded only by the `arrayBuffer` spy, not against other reads. Fix: pass `{ highWaterMark: 0 }` as the second argument to `new ReadableStream` in `unreadStream` (verified: 64/64 pass and both mutants are caught). Optionally also assert `request.bodyUsed === false`. Test-only, so the bundle doesn't change.
- **N-1, docs:** `docs/api-v1.md`, `docs/cloudflare-workers-deploy.md` and the CHANGELOG say non-numeric, negative or combined `Content-Length` gets the Worker's JSON 400 `invalid Content-Length`. Over HTTP/1.1 on workerd these get a plain-text 400 from the runtime before the Worker runs. Duplicate identical lengths, leading zeros and surrounding spaces are accepted. Suggest "the runtime normally rejects these; the Worker also returns 400 `invalid Content-Length` if one reaches it."
- **N-2, docs:** "A body shorter or longer than the declared length is 400 `invalid Content-Length`" only holds for Requests built in code. On the wire, extra bytes aren't part of the body. A short body waits until the client gives up, then gets 400 `expected multipart/form-data`.
- **I-1:** the cap still holds on the buffered path. On the wire `arrayBuffer()` reads at most the declared length (≤ cap). Only an in-code Request can buffer more, and the post-read check still rejects that with 413.
- **I-2:** a short body ties up the connection until the client or platform times out. Same on base, and it uses no Worker CPU while waiting.
- **I-3:** the room summary said bad lengths "stay on the streaming path". The code, docs and PR description say 400 without buffering, which is what 175ae76 already did.
- **I-4:** the PR description's test list includes 4 existing tests (chunked 413, stream stops at cap, fails closed, missing token 401). 12 tests are new.
- **I-5:** the cache keeps the secret string in module memory. It's already in `env` for the isolate's lifetime, and nothing logs it.
- **I-6:** in-isolate rotation is covered only by unit tests. On Cloudflare a secret change ships a new version and a new isolate, which the restart case covers.

## Next
Coder: the L-1 one-liner (plus N-1/N-2 wording if wanted). QA recheck of the delta, PM re-scan, deploy, DevOps version check, then QA live check (≤ 3 calls) reporting `cpuTime` for a realistic photo against 10 ms.
