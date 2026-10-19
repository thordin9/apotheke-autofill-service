# Changelog

All notable changes to this service. The HTTP contract is versioned
separately in [docs/api-v1.md](docs/api-v1.md) (all v1 changes are additive).

## [1.4.0] - 2026-10-19

### Added

- **Optional Cloudflare Workers AI backend (API v1.4).** Calls the Workers AI
  REST API directly. Confirmed default `@cf/google/gemma-4-26b-a4b-it` with
  thinking off and `response_format` JSON schema, from the bench in
  `bench/results/cf-workers-ai-2026-10-19` (6.4 Neurons/photo). Mistral Small
  3.1 uses `response_format` too (`guided_json` is ignored on image messages).
  Qwen 3.8 is not recommended. Llama 3.2 Vision and Llama 4 Scout are rejected,
  case-insensitively. The shipped default is `BACKEND=cloudflare` with
  `FALLBACK_BACKEND=ollama`. `FALLBACK_BACKEND` accepts `none`, `ollama`, or
  `cloudflare`. Ollama-primary with `FALLBACK_BACKEND=cloudflare` still works,
  and `BACKEND=ollama` with `FALLBACK_BACKEND=none` stays local.
- **Default changed.** A missing `CF_ACCOUNT_ID` or `CF_API_TOKEN`, a missing
  or unreadable credential file (`CF_ACCOUNT_ID_FILE` / `CF_API_TOKEN_FILE`),
  or a missing or unreadable budget state file, does not crash startup. One
  warning is logged (no secret values) and the request is served by Ollama.
  `/v1/info` then shows Cloudflare not configured and `can_serve` false. When
  a token is set, the default sends photos to Cloudflare. The Jetson example
  uses placeholder paths `/opt/autofill/cf-account-id` and
  `/opt/autofill/cf-api-token`.
- **Secret scan.** `.gitignore` ignores `service.env`, `*.env` (tracked `.env`
  and `*.env.example` stay tracked), `*.pem`, and `*.key`. CI runs gitleaks
  8.28.0 on the full history with the default rules enabled. The tarball
  download checks the official checksum
  `a65b5253807a68ac0cafa4414031fd740aeb55f54fb7e55f386acb52e6a840eb`.
  `.gitleaks.toml` allowlists only the historical placeholders
  `generate-a-random-token` and `Bearer ci-token`.
