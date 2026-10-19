#!/bin/sh
# Wait until the Jetson GPU is usable before Ollama starts (systemd ExecStartPre via ollama-wait-cuda.conf).
# Seen on the board after a reboot: ollama.service started while /dev/nvhost-ctrl-gpu, /dev/nvmap and the nvgpu
# sysfs paths already existed, but GPU discovery still logged only library=cpu ("offloaded 0/37 layers to GPU")
# 0.44 s after "discovering available GPUs", until a manual restart; after that it logged library=CUDA compute=7.2.
# So the race is CUDA initialisation, not the device nodes. Two gates, both bounded by CUDA_WAIT_S in total:
#   1. the device nodes and an nvgpu/gv11b sysfs path exist;
#   2. a cuInit probe succeeds: cuInit(0) == 0 and cuDeviceGetCount >= 1, through python3 ctypes on libcuda.so.1.
#      Each probe is a fresh python3 process (cuInit can't be retried in-process), capped at 10 s (timeout), and
#      runs as the Ollama user: as the unit's User= when ExecStartPre runs unprivileged (the normal case), or via
#      runuser when this script runs as root. It touches no model and doesn't talk to Ollama.
#   If python3 or libcuda is missing, gate 2 is skipped with a log line (device checks only).
# On timeout this script logs and exits 0 so ollama.service still starts (never fails the unit);
# ollama-gpu-check.service still restarts Ollama once per boot if it comes up on CPU.
#
# Env: CUDA_WAIT_S — max seconds to wait for both gates together (default 60, at most 150 so ExecStartPre stays
#   inside the drop-in's TimeoutStartSec=180). A whole number of seconds, no leading zero, at most 6 digits;
#   0 = skip the wait entirely (including the cuInit probe): log and exit 0 at once, so a reboot can force the
#   old race (QA boot R1b in docs/deploy-jetson.md: CUDA_WAIT_S=0 and CUDA_VISIBLE_DEVICES=-1 for the first start
#   only). Anything else is ignored and 60 is used; values above 150 are clamped; each with a warning (ExecStartPre
#   must not fail the unit on a bad env). The total wait can exceed CUDA_WAIT_S by at most 3 s: the last probe gets
#   only the time left (at least 1 s), plus timeout's 2 s kill grace.
#   CUDA_PROBE_PYTHON — interpreter for the cuInit probe (default python3).
#   CUDA_PROBE_USER — user the probe runs as when this script runs as root (default ollama).
#   CUDA_TEST_ROOT — if set, device/sysfs paths are prefixed with this directory (harness only).
#
# Every readiness check is logged with a wall-clock timestamp and the elapsed seconds since start, so the journal
# shows on each boot whether the device nodes were already there and whether cuInit ever failed early:
#   wait-cuda: <iso> (+Ns) device/sysfs: present|missing
#   wait-cuda: <iso> (+Ns) cuInit probe #k: <result> (exit N)
set -u
ROOT=${CUDA_TEST_ROOT:-}
PY=${CUDA_PROBE_PYTHON:-python3}
PROBE_USER=${CUDA_PROBE_USER:-ollama}
PROBE_TIMEOUT_S=10
DEFAULT_WAIT=60
MAX_WAIT=150
raw=${CUDA_WAIT_S:-$DEFAULT_WAIT}
case "$raw" in
  0) wait_s=0 ;;
  ''|*[!0-9]*|0*|???????*)
    echo "wait-cuda: CUDA_WAIT_S must be a whole number of seconds from 0 to $MAX_WAIT (e.g. 60; 0 = skip), got '$raw'; using $DEFAULT_WAIT" >&2
    wait_s=$DEFAULT_WAIT ;;
  *) wait_s=$raw ;;
esac
if [ "$wait_s" -gt "$MAX_WAIT" ]; then
  echo "wait-cuda: CUDA_WAIT_S=$wait_s is above $MAX_WAIT (TimeoutStartSec=180 in ollama-wait-cuda.conf); using $MAX_WAIT" >&2
  wait_s=$MAX_WAIT
fi

