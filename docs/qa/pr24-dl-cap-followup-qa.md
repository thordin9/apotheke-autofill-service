# QA: PR #24 dl-cap hook and timer follow-ups (QA of #23, L1-L6)

- PR #24, final head **`166335f`** (two commits on main `89e4f20`: `ec65f14`, then `166335f` for this QA's N1, N2
  and I3). Changed: `deploy/jetson/nm-dispatcher-90-dl-cap`, `deploy/jetson/dl-cap-check.sh`, the fake `tc`, the
  harness, `docs/deploy-jetson.md`, `CHANGELOG.md`. No unit files changed.
- **Verdict: PASS for merge at `166335f`.** L1-L6 are fixed as intended; the L5 deviation is acceptable. N1 and N2
  (found at `ec65f14`) are fixed with QA's suggested diff, line for line, and covered by three new harness tests;
  I3 is fixed in the docs. I1 (the wording about concurrency) and I2 stay open as Info and don't block.
- First pass at `ec65f14`: PASS with N1 and N2 as Low findings. Those results are kept below where they differ.

## Test plan
1. CI; the harness with a PATH on which any real `docker`, `tc`, `systemctl`, `journalctl`, `logger`, `curl`,
   `nmcli`, `ollama`, `nc`, `ss` or `wget` fails, timed, with /tmp compared before and after; shellcheck,
   `dash -n`/`bash -n`; gitleaks; a scan for the real token, private addresses and host names.
   At `166335f`: the diff `ec65f14..166335f` against QA's suggested N1/N2 diff, the new tests, and the N1/N2
   repros again.
2. L2: a `check` that times out, in the hook and in the timer script, once and on every run (is it logged, does
   anything restart).
3. L3: the lock path in both scripts, remaining `/run/lock` references, the lock file's mode when root creates it
   with the default umask 0022, a non-root user holding it, hook and timer contending.
4. L4: `DL_CAP_HOOK_BUDGET_S` edge values under dash (the board's `/bin/sh`).
5. L5: failure episodes over successive timer runs (failed, starting, back, failed again, missing while active),
   lines logged per episode, the marker's life cycle, a marker path that can't be written.
6. L1, L6, CHANGELOG and docs against the code; install steps for the board.

## Results

| # | Check | Result |
|---|---|---|
| 1 | CI | 3/3 green (lint-test, deploy-scripts, image) at `ec65f14` and at `166335f` |
| 1 | Harness | `ec65f14`: 279/279 in 3 min 34 s. **`166335f`: 282/282 in 3 min 33 s**, including the 3 new tests. No real command called. No temp files left behind |
| 1 | shellcheck 0.10.0, `dash -n`, `bash -n` | clean at both heads (`dash -n` on the `/bin/sh` scripts, `bash -n` on the bash ones) |
| 1 | Secrets and addresses | gitleaks: no leaks in `89e4f20..ec65f14` or `ec65f14..166335f`. Real token: 0 hits in tree, history and this doc. No addresses or host names added |
| 1 | `ec65f14..166335f` | Script lines are QA's suggested N1/N2 diff exactly: no line missing, none different. The only other script change is the I3 wording in the hook comment. Plus the 3 tests, the docs and the CHANGELOG |
| 2 | L2 hook, `tc` hanging, cap in place | logs `dl-cap.sh check timed out; leaving dl-cap alone`, no restart, cap untouched |
| 2 | L2 timer, `tc` hanging | `check timed out; trying again next run` after 4.0 s, no restart (was a restart at `46ff335`). Worst case with everything slow: 15.7 s, no restart |
| 2 | L2 forever (check times out on every run) | **not silent**: one `dl-cap-check` line on every run (5 min apart), exit 0. The unit doesn't go `failed`, so `systemctl --failed` doesn't show it (I2) |
| 3 | L3 lock path | both scripts default to `/run/dl-cap-hook.lock`; a static harness test compares them. No `/run/lock` in code or current docs; it remains only in the CHANGELOG ("moved from …") and the historical `docs/qa/pr23-dl-cap-hook-qa.md` |
| 3 | L3 lock file mode | `ec65f14`: created by `exec 9>>` under the default umask 0022 (NetworkManager-dispatcher.service and dl-cap-check.service set no `UMask=`), so root:root 0644. A non-root user can open it read-only and take `LOCK_EX`: reproduced with a root-owned 0644 file in a root 0755 directory, where root's `flock -w 2` timed out (N1). **`166335f`: root:root 0600** when root runs the real hook and the real `dl-cap-check.sh` under umask 022 in a root 0755 directory; a normal user's `flock` gets "Permission denied" on both files, while a 0644 control file there can still be locked. Harness: both 0600 tests pass |
| 3 | Hook + timer on the new path at the same moment, cap missing | 1 restart, 0 killed, 1 apply; the hook then logs `cap in place`. Lock held 20 s → the timer skips the round after 8.0 s |
| 4 | L4 (dash) | `''` → 8, no note. `abc 0 00 01 08 09 010 -1 +5 " 5" "5 " 1e3 5;id` → 8 with an "invalid" note. `1 9 10 59 60` as given. `61`, `99` → 60 with a "capped" note. `100`, `999` → 8 "invalid", not capped (I3). No dash abort. Code unchanged at `166335f`; the docs now say so |
| 5 | L5 episodes | failed ×3 → 3 restarts, **1** line; starting → skipped; failed → silent restart; cap back → marker removed, no line; failed ×2 → 1 line (new episode); missing while `active` → a line on each run (the restart path for a non-failed unit, as before); failed while the marker is still there → silent. At most one line per failed episode: no spam |
| 5 | L5 marker that can't be written | `ec65f14`: dash aborts with exit 2 before the restart and logs nothing: `{ : > "$FAILED_MARKER"; } 2>/dev/null` is a redirection error on a special built-in, and dash exits on that. bash continues (N2). **`166335f`: under dash and bash, exit 0, 1 restart, the "restarting" line, nothing on stderr** (marker in a directory that doesn't exist, unit `failed`). The `ec65f14` script still gives exit 2 and no restart in the same run. Harness test passes |
| 5 | L5 silent branch, restart fails | exit 1 with no `dl-cap-check` line; systemd logs the unit failure (Info) |
| 6 | L1, L6, CHANGELOG | L1: the docs, the hook comment and the CHANGELOG say budget + 3 s (~11 s). L6: the verify comment now reads "one dl-cap start, hook lines but normally no hook restart". The CHANGELOG lists L1-L6, and at `166335f` also the 0600 lock and the I3 wording |
| 6 | Install | Script changes only; the units are unchanged. Install both scripts; `daemon-reload` isn't needed (harmless). Nothing needs restarting: the timer runs the new script at its next run, and the hook runs on the next event. The old `/run/lock/dl-cap-hook.lock` is on tmpfs and goes at the next reboot |

