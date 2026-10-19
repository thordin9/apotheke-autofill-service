# QA: PR #17 boot fixes (dl-cap after NetworkManager, Ollama CUDA race)

- Round 1: PR #17 commit `fe5f5ba` on main `23e51e2`. Recheck: `cde402e` (rebased on main `63806a1`) and its merge with main, `639c148` (parents `cde402e` and `6f43494`; the diff against `cde402e` is exactly #21's CHANGELOG change).
- Harness with fake `tc`, `systemctl`, `journalctl` and `python3` (plus own probes with a `--since`-honouring journal fake and hanging cuInit fakes). No board run yet. The reboot test is in "Still to do".
- Result round 1: **dl-cap / NetworkManager hook: PASS. Ollama boot race: changes requested** (M1, M2).
- Result recheck (`639c148`): **PASS, ready for the normal reboot test.** All round-1 findings are fixed. The forced-race boot needs the corrected procedure (R1); two doc nits (R2, R3). See "Recheck".

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
Pending. Reboot tests with the read-only checklist below: one normal boot (expect path (a)), then the forced-race boots R1a and/or R1b (expect path (b)). Each must record:
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

**R1 (Medium, QA procedure; fix before the forced-race boot).** The `99-qa-skip-wait-cuda.conf` override in
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
