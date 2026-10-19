#!/bin/sh
# After Ollama starts, if GPU discovery logged only library=cpu, restart ollama.service at most once per boot.
# Complements wait-cuda.sh (ExecStartPre): the wait closes most of the race; this recovers the rest without looping
# when the board truly has no GPU. Never loads, pulls or allowlists models and never calls the Ollama HTTP API.
#
# Usage: ollama-gpu-check.sh        apply (default): restart at most once if CPU-only, then exit 0
#        ollama-gpu-check.sh check  exit 0 if the current start logged an "inference compute" line with library=CUDA,
#                                   else 1. Reads ollama.service's journal, so run it as root (sudo) or as a member of
#                                   the systemd-journal (or adm) group; otherwise it sees no lines and exits 1.
#
# Env: OLLAMA_GPU_CHECK_MARKER — restart marker path (default /run/ollama-gpu-check.restarted; tmpfs, cleared on reboot)
#      OLLAMA_GPU_CHECK_WAIT_S — seconds to wait for an "inference compute" journal line (default 120, longer than Ollama's own discovery
#        timeouts: 30 s bootstrap pass + 30 s validation pass in v0.15.x; same validation
#        as CUDA_WAIT_S: bad values fall back to 120 with a warning)
#      OLLAMA_GPU_CHECK_RESTART_DELAY_S — seconds to wait after seeing library=cpu before the single restart, so the
#        restart doesn't land in the same not-ready window (default 10; 0 to 300; bad values fall back to 10 with a
#        warning). The restarted Ollama also passes wait-cuda.sh's cuInit gate again.
set -u
MARKER=${OLLAMA_GPU_CHECK_MARKER:-/run/ollama-gpu-check.restarted}
DEFAULT_WAIT=120
raw=${OLLAMA_GPU_CHECK_WAIT_S:-$DEFAULT_WAIT}
case "$raw" in
  ''|*[!0-9]*|0*|???????*)
    echo "ollama-gpu-check: OLLAMA_GPU_CHECK_WAIT_S must be a positive whole number of seconds, at most 6 digits, got '$raw'; using $DEFAULT_WAIT" >&2
    wait_s=$DEFAULT_WAIT ;;
  *) wait_s=$raw ;;
esac
DEFAULT_DELAY=10
raw=${OLLAMA_GPU_CHECK_RESTART_DELAY_S:-$DEFAULT_DELAY}
case "$raw" in
  0) delay_s=0 ;;
  ''|*[!0-9]*|0*|????*)
    echo "ollama-gpu-check: OLLAMA_GPU_CHECK_RESTART_DELAY_S must be a whole number of seconds from 0 to 300, got '$raw'; using $DEFAULT_DELAY" >&2
    delay_s=$DEFAULT_DELAY ;;
  *) if [ "$raw" -gt 300 ]; then
       echo "ollama-gpu-check: OLLAMA_GPU_CHECK_RESTART_DELAY_S must be a whole number of seconds from 0 to 300, got '$raw'; using $DEFAULT_DELAY" >&2
       delay_s=$DEFAULT_DELAY
     else delay_s=$raw; fi ;;
esac

case "${1:-apply}" in
  check|apply) ;;
  *) echo "usage: ollama-gpu-check.sh [apply|check]" >&2; exit 2 ;;
esac

# Print journal lines for the current ollama.service start: by InvocationID, else since ActiveEnterTimestamp,
# else this boot.
ollama_journal() {
  # Prefer the start's InvocationID: exact, and immune to second resolution and wall-clock steps at boot.
  inv=$(systemctl show ollama.service -p InvocationID --value 2>/dev/null || true)
  case "$inv" in
    [0-9a-f][0-9a-f][0-9a-f][0-9a-f]*)
      journalctl -b "_SYSTEMD_INVOCATION_ID=$inv" --no-pager -o cat 2>/dev/null || true
      return ;;
  esac
  since=$(systemctl show ollama.service -p ActiveEnterTimestamp --value 2>/dev/null || true)
  if [ -n "$since" ] && [ "$since" != "n/a" ] && [ "$since" != "" ]; then
    journalctl -u ollama.service --since "$since" --no-pager -o cat 2>/dev/null || true
  else
    journalctl -u ollama.service -b --no-pager -o cat 2>/dev/null || true
  fi
}

