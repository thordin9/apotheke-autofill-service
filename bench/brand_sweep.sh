#!/bin/bash
# Brand / model number / serial number prompt sweep (bench/brand_variants.py). Run on the board as
# root, with nothing else using Ollama. Usage:
#   bench/brand_sweep.sh SETS_DIR OUT_DIR IMAGE MODEL [SET ...]
#     SETS_DIR  output of bench/make_sets.py (560/ 1024/ ...) plus warmup.jpg
#     OUT_DIR   gets raw/<variant>-<set>.jsonl, prompts/, schemas/, sweep.log, tegrastats.log
#     IMAGE     the service image, e.g. apotheke-autofill:jetson
#     MODEL     the Ollama tag, e.g. qwen2.5vl-jetson:3b
#     SET       image sizes to run (default: 560 1024); per size: base, v1_empty, v2_omit, v3_lean
# Then: python3 bench/score_brand.py OUT_DIR > OUT_DIR/scored.md
set -euo pipefail
BENCH_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SETS=$(realpath "$1"); OUT=$(realpath "$2"); IMAGE=$3; MODEL=$4; shift 4
SIZES=("$@"); [ ${#SIZES[@]} -gt 0 ] || SIZES=(560 1024)
CONFIGS=()
for s in "${SIZES[@]}"; do
  for v in base v1_empty v2_omit v3_lean; do CONFIGS+=("$v-$s=$MODEL@$s:$v"); done
done
mkdir -p "$OUT"
( tegrastats --interval 500 | while IFS= read -r l; do echo "$(date +%s.%N) $l"; done ) > "$OUT/tegrastats.log" 2>&1 &
TP=$!
trap 'pkill -P $TP 2>/dev/null; kill $TP 2>/dev/null; pkill -x tegrastats 2>/dev/null || true' EXIT
docker run --rm --runtime runc --no-healthcheck --name autofill-brand-sweep --network host --entrypoint "" \
  -e PYTHONPATH=/app:/bench -e OUT_DIR=/out -v "$BENCH_DIR":/bench:ro -v "$SETS":/sets:ro -v "$OUT":/out \
  -w /app "$IMAGE" python3 /bench/brand_sweep.py "${CONFIGS[@]}" 2>&1 | tee -a "$OUT/sweep.log"
