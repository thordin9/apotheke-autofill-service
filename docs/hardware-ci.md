# Hardware CI (Jetson)

> **Legacy, decommissioned on 2026-10-20.** `jetson-hardware.yml` is disabled in the Actions settings and kept
> for reference only. The service no longer runs on the board. See
> [deploy-jetson.md](deploy-jetson.md#decommissioned-2026-10-20).

`.github/workflows/jetson-hardware.yml` runs the service against the real model
(Moondream on the host Ollama) on the self-hosted runner `<jetson-host>`, which has the
`jetson` label (Jetson Xavier NX, L4T R35). It is **informational only**:
it is not a required status check, because the board can be offline. Don't add
it to branch protection.

## When it runs

Only by hand: `workflow_dispatch` (Actions → Jetson hardware → Run workflow, or
`gh workflow run jetson-hardware.yml`), with inputs `ollama_url` and `rounds`.
There is no schedule and no push trigger on purpose; run it once when you need
fresh numbers, for example after changing the model or the prompt.

> **Never** add `pull_request` or `pull_request_target`. The repo is public and
> the runner's user is in the `docker` group on a LAN machine, so a fork PR that
> reached it could run anything there. The workflow uses no secrets and a
> read-only `GITHUB_TOKEN`. Outside contributors' workflows need approval
> (repo Actions setting), and `main` requires a PR.

Until `Dockerfile.jetson` (PR #2) and the Ollama backend (PR #3) are on `main`,
a run **fails** with a message saying which one is missing.

## What it does

1. Preflight: checks the host Ollama is reachable and has `moondream` pulled,
   and records the board's L4T release, `nvpmodel` power mode, memory and disk.
2. `docker build -f Dockerfile.jetson` (tag `apotheke-autofill:jetson-ci`).
3. Starts `tegrastats` (1 s) for whole-board memory and GPU load.
4. Runs the image with `--network host`, `BACKEND=ollama`,
   `FALLBACK_BACKEND=none` (local only, no Cloudflare call),
   `OLLAMA_URL=http://127.0.0.1:11434`, `OLLAMA_LOG_RAW=1`,
   `OLLAMA_QUEUE_SIZE=0`, and a random per-run `AUTOFILL_TOKEN` (masked).
   It listens on `127.0.0.1:18080`, so it never clashes with or exposes the
   long-running service on port 8080.
5. `scripts/hardware_smoke.py wait`: waits up to 15 minutes for `/healthz`,
   then for `/v1/info` to report `ready: true` (model warm-up).
6. `scripts/hardware_smoke.py run`:
   - 401 `unauthorized` + `WWW-Authenticate` without a token (and with a wrong one)
   - `/v1/info` contract, `ready`, `backend == ollama`
   - each photo in `tests/fixtures/hardware/` posted with and without
     `context`, `rounds` times. Every 200 must match the Suggestion schema, and every
     error must be the JSON error envelope. `503 backend_error` and `504
     backend_timeout` are soft failures. At least 50% of requests must succeed.
   - latency per request, p50/p95/max
   - a concurrent pair: exactly one `503 backend_unavailable` with `Retry-After`
     (this is why the queue is 0 in this job; production default is 1)
7. Writes a Markdown summary to the run page, and uploads `hw-artifacts/` (build
   log, container log with raw model output, smoke JSON, tegrastats log,
   `ollama ps`, board info) as an artifact kept for 14 days.
8. Always removes the container and prunes only the dangling images it built
   (label `org.apotheke.ci=jetson-hardware`).

The service uses the production `BACKEND_TIMEOUT_S=25`, so the latency figures
match what Apotheke sees. The board's 30 Mbit/s download cap only affects the
image build, not the smoke test, which stays on loopback.

## Run the smoke test by hand

On any machine that can reach a running service (Python ≥ 3.8, stdlib only):

```bash
export AUTOFILL_TOKEN=...            # the service's token
python3 scripts/hardware_smoke.py wait --base-url http://<jetson-host>:8080
python3 scripts/hardware_smoke.py run  --base-url http://<jetson-host>:8080 \
    --concurrency 3                  # = OLLAMA_QUEUE_SIZE + 2 for a default-queue service
python3 scripts/hardware_smoke.py report --results hardware-smoke.json
```

Regenerate the fixture photos with `python scripts/make_hardware_fixtures.py`
(needs Pillow).

## Effect on the board's own service

The smoke test loads Moondream into the board's Ollama, which can only hold one model, so the production model gets evicted while it runs. The CI container uses `OLLAMA_KEEP_ALIVE=5m` instead of the image's `-1`. The Clean up step unloads Moondream (unless it was already loaded before the run) and reloads the models that `/api/ps` listed before the run, with `keep_alive: -1`. Until that reload finishes, the live service answers 503 with `Retry-After`, and it would recover by itself anyway.
