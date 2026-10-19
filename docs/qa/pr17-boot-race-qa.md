# QA: PR #17 boot fixes (dl-cap after NetworkManager, Ollama CUDA race)

- Final head: **`53196dc`** (fast-forward on `639c148`; comments, docs and tests only). Round 1: PR #17 commit `fe5f5ba` on main `23e51e2`. Recheck: `cde402e` (rebased on main `63806a1`) and its merge with main, `639c148` (parents `cde402e` and `6f43494`; the diff against `cde402e` is exactly #21's CHANGELOG change).
- Harness with fake `tc`, `systemctl`, `journalctl` and `python3` (plus own probes with a `--since`-honouring journal fake and hanging cuInit fakes). Board (xavier12, `639c148` installed, same units and scripts as `53196dc`): one forced-race boot (R1b) and three normal boots, see "Board tests".
- Result round 1: **dl-cap / NetworkManager hook: PASS. Ollama boot race: changes requested** (M1, M2).
- Result recheck (`639c148`): **PASS.** All round-1 findings are fixed. R1 (forced-race procedure) and the R2/R3 wording are fixed in the docs and comments at `53196dc`. Board: forced-race boot R1b **PASS** (path (b)); normal boots 1, 2 and 3 **PASS** (path (a)). See "Recheck" and "Board result".
- **Overall verdict: PASS for merge at `53196dc`.** Every boot ended with Ollama on the GPU, and the restart path ran exactly once when forced. Non-blocking follow-ups are listed under "Follow-ups".

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

## Board tests
All done on xavier12 on 2026-10-19 (Ollama 0.15.6, systemd 245, L4T R35 rev 6.0), read-only over SSH after each reboot:

| Boot | Expected | Result |
|---|---|---|
| R1b forced race (CPU-only first start) | path (b): restart once | **PASS**: one restart by gpu-check, then `library=CUDA` |
| Normal boot 1 | path (a) | **PASS**: cuInit OK at +1 s, `library=CUDA` on the first start, no restart |
| Normal boot 2 | path (a) | **PASS**: same |
| Normal boot 3 | path (a) | **PASS**: same |

