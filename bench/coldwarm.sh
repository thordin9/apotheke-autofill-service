#!/bin/bash
# Cold vs warm /v1/suggest: unload the model with `ollama stop`, then three sequential requests.
# Note: repeating the same photo hits Ollama's prompt cache, so "warm" here is a best case; use different photos
# (svcpass.sh) for realistic warm times.
source "$(dirname "$0")/common.sh"
M=$(sed -n 's/^OLLAMA_MODEL=//p' "$AUTOFILL_DIR/service.env"); P=${1:-$PHOTOS/hammer.jpg}
req() { curl -s -o /tmp/cw_$1.json -w "$1 http=%{http_code} total=%{time_total}s\n" -H @"$AUTH_HEADER_FILE" -F image=@"$P" -F "$CTX" "$SERVICE_URL/v1/suggest"; head -c 300 /tmp/cw_$1.json; echo; }
ollama stop "$M" >/dev/null 2>&1; sleep 3; echo "model=$M loaded: $(ollama ps | tail -n +2 | wc -l)"
req cold; req warm; req warm2