## Findings

**N1 (Low, L3 not fully closed at `ec65f14`; fixed in `166335f`).** `/run` is root-only, but the lock file is created 0644, and `flock(2)` works on a
read-only fd. So any local user can still hold `/run/dl-cap-hook.lock`: the hook then waits until its deadline and
goes on unlocked, and the timer skips every round. Repro: root creates the file with umask 022; a normal user runs
`flock /run/dl-cap-hook.lock sleep 600`, and root's `flock -w 2` times out. Fix: `umask 077` around both opens,
including the `(exec 9>>"$LOCK")` probe that creates the file first. With the fix the file is 0600 and the normal
user gets "Permission denied". Confirmed at `166335f` (see results).

**N2 (Low; fixed in `166335f`).** In `dl-cap-check.sh`, `[ "$state" = failed ] && { : > "$FAILED_MARKER"; } 2>/dev/null` ends the
script under dash (exit 2, no restart, no log line) if the marker can't be written: a non-writable directory or a
wrong `DL_CAP_CHECK_FAILED_MARKER`. On the board, root writes to `/run`, so it doesn't happen in normal use, but then
the safety net does nothing for a failed unit. The harness misses it because it always points the marker into its
temp dir. Fix: `( : > "$FAILED_MARKER" ) 2>/dev/null || :`, plus a test with an unwritable marker. (The same pattern
was in QA's suggested diff for L5.) Confirmed fixed at `166335f` under dash, with the harness test added.

**I1 (Info, from #23, not addressed).** The flock isn't what fixes N1 of #17, because NetworkManager already runs
dispatcher scripts one at a time; the wording about racing and "at the same moment" stays.

**I2 (Info).** A `check` that times out on every run is logged every 5 min, exit 0, and never shows as a failed
unit. That isn't silent, and a hanging `tc` means the kernel's netlink is stuck, which a restart wouldn't fix either.
Optional: exit 1 after a few consecutive timeouts, so that `systemctl --failed` shows it.

**I3 (Info; fixed in `166335f`).** The docs said a budget "above 60 is capped at 60". Only 61-99 are capped; 100 and
up count as invalid and fall back to 8 s (also logged). The hook comment, `docs/deploy-jetson.md` and the CHANGELOG
now say this, and match the code.

## Board check after install (pending)

DevOps installs `166335f` on `<jetson-host>`:
```bash
install -o root -g root -m 755 $REPO/deploy/jetson/nm-dispatcher-90-dl-cap /etc/NetworkManager/dispatcher.d/90-dl-cap
install -m 755 $REPO/deploy/jetson/dl-cap-check.sh /usr/local/sbin/dl-cap-check.sh
```
Then the checks. The timer run and the hook run below change nothing while the cap is in place:
```bash
sudo /usr/local/sbin/dl-cap.sh check; echo "exit=$?"                     # exit=0
tc -s filter show dev eth0 parent ffff:                                   # ip pref 2 and ipv6 pref 3, police 30Mbit ... drop
sudo systemctl start dl-cap-check.service                                 # one timer run now (or wait for the next one)
journalctl -t dl-cap-check --since -10min --no-pager                      # empty: no-op with the cap in place
systemctl show dl-cap-check.service -p Result,ExecMainStatus              # success, 0
sudo env -i /etc/NetworkManager/dispatcher.d/90-dl-cap eth0 dhcp4-change  # manual hook run, as NetworkManager would
journalctl -t dl-cap-hook --since -10min --no-pager                       # one line: "eth0 dhcp4-change: cap in place on eth0, no restart"
systemctl show dl-cap -p ActiveState,NRestarts                            # active, 0; not restarted by either run
stat -c '%U:%G %a %n' /run/dl-cap-hook.lock                                # root:root 0600
ls /run/lock/dl-cap-hook.lock 2>&1                                         # until reboot it may still exist from #23; unused
ls /run/dl-cap-check.failed-logged 2>&1                                    # absent
systemctl is-active dl-cap-check.timer; systemctl is-system-running; systemctl --failed --no-pager   # active, running, 0 units
```
Pass if: `check` exits 0, both filters are present, the timer run and the hook run were no-ops (no `dl-cap-check`
line, one `cap in place` hook line, dl-cap not restarted), the hook uses `/run/dl-cap-hook.lock` and it is root:root 0600, no failure marker,
the timer is active, and 0 failed units.

Raw logs are kept with the QA working files (not committed).
