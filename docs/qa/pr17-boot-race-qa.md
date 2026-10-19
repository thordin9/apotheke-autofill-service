# QA: PR #17 boot fixes (dl-cap after NetworkManager, Ollama CUDA race)

- PR #17, commit `fe5f5ba` on main `23e51e2`
- Harness with fake `tc`, `systemctl`, `journalctl` (plus own probes with a `--since`-honouring journal fake). No board run yet. The reboot test is in "Still to do".
- Result: **dl-cap / NetworkManager hook: PASS. Ollama boot race: changes requested** (M1, M2)

## Test plan
1. CI, shellcheck, secret and host/address scan. Harness at head, and the head's tests against main's scripts.
2. dl-cap: verify step, `check` mode, the verbatim iproute2-ss200127 fixture, the hook with every `is-enabled` state, interface and event, and repeated down/up events.
3. wait-cuda: both orderings, never ready, deadline boundaries, bad `CUDA_WAIT_S`, the board's sysfs paths. Start timeout vs ExecStartPre.
4. gpu-check: the board's exact `inference compute` lines, several lines in one start, old lower-case `library=cuda`, quoting, `source=` swapped, empty or late journal, journal hang, the previous start's lines, the marker in the same and a fresh `/run`, `check` exit codes.
5. Units: `systemd-analyze verify`, ordering and dependencies. Docs and CHANGELOG.

## Results
| # | Check | Result |
|---|---|---|
| 1 | CI / shellcheck / scans | green (127/127); clean; 0 hits |
| 1 | Head tests on main's scripts | 69 pass, 58 fail (all new cases) |
| 2 | dl-cap + hook | pass. `connectivity-change` never carries an interface (NetworkManager passes it empty), so that branch is dead |
| 3 | wait-cuda | exits 0 on every path, waits exactly `CUDA_WAIT_S`. Ready just before the deadline still times out (no final check). `CUDA_WAIT_S` above ~85 s exceeds the 90 s default start timeout |
| 4 | gpu-check | at most one restart per boot on every path; keys on `library=`, not `source=`. Gives up after 30 s with no restart if the line is later (M1). Last line wins and the match is case-sensitive (L2). `--since` has 1 s resolution: a CPU line in the same second as the restarted start is misread as "still not CUDA" (L3). A journal hang blocks the oneshot (no start timeout) |
| 5 | Units, docs | verify clean. Doc fixes: manual restart doesn't run gpu-check; `/healthz` has no readiness; the checklist must show which path took effect |

## Findings
- **M1** `OLLAMA_GPU_CHECK_WAIT_S` default 30 s is shorter than Ollama's discovery (30 s bootstrap per library dir plus 30 s validation in v0.15.x). Use 120 s.
- **M2** At the failing boot the device nodes (and per DevOps the sysfs paths) already existed, so wait-cuda likely returns at once. The race is CUDA initialisation. Gate on a bounded `cuInit` probe or nv* unit ordering. The single restart is otherwise ungated.
- **L1** Add `TimeoutStartSec` to the drop-in, or cap `CUDA_WAIT_S`.
- **L2** Any `library=cuda` line (any case) counts as GPU, scoped to the current start.
- **L3** Scope the journal by `_SYSTEMD_INVOCATION_ID`.
- **L4** `TimeoutStartSec` on gpu-check. Reconsider `Wants=ollama.service`.
- **L5** `dhcp4-change` re-applies the cap on every renewal and counts toward the start limit.

