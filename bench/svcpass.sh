#!/bin/bash
# One pass over the photos through /v1/suggest for a given model and image. Recreates the "autofill" container
# (host network, nvidia runtime, env file, restart unless-stopped) with OLLAMA_MODEL overridden, waits for
# /v1/info ready, warms up once with WARMUP_PHOTO (keep it outside the test set to avoid Ollama prompt-cache hits).
# Usage: bench/svcpass.sh <model> <image> <label>   -> $OUT/sp_<label>.jsonl, $OUT/sp_<label>.log
source "$(dirname "$0")/common.sh"
M=${1:?model}; IMG=${2:?image}; L=${3:?label}; WARMUP_PHOTO=${WARMUP_PHOTO:?set WARMUP_PHOTO to a photo outside the test set}
docker rm -f autofill >/dev/null 2>&1
docker run -d --name autofill --network host --runtime nvidia --restart unless-stopped \
  --env-file "$AUTOFILL_DIR/service.env" -e OLLAMA_MODEL="$M" "$IMG" >/dev/null
for i in $(seq 1 60); do curl -s -H @"$AUTH_HEADER_FILE" "$SERVICE_URL/v1/info" | grep -q '"ready":true' && break; sleep 2; done
echo "ready after ~$((i*2))s" > "$OUT/sp_$L.log"
curl -s -o /dev/null -w "warmup http=%{http_code} %{time_total}s\n" -H @"$AUTH_HEADER_FILE" -F image=@"$WARMUP_PHOTO" -F "$CTX" "$SERVICE_URL/v1/suggest" >> "$OUT/sp_$L.log"
: > "$OUT/sp_$L.jsonl"; tmp=$(mktemp)
for f in "$PHOTOS"/*.jpg; do
  m=$(curl -s -o "$tmp" -w '%{http_code} %{time_total}' -H @"$AUTH_HEADER_FILE" -F image=@"$f" -F "$CTX" "$SERVICE_URL/v1/suggest")
  python3 -c "import json,sys;b=open(sys.argv[4]).read();print(json.dumps({'photo':sys.argv[1],'http':int(sys.argv[2]),'secs':float(sys.argv[3]),'body':json.loads(b) if b.strip().startswith('{') else b}))" "$(basename "$f")" $m "$tmp" >> "$OUT/sp_$L.jsonl"
done
rm -f "$tmp"; ollama ps | tail -n +2 >> "$OUT/sp_$L.log"
