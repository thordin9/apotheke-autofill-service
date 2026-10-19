# QA: Jetson deployment guide and scripts (PR #9)

Reviewed commit `9d23ccb` (branch `docs/jetson-deploy`) on 2026-10-18 from the repository alone. The board was
not used. **Result: PASS**, with one Medium documentation fix and some Low items listed below.

## Test plan and results

### Secrets
| Test | Result |
|---|---|
| gitleaks 8.21.2 over the full branch history, the PR range and the working tree | only false positives: the `service.env.example` placeholder and the CI dummy token `ci-token` |
| grep of `git log -p`: bearer tokens, GitHub tokens (`ghp_`, `github_pat_`, `gh?_`), runner tokens, private keys, WireGuard keys and endpoints, private IPv4 ranges | none. The PR diff only contains `0.0.0.0` and `127.0.0.1` |
| The deployed token value, and its first and last 8 characters | not found |
| Host names | none added by this PR (pre-existing mentions in `docs/benchmarks.md` and `docs/hardware-ci.md` are tracked separately) |
| Token never on a command line | `--env-file` only. Token generated with the `printf` builtin under `umask 077`. curl reads `-H @file`. No `set -x` or echo. `bash -x run-service.sh` doesn't show the value |

### Settings against the service code
| Setting | Where | Result |
|---|---|---|
| Inbound cap 30 Mbit/s, all eth0 traffic, IPv4 and IPv6 | `deploy/jetson/dl-cap.sh` | pass |
| `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1` | `ollama-10-autofill.conf` | pass |
| `OLLAMA_HOST=0.0.0.0`, `OLLAMA_ORIGINS=*`, `OLLAMA_CONTEXT_LENGTH=8192` (recorded, optional) | `ollama-override.conf.example` | pass |
| `OLLAMA_KEEP_ALIVE=-1`, `BACKEND_TIMEOUT_S=45`, `AUTOFILL_TOKEN` | `service.env.example`. Names match `app/settings.py` and `app/backends/ollama.py` | pass |
| Port 8080, host network, `--runtime nvidia`, `--restart unless-stopped`, image tag argument | `run-service.sh`, `Dockerfile.jetson` | pass |
| Patched model `qwen2.5vl-jetson:3b` (max_pixels 501760, num_ctx 4096) | `bench/qwen2.5vl-jetson/`, `docs/benchmarks.md` | pass (see D1) |