R1a (exact pre-#17 Ollama start) was not run; R1b already exercised the restart path deterministically. Each run
records which path took effect, the time from `discovering available GPUs...` to `inference compute`, the wait-cuda
lines, `ollama ps` at 100% GPU, `/v1/info` ready with only `qwen2.5vl-jetson:3b`, both police filters and
`dl-cap.sh check` exit 0. Details are in the "Board result" sections at the end.

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
journalctl -b _SYSTEMD_INVOCATION_ID=$(systemctl show -p InvocationID --value ollama) -o short-precise --no-pager | grep -E 'wait-cuda|inference compute|overrode'   # current start only
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
  `inference compute ... library=CUDA` line this boot. wait-cuda logs `device/sysfs: present` and
  `cuInit probe #k: OK (...)`; probes #1..k-1 (`not ready yet` / `timed out`) show how long CUDA lagged.
- (b) The restart worked: gpu-check logs `waiting 10s before the single restart`, `restarting ollama.service once`,
  then `after restart, library=CUDA`. The marker file exists. The journal has a `library=cpu` line, then a later
  `library=CUDA` line, and a second set of wait-cuda lines for the restarted start. NRestarts stays 0 (a restart by
  systemctl doesn't count). With `_SYSTEMD_INVOCATION_ID` scoping, the round-1 L3 same-second misreport no longer applies.
- (c) Failed: the last `inference compute` line is `library=cpu`, or gpu-check logs
  `no inference compute line after 120s`, or `ollama ps` shows CPU.
- In every case, record: the gap from `discovering available GPUs...` to `inference compute`, every wait-cuda line
  (with its `+Ns`), and the kernel nvgpu timestamps vs the Ollama start.

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

To reproduce the old race on one boot, use the procedure under "Recheck" (R1). The override in
[`docs/deploy-jetson.md`](../deploy-jetson.md#ollama-and-its-drop-in) does not remove the `After=` ordering.

## Recheck (`cde402e`, merged as `639c148`)
| Check | Result |
|---|---|
| CI at `cde402e` and `639c148` | green (lint-test, deploy-scripts, image); deploy-scripts `passed=197 failed=0` |
| `53196dc` | fast-forward on `639c148`; with comments stripped the units and scripts are identical (comments, docs, 3 new harness tests); CI green, deploy-scripts 200/200; locally 200/200, /tmp unchanged, shellcheck, gitleaks and token scan clean; the documented `zz-force-race.conf` R1b block equals the one run on the board apart from the file names |
| `639c148` | parents `cde402e` + `6f43494`; tree equals a clean automatic merge; diff vs `cde402e` = #21's CHANGELOG change only |
| Harness at `cde402e` (local) | 197/197, 1 min 57 s; /tmp unchanged |
| shellcheck, `sh -n`/`bash -n` | clean |
| Secret scan | gitleaks: no leaks in the 6 new commits; the real token: 0 hits in history and tree. Tree-mode hits are the pre-existing placeholders in `ci.yml` and `service.env.example` (same at `fe5f5ba`) |
| `systemd-analyze verify` (gpu-check, dl-cap, Ollama with drop-in, nv*/nvpmodel stubs) | clean |
| M1 | default wait 120 s; a line after the wait still means no restart, by design |
| M2 | cuInit gate: ok-after-4 s → ready at +4 s; always failing → timeout at `CUDA_WAIT_S` (5.07 s for 5); missing interpreter or libcuda → device checks only. `CUDA_WAIT_S=0` → exits in 0.01 s, no probe, even with device nodes missing. Restart delay 10 s measured (restart at +10.01 s) |
| Probe bound | a hanging probe is killed at 10 s (exit 124), or at 10+2 s with SIGKILL when it ignores SIGTERM (exit 137); the last probe gets only the remaining time (min 1 s). Measured total = `CUDA_WAIT_S` (25.03 s for 25, 12.02 s for 12, 3.02 s for 3); no leftover process. Exception: a process stuck in uninterruptible kernel sleep can't be killed; then only systemd's `TimeoutStartSec=180` ends the start |
| L1 / arithmetic | wait-cuda ≤ `CUDA_WAIT_S` (≤150) + ≤ 3 s overrun < 180. gpu-check worst case 120 + 10 + Ollama stop (default stop timeout 90) + start (≤180) + 120 = 520 < 600 |
| L2 | the board's exact GPU line → GPU, CPU line → CPU; GPU+CPU in one start, `library="CUDA"`, `library=cuda variant=jetpack5` → GPU; `source=` swapped → follows `library=` |
| L3 | with InvocationID: the board's CPU line for the first start, then the GPU line right after the restart → `after restart, library=CUDA` (no same-second misreport) |
| L4 | `TimeoutStartSec=600`, `Requisite=` + `After=` (see R2) |
| L5 | hook: `up` → restart always; `dhcp4-change` / `connectivity-change` (empty or eth0) → `dl-cap.sh check`, restart only if missing; other events or interfaces → nothing; disabled/masked → nothing; `dl-cap.sh` not installed → restart. `dl-cap.sh`, `dl-cap.service`, fake `tc` and the fixture are unchanged since `fe5f5ba` |

**R1 (Medium, QA procedure; fixed at `53196dc`).** The `99-qa-skip-wait-cuda.conf` override in
`docs/deploy-jetson.md` uses an empty `After=` to drop `After=nv.service nvpmodel.service`. systemd can't reset
dependencies in a drop-in (systemd.unit(5): "Dependencies (After=, etc.) cannot be reset to an empty list"). In
systemd 245, `config_parse_unit_deps` ignores an empty value. A `systemd-analyze verify` debug dump with the override
loaded still lists `After: nv.service` and `After: nvpmodel.service`. So that boot is not the old setup. Also, the
old race happened once and may not recur, so even an exact old setup doesn't guarantee the restart path runs. Use
one of these two boots instead (both are temporary writes on the board; undo them right after the checklist):

```bash
# Boot R1a — exact pre-#17 Ollama start (no ExecStartPre, no nv* ordering), gpu-check still active:
mv /etc/systemd/system/ollama.service.d/ollama-wait-cuda.conf /root/ollama-wait-cuda.conf.qa
systemctl daemon-reload && reboot
#   ...checklist...; undo:
mv /root/ollama-wait-cuda.conf.qa /etc/systemd/system/ollama.service.d/ollama-wait-cuda.conf && systemctl daemon-reload

# Boot R1b — deterministic restart path: the first start of this boot is CPU-only and skips the wait; the
# restarted start has neither (the env files are gone), so it runs wait-cuda's cuInit gate and should find CUDA.
cat > /etc/tmpfiles.d/zz-qa-ollama-once.conf <<'EOF'
f /run/qa-ollama-cpu-once.env 0644 root root - CUDA_VISIBLE_DEVICES=-1
f /run/qa-ollama-nowait-once.env 0644 root root - CUDA_WAIT_S=0
EOF
cat > /etc/systemd/system/ollama.service.d/99-qa-once.conf <<'EOF'
[Service]
EnvironmentFile=-/run/qa-ollama-cpu-once.env
EnvironmentFile=-/run/qa-ollama-nowait-once.env
ExecStartPost=+/bin/rm -f /run/qa-ollama-cpu-once.env /run/qa-ollama-nowait-once.env
EOF
systemctl daemon-reload && reboot
#   ...checklist; expect path (b) and Ollama's "user overrode visible devices" warning only on the first start...; undo:
rm /etc/tmpfiles.d/zz-qa-ollama-once.conf /etc/systemd/system/ollama.service.d/99-qa-once.conf && systemctl daemon-reload
```

To check on the board for R1b: that `CUDA_VISIBLE_DEVICES=-1` makes 0.15.6 log `library=cpu` (if not, use
`OLLAMA_LLM_LIBRARY=cpu`, which skips the `cuda_jetpack5` library dir in discovery).

**R2 (Low, doc).** `Requisite=ollama.service` turns a stop or restart of `ollama.service` into a stop or try-restart
of `ollama-gpu-check.service` (systemd 245 `transaction.c` propagates over `UNIT_REQUISITE_OF`, same as Requires=).
So gpu-check re-runs after every manual `systemctl restart ollama` once it has run at boot. It still can't loop:
during its own restart gpu-check is `activating`, so try-restart collapses to nothing, and the `/run` marker allows
only one restart per boot. That is arguably good, but `docs/deploy-jetson.md` says "gpu-check itself only runs at
boot". Also, `systemctl start ollama-gpu-check` does nothing once the unit has run this boot (`RemainAfterExit=yes`),
so the "optional: run the check now" line only works on first install.

**R3 (Info).** The comment in `ollama-wait-cuda.conf` ("150 s (plus one probe of at most 10 s)") overstates the
overrun: the last probe gets only the remaining time (min 1 s, +2 s kill), so it is ≤ 3 s. The gpu-check unit
comment's 430 s leaves out Ollama's stop time (up to 90 s); the worst case is 520 s, still < 600. With `Requisite=`,
if Ollama is disabled at boot, gpu-check fails with "dependency" (the system shows as degraded) rather than skipping
quietly.

## Board result: forced-race boot R1b (2026-10-19, xavier12, `639c148` installed)

**Result: PASS (path (b)).** It was a read-only check over SSH after DevOps rebooted with the R1b files from this doc
(`zz-qa-ollama-once.conf` / `99-qa-once.conf`). Those files are the same as the `zz-force-race.conf` pair in
`docs/deploy-jetson.md` at `53196dc`, apart from the file names. The installed scripts and units match `639c148` by
sha256. Versions: Ollama 0.15.6, systemd 245, L4T R35 rev 6.0.

The board booted at 21:12:32 EEST. Times are journal wall clock (EEST). The monotonic time since kernel start is in
brackets.

| Time | Event |
|---|---|
| 21:12:54.444 [22.03 s] | `Starting Ollama Service` (first start, after `nvpmodel` finished at [21.43 s]) |
| 21:12:54.724 | `wait-cuda: … (+0s) CUDA_WAIT_S=0; skipping the wait entirely` |
| 21:12:54.838 | `Started Ollama Service`; gpu-check starts 21:12:54.885 |
| 21:12:55.494 → 21:12:55.785 [23.37 s] | `discovering available GPUs...`, `user overrode visible devices CUDA_VISIBLE_DEVICES=-1`, then `inference compute id=cpu library=cpu …` (0.29 s) |
| 21:13:02.892 [30.47 s] | gpu-check: `library=cpu on this start; waiting 10s before the single restart` (marker file 21:13:02.884) |
| 21:13:12.897 [40.48 s] | gpu-check: `restarting ollama.service once (/run/ollama-gpu-check.restarted)`; Ollama stopped in 0.03 s. It cancelled the CPU warm-up load of `qwen2.5vl-jetson:3b` that had started at 21:13:12.70 |
| 21:13:13.013 / 21:13:13.208 | `wait-cuda: (+0s) device/sysfs: present`, `cuInit probe #1: OK (cuInit=0 cuDeviceGetCount=0 devices=1); GPU ready` (0.2 s) |
| 21:13:13.419 → 21:13:17.406 [44.99 s] | `discovering available GPUs...`, then `inference compute … library=CUDA compute=7.2 name=CUDA0 description=Xavier libdirs=ollama,cuda_jetpack5 driver=11.4 … type=iGPU` (3.99 s). No override warning on this start (1 in the whole boot) |
| 21:13:17.829 | gpu-check: `after restart, library=CUDA`; unit finished, Result=success, 22.95 s in total |
| 21:13:27.95 | `offloaded 37/37 layers to GPU` (service warm-up); `POST /api/generate` 200 in 16.4 s |

The checks after boot all hold:
- `/run/ollama-gpu-check.restarted` is present, and both `/run` env files are gone. `ollama.service` has NRestarts=0, a new InvocationID, and `TimeoutStartUSec=3min`.
- `sudo ollama-gpu-check.sh check` exits 0 (`GPU (library=CUDA)`). Filtering by `_SYSTEMD_INVOCATION_ID` shows only the restarted start's wait-cuda lines and its CUDA line.
- `ollama ps`: `qwen2.5vl-jetson:3b`, 4.2 GB, 100% GPU, context 4096, Forever. A single `/v1/info` read (no reload was needed) gave `ready:true` with models listing only `qwen2.5vl-jetson:3b` (default, loaded). `/healthz` returns `{"status":"ok"}`. `service.env` and the container both have `OLLAMA_MODEL=ALLOWED_MODELS=qwen2.5vl-jetson:3b`. The container is `jetson-517352f`, healthy.
- `tc -s filter show dev eth0 parent ffff:`: `protocol ip pref 2` police 0x1 and `protocol ipv6 pref 3` police 0x2, each `rate 30Mbit burst 384Kb … action drop`, no drops. `dl-cap.sh check` exits 0.
- Units: dl-cap, NetworkManager-dispatcher, ollama and ollama-gpu-check are all enabled. The hook is `root:root 755`. `systemctl is-system-running` reports `running`, with 0 failed units.
- Board facts: both device nodes (`root:video 0660`), `/sys/module/nvgpu`, `/sys/devices/gpu.0`, `/sys/class/devfreq/17000000.gv11b` and `/sys/devices/platform/17000000.gv11b` exist. The kernel nvgpu lines come at [9.43 s] and [12.92 s]. `nv.service` and `nvpmodel.service` exist, and python3 is installed.

Observations (none blocks the result):
- **O1. Detection took 7.1 s.** gpu-check first logged at [30.47 s], 7.1 s after the CPU line at [23.37 s]. Its `systemctl show` / `journalctl` calls take 0.07 s now, and many units finished together at [30.3–30.5 s] (containerd, snapd, the dispatcher's queued restarts). So PID 1 / D-Bus was most likely busy at boot. That is well inside the 120 s wait.
- **O2. Two hook restarts of dl-cap at boot.** The cap was applied at [22.16 s]. NetworkManager had activated eth0 at [17.48 s] and reached CONNECTED_GLOBAL at [17.64 s]. The hook's restarts only ran at [30.47 s], probably blocked by the same busy window. The second restart killed the first's start mid-run (`code=killed, status=15/TERM`, `Failed with result 'signal'`). The third start applied the cap at [30.83 s]: the unit is now active with NRestarts=0 and no failed units. The `up` event restarts unconditionally; the `connectivity-change` check can then see the cap removed by that in-progress restart and queue another. The cap was gone for under 0.4 s at most.
- **O3. The board's clock jumps at boot.** It starts at the build time (2024-06-17). `systemd-timesyncd` restores the saved time, the clock steps 40.6 s forward at about [11.6 s], and NTP syncs at [40.45 s] with no further step. Two consequences:
  - systemd loaded the units while the clock read 2024, so `ollama.service` shows `NeedDaemonReload=yes`, and gpu-check's `systemctl restart` printed `Warning: The unit file … changed on disk`. That is harmless; the drop-ins were applied.
  - `--since` scoping would have been unreliable on this board. `_SYSTEMD_INVOCATION_ID` scoping (round-1 L3) is the right choice.

## Board result: normal boot 1 of 3 (2026-10-19, xavier12, `639c148` installed)

**Result: PASS (path (a)).** Read-only check over SSH after DevOps removed both R1b files and ran `daemon-reload`.
No R1b leftovers: `/etc/tmpfiles.d` is empty, `ollama.service` DropInPaths lists only `10-autofill.conf`,
`ollama-wait-cuda.conf` and `override.conf` (no `99-qa-once.conf`), and neither `/run` QA env file exists.

The board booted at 21:22:54 EEST. Times are journal wall clock (EEST), monotonic time in brackets.

| Time | Event |
|---|---|
| 21:23:20.333 [26.73 s] | `Starting Cap all incoming traffic on eth0…`; cap applied 21:23:20.575 |
| 21:23:20.426 [26.82 s] | `Starting Ollama Service` (single start; `nvpmodel` finished at [26.65 s]) |
| 21:23:20.653 / 21:23:21.100 | `wait-cuda: … (+0s) device/sysfs: present`, `cuInit probe #1: OK (cuInit=0 cuDeviceGetCount=0 devices=1); GPU ready` (0.45 s) |
| 21:23:21.106 | `Started Ollama Service`; gpu-check starts 21:23:21.117 |
| 21:23:21.538 → 21:23:25.972 [32.37 s] | `discovering available GPUs...`, then `inference compute … library=CUDA compute=7.2 name=CUDA0 …` (4.4 s). No override warning (0 in the whole boot); 1 `inference compute` line in the whole boot |
| 21:23:26.975 [33.37 s] | gpu-check: `GPU already in use (library=CUDA)`; unit finished, Result=success, 5.86 s in total. No restart |
| 21:23:42.03 | `offloaded 37/37 layers to GPU` (service warm-up); `POST /api/generate` 200 in 15.5 s |

The checks after boot all hold:
- No `/run/ollama-gpu-check.restarted` marker. `ollama.service` has NRestarts=0, `TimeoutStartUSec=3min`; `_SYSTEMD_INVOCATION_ID` scoping shows this start's wait-cuda lines and its CUDA line.
- `sudo ollama-gpu-check.sh check` exits 0 (`GPU (library=CUDA)`).
- `ollama ps`: `qwen2.5vl-jetson:3b`, 4.2 GB, 100% GPU, context 4096, Forever. A single `/v1/info` read gave `ready:true`, models listing only `qwen2.5vl-jetson:3b` (default, loaded). `/healthz` returns `{"status":"ok"}`. `service.env` and the container both have `OLLAMA_MODEL=ALLOWED_MODELS=qwen2.5vl-jetson:3b`; container `jetson-517352f`, healthy.
- `tc -s filter show dev eth0 parent ffff:`: `protocol ip pref 2` police 0x1 and `protocol ipv6 pref 3` police 0x2, each `rate 30Mbit burst 384Kb … action drop`, no drops. `dl-cap.sh check` exits 0.
- Units: dl-cap, NetworkManager-dispatcher, ollama and ollama-gpu-check enabled; hook `root:root 755`. `systemctl is-system-running` reports `running`, 0 failed units.
- `systemd-analyze` (30 s timeout, completed): startup 7.974 s (kernel) + 25.556 s (userspace) = 33.530 s. Blame: snapd.seeded 15.54 s, snapd 8.10 s, nv 7.50 s, nvpmodel 7.09 s, ollama-gpu-check 5.86 s, docker 4.99 s, containerd 1.18 s, ollama 677 ms (includes wait-cuda), NetworkManager 477 ms, dl-cap 154 ms.

Observations (none blocks the result):
- **O2 repeated.** Same pattern as R1b: NetworkManager activated eth0 at [17.93 s] (CONNECTED_GLOBAL [18.25 s]); dl-cap applied the cap at [26.97 s]; the hook's queued restarts then ran at [27.17 s] (stop) and [27.32 s], the second start was killed (`code=killed, status=15/TERM`, `Failed with result 'signal'`), and the third start applied the cap at [27.57 s]. The cap was gone for about 0.4 s; the unit ends active, NRestarts=0, no failed units. Reproducible, so likely on every boot; cosmetic, but it leaves a `Failed with result 'signal'` line in the journal each boot.
- **O3 repeated.** The clock again started at the build time and was restored by timesyncd, then stepped forward; `ollama.service` again shows `NeedDaemonReload=yes` (harmless, same cause).
- **O1 not repeated in this form.** gpu-check logged 1.0 s after the CUDA line (its poll interval).
- Both dl-cap and Ollama started ~8 s after `network-online.target` ([18.70 s]), right after `nvpmodel` finished, as in R1b.

## Board result: normal boot 2 of 3 (2026-10-19, xavier12, `639c148` installed)

**Result: PASS (path (a)).** Same read-only checks as boot 1. The reboot started at 21:26:47 EEST; the board was up at
21:27:36. Still no R1b leftovers (`/etc/tmpfiles.d` empty, DropInPaths = `10-autofill.conf`, `ollama-wait-cuda.conf`,
`override.conf`; no `/run` QA env files).

| Time | Event |
|---|---|
| 21:27:57.381 [21.42 s] | `Starting Cap all incoming traffic on eth0…`; cap applied 21:27:57.586 |
| 21:27:57.495 [21.54 s] | `Starting Ollama Service` (single start; `nvpmodel` finished at [20.97 s]) |
| 21:27:57.788 / 21:27:58.324 | `wait-cuda: … (+0s) device/sysfs: present`, `cuInit probe #1: OK (cuInit=0 cuDeviceGetCount=0 devices=1); GPU ready` (0.54 s) |
| 21:28:05.477 [29.52 s] | `Started Ollama Service` (7.15 s after wait-cuda's last line; see O4); gpu-check starts 21:28:05.620 |
| 21:28:06.022 → 21:28:10.006 [34.05 s] | `discovering available GPUs...`, then `inference compute … library=CUDA compute=7.2 name=CUDA0 …` (4.0 s). No override warning, 1 `inference compute` line in the whole boot |
| 21:28:10.328 [34.37 s] | gpu-check: `GPU already in use (library=CUDA)` (0.32 s after the CUDA line); Result=success, 4.7 s. No restart |
| 21:28:25.59 | `offloaded 37/37 layers to GPU`; `POST /api/generate` 200 in 15.2 s |

The checks after boot all hold: no restart marker, Ollama NRestarts=0, `TimeoutStartUSec=3min`; `ollama-gpu-check.sh check`
exits 0; `ollama ps` shows `qwen2.5vl-jetson:3b` 4.2 GB, 100% GPU, context 4096, Forever; one `/v1/info` read gave
`ready:true` listing only `qwen2.5vl-jetson:3b`; `/healthz` `{"status":"ok"}`; `service.env` and the container allow only
Qwen (container `jetson-517352f`, healthy); both police filters (ip pref 2, ipv6 pref 3, 30Mbit, no drops) present and
`dl-cap.sh check` exits 0; all four units enabled, hook `root:root 755`, `running`, 0 failed units.
`systemd-analyze` (30 s timeout, completed): 8.047 s (kernel) + 26.452 s (userspace) = 34.499 s. Blame: nvpmodel 8.33 s,
containerd 8.10 s, ollama 7.99 s (see O4), gpu-check 4.72 s, docker 4.69 s, nv 1.32 s, snapd.seeded 1.30 s, snapd 1.02 s,
NetworkManager 427 ms, dl-cap 114 ms.

Observations (none blocks the result):
- **O2 recurred (3 of 3 boots so far).** eth0 activated at [17.34 s] (CONNECTED_GLOBAL [17.63 s]); the cap was applied at
  [21.63 s]; the hook's queued restarts ran at [29.64 s] (stop) and [29.77 s]; the second start was killed
  (`code=killed, status=15/TERM`, `Failed with result 'signal'`), and the third applied the cap at [30.10 s]. The cap was gone
  for about 0.45 s; the unit ends active, NRestarts=0, no failed units.
- **O3 recurred.** Clock restored from the saved time by timesyncd, then stepped forward; `NeedDaemonReload=yes` again.
- **O4. PID 1 stalls for about 7 s during boot (likely the cause of O1 too).** wait-cuda logged `GPU ready` at [22.36 s]
  and its code path exits right after that line, but systemd recorded the ExecStartPre stop time and `Started Ollama Service`
  only at [29.52 s]. systemd (PID 1) logged nothing between [22.30 s] and [29.50 s], then a burst of units completed together
  (containerd, nvzramconfig, alsa-restore, nv-l4t-bootloader-config, Ollama). So the 7.99 s blame for `ollama.service` is
  mostly PID 1 not reaping/processing in that window, not wait-cuda. The dispatcher's queued dl-cap restarts were also
  released at the end of the stall. In R1b the same kind of burst at [30.3–30.5 s] explains O1's 7.1 s detection delay;
  in boot 1 the stall seems to have come before dl-cap/Ollama started (both ~8 s after `network-online.target`). Harmless
  to this PR; cause not investigated (read-only).

## Board result: normal boot 3 of 3 (2026-10-19, xavier12, `639c148` installed)

**Result: PASS (path (a)).** Same read-only checks. The reboot started at 21:31:03 EEST; the board was up at 21:31:52.
No R1b leftovers (same three drop-ins, `/etc/tmpfiles.d` empty, no `/run` QA env files).

| Time | Event |
|---|---|
| 21:32:13.598 [21.44 s] | `Starting Cap all incoming traffic on eth0…`; cap applied 21:32:13.815 |
| 21:32:13.692 [21.53 s] | `Starting Ollama Service` (single start; `nvpmodel` finished at [20.95 s]) |
| 21:32:13.964 / 21:32:14.441 | `wait-cuda: … (+0s) device/sysfs: present`, `cuInit probe #1: OK (cuInit=0 cuDeviceGetCount=0 devices=1); GPU ready` (0.48 s) |
| 21:32:14.446 [22.29 s] | `Started Ollama Service` (ExecStartPre 21:32:13 → 21:32:14; no gap this time); gpu-check starts 21:32:14.485 |
| 21:32:14.961 → 21:32:18.720 [26.56 s] | `discovering available GPUs...`, then `inference compute … library=CUDA compute=7.2 name=CUDA0 …` (3.8 s). No override warning, 1 `inference compute` line in the whole boot |
| 21:32:22.225 [30.06 s] | gpu-check: `GPU already in use (library=CUDA)` (3.5 s after the CUDA line, at the end of a PID 1 gap, see O4); Result=success, 7.8 s. No restart |
| 21:32:41.43 | `offloaded 37/37 layers to GPU`; `POST /api/generate` 200 in 15.3 s |

The checks after boot all hold: no restart marker, Ollama NRestarts=0, `TimeoutStartUSec=3min`; `ollama-gpu-check.sh check`
exits 0; `ollama ps` shows `qwen2.5vl-jetson:3b` 4.2 GB, 100% GPU, context 4096, Forever; one `/v1/info` read gave
`ready:true` listing only `qwen2.5vl-jetson:3b`; `/healthz` `{"status":"ok"}`; `service.env` and the container allow only
Qwen (container `jetson-517352f`, healthy); both police filters (ip pref 2, ipv6 pref 3, 30Mbit, no drops) present and
`dl-cap.sh check` exits 0; all four units enabled, hook `root:root 755`, `running`, 0 failed units.
`systemd-analyze` (30 s timeout, completed): 7.883 s (kernel) + 26.066 s (userspace) = 33.949 s. Blame: containerd 8.55 s,
nvpmodel 8.49 s, gpu-check 7.77 s, docker 3.79 s, snapd.seeded 1.28 s, nv 1.22 s, snapd 892 ms, ollama 754 ms,
NetworkManager 289 ms, dl-cap 122 ms.

Observations: O2 recurred (cap applied [21.65 s]; hook restarts at [30.02 s], second start killed with `status=15/TERM`,
cap re-applied [30.36 s], off for about 0.35 s; unit active, NRestarts=0). O3 recurred (`NeedDaemonReload=yes`). O4: PID 1
logged nothing from [22.46 s] to [29.97 s] (7.5 s); this time Ollama had already started, and the delayed items were
gpu-check's report and the hook's dl-cap restarts, both released at [30.0 s]. The journal is not persistent on this
board, so earlier boots can't be re-read for the same gap.

## Summary of all board boots

| Boot | Path | wait-cuda cuInit | discovery → `inference compute` | gpu-check | Restarts | dl-cap double restart (O2) | PID 1 gap (O4) |
|---|---|---|---|---|---|---|---|
| R1b | (b) | skipped on 1st start; OK in 0.2 s on the restarted start | 0.29 s (CPU), then 3.99 s (CUDA) | CPU seen, 10 s wait, one restart, `after restart, library=CUDA` | 1 (marker present) | yes, cap off < 0.4 s | yes (O1: 7.1 s detection delay) |
| N1 | (a) | OK in 0.45 s | 4.4 s | `GPU already in use` after 1.0 s | 0 | yes, ~0.4 s | likely, before dl-cap/Ollama start |
| N2 | (a) | OK in 0.54 s | 4.0 s | `GPU already in use` after 0.3 s | 0 | yes, ~0.45 s | 7.2 s (delayed `Started Ollama Service`) |
| N3 | (a) | OK in 0.48 s | 3.8 s | `GPU already in use` after 3.5 s | 0 | yes, ~0.35 s | 7.5 s (delayed gpu-check report) |

## Follow-ups (non-blocking)
1. **N1 / O2: dl-cap double restart at boot.** On every boot the NetworkManager hook's queued restarts (the `up` event
   restarts unconditionally; `connectivity-change` can then see the cap removed by the in-progress restart) stop the
   already-applied cap and start dl-cap twice; the second start kills the first (`Failed with result 'signal'` in the
   journal). The cap is off for about 0.4 s and the unit always ends active with no failed units. DevOps will fix this
   in a follow-up PR (for example, skip the restart when the cap is already in place on `up` as well, or serialise
   the hook).
2. **O4: ~7 s PID 1 gap during boot.** systemd logs nothing for about 7–7.5 s around [22–30 s] and then completes a
   burst of units; whatever is waiting on PID 1 then (an ExecStartPre exit, gpu-check's `systemctl`/`journalctl`
   calls, the hook's restarts) is delayed. Harmless for this PR; worth looking at separately (cause not investigated,
   read-only).
3. **O3: clock jump / `NeedDaemonReload=yes`.** The board starts at its build time (2024-06-17) until
   `systemd-timesyncd` restores the saved time, so the units load with a pre-step clock and `ollama.service` shows
   `NeedDaemonReload=yes` (and a "unit file changed on disk" warning on the forced restart). Harmless; the drop-ins
   apply. `_SYSTEMD_INVOCATION_ID` scoping makes gpu-check independent of the clock step.
4. Doc nits R2/R3 are fixed at `53196dc`; nothing else open.

Raw logs are kept with the QA working files (not committed).
