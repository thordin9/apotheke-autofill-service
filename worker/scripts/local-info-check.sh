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

# The expected /v1/info value is computed the way the Worker computes it:
# min(ADVERTISED_MAX_IMAGE_BYTES, max(0, MAX_UPLOAD_BYTES - MULTIPART_HEADROOM)),
# with the constants from src/multipart.ts and MAX_UPLOAD_BYTES from the
# wrangler.toml [vars] that this check runs with. Moving the advertised limit
# (for example to 786432) or the body cap needs no edit here.
read_const() {
  sed -nE "s/^export const $1 = ([0-9]+);.*/\\1/p" src/multipart.ts
}
advertised=$(read_const ADVERTISED_MAX_IMAGE_BYTES)
headroom=$(read_const MULTIPART_HEADROOM)
for value in "$advertised" "$headroom"; do
  if [[ ! "$value" =~ ^[0-9]+$ ]]; then
    echo "could not read ADVERTISED_MAX_IMAGE_BYTES and MULTIPART_HEADROOM from src/multipart.ts" >&2
    exit 1
  fi
done
# Same fallback as parseMaxUpload: missing, empty, non-numeric or 0 means 1048576.
cap=$(sed -nE 's/^MAX_UPLOAD_BYTES = "([^"]*)".*/\1/p' wrangler.toml | head -n1)
if [[ ! "$cap" =~ ^[0-9]+$ ]] || (( 10#$cap == 0 )); then
  cap=1048576
fi
image_cap=$(( 10#$cap - headroom ))
(( image_cap < 0 )) && image_cap=0
expected=$(( advertised < image_cap ? advertised : image_cap ))

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
INFO_BODY="$info" EXPECTED="$expected" ENFORCED="$image_cap" node --input-type=module <<'JS'
const body = JSON.parse(process.env.INFO_BODY);
const expected = Number(process.env.EXPECTED);
const enforced = Number(process.env.ENFORCED);
if (body.max_image_bytes !== expected) {
  process.stderr.write(`max_image_bytes ${body.max_image_bytes}, expected ${expected}\n`);
  process.exit(1);
}
if (body.tier !== "free") {
  process.stderr.write(`tier ${body.tier}, expected free\n`);
  process.exit(1);
}
if (body.enforced_image_bytes !== enforced) {
  process.stderr.write(`enforced_image_bytes ${body.enforced_image_bytes}, expected ${enforced}\n`);
  process.exit(1);
}
if ("recommended_image_bytes" in body) {
  process.stderr.write("recommended_image_bytes is set\n");
  process.exit(1);
}
process.stdout.write(
  `local /healthz 200; /v1/info max_image_bytes ${expected} tier free enforced_image_bytes ${enforced}\n`,
);
JS
