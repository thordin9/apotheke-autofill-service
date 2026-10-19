#!/bin/sh
# After Ollama starts, if GPU discovery logged only library=cpu, restart ollama.service at most once per boot.
# Complements wait-cuda.sh (ExecStartPre): the wait closes most of the race; this recovers the rest without looping
# when the board truly has no GPU. Never loads, pulls or allowlists models and never calls the Ollama HTTP API.
#
# Usage: ollama-gpu-check.sh        apply (default): restart at most once if CPU-only, then exit 0
#        ollama-gpu-check.sh check  exit 0 if the current start's "inference compute" line has library=CUDA, else 1
#
# Env: OLLAMA_GPU_CHECK_MARKER — restart marker path (default /run/ollama-gpu-check.restarted; tmpfs, cleared on reboot)
#      OLLAMA_GPU_CHECK_WAIT_S — seconds to wait for an "inference compute" journal line (default 30; same validation
#        as CUDA_WAIT_S: bad values fall back to 30 with a warning)
set -u
MARKER=${OLLAMA_GPU_CHECK_MARKER:-/run/ollama-gpu-check.restarted}
DEFAULT_WAIT=30
raw=${OLLAMA_GPU_CHECK_WAIT_S:-$DEFAULT_WAIT}
case "$raw" in
  ''|*[!0-9]*|0*|???????*)
    echo "ollama-gpu-check: OLLAMA_GPU_CHECK_WAIT_S must be a positive whole number of seconds, at most 6 digits, got '$raw'; using $DEFAULT_WAIT" >&2
    wait_s=$DEFAULT_WAIT ;;
  *) wait_s=$raw ;;
esac

case "${1:-apply}" in
  check|apply) ;;
  *) echo "usage: ollama-gpu-check.sh [apply|check]" >&2; exit 2 ;;
esac

# Print journal lines for the current ollama.service start (ActiveEnterTimestamp), or this boot as a fallback.
ollama_journal() {
  since=$(systemctl show ollama.service -p ActiveEnterTimestamp --value 2>/dev/null || true)
  if [ -n "$since" ] && [ "$since" != "n/a" ] && [ "$since" != "" ]; then
    journalctl -u ollama.service --since "$since" --no-pager -o cat 2>/dev/null || true
  else
    journalctl -u ollama.service -b --no-pager -o cat 2>/dev/null || true
  fi
}

# Exit 0 = library=CUDA, 1 = library=cpu (or other non-CUDA), 2 = no "inference compute" line yet.
compute_status() {
  ollama_journal | awk '
    /inference compute/ { line = $0 }
    END {
      if (line == "") exit 2
      if (line ~ /library=CUDA/) exit 0
      exit 1
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
  echo "ollama-gpu-check: no inference compute line in the journal yet" >&2
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
