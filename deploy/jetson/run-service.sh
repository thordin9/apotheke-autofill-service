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
# Ctrl-C or SIGTERM anywhere in steps 1-2 also rolls back to the previous container (exit 130 / 143).
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

# Safe in any state: removes the new container only once this run has started creating it (so the old one is
# never removed before it has been renamed), then puts <name>-prev back under its name and starts it.
NEW_STARTED=0
restore_prev() {
  if [ "$NEW_STARTED" = 1 ]; then docker rm -f "$NAME" >/dev/null 2>&1 || true; fi
  if exists "$PREV" && ! exists "$NAME"; then
    docker rename "$PREV" "$NAME" && docker start "$NAME" >/dev/null && echo "restored the previous container as $NAME" >&2
  fi
}
on_signal() { trap - INT TERM; echo "interrupted; rolling back" >&2; restore_prev; exit "$1"; }

if exists "$PREV"; then
  cat >&2 <<MSG
$PREV already exists (left from an interrupted earlier run). Resolve it first:
  if $NAME is missing or broken:  docker rm -f $NAME; docker rename $PREV $NAME && docker start $NAME
  otherwise (the new $NAME works): docker rm $PREV
MSG
  exit 1
fi
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
if exists "$NAME"; then
  docker rename "$NAME" "$PREV"
  docker stop "$PREV" >/dev/null
fi

NEW_STARTED=1
if ! docker run -d --name "$NAME" --network host --runtime nvidia --restart unless-stopped \
    --env-file "$ENV_FILE" "$IMAGE" >/dev/null; then
  echo "docker run failed" >&2; restore_prev; trap - INT TERM; exit 1
fi
if ! wait_ready; then
  docker logs --tail 50 "$NAME" >&2 2>&1 || true
  restore_prev; trap - INT TERM; exit 1
fi
trap - INT TERM
docker rm -f "$PREV" >/dev/null 2>&1 || true
echo "$NAME is running $IMAGE"
