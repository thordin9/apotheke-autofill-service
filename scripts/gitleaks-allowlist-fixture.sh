#!/bin/bash
# Planted tokens must be findings. History of this repo must stay clean.
# Pieces are concatenated here so this file is not itself a finding.
set -euo pipefail

if ! command -v gitleaks >/dev/null 2>&1; then
  echo "gitleaks is not on PATH" >&2
  exit 1
fi

root=$(cd "$(dirname "$0")/.." && pwd)
cfg="$root/.gitleaks.toml"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

own=$(printf '%s%s' 'Qp7mK2nR' '9xL4vT8wY3')
glued=$(printf '%s%s%s' 'Hc3dF6jB' '1yN5sW0z' 'Aa8eU2pL')
bearer=$(printf '%s%s%s%s%s' 'mZ4kP9qL' '2xV7nR3t' 'B6cD1fH5' 'jY8wQ0aE' 'uK')
same=$(printf '%s%s' 'Tr6wE1qA' '8nM4kP0sL2xV')
prefix=$(printf '%s%s' 'generate-a-random-' 'token>')
scheme=$(printf '%s%s' 'Bear' 'er')
dummy=$(printf '%s%s' 'ci-' 'token')

printf 'api_token=%s\n' "$own" > "$work/own-line.txt"
printf 'note token>%s%s\n' "$prefix" "$glued" > "$work/glued.txt"
printf 'note token>%s api_key="%s"\n' "$prefix" "$same" > "$work/same-line.txt"
printf "curl -H 'Authorization: %s %s' -H 'Authorization: %s %s' https://example.invalid/v1\n" \
  "$scheme" "$dummy" "$scheme" "$bearer" > "$work/curl-line.txt"

gitleaks detect --no-git --source "$work" --config "$cfg" --no-banner --report-format json --report-path "$work/report.json" >"$work/out" 2>&1 || true
python3 - "$work/report.json" << 'PY'
import json, sys
report = json.load(open(sys.argv[1]))
files = {item.get("File", "") for item in report}
needed = {"own-line.txt", "glued.txt", "same-line.txt", "curl-line.txt"}
missing = sorted(name for name in needed if not any(name in path for path in files))
if missing:
    raise SystemExit("planted tokens were not all findings: " + ", ".join(missing))
if len(report) < 4:
    raise SystemExit(f"expected at least 4 findings, got {len(report)}")
print("gitleaks planted tokens ok")
PY

gitleaks detect --source "$root" --config "$cfg" --redact --exit-code 1
echo "gitleaks history clean"