### Scripts
| Test | Result |
|---|---|
| `bash -n` / `sh -n` / `dash -n` | pass |
| shellcheck 0.10.0 | `run-service.sh` and `build-image.sh` clean. `dl-cap.sh`: SC2086 info only |
| `run-service.sh` with a stub `docker` | missing argument, env file or image → clear error, exit 1. Correct `docker run` arguments, quoting safe with spaces |
| `build-image.sh` with a stub `docker` | builds `apotheke-autofill:jetson-<sha>` from a `git archive` (Dockerfile and app present). Temp dir cleaned. A bad ref exits 128 (git's message) |
| `dl-cap.sh` with a stateful stub `tc` | apply, re-apply, stop, stop again, re-apply: always 0 or 2 filters, exit 0. Idempotent on rerun and on boot |
| `systemd-analyze verify` | `dl-cap.service` and both Ollama drop-ins are valid |

### Guide
| Test | Result |
|---|---|
| Every linked file and anchor exists (`deploy/jetson/*`, `bench/verify.sh`, `bench/make-auth-header.sh`, `bench/qwen2.5vl-jetson/*`, photos, benchmarks and hardware-ci pages) | pass |
| README links to the guide | pass |
| Feature 7 (`ALLOWED_MODELS`, `IMAGE_SIZES`) marked as pending | pass |
| Security note: Ollama open on the LAN without auth; runner hardening | pass |

## Findings
- **D1 (Medium):** the model recipe in `docs/benchmarks.md` runs the image `apotheke-autofill:jetson`, but `build-image.sh` creates `apotheke-autofill:jetson-<sha>`. Use the built tag in the recipe.
- **S1 (Low):** `dl-cap.sh` returns only the last `tc` exit status, so a failed IPv4 filter can go unnoticed. Add `set -e` after the delete line, and optionally `Restart=on-failure` in the unit.
- **D2 (Low):** the guide says Apotheke allows "the backend timeout plus 5 s". Apotheke's timeout is its own setting (default 50 s), so rephrase as a recommendation.
- **S2 (Low):** `build-image.sh` should print a clear message for an unknown ref.
- **S3 (Low):** `run-service.sh` removes the old container before starting the new one, so there's no fallback if `docker run` fails.
- **D3 (Low):** running as root on a repo owned by another user hits git's "dubious ownership" check. Document a root-owned checkout or `safe.directory`.
- **D4 (Low):** the model section needs a built image but comes before the build section.
- **Info:**
  - Show the `OLLAMA_VERSION` pin syntax.
  - Mention the CORS risk of `OLLAMA_ORIGINS=*`.
  - Note that `num_ctx 4096` overrides `OLLAMA_CONTEXT_LENGTH`.
  - The runner registration token is passed in argv.
  - Use absolute paths in the backup command.
  - `OLLAMA_LOG_RAW=1` keeps photo descriptions in the logs.

## Recheck rounds and known limitations

Verdict for 9e53fe1: **PASS.** Every finding from the first review (9d23ccb) and from the three rechecks is fixed. The scripts were checked with stateful docker and tc stubs; the board was not used.

### Round 1: 3ae9fd6 (QA findings folded in)
- D1: all docs use the same tag scheme, `apotheke-autofill:jetson-<short sha>` (`$IMAGE`).
- D2: Apotheke's client timeout is explained separately from `BACKEND_TIMEOUT_S`.
- D3: the guide covers git's "dubious ownership" check when running as root.
- D4: build before model setup.
- S1: `dl-cap.sh` tolerates "nothing to delete" and then fails on any `tc` error.
  - The unit uses `Restart=on-failure`, `RestartSec=10` and a start limit of 10 per 5 min.
  - That is valid for `Type=oneshot` from systemd 244, and the board has 245.
  - `systemd-analyze verify` is clean.
- S2: an unknown ref stops with a clear error before any build.
- S3: `run-service.sh` keeps the old container as `autofill-prev` and rolls back if `docker run` fails or the new container exits or isn't healthy within `WAIT_S` (120 s). It refuses to run if a `-prev` is left over.
- Info items addressed: Ollama version pin, CORS note, `num_ctx`, runner token in argv/history, absolute backup path, `OLLAMA_LOG_RAW=0`.
- New finding N1: Ctrl-C during a deploy left the old container stopped.

### Round 2: d9a5f3a (N1, signal handling)
- An INT/TERM trap rolls back at every stage: before or after the rename and stop, during `docker run`, and during the health wait. Exit codes are 130 / 143.
- The refusal message and the guide give the same, correct recovery steps.
- New finding N1b: a signal *during* a rollback started a second rollback that removed the just-restored container.

### Round 3: 9e53fe1 (N1b and Feature 7 wording)
- The rollback is idempotent: it removes the new container only once, then makes sure whatever is named `autofill` (the old one) runs. While a rollback runs, further INT/TERM are ignored.
- Signal tests in the stub:
  - INT and TERM at each stage of a failed rollback (unhealthy, and `docker run` failing);
  - a second signal during a signal-triggered rollback;
  - a sample of the forward stages.
- The old container always ends up running as `autofill`, a first deploy leaves nothing behind, and no `-prev` or stray containers remain.
- The Feature 7 text in the guide and `service.env.example` matches the service PR #10 code:
  - the default model comes from `OLLAMA_MODEL`;
  - `ALLOWED_MODELS` lists extras only;
  - models not installed are left out of `/v1/info`;
  - `IMAGE_SIZES` takes comma-separated values from 64 to 8192 with at most one `*`;
  - both settings are opt-in.
- Secrets: gitleaks and pattern greps on every new commit are clean, and the real token appears nowhere in history or the tree. The two remaining gitleaks hits are known placeholders. shellcheck is clean.

### Known limitations (accepted, not blocking)
- **Interrupted `docker run` with real Docker:** the daemon may still create the new container after the rollback has finished. The old container is then left stopped as `autofill-prev`. The next run refuses and prints the recovery steps (also in the guide).
- **Power cut mid-deploy:** a leftover `autofill-prev` needs the same manual recovery.
- **Signal during an already-running failure rollback:** it is ignored, and the script exits 1 (deploy failed, old container restored), not 130 / 143.
- **Signal after success, while `autofill-prev` is being removed:** this can leave a stopped `-prev` next to the working new container. Recovery: `docker rm autofill-prev`.
- **"Healthy" means the image HEALTHCHECK on `/healthz` answers,** not that the model is loaded (`ready` in `/v1/info`). Run the quick check under Verify after a deploy.
- **The first health probe runs 30 s after start,** so a good deploy takes about 30 s of downtime. Only one container can hold port 8080 on the host network.
- **`build-image.sh` and fetch failures:** if `git fetch` fails, it warns and builds the last fetched `origin/main`.
- **A non-numeric `WAIT_S`** gives a cryptic shell error, then rolls back safely.
- **Untagged `apotheke-autofill:jetson`** remains in comments in `Dockerfile.jetson`, the hosted CI workflow (never deployed) and historical benchmark text.
- **`IMAGE_SIZES` default:** without a `*`, PR #10 marks the largest size as recommended. The guide doesn't mention this.
- **Models not installed:** a request for one falls back to the default model. The guide only mentions that such models are hidden.
- **Feature 7 is still marked "pending (PR #10)".** Update the wording and fill in production values when PR #10 is merged and deployed.

### Post-merge: direct `/healthz` poll (8ae0b2d)
`run-service.sh` now polls `HEALTH_URL` (default `http://127.0.0.1:8080/healthz`, no auth needed) with `curl -m 3` every 2 s instead of waiting for Docker's first health check at 30 s. A switch drops from about 36 s to 8–10 s of downtime. Rechecked with the stateful fake docker and a fake curl:
- **Good path:** a good image switches as soon as `/healthz` answers.
- **Failure paths:** a dead or unhealthy image, a stuck start, a failing `docker run` and every N1/N1b signal case still end with the previous container running as `autofill`. Exit codes are 130 / 143 for an interrupt and 1 for a failed deploy. Nothing is left behind.
- **Fallback:** without `curl` on the host, or with `HEALTH_URL=` empty, it waits for Docker's health status as before.
- **Checks:** shellcheck is clean, the service tests pass and no secrets were added.

### Follow-up: port check and wall-clock `WAIT_S` (PR #14, 7faf542, merged as d4948c2)
- **Port check:** after the old container stops (or before anything starts, on a first deploy), a 2xx answer on `HEALTH_URL` makes the script refuse, put `autofill-prev` back and exit 1 without `docker run`. Signals during that rollback are ignored. An empty `HEALTH_URL` or no curl means no check, and no false refusal.
- **`WAIT_S`** is wall-clock time now. A hanging `/healthz` with `WAIT_S=10` gives up after about 13 s (3 s of that is the pre-check). It used to take about 25 s.
- **Tests:** `deploy/jetson/tests/run-tests.sh`, 43/43, runs in CI (`deploy-scripts`). Against the script before #14 it gives 38/43; the 5 failures are the new checks. QA's own probes passed 27/27 (pre-check edge cases, signals, wall clock). shellcheck clean, pytest 387 passed, no secrets added.
- **Non-2xx or hanging listener, follow-up in PR #16 (0b03312):** the port check still flags only a 2xx answer within 3 s, by design. Another process that holds the port but answers 404/500, or doesn't answer, gets past it. The new container then fails to bind and the switch rolls back to the previous container. #16 adds a hint: when the failed container's last log lines contain `address already in use` (uvicorn's bind error), the script prints `hint: … ss -ltnp 'sport = :8080'`. QA saw the hint only after a failed health wait whose logs contain that phrase. It did not appear after a successful switch, a failed `docker run`, the port refusal, other log errors, or Ctrl-C/SIGTERM.
- **`WAIT_S` validation (PR #16):** `WAIT_S` must be a positive whole number with no leading zero and at most 6 digits (1 to 999999). It is checked before any `docker` or `curl` call.
  - Refused with a clear message: `abc`, `1.5`, `0`, `00`, `-5`, `+5`, `010`, `' 5'`, `'5 '`, `5s`, `0x10`, `1e3`, `1000000`, 19- and 20-digit values, and non-ASCII digits.
  - Accepted: `1` (times out and rolls back normally), `86400` and `999999`. Empty or unset means the default, 120.
- **Tests (PR #16):** `run-tests.sh` 65/65 (about 88 s). Against the script before #16 it gives 54/65; the 11 failures are the new checks. The `/tmp` entry count is the same before and after a full run.
  - **Interrupts:** Ctrl-C in a terminal at 7 points, a closed terminal, and SIGINT, SIGTERM or SIGHUP to the process group at 7 timings. The run always stopped within 2 s (exit 130 / 143 / 129) and left no files or processes behind.
  - **Bad `TMPDIR`:** an unwritable or missing `TMPDIR` gives a clear message and exit 1.
  - **Known residual:** a few cases run in the foreground: the refusals, `build-image.sh` and `dl-cap.sh`, in the last seconds of a run. A Ctrl-C typed in a terminal during one of them reaches only that case. The run then continues to the end, may report that case as failed, and still cleans up (3 of 3 tries).
  - **Also checked:** QA's #14 probes still pass (27/27 signal and rollback, 7/7 rollback idempotence). shellcheck clean, pytest 387 passed, no secrets added.
