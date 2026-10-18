#!/bin/bash
# Build the Jetson image for one commit of this repo, tagged apotheke-autofill:jetson-<short sha>.
# Usage: deploy/jetson/build-image.sh [<commit-ish>]   (default: origin/main)
# Run on the Jetson itself (native arm64). The first build pulls dustynv/ollama:r35.4.1
# (about 5.8 GB compressed); at the board's 30 Mbit/s inbound cap that takes roughly half an hour.
# Running as root on a checkout owned by another user trips git's "dubious ownership" check:
# use a root-owned checkout, or `git config --global --add safe.directory <path>`.
set -euo pipefail
REPO_DIR=$(cd "$(dirname "$0")/../.." && pwd)
REF=${1:-origin/main}
cd "$REPO_DIR"
git fetch -q origin || echo "warning: git fetch failed; using local refs" >&2
if ! FULL=$(git rev-parse --verify --quiet "$REF^{commit}"); then
  echo "unknown ref '$REF' (not a commit in $REPO_DIR; try 'git fetch' or a full sha)" >&2; exit 1
fi
SHA=$(git rev-parse --short=7 "$FULL")
SRC=$(mktemp -d /tmp/autofill-src-XXXXXX)
trap 'rm -rf "$SRC"' EXIT
git archive "$FULL" | tar -x -C "$SRC"          # build exactly that commit, not the working tree
docker build -f "$SRC/Dockerfile.jetson" -t "apotheke-autofill:jetson-$SHA" "$SRC"
echo "built apotheke-autofill:jetson-$SHA"