## Still to do (board)
Reboot test with the read-only checklist (see the PR #17 QA report). It must record:
- which path took effect (wait, restart, or still CPU);
- the time from `discovering available GPUs...` to `inference compute`;
- the wait-cuda line and the kernel nvgpu timestamps;
- `ollama ps` at 100% GPU;
- `/v1/info` ready with only `qwen2.5vl-jetson:3b`;
- both police filters, and `dl-cap.sh check` exit 0.

## Reboot checklist (read-only)

Run after the reboot, as the board's admin user. Nothing below changes state. Two notes:
- `curl /v1/info` is a read, but if Ollama has just been restarted and the model is not loaded yet, the service
  itself queues its normal reload of the default model (`app/backends/ollama.py`, `info()`), as it would for any client.
- Never print `service.env` or `auth.hdr`. The grep below prints only the two named lines.

```bash
# 0. Versions, boot time (all times EEST)
date; uptime -s; ollama --version; systemctl --version | head -1; cat /etc/nv_tegra_release | head -1

# 1. Which boot-race path took effect
journalctl -b -u ollama -o short-precise --no-pager | grep -E 'wait-cuda|server config|discovering available GPUs|looking for compatible GPUs|failure during GPU discovery|inference compute|offloaded' | cut -c1-260
journalctl -b -u ollama-gpu-check -o short-precise --no-pager
ls -l --time-style=full-iso /run/ollama-gpu-check.restarted 2>&1    # present = gpu-check restarted Ollama this boot
systemctl show ollama -p NRestarts,ActiveEnterTimestamp,ExecMainStartTimestamp,InvocationID,TimeoutStartUSec,DropInPaths
systemctl show ollama-gpu-check -p ActiveState,SubState,Result,ExecMainStatus,ActiveEnterTimestamp,InactiveExitTimestamp
sudo /usr/local/sbin/ollama-gpu-check.sh check; echo "gpu-check check exit=$?"   # 0 = this start logged library=CUDA (sudo: needs journal access)
systemd-analyze critical-chain ollama.service ollama-gpu-check.service
systemd-analyze blame | grep -E 'ollama|dl-cap|NetworkManager-wait-online|nvpmodel|nv\.service'

# 2. Ollama on GPU
ollama ps                                   # qwen2.5vl-jetson:3b, PROCESSOR 100% GPU, UNTIL Forever
# if empty right after a gpu-check restart: run step 3 once, wait ~20 s, then repeat `ollama ps`

# 3. Service ready, Qwen-only allowlist
curl -s localhost:8080/healthz; echo        # {"status":"ok"} (liveness only)
curl -s -H @/opt/autofill/auth.hdr localhost:8080/v1/info; echo   # "ready":true, "models" lists only qwen2.5vl-jetson:3b
sudo grep -E '^(ALLOWED_MODELS|OLLAMA_MODEL)=' /opt/autofill/service.env   # ALLOWED_MODELS=qwen2.5vl-jetson:3b only

# 4. Inbound cap: both police filters
tc -s filter show dev eth0 parent ffff:     # protocol ip (pref 2) and protocol ipv6 (pref 3), each police rate 30Mbit burst 384Kb drop
/usr/local/sbin/dl-cap.sh check; echo "dl-cap check exit=$?"   # 0
journalctl -b -u dl-cap -u NetworkManager-dispatcher -u NetworkManager -o short-precise --no-pager | grep -E 'dl-cap|eth0.*state change|90-dl-cap|Started|Deactivated|start-limit'
systemctl show dl-cap -p NRestarts,Result,ActiveEnterTimestamp
systemctl is-enabled dl-cap NetworkManager-dispatcher ollama ollama-gpu-check
stat -c '%U:%G %a %n' /etc/NetworkManager/dispatcher.d/90-dl-cap   # root:root 755

# 5. Board facts the scripts assume
ls -l /dev/nvhost-ctrl-gpu /dev/nvmap; ls -d /sys/module/nvgpu /sys/devices/gpu.0 /sys/class/devfreq/*gv11b* /sys/devices/platform/17000000.gv11b 2>&1
journalctl -k -b -o short-precise --no-pager | grep -iE 'nvgpu|gv11b|gk20a' | head -20   # when the GPU driver came up vs ollama start
systemctl list-unit-files | grep -E '^(nv|nvpmodel|nvfan|nvphs)' ; command -v python3
```

## Reading the result (exactly one must apply)

- (a) wait-cuda alone worked: gpu-check logs `GPU already in use (library=CUDA)`, no marker file, one
  `inference compute ... library=CUDA` line this boot. wait-cuda logs `GPU ready` (immediate) or `GPU ready after Ns`.
- (b) The restart worked: gpu-check logs `library=cpu on this start; restarting ollama.service once` then
  `after restart, library=CUDA`. The marker file exists. The journal has a `library=cpu` line, then a later
  `library=CUDA` line. NRestarts stays 0 (a restart by systemctl doesn't count).
  Note (finding L3): if gpu-check says `after restart, still not library=CUDA (status=1)` but step 1's last
  `inference compute` line is `library=CUDA` and `check` exits 0, the restart did work. The message came from the
  old CPU line falling in the same second as the new start.
- (c) Failed: the last `inference compute` line is `library=cpu`, or gpu-check logs
  `no inference compute line after 30s` (finding M1), or `ollama ps` shows CPU.
- In every case, record: the gap from `discovering available GPUs...` to `inference compute`, the wait-cuda line,
  and the kernel nvgpu timestamps vs the Ollama start. These decide M1/M2.

## Applied in this PR (after the findings above)

The Ollama boot half of #17 was updated against the findings above:

| Finding | Change |
|---|---|
| M1 | `OLLAMA_GPU_CHECK_WAIT_S` default 120 s |
| M2 | `wait-cuda.sh` polls a bounded `cuInit` probe as the `ollama` user; drop-in `After=nv.service nvpmodel.service`; optional `OLLAMA_GPU_CHECK_RESTART_DELAY_S` (default 10) before the once-per-boot restart. `CUDA_WAIT_S=0` skips the wait (QA race reproduction). Every device/sysfs and cuInit result is logged with a timestamp and `+Ns` elapsed |
| L1 | `TimeoutStartSec=180` on the drop-in; `CUDA_WAIT_S` capped at 150 |
| L2 | Any `library=cuda` line (any case, optional quotes) counts as GPU |
| L3 | Journal scoped by `_SYSTEMD_INVOCATION_ID` |
| L4 | `TimeoutStartSec=600` on gpu-check; `Requisite=`/`After=ollama.service` (not `Wants=`) |
| L5 | Dispatcher: `up` always restarts; `dhcp4-change` / empty-interface `connectivity-change` check then restart only if the cap is missing |
| I1 | One last readiness check at the `CUDA_WAIT_S` deadline |
| I2 | Aggregate no-model-calls assertion across every gpu-check case |
| D1–D3 | Docs: gpu-check only runs at boot; `/healthz` is liveness only; `ollama ps` empty until `/v1/info` reloads |
| D4 | Docs + `check` hint: needs root or the `systemd-journal` group |

To reproduce the old race on one boot (skip wait-cuda, keep the once-per-boot restart), see
[Reproduce the old race (QA)](../deploy-jetson.md#ollama-and-its-drop-in) in `docs/deploy-jetson.md`.
