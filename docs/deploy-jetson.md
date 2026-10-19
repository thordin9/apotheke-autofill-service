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
which CI's `deploy-scripts` job runs. It uses fake `docker`, `curl` and `tc` commands (no Docker, network or root
needed) and checks: switches, rollbacks, signals before and during a rollback, the `/healthz` poll, the port check,
the wall-clock `WAIT_S` and its validation, the bind-error hint, `build-image.sh` refs and `dl-cap.sh`. Run it
locally the same way. It takes about 1 min 30 s, because the signal cases use real timing, and removes its
temporary files when it exits, also when interrupted with Ctrl-C.

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

```bash
install -m 755 $REPO/deploy/jetson/dl-cap.sh /usr/local/sbin/dl-cap.sh
install -m 644 $REPO/deploy/jetson/dl-cap.service /etc/systemd/system/dl-cap.service
systemctl daemon-reload && systemctl enable --now dl-cap
tc filter show dev eth0 ingress          # two filters: police rate 30Mbit burst 384Kb ... drop
```

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

Everything comes back on its own after a reboot or power cut, tested on 2026-10-18:

- `docker.service` is enabled, and the `autofill` container has `--restart unless-stopped`.
- `ollama.service` is enabled. On start the service warms the model up, which loads `qwen2.5vl-jetson:3b` and,
  with `OLLAMA_KEEP_ALIVE=-1`, keeps it loaded. `/v1/info` reports `ready: true` once that's done (about 15 s
  after Ollama is up).
- `dl-cap.service` is enabled and reapplies the 30 Mbit/s cap.
- The runner service is enabled and reconnects to GitHub.
- The power mode persists (`nvpmodel` mode 8).

After a reboot, the quick check under [Verify](#verify) is enough.

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
