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
