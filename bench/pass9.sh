#!/bin/bash
# One pass over the photos against the running service (no container changes); prints HTTP, time, name,
# category and tags per photo, and writes the raw responses to $OUT/pass9_<label>.jsonl.
# Usage: bench/pass9.sh <label>
source "$(dirname "$0")/common.sh"
L=${1:?label}; J=$OUT/pass9_$L.jsonl; : > "$J"; tmp=$(mktemp)
for f in "$PHOTOS"/*.jpg; do
  m=$(curl -s -o "$tmp" -w '%{http_code} %{time_total}' -H @"$AUTH_HEADER_FILE" -F image=@"$f" -F "$CTX" "$SERVICE_URL/v1/suggest")
  python3 - "$(basename "$f")" $m "$tmp" "$J" <<'PY'
import json, sys
photo, code, secs, tmp, out = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), sys.argv[4], sys.argv[5]
raw = open(tmp).read()
body = json.loads(raw) if raw.strip().startswith("{") else raw
with open(out, "a") as fh:
    fh.write(json.dumps({"photo": photo, "http": code, "secs": secs, "body": body}) + "\n")
b = body if isinstance(body, dict) else {}
v = lambda k: (b.get(k) or {}).get("value")  # noqa: E731
tags = ", ".join(t.get("value", "") for t in b.get("tags", []))
print(f"{photo} | {code} | {secs:.2f}s | {v('name')} | {v('category')} | {tags} | {b.get('error', '')}")
PY
done
rm -f "$tmp"