- **Deadline.** `REQUEST_DEADLINE_S` (default 45, must be under Apotheke's 50 s)
  plus a warm reserve (15 s) or a cold/loading reserve (35 s). The Cloudflare
  slice is 20 s when Ollama is warm and about 10 s when it is cold. Startup
  refuses a config that does not fit. A fallback that would miss the deadline
  is 503 with `Retry-After`.
- **Daily Neuron budget.** `CF_DAILY_NEURON_BUDGET` defaults to 6000 (about 930
  Gemma 4 photos). `usage.neurons` is charged when present; otherwise token
  rates; otherwise a conservative estimate. The state file lives on
  `/var/lib/autofill` (Jetson host dir `/opt/autofill/data`). A corrupt,
  unreadable, or unwritable file spends the rest of the UTC day.
- **v1.3 Jetson QA.** `docs/qa/v1.3-jetson-qa.md` records the Jetson pass. The
  file is unchanged from the report.
- **Contract.** `/v1/suggest` gains optional `backend_used` (`ollama` or
  `cloudflare`). `model_used` is the model that answered. `/v1/info` gains
  `backends`, the Cloudflare budget, and the effective slice and reserve.
  `can_serve` is false after 401/403, Workers Paid, a used-up budget, or
  missing config, and recovers on a later success or the UTC reset. `/healthz`
  still probes local Ollama: `503` `{"status":"degraded"}`, plus
  `"serving":"fallback"` or `"serving":"primary"` only while Cloudflare can
  serve. `version` stays `"1"`.

### Fixed

- **Health-probe wording.** An Ollama error during `/healthz` keeps the last
  result. It does not always leave the probe at `ok`.
- **Category names.** Newlines in a client category are folded onto one prompt
  line so the name cannot break the prompt.
- **Unencodable context (QA F-1).** A lone surrogate, or any other value that
  cannot be encoded, in a context string (`categories`, `tags`, `locale`, or
  `model`) is `400` `bad_request` with the usual error object. The log has no
  traceback.
- **Category controls (QA F-2).** NUL and other C0/C1 controls, and bidi
  overrides, are stripped from client category names before the prompt, the
  schema enum, and the category returned to the client. The same classes as
  the brand fields. A NUL never comes back in the response.
- **v3_lean latency.** The changelog's v3_lean cost is the measured +0.7 s.

## [Unreleased]

### Fixed

- **dl-cap double restart at boot (QA #17 follow-up N1).** At boot NetworkManager sends `up` and
  `connectivity-change` for `eth0` at the same moment. The dispatcher hook restarted `dl-cap.service` on every `up`,
  the `connectivity-change` check could then see the cap removed by that restart's stop, and the second restart killed
  the first while it was starting (`status=15/TERM`, `Failed with result 'signal'`), leaving the board without the
  inbound cap for about 0.4 s on every boot. `deploy/jetson/nm-dispatcher-90-dl-cap` now serialises invocations
  with `flock` (`/run/lock/dl-cap-hook.lock`), leaves `dl-cap.service` alone while it has a job queued, waits for a
  start or stop in progress to settle, and then restarts (still `--no-block`) only if `dl-cap.sh check` fails, on
  `up` as well. The lock wait and the settle wait share one deadline, `DL_CAP_HOOK_BUDGET_S` (default 8 s), and
  every `systemctl`/`check` call is bounded by `timeout`, so the hook finishes in about 9 s at worst, well under
  NetworkManager's dispatcher script timeout (600 s in NM 1.22), and never holds up later events for long. When
  the time runs out it falls through: unlocked, a busy or queued unit is left alone, otherwise check and restart; a
  `systemctl` call that times out leaves dl-cap alone. Each decision is logged with the tag `dl-cap-hook`. Ignored
  events and interfaces, the disabled-unit rule, `dl-cap.sh` and `dl-cap.service` (including the post-apply check) are unchanged. The test
  harness gains a systemd-like fake `dl-cap.service` job, concurrent-event tests and worst-case runtime tests
  (at most 10 s). Board check over a reboot pending.

### Added

- **Periodic inbound-cap check (`dl-cap-check.timer`).** Closes the gap where the hook leaves dl-cap alone after a
  `systemctl` timeout and nothing else restores the cap. New `deploy/jetson/dl-cap-check.sh`,
  `dl-cap-check.service` (oneshot, `TimeoutStartSec=30`) and `dl-cap-check.timer` (`OnBootSec=2min`,
  `OnUnitActiveSec=5min`, `AccuracySec=30s`). The check does nothing if `dl-cap.service` is disabled, has a job
  queued or is starting or stopping. Otherwise it runs `dl-cap.sh check` and, only if that fails, logs under the tag
  `dl-cap-check` and runs `systemctl --no-block restart dl-cap.service`. It shares the hook's lock file (waits at
  most 8 s, else skips the round) and bounds every call (4 s each, about 24 s worst case). Install with
  `systemctl enable --now dl-cap-check.timer` (see docs/deploy-jetson.md). Side effect: a plain `systemctl stop
  dl-cap` is undone within 5 minutes; `systemctl disable --now dl-cap` keeps the cap off.

## [1.3.0] - 2026-10-19

### Added

- **Suggest `brand`, `model_number`, `serial_number` (API v1.3).** The Ollama
  prompt and `format` schema are the Feature 9 v3_lean text when the client
  sends no `context.categories`: that prompt matches the winning bench file,
  and the schema caps tags at 3 and the description at 100 characters.
  Client categories replace that bench list in the prompt and the schema enum
  (trimmed, de-duplicated, at most 50). HTTP response clamps stay name ≤120,
  category ≤80, at most 10 tags of ≤40 characters, and description ≤500.
  Successful suggestions include the three strings (trimmed, controls and lone
  surrogates dropped, hard-cut to 128 characters, `""` when missing or
  unreadable). The values are never logged, including with `OLLAMA_LOG_RAW=1`
  and when the raw answer is truncated or uses spaced keys. GPU-confirmed warm
  medians for the production model `qwen2.5vl-jetson:3b`, 19 photos each:
  base-560 8.58 s, v3_lean-560 9.27 s (+0.69 s); base-1024 13.89 s,
  v3_lean-1024 14.62 s (+0.73 s). Echo exposes them through `ECHO_BRAND`,
  `ECHO_MODEL_NUMBER` and `ECHO_SERIAL_NUMBER` (`json:` prefix for null or a
  nested value).

- **Per-model `state` and `size_vram`.** `models` entries on `GET /v1/info`
  gain `state` (`cold` | `loading` | `warm`). `loaded` still means "listed in
  `/api/ps`" (resident or still loading). `state` is `warm` after a background
  load of that model finishes successfully (startup warm-up or a later reload)
  or after a suggest for it finishes successfully. A model that is only listed
  in `/api/ps` stays `loading`. `size_vram` is the byte count from `/api/ps`,
  the source of truth for GPU residency. `/healthz` stays status-only
  (`{"status":"ok"}` or `503 {"status":"degraded"}`) and does not list models.
  It answers degraded when a warm model or the loaded production model has
  `size_vram` 0, which is what a CPU-only runner looks like after Ollama
  starts before CUDA.

### Fixed

- **In-flight admission counted too little load (F5).** A request queued
  behind one that is itself loading, or behind a background reload, is refused
  with `503` and `Retry-After` when the remaining load would push it past 90%
  of `BACKEND_TIMEOUT_S`, instead of being admitted and later timing out.
- **Queue-full / switch-busy `Retry-After` omitted the switch load (F6).**
  That value now adds the load budget of the model being switched to (estimate
  plus max(2 s, 30%)) and is capped at `BACKEND_TIMEOUT_S` + 5 s.

### Documentation

- **Brand/model/serial prompt variants and small-VLM shortlist (2026-10-19).** `docs/benchmarks.md`
  adds Feature 9 results: four prompt/schema variants on `qwen2.5vl-jetson:3b` at 560 and 1024 px.
  Winner **`v3_lean`** (+0.7 s, fewest invented values). `v2_omit` invents heavily — do not use.
  Small-model pulls: MiniCPM-V4 runs but category accuracy lags; library `gemma3:4b` needs a newer
  Ollama. InternVL3/SmolVLM2 are not inherently CPU-only. The earlier ~80–110 s/photo was the
  18:22–19:28 CPU-only Ollama boot window; after CUDA restore they run on GPU (~19.5 s InternVL,
  ~17 s SmolVLM for a single 560 px request) and still are not allowlisted. Production stays
  on Qwen with `ALLOWED_MODELS=qwen2.5vl-jetson:3b`. New scripts: `bench/brand_variants.py`,
  `bench/brand_sweep.py`, `bench/brand_sweep.sh`, `bench/score_brand.py`,
  `bench/photos/brand_readability.json`. Raw: `bench/results/brandserial-2026-10-19/`,
  `bench/results/modelbench-2026-10-19/`.
  GPU retime after that boot window confirms Feature 9 medians (v3_lean +0.7 s).

- **Jetson deployment guide (PR #9).** `docs/deploy-jetson.md` documents the production setup on the Jetson
  Xavier NX: platform, Ollama drop-in, the `qwen2.5vl-jetson:3b` model, `service.env`, building
  (`apotheke-autofill:jetson-<sha>`), switching with rollback, verifying, redeploying, rolling back, the 30 Mbit/s
  inbound cap, the self-hosted runner and reboot behaviour. New scripts in `deploy/jetson/`: `build-image.sh`,
  `run-service.sh` (keeps the old container as `autofill-prev` and restores it if the new one fails or the switch
  is interrupted), `service.env.example`, the Ollama drop-in, `dl-cap.sh` and `dl-cap.service`. Deployed on the
  board at `517352f` with `IMAGE_SIZES=448,560*,1024` and
  `ALLOWED_MODELS=qwen2.5vl-jetson:3b`, which lists only the default and turns on `models` in `/v1/info`. `bench/verify.sh` no longer prints
  Ollama's spinner.

- **Image-size sweep and brand/serial benchmark (2026-10-18).** `docs/benchmarks.md`
  gains a section with hardware/software versions, the exact prompt, schema and
  request settings, and results for `qwen2.5vl-jetson:3b` at `max_pixels`
  501760/401408/301056/200704 and at 1024/672/560/448 px uploads on 19 photos
  (10 new ones with brand names, model and serial numbers, with ground truth).
  Recommendation: `IMAGE_SIZES=448,560*,1024` (560 px: median 8.3 s instead of
  13.9 s, same accuracy), `ALLOWED_MODELS` unset. `qwen3-vl:2b` does not fit in
  memory with Ollama 0.15.6 and must not be allowlisted. New scripts:
  `bench/sweep.sh`, `bench/sweep.py`, `bench/score.py`, `bench/make_sets.py`;
  Modelfiles for the extra tags in `bench/qwen2.5vl-jetson/`; raw results in
  `bench/results/sweep-2026-10-18/`.

### Fixed

- **`deploy/jetson`: the 30 Mbit/s inbound cap survives NetworkManager taking over `eth0` after boot.** On the
  board, `dl-cap.service` ran and exited 0, then NetworkManager reset `eth0` a moment later and the ingress qdisc,
  and with it the cap, was silently gone until a manual `systemctl restart dl-cap`. New NetworkManager dispatcher
  hook `deploy/jetson/nm-dispatcher-90-dl-cap` (installed as `/etc/NetworkManager/dispatcher.d/90-dl-cap`, root,
  mode 755) restarts `dl-cap.service` (`--no-block`) on `up` for `eth0`. On `dhcp4-change` for `eth0` and on
  `connectivity-change` (empty interface), it runs `dl-cap.sh check` first and only restarts if the cap is missing,
  so a lease renewal does not leave a gap or burn the start limit. Disabled units stay off. `dl-cap.sh` now checks
  after applying that `tc filter show dev eth0 parent ffff:` lists police filters for `ip` and `ipv6`, and exits 1
  if not, so `Restart=on-failure` retries. A new `dl-cap.sh check` mode exits 1 when the cap is missing, and an
  unknown argument exits 2. The fake `tc` matches the board's iproute2-ss200127 `filter show` format (police on a
  separate line from `protocol ip`/`ipv6`), with a verbatim fixture test. Fake `systemctl` covers the hook. CI
  shellchecks the hook. `docs/deploy-jetson.md` covers why the hook exists, how to install it, and how to verify
  the cap, including after a reboot.

- **`deploy/jetson`: Ollama waits for CUDA at boot and restarts once if it came up on CPU.** After a reboot,
  `ollama.service` could start while `/dev/nvhost-ctrl-gpu` and `/dev/nvmap` already existed but GPU discovery still
  logged only `library=cpu` until a manual restart (CUDA init race, not missing device nodes). New `wait-cuda.sh`
  (`ExecStartPre` via `ollama-wait-cuda.conf`, `After=nv.service nvpmodel.service`, `TimeoutStartSec=180`) waits up
  to `CUDA_WAIT_S` (default 60, capped at 150; `0` = skip) for the device nodes / nvgpu sysfs **and** a bounded
  `cuInit` probe as the `ollama` user, logging every check with a timestamp and `+Ns` elapsed; exits 0 on timeout
  so the unit never fails forever. New `ollama-gpu-check.sh` / `ollama-gpu-check.service` (`Requisite=`/`After=`,
  `TimeoutStartSec=600`) scopes this start's journal by `_SYSTEMD_INVOCATION_ID`, treats any `library=cuda` line
  (any case) as GPU, waits `OLLAMA_GPU_CHECK_WAIT_S` (default 120) for the compute line, then optionally delays
  (`OLLAMA_GPU_CHECK_RESTART_DELAY_S`, default 10) and restarts `ollama.service` at most once per boot (`/run`
  marker). Never loops when there is truly no GPU, never starts a disabled Ollama, never loads or pulls models.
  `check` needs root or the journal group. Harness covers both orderings, cuInit fail-then-OK / missing python3 /
  missing libcuda / hang, the dispatcher check-then-restart, Requisite, InvocationID scoping, board journal lines,
  and an aggregate no-`/api/generate`|`/api/pull` assertion across every gpu-check case. Docs add install steps, a
  post-reboot checklist, a one-boot forced-race procedure for QA (R1b: `CUDA_VISIBLE_DEVICES=-1` and `CUDA_WAIT_S=0`
  for the first start only), and `docs/qa/pr17-boot-race-qa.md`.

- **`deploy/jetson/run-service.sh`: refuse a switch when port 8080 is taken; wall-clock `WAIT_S`.** After the old
  container is stopped, the script refuses (and restores it) if `HEALTH_URL` still answers, because something else
  holds the port and its answer would be taken for the new container. On a first deploy it refuses before starting
  anything. The health wait now counts wall-clock time, so a hanging `/healthz` can't stretch `WAIT_S`. The deploy
  scripts' fake-docker test harness is now in `deploy/jetson/tests/` and runs in CI (`deploy-scripts` job).

- **`deploy/jetson/run-service.sh`: validate `WAIT_S`; bind-error hint; test harness cleans up (QA of #14).**
  `WAIT_S` must be a positive whole number of seconds, at most 6 digits; anything else (`abc`, `1.5`, `0`, a number
  too large for shell arithmetic) now exits 1 with a clear message before any container is touched, instead of a
  cryptic shell error mid-switch. When a new container fails and its logs show `address already in use` (a foreign
  process on port 8080 that doesn't answer `HEALTH_URL` with 2xx, so the port check can't see it), the script prints
  a hint to check `ss -ltnp 'sport = :8080'`. `deploy/jetson/tests/run-tests.sh` keeps all scratch dirs under one
  temporary parent removed on exit, also on Ctrl-C, after stopping the running case (it left about 45 dirs in `/tmp`
  per run), and gains 22 cases (65 in total). `docs/deploy-jetson.md`: port-check limits and the hint, `WAIT_S`
  rule, wording, and the harness run time (about 1 min 30 s, not two minutes).

- **Cold-start `Retry-After` per model (Ollama).** A 503 "model '…' is
  loading" now sends that model's remaining load time: max(15 s, its last
  measured load) plus a margin of max(2 s, 30%), so a load up to 30% slower
  than the last still fits, minus how long its background load has already
  run, at least 1 s (20 s for an unmeasured model), and capped at
  `BACKEND_TIMEOUT_S` + 5 s, the recommended client timeout (50 at 45 s), so
  Apotheke, which never retries when `Retry-After` exceeds its timeout,
  still retries. The cap also applies to a switch queued behind another load. It was always 15 s (or `OLLAMA_RETRY_AFTER_S` if higher), so a
  client's single retry could land mid-load for a model that loads slower.
  `OLLAMA_RETRY_AFTER_S` no longer applies to cold-start 503s.
- **`/v1/info` no longer evicts a model being switched to (Ollama).** While a
  model switch or background load is in flight, including a request waiting
  for its model to load, `/v1/info` reports `ready: false` if `/api/ps` does
  not list an allowlisted model yet, but no longer queues a reload of the
  default model that would evict the model being loaded. Work in flight is
  checked before and after the `/api/ps` read, so a load or request that
  finishes during it cannot trigger the reload either. The guard also
  applies without `ALLOWED_MODELS`: the reload of an unloaded default waits
  while a request is in flight.
- **A switch during another background load is queued (Ollama).** The
  requested model used to be dropped when another background load was
  running, although the 503 said it was loading, so the retry paid a second
  cold cycle. It is now loaded right after the running load (one at a time,
  one queued slot, latest request wins), and its `Retry-After` includes the
  rest of the running load.
- **Echo serializes requests with `ALLOWED_MODELS` set.** Like Ollama with
  one parallel slot and a queue of one, concurrent requests for different
  models now follow the queued-switch rule (wait, "queue full", or "a model
  switch would time out"). Single requests, and echo without
  `ALLOWED_MODELS`, are unchanged.
- **Strict `IMAGE_SIZES` parsing.** Each entry must match `^\d+\*?$`;
  `672**`, `+448`, `4_48` and `672 *` now fail at startup instead of being
  accepted.

### Added

- **Model choice (API v1.2).** `ALLOWED_MODELS` lists models a client may pick
  with the new optional `context.model`; the configured model stays the
  default. `/v1/info` gains optional `models` (default first, then installed
  allowlisted models, with `default`, `loaded` and per-model
  `avg_latency_ms`). Unknown or not-installed models fall back to the default.
  Every `/v1/suggest` answer reports the optional `model_used`.
- **Image size presets (API v1.2).** `IMAGE_SIZES` (e.g. `448,672*,1024`, `*` =
  recommended) is advertised as optional `image_sizes` in `/v1/info`. The client
  resizes; the service does not.
- **Echo model simulation.** Echo reads `ALLOWED_MODELS` / `IMAGE_SIZES` too and
  adds `ECHO_MODEL`, `ECHO_LOAD_MS` and `ECHO_INSTALLED_MODELS`, so clients can
  test model switching, cold starts and fallbacks without a model.

### Changed

- A request for a model other than the loaded one counts as a cold start and
  follows the existing fast-503 / wait rule. Admission timings (moving average,
  last-5 window, load estimate) are tracked per model; the top-level
  `avg_latency_ms` stays the default model's.
- With `ALLOWED_MODELS` set, `/v1/info` reports ready and skips reloading the
  default while any allowlisted model is loaded, so a picked model is not
  evicted.
- `context.model`, when present, must be a string (otherwise 400).

### Unchanged

- Without `ALLOWED_MODELS` and `IMAGE_SIZES`, `/v1/info` is identical to
  before, and requests without `context.model` behave as before (they only
  gain `model_used`).
