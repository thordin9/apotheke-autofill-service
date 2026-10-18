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
# Env: ENV_FILE (default /opt/autofill/service.env), NAME (default autofill), WAIT_S (default 120),
#      HEALTH_URL (default http://127.0.0.1:8080/healthz; polled directly so the switch doesn't wait for
#      Docker's first 30 s health interval; empty = rely on Docker's health status only).
set -euo pipefail
IMAGE=${1:?usage: run-service.sh apotheke-autofill:jetson-<sha>}
ENV_FILE=${ENV_FILE:-/opt/autofill/service.env}
NAME=${NAME:-autofill}
PREV="$NAME-prev"
WAIT_S=${WAIT_S:-120}
HEALTH_URL=${HEALTH_URL-http://127.0.0.1:8080/healthz}
[ -r "$ENV_FILE" ] || { echo "missing $ENV_FILE (copy deploy/jetson/service.env.example)" >&2; exit 1; }
docker image inspect "$IMAGE" >/dev/null || { echo "image $IMAGE not found; build it first (build-image.sh)" >&2; exit 1; }

exists() { docker container inspect "$1" >/dev/null 2>&1; }

# Wait until the running container answers HEALTH_URL, or Docker reports it healthy (image HEALTHCHECK), or,
# without either, it is still running after 10 s.
url_ok() { [ -n "$HEALTH_URL" ] && command -v curl >/dev/null && curl -fsS -m 3 -o /dev/null "$HEALTH_URL" 2>/dev/null; }
wait_ready() {
  local t=0 state health
  while [ "$t" -lt "$WAIT_S" ]; do
    state=$(docker inspect -f '{{.State.Status}}' "$NAME" 2>/dev/null || echo missing)
    health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$NAME" 2>/dev/null || echo none)
    case "$state/$health" in
      running/healthy) return 0 ;;
      running/none) url_ok && return 0; [ "$t" -ge 10 ] && return 0 ;;
      running/starting) url_ok && return 0 ;;
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
  if [ "$NEW_STARTED" = 1 ]; then docker rm -f "$NAME" >/dev/null 2>&1 || true; NEW_STARTED=0; fi
  if exists "$PREV" && ! exists "$NAME"; then docker rename "$PREV" "$NAME" || return 0; fi
  # the new container is gone by now, so $NAME (if any) is the previous one: make sure it runs (no-op if it does)
  if exists "$NAME"; then docker start "$NAME" >/dev/null && echo "previous container is running as $NAME" >&2; fi
}
# A rollback ignores further INT/TERM (inherited by the docker commands it runs), so a second signal can't start
# a second rollback that would remove the just-restored container.
on_signal() { trap '' INT TERM; echo "interrupted; rolling back" >&2; restore_prev; exit "$1"; }

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
  echo "docker run failed" >&2; trap '' INT TERM; restore_prev; exit 1
fi
if ! wait_ready; then
  docker logs --tail 50 "$NAME" >&2 2>&1 || true
  trap '' INT TERM; restore_prev; exit 1
fi
trap - INT TERM
docker rm -f "$PREV" >/dev/null 2>&1 || true
echo "$NAME is running $IMAGE"
