# QA: PR #23 dl-cap NetworkManager hook dedupe and periodic cap check (QA #17 follow-up N1)

- PR #23, branch `fix/dl-cap-dispatcher-dedupe`, on main `4f25ede`. Reviewed heads: `db57831` (serialised hook),
  `ea46c13` (one runtime deadline), **`46ff335`** (final: adds `dl-cap-check.timer`).
- **Verdict at `46ff335`: PASS, including the board reboot check on xavier12 (2026-10-19).** No High or Medium
  findings remain open; M1 (found at `ea46c13`) is closed by the timer. The Low and Info items below are follow-ups
  and don't block. See "Board check".
- Changed: `deploy/jetson/nm-dispatcher-90-dl-cap`, new `dl-cap-check.sh`, `dl-cap-check.service`,
  `dl-cap-check.timer`, harness and fakes, `docs/deploy-jetson.md`, `CHANGELOG.md`. `dl-cap.sh` and `dl-cap.service`
  are unchanged.

## Test plan
1. CI, shellcheck, `sh -n`/`dash -n`, `systemd-analyze verify`, gitleaks, a scan for the real token and for
   private addresses and host names. The full harness at each head, timed, with a PATH that fails on any real
   `docker`, `tc`, `systemctl`, `journalctl`, `logger`, `curl`, `nmcli`, `ollama`, `nc`, `ss` or `wget`. /tmp checked before and after.
2. The N1 evidence from the #17 boots (R1b and normal boots 1-3): eth0 is activated at about [17-18 s], the boot
   start applies the cap at about [21-27 s], and the hook's restarts arrive at about [27-30 s], right after a
   ~7-8 s PID 1 gap. Replay that order with the old (`4f25ede`) and the new hook. The NetworkManager dispatcher
   runs scripts **one at a time**, so the replay is serial, not concurrent.
3. NetworkManager 1.22.10 source (the version on L4T R35 / Ubuntu 20.04): the dispatcher timeout and kill, the
   request queue, and when an activation resets tc (`tc_commit`). systemd 245 source: `systemctl show -p Job`.
4. Hook: a queued start, restart or stop job; activating, deactivating, failed, inactive, disabled and masked
   units; the lock (location, a kill while holding it, inheritance by children); `--no-block`; IPv6 coverage of
   `check`; how much it logs.
5. Deadline (`ea46c13`): trace `DL_CAP_HOOK_BUDGET_S`. Hangs in `is-enabled`, the lock, `show`, `tc` and
   `restart`, alone and together, under dash and bash. Bad, 0, leading-zero and huge budgets.
6. Timer (`46ff335`): the shared lock and deadlock risk, a slow boot or a start in progress, a disabled unit, a
   failed unit, `daemon-reload`, `Persistent=`, the timeout trace, the logs, the install docs.
7. Docs and CHANGELOG against the code.

## Results

