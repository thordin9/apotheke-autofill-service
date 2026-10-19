#!/bin/sh
# Periodic safety net for the 30 Mbit/s inbound cap, run by dl-cap-check.service from dl-cap-check.timer (2 min
# after boot, then every 5 min). The NetworkManager hook (nm-dispatcher-90-dl-cap) leaves dl-cap.service alone when
# a systemctl call times out (PID 1 busy) or the unit is busy, so a reset of eth0 at such a moment could leave the
# cap off; this puts it back within about 5 minutes. Install as /usr/local/sbin/dl-cap-check.sh (mode 755).
#   dl-cap.service disabled (or is-enabled times out)  -> nothing: a disabled cap stays off
#   lock held by the hook for more than LOCK_WAIT_S     -> nothing this round (the hook is handling an event)
#   dl-cap.service has a job queued, or is activating/deactivating/reloading -> nothing: that start verifies the cap
#   `dl-cap.sh check` passes                           -> nothing (silent, so the timer doesn't fill the journal)
#   otherwise: log (tag dl-cap-check) and `systemctl --no-block restart dl-cap.service`
# Uses the hook's lock file, so the two never restart dl-cap at the same moment (a restart during a running start
# kills that start: QA #17 N1). Every call is bounded: is-enabled, show, check and restart CALL_S each, plus the
# lock wait: 4 x 4 + 8 = 24 s worst case, under the unit's TimeoutStartSec=30.
# Exit 0 except when the restart call itself fails or times out (exit 1, so the unit shows it).
DEV=eth0   # must match DEV in dl-cap.sh
DL_CAP=/usr/local/sbin/dl-cap.sh   # the harness rewrites this line to point at the repo copy
UNIT=dl-cap.service
LOCK=${DL_CAP_HOOK_LOCK:-/run/lock/dl-cap-hook.lock}   # same file as nm-dispatcher-90-dl-cap
LOCK_WAIT_S=${DL_CAP_CHECK_LOCK_WAIT_S:-8}
case "$LOCK_WAIT_S" in ''|*[!0-9]*) LOCK_WAIT_S=8 ;; esac
[ "$LOCK_WAIT_S" -le 8 ] || LOCK_WAIT_S=8   # keep the worst case under TimeoutStartSec
CALL_S=4

log() { logger -t dl-cap-check -- "$*" 2>/dev/null || :; }

timeout "$CALL_S" systemctl is-enabled --quiet "$UNIT" 2>/dev/null; rc=$?
[ "$rc" = 124 ] && log "systemctl is-enabled timed out; trying again next run"
[ "$rc" = 0 ] || exit 0

if (exec 9>>"$LOCK") 2>/dev/null && command -v flock >/dev/null 2>&1; then
  exec 9>>"$LOCK"
  if ! flock -w "$LOCK_WAIT_S" 9; then
    log "lock $LOCK busy for ${LOCK_WAIT_S}s (NetworkManager hook running); trying again next run"
    exit 0
  fi
fi

if ! props=$(timeout "$CALL_S" systemctl show -p ActiveState -p Job "$UNIT" 2>/dev/null); then
  log "systemctl show timed out or failed; trying again next run"
  exit 0
fi
state=$(printf '%s\n' "$props" | sed -n 's/^ActiveState=//p')
job=$(printf '%s\n' "$props" | sed -n 's/^Job=//p')
case "$job" in 0|"("*) job="" ;; esac
[ -n "$job" ] && exit 0
case "$state" in activating|deactivating|reloading) exit 0 ;; esac

if [ -x "$DL_CAP" ] && timeout "$CALL_S" env DEV="$DEV" "$DL_CAP" check >/dev/null 2>&1; then
  exit 0
fi
log "30 Mbit/s inbound cap missing on $DEV (dl-cap.service $state); restarting dl-cap.service"
if ! timeout "$CALL_S" systemctl --no-block restart "$UNIT"; then
  log "systemctl restart dl-cap.service failed or timed out"
  exit 1
fi
exit 0
