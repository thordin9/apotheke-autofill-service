#!/bin/bash
# (Re)create the long-running autofill container on the Jetson, keeping the old one as a fallback.
# Usage: sudo deploy/jetson/run-service.sh apotheke-autofill:jetson-<sha>
# Model, timeout and token come from the env file (mode 600, root only), so the token never appears
# on a command line, in `ps` or in shell history. See docs/deploy-jetson.md.
#
# Order (port 8080 on the host network can only be bound by one container at a time):
#   1. rename the running container to <name>-prev and stop it
#   2. start the new one and wait until Docker reports it healthy (or running, if it has no healthcheck)
#   3. success: remove <name>-prev.  Failure: remove the new one, rename <name>-prev back and start it.
# Env: ENV_FILE (default /opt/autofill/service.env), NAME (default autofill), WAIT_S (default 120).
set -euo pipefail
IMAGE=${1:?usage: run-service.sh apotheke-autofill:jetson-<sha>}
ENV_FILE=${ENV_FILE:-/opt/autofill/service.env}
NAME=${NAME:-autofill}
PREV="$NAME-prev"
WAIT_S=${WAIT_S:-120}
[ -r "$ENV_FILE" ] || { echo "missing $ENV_FILE (copy deploy/jetson/service.env.example)" >&2; exit 1; }
docker image inspect "$IMAGE" >/dev/null || { echo "image $IMAGE not found; build it first (build-image.sh)" >&2; exit 1; }

exists() { docker container inspect "$1" >/dev/null 2>&1; }

# Wait until the container is healthy (image HEALTHCHECK) or, without a healthcheck, still running after 10 s.
wait_ready() {
  local t=0 state health
  while [ "$t" -lt "$WAIT_S" ]; do
    state=$(docker inspect -f '{{.State.Status}}' "$NAME" 2>/dev/null || echo missing)
    health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$NAME" 2>/dev/null || echo none)
    case "$state/$health" in
      running/healthy) return 0 ;;
      running/none) [ "$t" -ge 10 ] && return 0 ;;
      running/starting) ;;
      *) echo "new container is $state/$health" >&2; return 1 ;;
    esac
    sleep 2; t=$((t + 2))
  done
  echo "new container not healthy after ${WAIT_S}s" >&2; return 1
}

restore_prev() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  if exists "$PREV"; then
    docker rename "$PREV" "$NAME" && docker start "$NAME" >/dev/null && echo "restored the previous container as $NAME" >&2
  fi
}

if exists "$PREV"; then
  echo "$PREV already exists (left from an earlier run?); remove or rename it first" >&2; exit 1
fi
if exists "$NAME"; then
  docker rename "$NAME" "$PREV"
  docker stop "$PREV" >/dev/null
fi

if ! docker run -d --name "$NAME" --network host --runtime nvidia --restart unless-stopped \
    --env-file "$ENV_FILE" "$IMAGE" >/dev/null; then
  echo "docker run failed" >&2; restore_prev; exit 1
fi
if ! wait_ready; then
  docker logs --tail 50 "$NAME" >&2 2>&1 || true
  restore_prev; exit 1
fi
docker rm -f "$PREV" >/dev/null 2>&1 || true
echo "$NAME is running $IMAGE"
