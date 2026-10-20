#!/usr/bin/env bash
# Build the Worker and check /healthz and /v1/info in the local runtime.
# Remote bindings, including Workers AI, stay off. No account credentials.
set -euo pipefail

cd "$(dirname "$0")/.."

unset CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_KEY
export CI=true
export WRANGLER_SEND_METRICS=false

root="${TMPDIR:-/tmp}/autofill-local-info-$$"
mkdir -p "$root"
log="$root/wrangler-dev.log"
bundle="$root/bundle"
port=8787
inspector_port=9239
token=ci-local-check
pid=""

cleanup() {
  if [[ -n "$pid" ]]; then
    kill -- "-${pid}" 2>/dev/null || kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  fi
  rm -rf "$root"
}
trap cleanup EXIT

npx wrangler deploy --dry-run --outdir "$bundle" --no-latest >/dev/null

BUNDLE_JS="$bundle/index.js" node --input-type=module <<'JS'
import fs from "node:fs";

const src = fs.readFileSync(process.env.BUNDLE_JS, "utf8");
const match = src.match(/export\s*\{([\s\S]*?)\};/);
if (!match) {
  process.stderr.write("bundle has no export block\n");
  process.exit(1);
}
const names = match[1]
  .split(",")
  .map((part) => part.trim())
  .filter(Boolean)
  .map((part) => {
    const bits = part.split(/\s+as\s+/);
    return (bits[1] ?? bits[0]).trim();
  });
process.stdout.write(`${names.join("\n")}\n`);
const allowed = new Set(["Budget", "default"]);
if (names.length !== allowed.size || names.some((name) => !allowed.has(name))) {
  process.stderr.write("unexpected bundle exports\n");
  process.exit(1);
}
JS

set -m
npx wrangler dev --local --no-latest \
  --ip 127.0.0.1 \
  --port "$port" \
  --inspector-port "$inspector_port" \
  --show-interactive-dev-session false \
  --persist-to "$root/state" \
  --var "AUTOFILL_TOKEN:${token}" \
  --log-level error \
  >"$log" 2>&1 &
pid=$!
set +m

ready=0
for _ in $(seq 1 30); do
  if curl -sf --max-time 2 "http://127.0.0.1:${port}/healthz" >/dev/null; then
    ready=1
    break
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    break
  fi
  sleep 1
done

if [[ "$ready" != 1 ]]; then
  sed "s/${token}/[redacted]/g" "$log" >&2 || true
  echo "local worker did not answer /healthz within 30s" >&2
  exit 1
fi

health=$(curl -sf --max-time 5 "http://127.0.0.1:${port}/healthz")
printf '%s' "$health" | grep -q '"status":"ok"'

info=$(curl -sf --max-time 5 \
  -H "Authorization: Bearer ${token}" \
  "http://127.0.0.1:${port}/v1/info")
INFO_BODY="$info" node --input-type=module <<'JS'
const body = JSON.parse(process.env.INFO_BODY);
if (body.max_image_bytes !== 524288) {
  process.stderr.write(`max_image_bytes ${body.max_image_bytes}\n`);
  process.exit(1);
}
process.stdout.write("local /healthz 200; /v1/info max_image_bytes 524288\n");
JS
