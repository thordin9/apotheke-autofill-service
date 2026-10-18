# QA: B2/B3 follow-up (cold-start Retry-After, /v1/info during a switch)

- PR #11, commit `adb2c90` on main `65fad42`
- Backend: the repo's fake Ollama (`tests/ollama_mock.py`) with a real clock. No hardware run; the Jetson check is in "Still to do".
- Result: **PASS** (two low-severity follow-ups below)

## Test plan
1. Run the suite and lint. Check that the new tests fail on main.
2. B2: Retry-After for measured loads above and below 15 s, rounding, the countdown on retries, the minimum of 1, an unmeasured model, `OLLAMA_RETRY_AFTER_S`, and the busy and warm-up 503s. Check against Apotheke v2.3.2's single-retry rule.
3. B3: poll `/v1/info` during a 25 s switch (background load) and during a 45 s waiting switch.
4. `/v1/info` when idle: allowlisted model loaded, nothing loaded, a non-allowlisted model loaded, and the same without `ALLOWED_MODELS`.
5. Docs and the PR body's test list.

## Results
| # | Check | Result |
|---|---|---|
| 1 | ruff; pytest | clean; **353 passed**. 7 of the 9 new tests fail on main (as stated); 3 repeated runs show no flakes |
| 2 | Retry-After values | measured load 22.4 s gives 23; 22.0 gives 22; 40 gives 40; 4 s or unmeasured gives 15. Countdown 23, 21, 19, 17 at t=0, 2, 4, 5.5 s, with one load. Minimum 1 |
| 2 | Other 503s | busy and warm-up still use `OLLAMA_RETRY_AFTER_S`. It no longer raises cold-start 503s (in the CHANGELOG) |
| 2 | Apotheke v2.3.2 | retries once if Retry-After ≤ its timeout setting (default 50, max 60). 23 s and 40 s are retried, and the retry gets 200 when the load takes as long as last time |
| 3 | 25 s switch, info polled | always `ready:false` during the load; no default reload; chosen model stays loaded |
| 3 | 45 s waiting switch, info polled | always `ready:false`; no default reload; 200 from the chosen model |
| 4 | Idle, allowlisted model loaded | `ready:true`, **no** default reload. The chosen model survives settings visits and Test connection |
| 4 | Idle, nothing loaded | default reloaded (as before) |
| 4 | Idle, non-allowlisted model loaded | default reloaded, evicting that model (intended) |
| 4 | Without `ALLOWED_MODELS` | same as v1. New: the reload waits while a request is in flight |
| 5 | Docs | api-v1, OpenAPI (valid), README and CHANGELOG match the measured behaviour. The PR body lists all 9 new tests |

## Findings
| ID | Severity | Finding |
|---|---|---|
| B3b | Low | `/v1/info` reads `/api/ps` before checking for work in flight (ollama.py:824 vs :832). A load that finishes during that call can still trigger a default reload, evicting the model that just loaded. Rare (a window of milliseconds). Fix: check for work in flight before the `/api/ps` call as well |
| B2-note | Low | Retry-After rounds up the last measured load, a 0-1 s margin. If the next load is slower, the client's single retry gets another 503. Suggest a small margin |
| Doc | Info | The B3 guard also applies without `ALLOWED_MODELS` (reload waits while a request is in flight); the docs don't say so |

## Still to do
The Jetson run after "board back": a cold switch at 45 s while polling `/v1/info` (with no default reload expected), and a 25 s switch after a measured load, where Retry-After should be at least that load.
