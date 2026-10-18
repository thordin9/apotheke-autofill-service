#!/bin/bash
# Size/model sweep (docs/benchmarks.md, "Image-size sweep"). Run on the board as root, with nothing else using Ollama:
# every config unloads all models first. Usage:
#   bench/sweep.sh SETS_DIR OUT_DIR IMAGE CONFIG [CONFIG ...]
#     SETS_DIR  output of bench/make_sets.py (448/ 560/ 672/ 1024/) plus warmup.jpg
#     OUT_DIR   gets sweep.jsonl, sweep.log and tegrastats.log (epoch-stamped, for peak RAM)
#     IMAGE     the service image, e.g. apotheke-autofill:jetson (sweep.py imports its prompt and schema)
#     CONFIG    label=model_tag@set, e.g. q25-200k@1024=qwen2.5vl-jetson:3b-200k@1024
set -euo pipefail
BENCH_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SETS=$(realpath "$1"); OUT=$(realpath "$2"); IMAGE=$3; shift 3
mkdir -p "$OUT"
( tegrastats --interval 500 | while IFS= read -r l; do echo "$(date +%s.%N) $l"; done ) > "$OUT/tegrastats.log" 2>&1 &
TP=$!
trap 'pkill -P $TP 2>/dev/null; kill $TP 2>/dev/null; pkill -x tegrastats 2>/dev/null || true' EXIT
docker run --rm --runtime runc --no-healthcheck --name autofill-sweep --network host --entrypoint "" \
  -e PYTHONPATH=/app -v "$BENCH_DIR":/bench:ro -v "$SETS":/sets:ro -v "$OUT":/out \
  -w /app "$IMAGE" python3 /bench/sweep.py "$@" 2>&1 | tee -a "$OUT/sweep.log"
