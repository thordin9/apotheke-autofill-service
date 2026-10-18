# QA: B1/E1/P1 + B3b/B2-margin follow-up (queued switches, echo serialization, strict IMAGE_SIZES)

- PR #12, commit `bcd8ded` on main `7f0357e`
- Backend: echo over HTTP and the repo's fake Ollama (`tests/ollama_mock.py`). No hardware run; the Jetson checks are pending, after deploy.
- Result: **PASS with notes** (one low-severity doc gap, the rest Info)

## Test plan
1. Run the suite and lint, and check CI and the merge. Diff the changed existing tests to confirm only expected values changed. Re-run the QA probes from #10 and #11.
2. B3b: an independent repro of the stale `/api/ps` snapshot race (a background load or an in-flight request ending during the read), run on main and on the PR.
3. B1: staggered and truly concurrent switch storms, latest-wins, strictly serial loads, the queued load after an HTTP error and after a connection error, a hung load, a request cancelled while queued, and Retry-After for a queued load.
4. E1: echo with `ALLOWED_MODELS` (serialized, queue full, "switch would time out", a cancelled or disconnected request, a 30-request storm). Echo without it (still parallel; the v1 field-by-field comparison against 7339211).
5. P1: `IMAGE_SIZES` with non-ASCII digits, whitespace, empty entries, signs, underscores, multiple or misplaced stars, leading zeros, and the 64-8192 range.
6. Retry-After margin and cap at 10, 25 and 45 s, measured against Apotheke v2.3.2's single retry (it retries if Retry-After ≤ the admin timeout; default 50, max 60), and what happens to a retry that lands mid-load.
7. Regressions: `/v1/info` polling during 25 s and 45 s switches, and the idle states with and without `ALLOWED_MODELS`.
8. Docs and the PR body's test list. Token never in responses or logs.

## Results
| # | Check | Result |
|---|---|---|
| 1 | ruff; pytest; CI; merge | clean; **387 passed** (353 + 34); CI green; merges cleanly. 7 existing tests changed in expected values only (15→20, 23→30, 13→20) |
| 2 | B3b repro | fails on main (default reloaded, evicting the model that just loaded); **passes** on the PR, for both a load and a request ending during the read |
| 3 | B1 | 7 staggered and 51 concurrent switches: all answered, no hangs; loads run strictly one at a time, the latest request wins, state clears afterwards. The queued load still runs after an HTTP 500 or connection error. A hung load doesn't block other requests. A request cancelled while queued frees its slot. Queued Retry-After 39 at 40 s, and that load happens |
| 4 | E1 | serialized with `ALLOWED_MODELS`: queue full on the third request, "switch would time out" when it doesn't fit, slot freed after a timeout or disconnect, 30-request storm with no hangs. Without it: parallel, and v1 identical to 7339211 apart from `model_used` |
| 5 | P1 | non-ASCII digits, `672**`, `672 *`, `*672`, `+448`, `-448`, `4_48`, `448.0`, `1e3`, two `*`, 63 and 8193 all fail at startup with a clear message. Spaces, empty entries and leading zeros are accepted |
| 6 | Margin and cap | unmeasured 20; 22.4 s measured gives 30; 40 s gives 50 at 45 s and 30 at 25 s; cap = timeout + 5. At 45 s, a 50 is retried by Apotheke at its default timeout of 50. A capped retry that lands mid-load gets a clean, fast 503 with the time left (no hang or 504) |
| 7 | Regressions | `/v1/info` polled during switches: always `ready:false`, never a default reload. Idle allowlisted model kept; nothing loaded or a non-allowlisted model loaded: default reloaded; v1 behaviour unchanged |
| 8 | Docs | api-v1, OpenAPI (valid), README and CHANGELOG match the measured behaviour, including the guard without `ALLOWED_MODELS`. Cache-expiry tests added. The PR body lists all 23 new tests. Token not found |

## Findings
| ID | Severity | Finding |
|---|---|---|
| N1 | Low (doc) | Not documented: if a client's timeout is below `BACKEND_TIMEOUT_S + 5` (e.g. Apotheke set to 30 against 45), a capped Retry-After of 50 is above it and Apotheke doesn't retry |
| I1 | Info | Latest-wins: a queued model can be replaced by a third model's request during the same load, so that client's single retry gets another 503 (documented) |
| I2 | Info | Echo's busy Retry-After for "switch would time out" leaves out `ECHO_LOAD_MS` |
| N2 | Info | CHANGELOG first Fixed bullet has an overlong line, with the history sentence after the cap sentence |

## Pending, after deploy (Jetson, after "board back")
1. Switch models while the default reloads in the background. The Retry-After should include the running load, and the retry should find the picked model loading or loaded.
2. A 45 s switch to a cold model while polling `/v1/info`: `ready:false`, no default reload.
3. A model whose load + answer doesn't fit at 45 s: Retry-After ≤ 50, and Apotheke at its default timeout of 50 retries once.
