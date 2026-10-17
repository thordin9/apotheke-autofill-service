#!/bin/bash
# Post-deploy checks: /healthz, /v1/info, one suggest, cold recovery after `ollama stop`, two concurrent calls.
# Sends real requests to the service and unloads the model: run it only when nobody else is using the board.
source "$(dirname "$0")/common.sh"
M=$(sed -n 's/^OLLAMA_MODEL=//p' "$AUTOFILL_DIR/service.env")
sug() { curl -s -o /tmp/v_$1.json -D /tmp/vh_$1 -w "$1 http=%{http_code} t=%{time_total}s" -H @"$AUTH_HEADER_FILE" -F image=@"$PHOTOS/$2" -F "$CTX" "$SERVICE_URL/v1/suggest"
        echo " retry-after=$(grep -i '^retry-after' /tmp/vh_$1 | tr -d '\r' | cut -d' ' -f2) $(head -c 160 /tmp/v_$1.json)"; }
info() { curl -s -H @"$AUTH_HEADER_FILE" "$SERVICE_URL/v1/info" | python3 -c "import json,sys;d=json.load(sys.stdin);print('info', d['name'], 'ready=', d['ready'])"; }
for i in $(seq 1 60); do curl -s -H @"$AUTH_HEADER_FILE" "$SERVICE_URL/v1/info" | grep -q '"ready":true' && break; sleep 2; done
echo "healthz $(curl -s "$SERVICE_URL/healthz")"; info
sug basic hammer.jpg
echo "--- cold: ollama stop $M"; ollama stop "$M"; sleep 2; info
sug cold1 laptop.jpg
for i in $(seq 1 30); do sleep 2; curl -s -H @"$AUTH_HEADER_FILE" "$SERVICE_URL/v1/info" | grep -q '"ready":true' && break; done; echo "ready again after ~$((i*2+2))s"; info
sug cold_retry laptop.jpg
echo "--- two at once"
sug c1 sneakers.jpg & sleep 0.5; sug c2 t-shirt.jpg; wait
ollama ps | tail -n +2
