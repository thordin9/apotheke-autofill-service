# Deploying the service on a Jetson Xavier NX

This page describes how the autofill service runs on the project's Jetson board, so the setup can be
rebuilt from this repo alone. Everything below is the production setup as of 2026-10-18 12:54 EEST (main
`517352f`, image `apotheke-autofill:jetson-517352f`, model `qwen2.5vl-jetson:3b`, `BACKEND_TIMEOUT_S=45`,
`IMAGE_SIZES=448,560*,1024`, `ALLOWED_MODELS=qwen2.5vl-jetson:3b`). The files it refers to are in
[`deploy/jetson/`](../deploy/jetson) and [`bench/`](../bench). Measurements are in [benchmarks.md](benchmarks.md),
and the hand-run hardware workflow is described in [hardware-ci.md](hardware-ci.md).

Paths on the board: the service's files live in `/opt/autofill/`, and the repo is checked out somewhere on the
board (written `$REPO` below). All commands run as root unless noted otherwise.

## Contents

1. [Platform](#platform)
2. [Ollama and its drop-in](#ollama-and-its-drop-in)
3. [Build the image](#build-the-image)
4. [The model: qwen2.5vl-jetson:3b](#the-model-qwen25vl-jetson3b)
5. [service.env](#serviceenv)
6. [Run the container](#run-the-container)
7. [Verify](#verify)
8. [Redeploy a new commit](#redeploy-a-new-commit)
9. [Roll back](#roll-back)
10. [Inbound bandwidth limit (30 Mbit/s)](#inbound-bandwidth-limit-30-mbits)
11. [Self-hosted GitHub Actions runner](#self-hosted-github-actions-runner)
12. [Reboot behaviour](#reboot-behaviour)
13. [Model and image size choice: ALLOWED_MODELS and IMAGE_SIZES](#model-and-image-size-choice-allowed_models-and-image_sizes)

## Platform

| | |
|---|---|
| Board | NVIDIA Jetson Xavier NX Developer Kit, root filesystem on NVMe |
| OS | L4T R35.6.0 (JetPack 5), kernel 5.10.216-tegra |
| Power | `nvpmodel` mode 8, `MODE_20W_6CORE` (set once with `nvpmodel -m 8`; it persists across reboots). `jetson_clocks` off |
| Memory | 6.8 GB RAM shared by CPU and GPU. Swap: 6 × 569 MB zram (L4T's default `nvzramconfig`) plus an 8 GB swap file on NVMe |
| Docker | 24.0.7 with the NVIDIA container runtime. On this board `nvidia` is also the default runtime (`/etc/docker/daemon.json`), but the scripts pass `--runtime nvidia` explicitly so they work where it isn't |
| Ollama | 0.15.6, installed on the host (`/usr/local/bin/ollama`) as the `ollama.service` systemd unit on port 11434 |
| Network | All traffic coming into `eth0` is capped at 30 Mbit/s (see below) |
| Disk | Each service image is about 10.6 GB (the `dustynv/ollama:r35.4.1` base is most of it, and is shared between tags) |

Check them on a board:

```bash
head -1 /etc/nv_tegra_release      # R35 (release), REVISION: 6.0
nvpmodel -q                        # NV Power Mode: MODE_20W_6CORE / 8
free -m; swapon --show
docker version --format '{{.Server.Version}}'; docker info | grep -i runtime
ollama --version
```

## Ollama and its drop-in

Ollama runs on the host, not in a container, and the service container reaches it at `http://127.0.0.1:11434`
through `--network host`. Install it with Ollama's Linux installer (which creates the `ollama` user and
`ollama.service`), then add the drop-in:

```bash
curl -fsSL https://ollama.com/install.sh | OLLAMA_VERSION=0.15.6 sh   # pinned to the board's version
install -D -m 644 $REPO/deploy/jetson/ollama-10-autofill.conf \
  /etc/systemd/system/ollama.service.d/10-autofill.conf
systemctl daemon-reload && systemctl restart ollama
systemctl show ollama -p Environment               # check the variables arrived
```

[`ollama-10-autofill.conf`](../deploy/jetson/ollama-10-autofill.conf) sets:

- `OLLAMA_MAX_LOADED_MODELS=1`: only one model in memory. Qwen takes 4.2 GB of the board's 6.8 GB, so a second
  model would push it off the GPU or out of memory. The side effect is that anyone who loads another model on the
  board's Ollama evicts Qwen. The service then answers 503 with `Retry-After` (or waits, see `BACKEND_TIMEOUT_S`)
  and reloads it.
- `OLLAMA_NUM_PARALLEL=1`: one request at a time. A second parallel slot costs extra context memory and makes both
  requests slower. The service already queues requests itself.
- `OLLAMA_FLASH_ATTENTION=1`: harmless, but has **no effect** here. Ollama logs "flash attention enabled but not
  supported by gpu" for the Xavier's compute 7.2 GPU.

The board also has a separate `override.conf` with board-wide settings (`OLLAMA_HOST=0.0.0.0`, `OLLAMA_ORIGINS=*`,
`OLLAMA_CONTEXT_LENGTH=8192`). The service doesn't need it. It is recorded in
[`ollama-override.conf.example`](../deploy/jetson/ollama-override.conf.example). Note that `OLLAMA_HOST=0.0.0.0` makes
Ollama reachable from the LAN without authentication, and `OLLAMA_ORIGINS=*` lets any web page open in a browser on
the LAN call that API (CORS), for example to pull or delete models. If nothing else needs them, leave both out.
`OLLAMA_CONTEXT_LENGTH` is only Ollama's default: `qwen2.5vl-jetson:3b` sets `num_ctx 4096` in its Modelfile, which
takes precedence, so the service's model runs at 4096 regardless.

**Boot race: Ollama before CUDA.** After a reboot on 2026-10-19, `ollama.service` started while
`/dev/nvhost-ctrl-gpu` and `/dev/nvmap` already existed, but GPU discovery still logged only `library=cpu`
(`offloaded 0/37 layers to GPU`) 0.44 s after `discovering available GPUs`, until a manual
`systemctl restart ollama`, after which it logged `library=CUDA compute=7.2 name=CUDA0 description=Xavier`.
The race is CUDA initialisation, not the device nodes. Two pieces fix that:

1. [`wait-cuda.sh`](../deploy/jetson/wait-cuda.sh) + drop-in
   [`ollama-wait-cuda.conf`](../deploy/jetson/ollama-wait-cuda.conf) (`ExecStartPre`, `After=nv.service
   nvpmodel.service`, `TimeoutStartSec=180`): waits up to `CUDA_WAIT_S` seconds (default 60, capped at 150) for
   the device nodes **and** an nvgpu/gv11b sysfs path, then polls a bounded `cuInit` probe as the `ollama` user
   (`python3` + `libcuda.so.1`, fresh process per try, 10 s per probe). Every check is logged with a wall-clock
   timestamp and the elapsed seconds (`device/sysfs: present|missing`, `cuInit probe #N: …`), so the journal
   shows whether `cuInit` ever failed early on that boot. On timeout it logs and exits 0 so the unit still
   starts (never fails forever). `CUDA_WAIT_S=0` skips the wait entirely (including the cuInit probe).
2. [`ollama-gpu-check.sh`](../deploy/jetson/ollama-gpu-check.sh) +
   [`ollama-gpu-check.service`](../deploy/jetson/ollama-gpu-check.service) (`Requisite=`/`After=ollama.service`,
   `TimeoutStartSec=600`): after Ollama is up, reads this start's journal (by `_SYSTEMD_INVOCATION_ID`) for any
   `inference compute` line with `library=cuda` (any case). If only `library=cpu` was found, it waits
   `OLLAMA_GPU_CHECK_RESTART_DELAY_S` (default 10) then restarts `ollama.service` **at most once per boot**
   (marker in `/run/ollama-gpu-check.restarted`). It never loops when there is truly no GPU, never starts a
   disabled Ollama, and never loads, pulls or allowlists models. `ollama-gpu-check.sh check` exits 0 for GPU and
   1 for CPU-only; it needs root or the `systemd-journal` group to read the journal.

Install with the drop-in above:

```bash
install -D -m 755 $REPO/deploy/jetson/wait-cuda.sh /usr/local/sbin/wait-cuda.sh
install -D -m 644 $REPO/deploy/jetson/ollama-wait-cuda.conf   /etc/systemd/system/ollama.service.d/ollama-wait-cuda.conf
install -D -m 755 $REPO/deploy/jetson/ollama-gpu-check.sh /usr/local/sbin/ollama-gpu-check.sh
install -D -m 644 $REPO/deploy/jetson/ollama-gpu-check.service /etc/systemd/system/ollama-gpu-check.service
systemctl daemon-reload
systemctl enable ollama-gpu-check.service
systemctl restart ollama          # picks up ExecStartPre (re-runs gpu-check too if it already ran this boot)
systemctl start ollama-gpu-check  # first install only: run the check now (no-op once it has run this boot)
sudo /usr/local/sbin/ollama-gpu-check.sh check   # exit 0 = library=CUDA (needs journal access)
```

**gpu-check after a manual restart.** gpu-check is ordered `After=ollama.service` and has
`Requisite=ollama.service`, so systemd propagates a stop or restart of Ollama to it: once gpu-check has run this
boot, every manual `systemctl restart ollama` re-runs `ollama-gpu-check` after Ollama's new start (and a
`systemctl stop ollama` stops it). It still can't loop: while gpu-check does its own restart it is still activating,
so that restart doesn't trigger it again, and the `/run` marker allows only one restart per boot. Because of
`RemainAfterExit=yes`, `systemctl start ollama-gpu-check` does nothing once the unit has run this boot; use
`systemctl restart ollama-gpu-check` to run it again by hand. If `ollama.service` is disabled at boot, gpu-check
fails at once with a dependency error (the system shows as degraded) and does nothing.

**Forced-race boot (QA, R1b).** The race happened once and may not recur, so to exercise the once-per-boot restart
path on purpose, make Ollama's first start of one boot CPU-only and skip the wait. (An empty `After=` in a drop-in
can't do this: systemd ignores it, so `After=nv.service nvpmodel.service` stays.) For one boot only, the first start
runs with `CUDA_VISIBLE_DEVICES=-1` and `CUDA_WAIT_S=0`. On xavier12 (2026-10-19, 21:07 EEST) this gave
`library=cpu`, and `ollama-gpu-check.sh check` exited 1 with `CPU-only`; without it Ollama logs `library=CUDA` again,
so `OLLAMA_LLM_LIBRARY` isn't needed.

`/run` is a tmpfs, so the variables have to be created at boot: a `tmpfiles.d` entry writes them to `/run` before
Ollama starts, a persistent drop-in reads them with `EnvironmentFile=-` (read at each start, so no `daemon-reload` is
needed in between), and `ExecStartPost` deletes them once the first start is running. gpu-check's restart (and any
later start) then has neither variable and runs wait-cuda's cuInit gate as usual. As root:

```bash
cat > /etc/tmpfiles.d/zz-force-race.conf <<'EOF'
f /run/ollama-force-race-cpu.env 0644 root root - CUDA_VISIBLE_DEVICES=-1
f /run/ollama-force-race-nowait.env 0644 root root - CUDA_WAIT_S=0
EOF
cat > /etc/systemd/system/ollama.service.d/zz-force-race.conf <<'EOF'
[Service]
EnvironmentFile=-/run/ollama-force-race-cpu.env
EnvironmentFile=-/run/ollama-force-race-nowait.env
ExecStartPost=+/bin/rm -f /run/ollama-force-race-cpu.env /run/ollama-force-race-nowait.env
EOF
systemctl daemon-reload && reboot
```

Expect path (b) of the reboot checklist: on the first start `wait-cuda` logs `CUDA_WAIT_S=0; skipping the wait`
and Ollama logs `library=cpu`; gpu-check waits 10 s, restarts Ollama
once, and logs `after restart, library=CUDA`. After the checklist, remove both files, reload, and restart Ollama to
exercise the manual-restart path (gpu-check re-runs and should log `GPU already in use (library=CUDA)`):

```bash
rm /etc/tmpfiles.d/zz-force-race.conf /etc/systemd/system/ollama.service.d/zz-force-race.conf
systemctl daemon-reload && systemctl restart ollama
journalctl -u ollama-gpu-check -n 5 -o cat
```

Leave the two files in place only for that one reboot test. The other forced boot (R1a, without the wait-cuda
drop-in) is described in [`docs/qa/pr17-boot-race-qa.md`](qa/pr17-boot-race-qa.md).

## Build the image

Build on the board itself (native arm64). Images are tagged **`apotheke-autofill:jetson-<short sha>`** with the
7-character commit they were built from, so every deployed image maps to one commit and older ones stay
available for rollback. [`build-image.sh`](../deploy/jetson/build-image.sh) builds from a `git archive` of that
commit, not from the working tree:

```bash
cd $REPO && git fetch origin
deploy/jetson/build-image.sh origin/main          # → built apotheke-autofill:jetson-<sha>
IMAGE=apotheke-autofill:jetson-<sha>              # use the tag it printed in the steps below
```

Run it as root on the board. git refuses to work in a checkout owned by another user when run as root
("detected dubious ownership in repository"). Either keep a root-owned checkout for deploys (for example
`/opt/autofill/repo`), or allow that one path: `git config --global --add safe.directory /path/to/checkout`.

The first build pulls `dustynv/ollama:r35.4.1` (about 5.8 GB compressed), which takes roughly half an hour through
the 30 Mbit/s inbound cap. Later builds reuse it. An unknown ref stops with a clear error before anything is built.

## The model: qwen2.5vl-jetson:3b

**Why stock `qwen2.5vl:3b` fails.** When it loads a model, Ollama reserves memory for the largest image the model
accepts. For Qwen 2.5 VL that is 2 MP. The vision encoder's full-attention layers then need a single
**7.3 GiB CUDA buffer**, which is more than the board's total RAM. Flash attention would shrink that buffer, but
Ollama doesn't support it on this GPU. So the allocation fails, Ollama retries with fewer GPU layers down to zero,
and the model ends up on the CPU, far too slow for any client timeout. Sending smaller photos doesn't help, because
the reservation is sized for the model's maximum image, not the image sent.

**The fix** is `qwen2.5vl-jetson:3b`: the same weights, with `qwen25vl.vision.max_pixels = 501760` (28 × 28 × 640,
about 0.5 MP) added to the GGUF metadata, and `num_ctx 4096` in the Modelfile. It uses 4.2 GB, runs 100% on the GPU
(all 37 layers) and loads in about 15 s. Accuracy on the benchmark photos stayed at 9/9.

The Modelfile and the patch script are in [`bench/qwen2.5vl-jetson/`](../bench/qwen2.5vl-jetson). The step-by-step
recipe (pull the stock model, find its GGUF blob, run `gguf_set_maxpix.py`, `ollama create`) is in
[benchmarks.md, "Building qwen2.5vl-jetson:3b"](benchmarks.md#building-qwen25vl-jetson3b). It uses the service image
built above (`$IMAGE`) only for its Python and the `gguf` package. Any Python 3.8+ with `pip install gguf numpy`
works as well. Check the result:

```bash
ollama show qwen2.5vl-jetson:3b      # Parameters: num_ctx 4096, temperature 0.0001
ollama ps                            # after a request: qwen2.5vl-jetson:3b, 100% GPU
```

## service.env

`/opt/autofill/service.env` holds the service's settings, including the token. It is owned by root with mode 600,
and the container reads it with `--env-file`, so the token never appears on a command line or in `ps`. Start from
[`service.env.example`](../deploy/jetson/service.env.example):

| Variable | Value | Why |
|---|---|---|
| `BACKEND` | `ollama` | Use the Ollama backend (the image's default too) |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | The host's Ollama, reachable because of `--network host` |
| `OLLAMA_MODEL` | `qwen2.5vl-jetson:3b` | The patched model above |
| `OLLAMA_LOG_RAW` | `0` | `1` logs every raw model answer, including the description of each photo, in the container log. Useful for debugging bad suggestions, but leave it off in production. |
| `AUTOFILL_TOKEN` | a random secret | Bearer token clients send. Placeholder in the example; never commit the real one |
| `OLLAMA_KEEP_ALIVE` | `-1` | Keep the model loaded forever. The board is dedicated to the service, so no request should wait for a reload after an idle spell |
| `BACKEND_TIMEOUT_S` | `45` | See below |
| `IMAGE_SIZES` | `448,560*,1024` | Image-size presets offered to Apotheke, 560 px recommended. See [Model and image size choice](#model-and-image-size-choice-allowed_models-and-image_sizes) |
| `ALLOWED_MODELS` | `qwen2.5vl-jetson:3b` | Lists only the default model, so `/v1/info` gets a `models` field (Apotheke v2.4's model dropdown) with no extra model and no model switches. See the same section |

Cloudflare is not enabled by this file. An optional Workers AI fallback is documented in
[docs/cloudflare-backend.md](cloudflare-backend.md), including the env vars and the DevOps steps for
putting a token in this file (root, mode 600, never on a command line or in a label). Leave
`FALLBACK_BACKEND` unset or `none` until that opt-in is deliberate. The primary stays `ollama`.

**45 rather than 25.** A warm Qwen request takes about 13.5 s, and loading the model about 15 s. The service admits
a request only if load (if needed) plus answer fits in 90% of the timeout, judged by the slowest of the last 5
answers and a load time never assumed below 15 s.

- At **25**, a request that finds the model unloaded, or would queue behind another, doesn't fit. It gets a fast
  503 with `Retry-After` while the model reloads in the background. That was the right setting while Apotheke
  v2.2 clients, which time out at 30 s, were in use.
- At **45**, a cold request (15 + 13.5 s) or one queued behind another (2 × 13.5 s) fits, so it waits and gets its
  answer. Apotheke v2.3 and later show progress meanwhile. Apotheke's client timeout is its own admin setting (default
  50 s), independent of this one. Keep it at least about 5 s above `BACKEND_TIMEOUT_S`, so the service's own 503/504
  reaches the user instead of a client-side timeout. Measured
  on the board: cold 29.5 s, warm 5.5 s, the queued second of two concurrent requests 28.6 s.

Create the file without printing the token, then write the curl header file the scripts use:

```bash
install -d -m 755 /opt/autofill
umask 077
sed '/^AUTOFILL_TOKEN=/d' $REPO/deploy/jetson/service.env.example > /opt/autofill/service.env
printf 'AUTOFILL_TOKEN=%s\n' "$(openssl rand -hex 32)" >> /opt/autofill/service.env
chmod 600 /opt/autofill/service.env
# /opt/autofill/auth.hdr (mode 600, one line "Authorization: Bearer <token>") for curl -H @file:
$REPO/bench/make-auth-header.sh /opt/autofill/service.env /opt/autofill/auth.hdr
```

Give the token to Apotheke's admin over a private channel. To rotate it, replace the line, rerun
`make-auth-header.sh`, recreate the container with `run-service.sh`, and update Apotheke's settings.

Before changing `service.env`, keep a copy:
`cp -p /opt/autofill/service.env /opt/autofill/service.env.bak-$(date +%Y%m%d%H%M)`.

## Run the container

Once the model and `service.env` exist, (re)create the container with [`run-service.sh`](../deploy/jetson/run-service.sh):

```bash
deploy/jetson/run-service.sh "$IMAGE"
```

The script keeps the old container as a fallback. Port 8080 on the host network can only be held by one container,
so it renames the running `autofill` to `autofill-prev` and stops it, then starts the new one and waits up to
`WAIT_S` (default 120 s) until it answers `http://127.0.0.1:8080/healthz` (`HEALTH_URL`), or Docker reports it
healthy through the image's `HEALTHCHECK`. Polling `/healthz` directly matters, because Docker's first health check
only runs after 30 s, and the old container is already stopped by then. On the board (2026-10-18), the first real switch waited for
Docker's health status and took 36 s from start to finish. With `/healthz` polled directly, later switches took
8–10 s, which includes stopping the old container. The fast poll uses `curl` on the host (L4T has it). Without
`curl`, or with `HEALTH_URL` set to empty, the script falls back to Docker's health status. `WAIT_S` counts
wall-clock time, so a hanging `/healthz` (each poll times out after 3 s) can't stretch the wait. `WAIT_S` must be a
positive whole number of seconds with no leading zero and at most 6 digits (up to 999999); anything else (`abc`,
`1.5`, `0`, a 7-digit or larger number) stops the script with a clear message before it touches any container.
`/healthz` stays `200` until a model is resident. Once a warm model, or the loaded production model, has Ollama `size_vram` 0, it answers `503` `{"status":"degraded"}` so a CPU-only runner (Ollama started before CUDA) does not look healthy. `curl -fsS` then fails.

**Port check.** Once the old container is stopped, nothing should answer `HEALTH_URL`. If something still does,
another process or container is holding port 8080, and its answer would be mistaken for the new container's. The
script then refuses to switch: it starts nothing and puts `autofill-prev` back. On a first deploy, with no old
container, it just refuses. Find what holds the port with `ss -ltnp 'sport = :8080'` before trying again.
The check only sees a process that answers `HEALTH_URL` with a 2xx status. One that holds the port but answers
404 or 500 gets past it; the new container then fails to bind, and the script rolls back as usual. In that
case the new container's log lines, which the script prints before the rollback, show a bind error
(`address already in use`), and the script adds a hint to check `ss -ltnp 'sport = :8080'` for the process
holding the port.

On success it removes `autofill-prev`. If `docker run` fails, or the new container exits or isn't healthy in time, it prints the
new container's last log lines, removes it, and renames and starts `autofill-prev` again, so the old version keeps
serving. Ctrl-C or SIGTERM during the switch rolls back the same way, and further signals are ignored while the rollback
runs. One caveat with real Docker: if `docker run` is interrupted, the daemon may still create the new container
after the rollback has finished. The old container is then left stopped as `autofill-prev`, and the recovery steps
below apply. If an
`autofill-prev` is still left over (for example after a power cut mid-deploy), the script refuses to run until you
resolve it:

- if `autofill` is missing or broken: `docker rm -f autofill` (only if a broken one exists), then
  `docker rename autofill-prev autofill && docker start autofill`;
- otherwise (the new `autofill` works): `docker rm autofill-prev`.

The script's behaviour is covered by [`deploy/jetson/tests/run-tests.sh`](../deploy/jetson/tests/run-tests.sh),
which CI's `deploy-scripts` job runs. It uses fake `docker`, `curl`, `tc`, `systemctl` and `journalctl`
commands (no Docker, network or root needed) and checks: switches, rollbacks, signals before and during a rollback,
the `/healthz` poll, the port check, the wall-clock `WAIT_S` and its validation, the bind-error hint,
`build-image.sh` refs, `dl-cap.sh` (including a verbatim board `tc filter show` fixture), the NetworkManager hook,
`wait-cuda.sh` and `ollama-gpu-check.sh`. Run it locally the same way. It takes about 1 min 45 s, because the
signal and wait-timeout cases use real timing, and removes its temporary files when it exits, also when interrupted
with Ctrl-C.

The new container is started with:

```bash
docker run -d --name autofill --network host --runtime nvidia --restart unless-stopped \
  --env-file /opt/autofill/service.env apotheke-autofill:jetson-<sha>
```

- `--network host`: the service listens on port 8080 of the board and reaches Ollama on `127.0.0.1:11434`.
- `--runtime nvidia`: the base image expects the L4T GPU libraries the NVIDIA runtime mounts.
- `--restart unless-stopped`: Docker restarts it after a crash and after a reboot.

`docker ps --filter name=autofill --format '{{.Image}}'` shows which tag is deployed. (The board's older
`/opt/autofill/run-service.sh` has the tag written into the script instead of taking it as an argument.)

## Verify

[`bench/verify.sh`](../bench/verify.sh) checks `/healthz`, `/v1/info` (`ready`, `avg_latency_ms`), one suggestion,
recovery after `ollama stop` (cold start) and two concurrent requests. It reads the token from
`/opt/autofill/auth.hdr` and the model from `service.env`.

```bash
$REPO/bench/verify.sh
```

It unloads the model and sends real requests, so run it only when nobody else is using the board. Expected with
`BACKEND_TIMEOUT_S=45`: healthz ok, `ready= True`, the basic suggestion 200 in about 5–14 s, the cold request
200 in about 30 s, and both concurrent requests 200, the second after about 28 s. With 25, the cold and second
concurrent requests get a fast 503 with `Retry-After` instead. The script ends with `ollama ps`, which should show
`qwen2.5vl-jetson:3b` at 100% GPU.

A quick check that doesn't unload anything:

```bash
curl -s localhost:8080/healthz
curl -s -H @/opt/autofill/auth.hdr localhost:8080/v1/info
docker ps --filter name=autofill --format '{{.Image}} {{.Status}}'
```

## Redeploy a new commit

```bash
cd $REPO && git fetch origin
deploy/jetson/build-image.sh origin/main                 # note the printed tag
docker ps --filter name=autofill --format '{{.Image}}'   # note the current tag, for rollback
deploy/jetson/run-service.sh apotheke-autofill:jetson-<new sha>
$REPO/bench/verify.sh
```

If the new container doesn't come up healthy, `run-service.sh` puts the previous one back by itself (see
[Run the container](#run-the-container)) and exits non-zero.

The model stays loaded in Ollama while the container is replaced, so the new container is ready within seconds.
If the new commit adds environment variables, add them to `service.env` first (keep a backup). Keep the previous
image until the new one has passed verify and QA, then remove old tags with `docker rmi` to free disk space.

## Roll back

```bash
docker images apotheke-autofill                          # pick the previous jetson-<sha>
cp -p /opt/autofill/service.env.bak-<stamp> /opt/autofill/service.env   # only if the env changed
deploy/jetson/run-service.sh apotheke-autofill:jetson-<previous sha>
$REPO/bench/verify.sh
```

If the previous image has been removed, rebuild it with `build-image.sh <previous sha>`.

## Inbound bandwidth limit (30 Mbit/s)

All traffic coming into the board on `eth0` is limited to 30 Mbit/s, **with no exemptions**: internet downloads,
LAN, SSH and photo uploads to the service alike. The L4T R35 kernel has no IFB module, so the traffic can't be
shaped. Instead [`dl-cap.sh`](../deploy/jetson/dl-cap.sh) polices it with a `tc` ingress filter for IPv4 and IPv6
(rate 30 Mbit, burst 384 KB). Packets over the rate are dropped, and TCP backs off to about 30 Mbit/s.
[`dl-cap.service`](../deploy/jetson/dl-cap.service) applies it at boot. The script stops at the first failing `tc`
call (`set -e`), so the unit fails visibly instead of leaving a partial cap. The unit has `Restart=on-failure`
(every 10 s, at most 10 times in 5 minutes), for example while `eth0` isn't up yet. systemd allows that for
`Type=oneshot` since version 244; L4T R35 (Ubuntu 20.04) has 245.

After applying, the script checks that `tc filter show dev eth0 parent ffff:` lists a `police` filter for both
`ip` and `ipv6`, and exits 1 if not, so `Restart=on-failure` tries again rather than reporting success with no cap.
`dl-cap.sh check` runs the same check without changing anything: it exits 0 if the cap is in place and 1 if it's
missing.

**NetworkManager hook.** `dl-cap.service` runs once, after `network-online.target`. On the board, NetworkManager
sometimes takes over `eth0` a moment *after* that (journal: `device (eth0): state change: unmanaged -> unavailable`,
`created default wired connection`, then `carrier: link connected`). That resets the device, the ingress qdisc
goes with it, and the cap is silently gone while `dl-cap.service` still shows `active (exited)`; only a manual
`systemctl restart dl-cap` brought it back. The NetworkManager dispatcher hook
[`nm-dispatcher-90-dl-cap`](../deploy/jetson/nm-dispatcher-90-dl-cap) fixes that: when `eth0` (the `DEV` in
`dl-cap.sh`) comes `up`, it runs `systemctl --no-block restart dl-cap.service`. On `dhcp4-change` for `eth0`, and
on `connectivity-change` (which NetworkManager sends with an empty interface), it runs `dl-cap.sh check` first and
only restarts if the cap is missing — a lease renewal must not leave a short uncapped gap or burn the unit's start
limit (10 per 5 min). `--no-block` queues the restart and returns at once, so the hook never holds up
NetworkManager's dispatcher while `network-online.target` is still pending. Other interfaces and other events are
ignored, and the hook always exits 0. It does nothing while `dl-cap.service` is disabled, so
`systemctl disable --now dl-cap` keeps the cap off. A plain `systemctl stop dl-cap` lasts only until the next
`eth0` `up`.

Install (as root). NetworkManager only runs dispatcher scripts that are owned by root and not writable by group or
others:

```bash
install -m 755 $REPO/deploy/jetson/dl-cap.sh /usr/local/sbin/dl-cap.sh
install -m 644 $REPO/deploy/jetson/dl-cap.service /etc/systemd/system/dl-cap.service
install -o root -g root -m 755 $REPO/deploy/jetson/nm-dispatcher-90-dl-cap /etc/NetworkManager/dispatcher.d/90-dl-cap
systemctl daemon-reload && systemctl enable --now dl-cap
```

Verify, now and **after every reboot** (the failure only showed up after a reboot):

```bash
tc -s filter show dev eth0 parent ffff:   # two filters (protocol ip and ipv6), each: police ... rate 30Mbit burst 384Kb ... drop
/usr/local/sbin/dl-cap.sh check           # "cap is in place on eth0", exit 0; exit 1 if it's missing
journalctl -b -u dl-cap -u NetworkManager-dispatcher   # after a reboot: dl-cap restarted once NetworkManager had eth0
```

With `-s`, each filter also shows `Sent ... bytes` and, under load, `dropped` and `overlimits` counters. If the
filters are missing, run `systemctl restart dl-cap` and check that `/etc/NetworkManager/dispatcher.d/90-dl-cap`
exists, is owned by root and has mode 755. The test harness covers the verify step, `check` and the hook (with fake
`tc` and `systemctl`).

`systemctl stop dl-cap` removes it. Effects: image builds and `ollama pull` are slow (see above), and a 1 MB photo
upload takes about 0.3 s. Service timings measured on the board itself (loopback) are not affected.

## Self-hosted GitHub Actions runner

The board runs a GitHub Actions runner for this repository, used only by the hand-run
[Jetson hardware workflow](hardware-ci.md).

| | |
|---|---|
| Runner | actions/runner 2.338.0 (linux-arm64) in `/opt/actions-runner`, registered to this repo only |
| Labels | `self-hosted`, `Linux`, `ARM64`, `jetson` (workflows use `runs-on: [self-hosted, jetson]`) |
| User | `ghrunner`, a member of the `docker` group (so effectively root on the board) |
| Service | `actions.runner.thordin9-apotheke-autofill-service.<runner-name>.service`, enabled, created by `svc.sh install` |

Set-up:

```bash
useradd -m -s /bin/bash ghrunner && usermod -aG docker ghrunner
mkdir -p /opt/actions-runner && chown ghrunner: /opt/actions-runner && cd /opt/actions-runner
# Download and unpack actions-runner-linux-arm64-<version>.tar.gz as ghrunner (see Settings → Actions → Runners → New runner).
sudo -u ghrunner ./config.sh --url https://github.com/thordin9/apotheke-autofill-service \
  --token <registration token from that page> --name <runner-name> --labels jetson --unattended
./svc.sh install ghrunner && ./svc.sh start
```

The registration token is short-lived (about an hour) and only works once, but it is passed on the command line, so
it is briefly visible in `ps` and lands in shell history. Run `config.sh` from a shell that doesn't keep history
(for example `HISTFILE=/dev/null bash`, or prefix the command with a space if `HISTCONTROL=ignorespace`), and
don't store the token. The runner keeps its own credentials
in `/opt/actions-runner/.credentials*`, which must not leave the board.

**Security.** The repo is public and the runner's user can control Docker on a LAN machine. So:

- The hardware workflow only runs by hand (`workflow_dispatch`). Never add `pull_request`, `pull_request_target`
  or any trigger a fork can fire, and there is no schedule.
- The repo's Actions setting "Approval for running fork pull request workflows from contributors" is set to
  **"Require approval for all external contributors"** (API: `approval_policy: all_external_contributors`), so a
  fork's workflow doesn't run until a maintainer approves it.
- The workflow uses no secrets and a read-only `GITHUB_TOKEN`.

## Reboot behaviour

Everything comes back on its own after a reboot or power cut, tested on 2026-10-18 (dl-cap / Ollama CUDA boot
fixes added after the 2026-10-19 reboot that left the inbound cap and Ollama on CPU until a manual restart):

- `docker.service` is enabled, and the `autofill` container has `--restart unless-stopped`.
- `ollama.service` is enabled, with `wait-cuda.sh` as `ExecStartPre` and `ollama-gpu-check.service` after it
  (see [Ollama and its drop-in](#ollama-and-its-drop-in)). On start the service warms the model up, which loads
  `qwen2.5vl-jetson:3b` and, with `OLLAMA_KEEP_ALIVE=-1`, keeps it loaded. `/v1/info` reports `ready: true` once
  that's done (about 15 s after Ollama is up).
- `dl-cap.service` is enabled and reapplies the 30 Mbit/s cap, and the NetworkManager hook reapplies it again if
  NetworkManager takes over `eth0` afterwards. Check it with `tc -s filter show dev eth0 parent ffff:` or
  `dl-cap.sh check` (see [Inbound bandwidth limit](#inbound-bandwidth-limit-30-mbits)).
- The runner service is enabled and reconnects to GitHub.
- The power mode persists (`nvpmodel` mode 8).

**Post-reboot checklist** (a reboot test is only green when all of these hold):

```bash
ollama ps                              # qwen2.5vl-jetson:3b at 100% GPU, keep_alive Forever
/usr/local/sbin/ollama-gpu-check.sh check   # exit 0 (library=CUDA in this start's journal)
/usr/local/sbin/dl-cap.sh check        # "cap is in place on eth0"
tc filter show dev eth0 parent ffff:   # two police filters (protocol ip and ipv6), rate 30Mbit burst 384Kb
curl -s localhost:8080/healthz         # {"status":"ok"} (liveness only)
curl -s -H @/opt/autofill/auth.hdr localhost:8080/v1/info   # "ready":true, models: only qwen2.5vl-jetson:3b
journalctl -b -u ollama-gpu-check -o cat   # which path: "GPU already in use" or "restarting ... once" + "after restart, library=CUDA"
```

After a reboot, the quick check under [Verify](#verify) is also enough once the checklist above has passed once
on this image.

## Model and image size choice: ALLOWED_MODELS and IMAGE_SIZES

Feature 7 (service PRs #10 and #11, API v1.2) lets Apotheke's admin choose the model and the image size. It is
deployed on the board with:

```
IMAGE_SIZES=448,560*,1024
ALLOWED_MODELS=qwen2.5vl-jetson:3b
```

- `IMAGE_SIZES`: a comma-separated list of long edges in pixels, each 64–8192, with one `*` marking the recommended
  size. Without a `*` the largest size is recommended. More than one `*` or a bad value stops the service at
  startup. The client resizes the photo; the service doesn't. **560 px is recommended** because photos sent at
  560 px answer in a median 8.3 s instead of 13.9 s at 1024 px, with the same accuracy on the 19 benchmark photos.
  448 px is a bit faster but names get worse, and 1024 px stays for labels with small print. 672 px was slower than
  560 px and no more accurate, so it's left out. See
  [benchmarks.md, "Image-size sweep and Qwen3-VL 2B"](benchmarks.md#image-size-sweep-and-qwen3-vl-2b-2026-10-18).
- `ALLOWED_MODELS`: a comma-separated list of the Ollama tags clients may request. The default always comes from
  `OLLAMA_MODEL` and is always allowed, so it doesn't need to be listed. Tags that aren't installed are left out of
  `/v1/info` instead of causing an error. On the board it is set to **only the default model**,
  `qwen2.5vl-jetson:3b`. That adds nothing a client could switch to. Its only effect is that `/v1/info` gets a
  `models` field (just Qwen, marked default and loaded), which Apotheke v2.4 needs to show its model dropdown. A
  request for any other model falls back to Qwen and reports `model_used: qwen2.5vl-jetson:3b`. No other model is
  listed, for two reasons:
  - The lower-`max_pixels` variants (`qwen2.5vl-jetson:3b-200k/-300k/-400k`) give the same speed and accuracy curve
    as client-side resizing, but every switch is a 21–29 s cold start that evicts the default model.
  - `qwen3-vl:2b` is installed but **must not be listed**. It can't load on this board with Ollama 0.15.6, because
    its 2 MP image reservation is hard-coded and runs out of memory, and the service offers any installed model
    that is listed.

Both are opt-in. With `ALLOWED_MODELS` unset, `/v1/info` has no `models` list and `context.model` is ignored.
With `IMAGE_SIZES` unset, it has no `image_sizes`. Every response reports `model_used`. Check the deployed values:

```bash
curl -s -H @/opt/autofill/auth.hdr localhost:8080/v1/info | python3 -m json.tool
# "ready": true,
# "models": [{"id": "qwen2.5vl-jetson:3b", "default": true, "loaded": true}],
# "image_sizes": [448, 560 (recommended), 1024]
```

Only one model fits in memory, so if another model is ever listed, a request for a model other than the loaded one
is a cold start of at least 15 s and follows the same fast-503 or wait rules.
