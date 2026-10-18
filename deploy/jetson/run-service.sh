#!/bin/bash
# (Re)create the long-running autofill container on the Jetson.
# Usage: sudo deploy/jetson/run-service.sh apotheke-autofill:jetson-<sha>
# Model, timeout and token come from the env file (mode 600, root only), so the token never appears
# on a command line, in `ps` or in shell history. See docs/deploy-jetson.md.
set -euo pipefail
IMAGE=${1:?usage: run-service.sh apotheke-autofill:jetson-<sha>}
ENV_FILE=${ENV_FILE:-/opt/autofill/service.env}
NAME=${NAME:-autofill}
[ -r "$ENV_FILE" ] || { echo "missing $ENV_FILE (copy deploy/jetson/service.env.example)" >&2; exit 1; }
docker image inspect "$IMAGE" >/dev/null || { echo "image $IMAGE not found; build it first (build-image.sh)" >&2; exit 1; }
docker rm -f "$NAME" >/dev/null 2>&1 || true
exec docker run -d --name "$NAME" --network host --runtime nvidia --restart unless-stopped \
  --env-file "$ENV_FILE" "$IMAGE"
