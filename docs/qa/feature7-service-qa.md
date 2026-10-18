# QA: Feature 7 service (model and image size choice, API v1.2)

- PR #10, commit `5cd350d`, tested against main `7339211` (v1 baseline)
- Backend: echo over HTTP, plus the repo's fake Ollama (`tests/ollama_mock.py`) for the Ollama-only rules. No hardware run yet; a final Jetson run follows "board back".
- Result: **PASS**, once the duplicated `ECHO_MODE` row in the README env table is fixed.

## Test plan

1. Run the full suite and lint as CI does. Review the new tests branch by branch.
2. With no new env, compare `/v1/info` and `/v1/suggest` field by field against v1 (main `7339211`).
3. Env parsing: `ALLOWED_MODELS` (blanks, repeats, the default listed again, case, not installed) and `IMAGE_SIZES` (`*`, no `*`, two `*`, duplicates, non-numeric, out of range, empty).
4. `context.model`: missing, null, non-string, empty, unknown, different case, `:latest` spelling, allowlisted but not installed.
5. Model switch with Jetson numbers (load 15 s, answer 13.5 s): fast 503 + `Retry-After` at 25 s, waiting at 45 s, switching back, concurrent requests.
6. Per-model `avg_latency_ms`; the 30 s installed-model cache (install and removal) on fake Ollama with a real clock.
7. The token never appears in any response, header or log.
8. Old client: Apotheke v2.3.2's autofill client against the service, with and without the new env.
9. Docs: contract doc, OpenAPI (validated, live responses checked against it), README env table, `.env.example`, CHANGELOG, compose. Check that the PR body's lists of docs and tests match the files changed. Check the README merge with PR #9.

## Results

| # | Check | Result |
|---|---|---|
| 1 | ruff check + format; pytest | clean; **344 passed** (267 existing, unchanged, + 77 new) |
| 1 | Test coverage | allowlist, fallback, 400, cold start on a switch (25/45 s), queued switch, per-model latency, eviction fix and v1 unchanged are all covered. Gaps listed below |
| 2 | v1 with no new env | `/v1/info` identical; suggest identical except the added `model_used`. 401, 413, 415, 400 and 503 identical; `/healthz` stays open |
| 3 | Env parsing | two `*`, non-numeric and out-of-range (64-8192) values give a clear startup error. Empty values mean the feature is off. Repeats are removed; the default is always first and listed once; uninstalled models are hidden |
| 4 | `context.model` | non-string gives 400. Empty, unknown, different case or not installed falls back to the default; `model_used` is always right |
| 5 | Switch at 25 s | 503 in 0.01 s, `Retry-After: 15` (10 when retried 5 s later); 200 after the load, in 13.5 s; switching back is cold again |
| 5 | Switch at 45 s | waits and answers 200 in 28.5 s, with no 503 |
| 6 | Per-model latency, 30 s cache | per model; the top-level value stays the default's. An install or removal shows up after 30 s, not before |
| 7 | Token | not found in responses, headers or logs |
| 8 | Apotheke v2.3.2 client | Test connection and suggestions identical with and without the new env. A switched model shows as "warming up, try again" (A1 retry applies) |
| 9 | Docs | OpenAPI valid and matches the live responses. The README, `.env.example` (every env var the app reads), CHANGELOG and compose are present and accurate. The PR body lists match the 17 changed files and the 47 named tests |
| 9 | PR #9 README | no conflict; the local merge passes 344 tests |

## Findings

| ID | Severity | Finding |
|---|---|---|
| D1 | Low, fix before merge | README env table lists `ECHO_MODE` twice (README.md:174, :177) |
| B1 | Low | Ollama: a switch 503 says "model X is loading", but X's load isn't started while another background reload is running (`_reload` is a no-op, ollama.py:904-912). The retry costs one extra cold cycle |
| B2 | Low | Ollama: the switch `Retry-After` is always 15 s, ignoring a longer measured load time (ollama.py:1072), so the client's single retry can land mid-load. Echo uses the remaining load time instead |
| B3 | Low, conditional | Ollama: `/v1/info` during an in-flight switch whose model isn't listed in `/api/ps` yet queues a reload of the default, which then evicts the picked model (ollama.py:821-829). Check in the Jetson run: call `/v1/info` during a model switch |
| E1 | Low | Echo doesn't serialize requests: concurrent requests for different models both succeed. The queued-switch rule is tested only with fake Ollama |
| P1-P2 | Info | `IMAGE_SIZES` also accepts `672**`, `+448` and `4_48`; model names are case-sensitive (safe fallback) |

Doc gaps (non-blocking):
- `docs/api-v1.md` says `Retry-After` is at least 15 s, but echo sends the remaining load time.
- The `Retry-After` description in `openapi.yaml` doesn't mention a model switch.
- PR #9's `docs/deploy-jetson.md` (Feature 7 section) describes `ALLOWED_MODELS` as including the default and requiring every model to be installed. Fix that in whichever PR lands second.

Suggested extra tests (non-blocking): an install or removal shown after the cache TTL; `/v1/info` during a loading switch; switch `Retry-After` with a measured load over 15 s; a switch while another reload is running.

## Still to do

The final Jetson run after "board back", with the env set to ITAdvisor's recommended values: a cold switch at 45 s, `/v1/info` polled during the switch (B3), and switching back.