# True when the Jetson GPU device nodes and at least one nvgpu/sysfs readiness path exist under ROOT.
gpu_ready() {
  [ -e "$ROOT/dev/nvhost-ctrl-gpu" ] && [ -e "$ROOT/dev/nvmap" ] || return 1
  [ -d "$ROOT/sys/module/nvgpu" ] && return 0
  [ -e "$ROOT/sys/devices/gpu.0" ] && return 0
  # Shell glob: if nothing matches, the pattern is left literal and -e fails.
  set -- "$ROOT"/sys/class/devfreq/*gv11b*
  [ -e "$1" ] && return 0
  return 1
}

# ISO-8601 local timestamp + elapsed seconds since start; used on every readiness log line.
ts() {
  now=$(date +%s)
  printf '%s (+%ss)' "$(date -Iseconds 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S%z')" "$((now - start))"
}

log() { echo "wait-cuda: $(ts) $*"; }

# The probe: exit 0 = cuInit 0 and >= 1 device, 1 = not ready yet, 3 = libcuda/ctypes unavailable (skip gate 2).
# No single quotes inside (it is passed as one sh argument).
PROBE='
import sys
try:
    import ctypes
except Exception as e:
    print("ctypes unavailable: %s" % e); sys.exit(3)
lib = None
for name in ("libcuda.so.1", "/usr/lib/aarch64-linux-gnu/tegra/libcuda.so.1"):
    try:
        lib = ctypes.CDLL(name); break
    except OSError:
        pass
if lib is None:
    print("libcuda.so.1 not loadable"); sys.exit(3)
r = lib.cuInit(0)
n = ctypes.c_int(0)
c = lib.cuDeviceGetCount(ctypes.byref(n)) if r == 0 else -1
print("cuInit=%d cuDeviceGetCount=%d devices=%d" % (r, c, n.value))
sys.exit(0 if r == 0 and c == 0 and n.value >= 1 else 1)
'

# Run one probe in a fresh process, as PROBE_USER when we are root. $1 = per-probe timeout in seconds.
# Sets probe_out to the probe's output; returns its exit status (124/137 = timed out).
run_probe() {
  if [ "$(id -u)" = 0 ] && [ "$PROBE_USER" != root ]; then
    if command -v runuser >/dev/null 2>&1 && id "$PROBE_USER" >/dev/null 2>&1; then
      probe_out=$(timeout -k 2 "$1" runuser -u "$PROBE_USER" -- "$PY" -c "$PROBE" 2>&1)
      return $?
    fi
    [ -n "${warned_root:-}" ] || echo "wait-cuda: $(ts) running as root but cannot switch to '$PROBE_USER' (runuser or user missing); probing as root" >&2
    warned_root=1
  fi
  probe_out=$(timeout -k 2 "$1" "$PY" -c "$PROBE" 2>&1)
}

start=$(date +%s)
remaining() { now=$(date +%s); echo $((wait_s - (now - start))); }

# CUDA_WAIT_S=0: skip the wait entirely (including the cuInit probe). Used by QA to reproduce the old race.
if [ "$wait_s" -eq 0 ]; then
  echo "wait-cuda: $(ts) CUDA_WAIT_S=0; skipping the wait entirely — Ollama starts at once"
  exit 0
fi

# Gate 1: device nodes and sysfs. Log every check with a timestamp so the journal shows whether they were
# already present at the start of the wait (the bad boot of 2026-10-19).
if gpu_ready; then
  log "device/sysfs: present"
else
  log "device/sysfs: missing; waiting up to ${wait_s}s for nvhost-ctrl-gpu, nvmap, and nvgpu/gv11b sysfs"
  while :; do
    if [ "$(remaining)" -le 0 ]; then
      # One last check at the deadline (I1).
      if gpu_ready; then log "device/sysfs: present (at deadline)"; break; fi
      log "device/sysfs: missing; timed out after ${wait_s}s; starting Ollama anyway (ollama-gpu-check may restart once if CPU-only)" >&2
      exit 0
    fi
    if gpu_ready; then log "device/sysfs: present"; break; fi
    sleep 1
  done
fi

# Gate 2: cuInit probe. Every probe result is logged (return code, device count, elapsed time) so a later
# journal review can tell whether cuInit ever failed early on this boot.
if ! command -v "$PY" >/dev/null 2>&1; then
  log "$PY not found; skipping the cuInit check (device checks only). GPU ready"
  exit 0
fi
probe_n=0
while :; do
  left=$(remaining)
  t=$PROBE_TIMEOUT_S
  [ "$left" -lt "$t" ] && t=$left
  [ "$t" -lt 1 ] && t=1   # the last probe at the deadline still gets 1 s
  probe_n=$((probe_n + 1))
  run_probe "$t"; rc=$?
  case "$rc" in
    0)
      log "cuInit probe #$probe_n: OK ($probe_out); GPU ready"
      exit 0 ;;
    3)
      log "cuInit probe #$probe_n: unavailable ($probe_out); device checks only. GPU ready"
      exit 0 ;;
    124|137)
      log "cuInit probe #$probe_n: timed out after ${t}s (exit $rc)" ;;
    *)
      log "cuInit probe #$probe_n: not ready yet (exit $rc: $(printf '%s' "$probe_out" | tail -n 1 | cut -c1-200))" ;;
  esac
  if [ "$(remaining)" -le 0 ]; then
    log "cuInit never ready; timed out after ${wait_s}s; starting Ollama anyway (ollama-gpu-check may restart once if CPU-only)" >&2
    exit 0
  fi
  sleep 1
done
