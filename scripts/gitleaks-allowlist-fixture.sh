#!/bin/bash
# The placeholder allowlist must still report a secret glued on with no whitespace.
# Pieces of the fixture are concatenated here so this file is not itself a finding.
set -euo pipefail

if ! command -v gitleaks >/dev/null 2>&1; then
  echo "gitleaks is not on PATH" >&2
  exit 1
fi

root=$(cd "$(dirname "$0")/.." && pwd)
cfg="$root/.gitleaks.toml"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# No whitespace after ">": the old allowlist (^generate-a-random-token>) hid this.
# A literal "<" after ">" is not a generic-api-key capture, so the fixture
# glues the value directly, which is the match that regex used to swallow.
prefix=$(printf '%s%s' 'generate-a-random-' 'token>')
value=$(printf '%s%s' 'AbcdEFGH' 'ijkl9012')
printf 'note token>%s%s\n' "$prefix" "$value" > "$work/bad.txt"

set +e
gitleaks detect --no-git --source "$work" --config "$cfg" --no-banner --report-format json --report-path "$work/report.json" >"$work/out" 2>&1
code=$?
set -e
if [ "$code" -eq 0 ]; then
  echo "glued placeholder was allowlisted; require whitespace after the closing angle bracket" >&2
  exit 1
fi
if [ "$code" -ne 1 ]; then
  echo "gitleaks exited $code" >&2
  cat "$work/out" >&2
  exit 1
fi
if ! grep -q 'generic-api-key' "$work/report.json"; then
  echo "expected a generic-api-key finding for the glued placeholder" >&2
  exit 1
fi

clean=$(mktemp -d)
cp "$root/deploy/jetson/service.env.example" "$clean/service.env.example"
cp "$root/.github/workflows/ci.yml" "$clean/ci.yml"
gitleaks detect --no-git --source "$clean" --config "$cfg" --no-banner --redact
rm -rf "$clean"
echo "gitleaks allowlist anchor ok"