# Exit 0 = library=CUDA, 1 = library=cpu (or other non-CUDA), 2 = no "inference compute" line yet.
compute_status() {
  # Any "inference compute" line with library=cuda (any case, optionally quoted) counts as GPU: Ollama prints
  # one line per device, and older versions print library=cuda in lower case.
  ollama_journal | awk '
    /msg="server config"/ { n = 0; gpu = 0 }   # each Ollama start logs this first; keeps only the last start
    /inference compute/ { n++; l = tolower($0); if (l ~ /library="?cuda"?( |$)/) gpu = 1 }
    END {
      if (n == 0) exit 2
      exit gpu ? 0 : 1
    }'
}

# Poll until compute_status is 0 or 1, or wait_s seconds elapse (then return the last status, or 2).
wait_compute() {
  start=$(date +%s)
  last=2
  while :; do
    compute_status
    last=$?
    [ "$last" -ne 2 ] && return "$last"
    now=$(date +%s)
    [ $((now - start)) -ge "$wait_s" ] && return 2
    sleep 1
  done
}

if [ "${1:-apply}" = "check" ]; then
  compute_status
  st=$?
  if [ "$st" = 0 ]; then echo "ollama-gpu-check: GPU (library=CUDA)"; exit 0; fi
  if [ "$st" = 1 ]; then echo "ollama-gpu-check: CPU-only (no library=CUDA)" >&2; exit 1; fi
  if [ "$(id -u)" != 0 ]; then
    echo "ollama-gpu-check: no inference compute line found; reading ollama's journal needs root or the systemd-journal group (try sudo)" >&2
  else
    echo "ollama-gpu-check: no inference compute line in the journal yet" >&2
  fi
  exit 1
fi

# apply
if ! systemctl is-active --quiet ollama.service 2>/dev/null; then
  echo "ollama-gpu-check: ollama.service is not active; nothing to do"
  exit 0
fi

wait_compute
st=$?
if [ "$st" = 0 ]; then
  echo "ollama-gpu-check: GPU already in use (library=CUDA)"
  exit 0
fi
if [ "$st" = 2 ]; then
  echo "ollama-gpu-check: no inference compute line after ${wait_s}s; leaving Ollama as-is" >&2
  exit 0
fi

# CPU-only
if [ -e "$MARKER" ]; then
  echo "ollama-gpu-check: still library=cpu, but already restarted once this boot ($MARKER); not looping" >&2
  exit 0
fi

mkdir -p "$(dirname "$MARKER")" 2>/dev/null || true
if ! touch "$MARKER" 2>/dev/null; then
  echo "ollama-gpu-check: cannot write marker $MARKER; not restarting" >&2
  exit 0
fi

if [ "$delay_s" -gt 0 ]; then
  echo "ollama-gpu-check: library=cpu on this start; waiting ${delay_s}s before the single restart"
  sleep "$delay_s"
fi
echo "ollama-gpu-check: library=cpu on this start; restarting ollama.service once ($MARKER)"
# Blocking restart so the follow-up check sees the new start's journal. Never pull/load models.
if ! systemctl restart ollama.service; then
  echo "ollama-gpu-check: systemctl restart ollama.service failed; marker kept so we do not loop" >&2
  exit 0
fi

wait_compute
st=$?
if [ "$st" = 0 ]; then
  echo "ollama-gpu-check: after restart, library=CUDA"
  exit 0
fi
echo "ollama-gpu-check: after restart, still not library=CUDA (status=$st); not restarting again" >&2
exit 0
