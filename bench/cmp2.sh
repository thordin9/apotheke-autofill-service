#!/bin/bash
# Raw model comparison straight against Ollama (no service): one model at a time, Ollama restarted before each
# (frees memory the previous runner kept), tegrastats for peak RAM. Stop the autofill container first so nothing
# else calls Ollama. Runs bench/rawbench.py inside the service image, so it uses that image's prompt and parser.
# Usage: sudo bench/cmp2.sh <image> <model> [model...]   -> $OUT/raw_<model>_cap256.json, $OUT/cmp2.log
source "$(dirname "$0")/common.sh"
IMG=${1:?image}; shift; : > "$OUT/cmp2.log"
for M in "$@"; do
  systemctl restart ollama; sleep 8; sync; echo 3 > /proc/sys/vm/drop_caches
  T=$OUT/tegra_cmp2_${M//[:\/]/_}.log
  { echo "== $M $(date -Is)"; free -m; } >> "$OUT/cmp2.log"
  tegrastats --interval 500 --logfile "$T" &
  docker run --rm --network host -e OLLAMA_MODEL="$M" -e PHOTO_GLOB='/photos/*.jpg' -e REQ_TIMEOUT=600 \
    -v "$PHOTOS":/photos:ro -v "$OUT":/out -v "$BENCH_DIR/rawbench.py":/app/rawbench.py:ro -w /app \
    "$IMG" python3.11 rawbench.py >> "$OUT/cmp2.log" 2>&1
  tegrastats --stop; sleep 1
  awk '{for(i=1;i<=NF;i++) if($i=="RAM"){split($(i+1),a,"/"); if(a[1]>m)m=a[1]}} END{print "peak RAM MB", m}' "$T" >> "$OUT/cmp2.log"
  ollama stop "$M" >/dev/null 2>&1
done
