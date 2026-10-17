#!/bin/bash
# 100 sequential /v1/suggest calls cycling the photos; per-request HTTP status and time; tegrastats log for peak RAM.
# Usage: bench/bench100.sh <label>   -> $OUT/bench100_<label>.csv (n,photo,http,seconds) and $OUT/tegra_<label>.log
source "$(dirname "$0")/common.sh"
L=${1:?label}; CSV=$OUT/bench100_$L.csv; TL=$OUT/tegra_$L.log
cd "$PHOTOS"; files=(*.jpg); : > "$CSV"
tegrastats --interval 500 --logfile "$TL" &
for i in $(seq 1 100); do f=${files[$(( (i-1) % ${#files[@]} ))]}
  r=$(curl -s -o /dev/null -w '%{http_code},%{time_total}' -H @"$AUTH_HEADER_FILE" -F image=@"$f" -F "$CTX" "$SERVICE_URL/v1/suggest")
  echo "$i,$f,$r" >> "$CSV"; done
tegrastats --stop
python3 - "$CSV" "$TL" <<'PY'
import csv, re, statistics, sys
rows = list(csv.reader(open(sys.argv[1]))); t = sorted(float(r[3]) for r in rows)
codes = {}; [codes.__setitem__(r[2], codes.get(r[2], 0) + 1) for r in rows]
ram = [int(m) for m in re.findall(r"RAM (\d+)/", open(sys.argv[2]).read())]
gpu = [float(m) for m in re.findall(r"GPU@([\d.]+)C", open(sys.argv[2]).read())]
print(f"n={len(t)} codes={codes} mean={statistics.mean(t):.2f} p50={statistics.median(t):.2f} "
      f"p95={t[max(0, int(.95 * len(t)) - 1)]:.2f} max={t[-1]:.2f} peakRAM={max(ram, default=0)}MB maxGPU={max(gpu, default=0)}C")
PY
