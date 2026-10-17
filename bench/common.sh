# Shared settings for the bench scripts. Source it; override any value from the environment.
#   AUTOFILL_DIR      board directory holding service.env and auth.hdr (default /opt/autofill)
#   AUTH_HEADER_FILE  mode-600 file with one line "Authorization: Bearer <token>"; curl reads it
#                     with -H @file, so the token never appears on a command line or in `ps`
#   PHOTOS            directory of test photos (default: bench/photos next to this file)
#   OUT               where results go (default: bench/out next to this file)
#   SERVICE_URL       the autofill service (default http://127.0.0.1:8080)
BENCH_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
AUTOFILL_DIR=${AUTOFILL_DIR:-/opt/autofill}
AUTH_HEADER_FILE=${AUTH_HEADER_FILE:-$AUTOFILL_DIR/auth.hdr}
PHOTOS=${PHOTOS:-$BENCH_DIR/photos}
OUT=${OUT:-$BENCH_DIR/out}
SERVICE_URL=${SERVICE_URL:-http://127.0.0.1:8080}
CTX='context={"categories":["Tools","Electronics","Clothing","Books","Kitchen","Furniture","Lighting","Shoes","Other"]}'
mkdir -p "$OUT"
[ -r "$AUTH_HEADER_FILE" ] || { echo "missing $AUTH_HEADER_FILE (see docs/benchmarks.md, Reproduce)" >&2; exit 1; }