| # | Check | Result |
|---|---|---|
| 1 | CI | 3/3 green (lint-test, deploy-scripts, image) at `db57831`, `ea46c13` and `46ff335` |
| 1 | Harness, local | `ea46c13`: 233/233 (foreground run). `46ff335`: **262/262 in 3 min 10 s**. No real command called. /tmp unchanged |
| 1 | shellcheck 0.10.0, `dash -n`, `bash -n` | clean at all three heads |
| 1 | `systemd-analyze verify` (dl-cap, dl-cap-check service and timer) | clean |
| 1 | Secrets and addresses | gitleaks: no leaks in `4f25ede..46ff335`. Real token: 0 hits in tree and history. No private addresses or host names added |
| 2 | Serial replay, cap already applied (the board's boot) | old hook: 2 restarts, 1 start killed (N1 reproduced). New: **0 restarts**, one `cap in place` line per event |
| 2 | Serial replay, cap missing (NetworkManager reset the qdisc) | old: 2 restarts, 1 killed. New: 1 restart, 0 killed. The next event waits for the start to settle and then sees `cap in place` |
| 2 | Boot start job queued (inactive, `Job=N`) | both events log `already has job N queued`, no restart |
| 3 | NM 1.22.10 dispatcher | `SCRIPT_TIMEOUT 600` (per script, then `kill(pid, SIGKILL)` on the script PID only) in `dispatcher/nm-dispatcher.c`. `CALL_TIMEOUT` 10 min for the whole request in `src/nm-dispatcher.c`. Requests are queued (`requests_waiting`) and scripts run one at a time |
| 3 | NM 1.22.10 tc reset | `activate_stage2_device_config` → `tc_commit` → `nm_platform_qdisc_sync` with an empty list deletes every qdisc (the ingress qdisc too) on each managed activation. That happens before the device reaches `activated` and before `up` is dispatched |
| 3 | systemd 245 `show -p Job` | prints `Job=<id>` only when a job exists, otherwise nothing; the hook handles both |
| 4 | `up` with the cap in place, link bounce, re-plug | A re-plug that NetworkManager turns into deactivate + activate resets tc in stage 2, so `up` finds the cap missing and restarts. A short carrier blip without reactivation keeps the qdisc (the kernel keeps ingress across link down/up), and there is nothing to do. down/up is still covered |
| 4 | `--no-block` | the job is queued before `systemctl` returns, so the next serial event sees it. Nothing in the same run checks after the restart |
| 4 | IPv6 | `check` needs a police filter for both `protocol ip` and `protocol ipv6`. The rate and action are not compared (pre-existing) |
| 4 | Disabled or masked | exits after `is-enabled`, no lock and no further calls (hook and check) |
| 4 | Logging | hook: one line per handled event (`up`, `dhcp4-change` at each lease renewal, `connectivity-change` on state changes), plus one line on a timeout or a failed restart. Check: silent while the cap is in place |
| 5 | Deadline trace (default 8 s, reserve 2 s, every later call at least 1 s) | `is-enabled` gets up to 8 s, the lock gets up to the deadline minus 2, `show` the deadline minus 2 (at least 1), `check` the deadline minus 1 (at least 1), `restart` up to the deadline (at least 1). Measured: all calls hang 8.0 s; lock held 60 s 6.0 s; unit stuck activating 5.1-6.1 s; `show` hangs 6.0 s; restart hangs 8.0 s. Worst case (is-enabled 7.9 s, then slow show, tc and restart): **10.9 s** (L1) |
| 5 | Budget values | `''`, `abc`, `0`, `-1` → 8 s. `010` → octal 8 in dash. `08` and a 20-digit value: **dash aborts with exit 2** ("Illegal number"). bash: `08` → error message, run continues. `30` → 28 s (no upper bound). L4 |
| 6 | Timer timeout trace | `is-enabled` 4 + lock 8 + `show` 4 + `check` 4 + `restart` 4 = 24 s < `TimeoutStartSec=30`. Measured worst 19.7 s (rc=1, restart timed out); all calls hanging 7.9 s; lock held 8.0 s, then the round is skipped |
| 6 | Timer and hook at the same moment, cap missing | 1 restart, 0 killed, 1 apply; the hook then logs `cap in place`. Each side takes only this one lock and every call is bounded, so there is no deadlock |
| 6 | Slow boot, start in progress | a queued job or `activating` → the round is skipped. `After=dl-cap.service` also makes the check's start wait behind a queued dl-cap job. No restart loop |
| 6 | Failed `dl-cap.service` | restarts and logs on **every** run, 5 min apart, forever. That is below the start limit (10 per 300 s), so it is not rate-limited, and each restart can start another round of up to 10 `Restart=on-failure` attempts (L5) |
| 6 | `daemon-reload`, `Persistent=` | monotonic timer (`OnBootSec` + `OnUnitActiveSec`). systemd keeps its last trigger across `daemon-reload`, so the schedule continues. `Persistent=` is unset, which is right: it only applies to `OnCalendar=`. `enable --now` at runtime fires once at once (OnBootSec has passed and there is no previous trigger), then every 5 min |
| 7 | Docs, CHANGELOG | Install lines match the files and modes; `enable --now dl-cap-check.timer` after `daemon-reload`; verify lines include `list-timers` and `-t dl-cap-check`. One verify comment is stale (L6) |

## Findings

**M1 (Medium, found at `ea46c13`, closed at `46ff335`).** When a `systemctl` call times out, the hook leaves dl-cap
alone. The 8 s budget is shorter than the PID 1 gaps seen at boot (7.2-8.4 s; on normal boot 3, [12.5-20.9 s]
covers eth0's activation at [17.6 s]). If NetworkManager reset the qdisc at such a moment and no event followed,
the cap would stay off indefinitely, where main's hook would have restarted it after the stall. Closed by
`dl-cap-check.timer`: worst case, the cap is back within about 5.5 min.

**L1 (Low, doc).** The worst case is about `BUDGET + 3` s (10.9 s measured), not "about 9 s": once `is-enabled`
has used up the budget, `show`, `check` and `restart` each still get the 1 s minimum. The harness bound tests
(≤ 10 s) don't cover a slow `is-enabled`. Fix: say ~11 s, or leave dl-cap alone once the deadline has passed.

**L2 (Low).** A `dl-cap.sh check` that times out (exit 124) counts as "cap missing", in the hook and in the timer
script, and leads to a restart. Measured: `tc` hanging with the cap in place → restart after 4 s. That doesn't fit
"unknown → leave it alone", and a restart drops the cap briefly. Fix: treat 124 as unknown (see the diff).

**L3 (Low).** The lock is `/run/lock/dl-cap-hook.lock`, and `/run/lock` is world-writable (1777). A local user can
create the file and hold the lock. The hook then waits until its deadline and goes on unlocked, and the timer skips
its rounds. With `fs.protected_regular`, root's open fails and both go on without the lock. There is no privilege
escalation (`protected_symlinks`). The lock is on tmpfs, and a kill releases it when the last fd closes; the empty
file left behind is harmless. Children inherit fd 9: measured, a SIGKILLed hook's orphaned `systemctl` held the lock
for 5 s more, which the timeouts now bound. Fix: `/run/dl-cap-hook.lock` (root-only), in both scripts.

**L4 (Low).** `DL_CAP_HOOK_BUDGET_S` validation: under dash, `08`/`09` or a very long number makes the hook abort
with exit 2 and no log line; `010` is read as octal; there is no upper bound. NetworkManager builds the script
environment itself, so this matters only for manual or harness runs. Fix: reject a leading zero and more than
2 digits, and cap the value at 60.

**L5 (Low).** A `dl-cap.service` stuck in `failed` (e.g. `tc` keeps failing) gets a restart and a `dl-cap-check`
log line every 5 min forever, and each restart can start up to 10 more failing attempts (`RestartSec=10`). Retrying
is the point of a safety net, but the journal fills. Fix: keep retrying and log once per boot (marker in `/run`), or
back off. If the restart call itself fails, `dl-cap-check.service` exits 1 and the system shows as `degraded` until
the next good run.

**L6 (Low, doc).** `docs/deploy-jetson.md` still says next to `journalctl -b -u dl-cap -u NetworkManager-dispatcher`
"after a reboot: dl-cap restarted once NetworkManager had eth0". With this PR a normal boot has **no** hook restart.

**I1 (Info).** The flock isn't what fixes N1 on the board, because NetworkManager already runs dispatcher scripts
one at a time. The fix is the queued-job/busy detection plus check-before-restart on `up`. The CHANGELOG and docs
wording ("at the same moment", "racing") and the concurrent harness pairs describe a race the dispatcher doesn't
produce. The lock still matters for the timer and for manual runs.

**I2 (Info).** A queued *stop* job is logged as "already has job N queued; leaving it (its start applies and
verifies the cap)". The cap then stays off until the next event or timer run. Only the wording is off.

**I3 (Info).** When `timeout` cuts off `systemctl --no-block restart`, the request may already have reached PID 1,
so "restart failed or timed out" can appear while the restart still happens. Harmless. A budget of 1-2 s logs
"lock busy" without waiting.

A suggested patch for L2, L4 and L5 is in `suggested-fix-46ff335.diff` (QA working files; shellcheck clean, not run
through the harness).

## Board check

DevOps installed `46ff335` on xavier12 (the hook, `dl-cap-check.sh`, both units, `daemon-reload`,
`systemctl enable --now dl-cap-check.timer`) and rebooted once at 22:26:14 EEST; the board was back at 22:27:05.
QA then checked it independently, read-only over SSH, from 22:32 to 22:35 EEST. There were no unloads, restarts or
writes on the board. Times are EEST; bracketed values are seconds since boot (`-o short-monotonic`).

### Commands

```bash
# v1.3 after-boot checklist (docs/qa/v1.3-jetson-qa.md, section 5): Ollama on GPU, /healthz, /v1/info
# ready with only qwen2.5vl-jetson:3b, 0 failed units.

# dl-cap / hook (N1)
journalctl -b -u dl-cap | grep -c "Failed with result 'signal'"        # 0
journalctl -b -u dl-cap -o short-monotonic --no-pager                   # one Starting/applied/Finished, no Stopping
journalctl -b -t dl-cap-hook -o short-monotonic --no-pager              # exactly one line per handled event (up, connectivity-change, ...)
/usr/local/sbin/dl-cap.sh check; echo "exit=$?"                          # exit=0
tc -s filter show dev eth0 parent ffff:                                  # protocol ip pref 2 and ipv6 pref 3, police rate 30Mbit ... drop
systemctl show dl-cap -p ActiveState,NRestarts,ActiveEnterTimestampMonotonic
findmnt -T /run/lock -o TARGET,FSTYPE; stat -c '%U:%G %a %n' /run/lock /run/lock/dl-cap-hook.lock

# timer (run after uptime > 2.5 min)
systemctl is-enabled dl-cap-check.timer; systemctl is-active dl-cap-check.timer   # enabled, active
systemctl list-timers --all --no-pager dl-cap-check.timer                          # LAST ~2 min after boot, NEXT +5 min
journalctl -b -u dl-cap-check -o short-monotonic --no-pager                        # first run at ~[120-150 s], Finished/Succeeded
systemctl show dl-cap-check.service -p Result,ExecMainStatus,ExecMainStartTimestampMonotonic   # success, 0
journalctl -b -t dl-cap-check --no-pager                                           # empty: the runs were no-ops
systemctl is-system-running; systemctl --failed --no-pager                         # running, 0 units
```

Pass if:
- 0 `Failed with result 'signal'`, and one `dl-cap-hook` line per event.
- `check` exits 0 and both police filters are present.
- The timer is enabled and active. Its runs (the first about 2 min after boot) succeeded and were no-ops: no `dl-cap-check` journal line, and dl-cap was not restarted.
- 0 failed units.

### Results

| Check | Result |
|---|---|
| Boot | Up since 22:27:05. Ollama started [27.43 s]; `wait-cuda`: device present, then `cuInit probe #1: OK … GPU ready` (22:27:32). `library=CUDA` (Xavier) at 22:27:37. `ollama-gpu-check`: "GPU already in use (library=CUDA)". `offloaded 37/37 layers to GPU` at 22:27:53, runner started in 13.03 s |
| Service | `apotheke-autofill:jetson-5fabeaa`, running, healthy, RestartCount 0 (container started 22:27:37). Ollama NRestarts 0 |
| GPU | `ollama ps`: `qwen2.5vl-jetson:3b` 4.2 GB, 100% GPU, Forever. `/api/ps`: size 4192202368 = size_vram 4192202368 |
| `/healthz` (no auth, over the network) | Exactly `{"status":"ok"}`, 200 |
| `/v1/info` (token) | `ready:true`, only `qwen2.5vl-jetson:3b`, `state:"warm"` before any QA suggest, `size_vram` 4192202368 |
| One warm suggest at 560 (seed categories) | **200 in 10.76 s** end to end from the QA box (Ollama `/api/chat` 10.22 s). Electronics; `brand`, `model_number` and `serial_number` present and correct (the Sony label values) |
| `docker logs autofill` | 0 token hits; 0 hits for the 23 identity values returned in the v1.3 pass. The current boot's section is 23 lines: startup plus access lines only (15 `/healthz`, 3 `/v1/info`, 1 suggest), no tracebacks. The only tracebacks in `docker logs` are from before the reboot: the two lone-surrogate probes of the v1.3 pass (F-1 in `v1.3-jetson-qa.md`) |
| `journalctl -b -u dl-cap` | 0 `signal` lines. One start: Starting [27.34 s], "30 Mbit/s inbound cap applied on eth0" [27.54 s] (22:27:32), Finished [27.57 s]; no Stopping. `ActiveState=active`, `Result=success`, `NRestarts=0`, `ActiveEnterTimestampMonotonic` 27571920 (unchanged after both timer runs) |
| `journalctl -b -t dl-cap-hook` | 4 lines, one per event, **0 restarts**: [26.19 s] (22:27:30) `(none) connectivity-change: systemctl show timed out or failed; leaving dl-cap alone`; [27.90 s] `(none) connectivity-change: cap in place on eth0, no restart`; [28.84 s] `eth0 up: cap in place on eth0, no restart`; [29.05 s] `(none) connectivity-change: cap in place on eth0, no restart` |
| `dl-cap.sh check` | "30 Mbit/s inbound cap is in place on eth0", exit 0 |
| tc | `protocol ip pref 2` and `protocol ipv6 pref 3`, both `police rate 30Mbit burst 384Kb mtu 2Kb action drop` |
| Lock | `/run/lock` tmpfs `root:root 1777`; `/run/lock/dl-cap-hook.lock` `root:root 644` (location as in L3) |
| Timer | enabled, active. Runs at 22:29:04 [120.09 s] and 22:34:05 [420.84 s]: each Starting → Succeeded → Finished in about 0.2 s, `Result=success`, `ExecMainStatus=0`. NEXT 22:39:05. `journalctl -b -t dl-cap-check`: **0 lines** (both runs no-ops) |
| System | `systemctl is-system-running`: running. `systemctl --failed`: 0 units |

**The first hook line is expected and harmless.** The `connectivity-change` event at [26.19 s] arrived while PID 1
was busy with the boot transaction. Its `systemctl show` hit the hook's deadline, and the hook left dl-cap alone, as
designed (the M1 behaviour, now backed by the timer). The boot start of `dl-cap.service` applied the cap
1.35 s later (journal seconds 22:27:30 → 22:27:32). The next three events then found `cap in place` and did nothing. That is the
N1 fix working on hardware: the #17 boots had 2 hook restarts and a killed start at this point, this boot had **0**.
Nothing depended on the timer this time: its two runs were no-ops.

**Board verdict: PASS.** Every pass condition is met. The v1.3 after-boot checklist is also clean: GPU, `/healthz`,
`/v1/info` warm, one warm suggest at 560 OK, logs clean, 0 failed units.

Raw logs are kept with the QA working files (not committed).
