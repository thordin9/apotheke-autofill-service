#!/bin/sh
# Wait until the Jetson GPU looks usable before Ollama starts (systemd ExecStartPre via ollama-wait-cuda.conf).
# Seen on the board after a reboot: ollama.service started while /dev/nvhost-ctrl-gpu and /dev/nvmap already
# existed, but GPU discovery still logged only library=cpu ("offloaded 0/37 layers to GPU") until a manual
# restart; after that it logged library=CUDA compute=7.2. Waiting for the nvgpu sysfs/devfreq path as well as
# the device nodes closes most of that race; ollama-gpu-check.sh still restarts once if Ollama comes up on CPU.
#
# On timeout this script logs and exits 0 so ollama.service still starts (never fails the unit forever).
# Env: CUDA_WAIT_S — max seconds to wait (default 60). Positive whole number, no leading zero, at most 6 digits;
#   anything else is ignored and 60 is used, with a warning (ExecStartPre must not fail the unit on a bad env).
#   CUDA_TEST_ROOT — if set, device/sysfs paths are prefixed with this directory (harness only).
set -u
ROOT=${CUDA_TEST_ROOT:-}
DEFAULT_WAIT=60
raw=${CUDA_WAIT_S:-$DEFAULT_WAIT}
case "$raw" in
  ''|*[!0-9]*|0*|???????*)
    echo "wait-cuda: CUDA_WAIT_S must be a positive whole number of seconds, at most 6 digits (e.g. 60), got '$raw'; using $DEFAULT_WAIT" >&2
    wait_s=$DEFAULT_WAIT ;;
  *) wait_s=$raw ;;
esac

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

if gpu_ready; then
  echo "wait-cuda: GPU ready"
  exit 0
fi

echo "wait-cuda: waiting up to ${wait_s}s for Jetson GPU (nvhost-ctrl-gpu, nvmap, and nvgpu/gv11b sysfs)"
start=$(date +%s)
while :; do
  now=$(date +%s)
  elapsed=$((now - start))
  if [ "$elapsed" -ge "$wait_s" ]; then
    echo "wait-cuda: timed out after ${wait_s}s; starting Ollama anyway (ollama-gpu-check may restart once if CPU-only)" >&2
    exit 0
  fi
  if gpu_ready; then
    echo "wait-cuda: GPU ready after ${elapsed}s"
    exit 0
  fi
  sleep 1
done
