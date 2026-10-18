#!/bin/bash
# Tests for deploy/jetson/*.sh with fake docker/curl/tc on PATH (no Docker, no network, no root).
# Usage: deploy/jetson/tests/run-tests.sh        (about 2 minutes; the signal cases use real timing)
# Exit code: number of failed cases (0 = all passed).
set -u
set -m   # job control: background jobs keep default SIGINT handling, so the signal cases can send it
HERE=$(cd "$(dirname "$0")" && pwd)
JET=$(cd "$HERE/.." && pwd)
RS=$JET/run-service.sh
FAKE=$HERE/fake-bin
PASS=0; FAIL=0
OLD="autofill=old:img:running"; NEW="autofill=new:img:running"

state() { local o="" f; for f in "$DS"/c/*; do [ -e "$f" ] && o="$o $(basename "$f")=$(cut -d' ' -f1,2 "$f" | tr ' ' :)"; done; echo "${o# }"; }
result() { # name ok detail
  if [ "$2" = 1 ]; then PASS=$((PASS+1)); printf 'PASS  %-58s %s\n' "$1" "$3"
  else FAIL=$((FAIL+1)); printf 'FAIL  %-58s %s\n' "$1" "$3"; sed 's/^/      | /' "$DS/out"; fi; }
newds() { DS=$(mktemp -d); export DS; mkdir -p "$DS/c"; touch "$DS/env"; }

# rs NAME EXP_EXIT EXP_STATE FIRST SIGNALS [ENV...]
#   FIRST=1: no previous container. SIGNALS: space-separated SIG@seconds, e.g. "INT@3 TERM@4", or "".
rs() {
  local name=$1 ee=$2 es=$3 first=$4 sigs=$5 t=0 s sig d pid rc st; shift 5
  newds
  [ "$first" = 1 ] || echo "old:img running healthy 1" > "$DS/c/autofill"
  env PATH="$FAKE:$PATH" ENV_FILE="$DS/env" "$@" bash "$RS" new:img > "$DS/out" 2>&1 &
  pid=$!
  for s in $sigs; do sig=${s%@*}; d=${s#*@}
    sleep "$(awk -v a="$d" -v b="$t" 'BEGIN{print a-b}')"; t=$d; kill -"$sig" "$pid" 2>/dev/null; done
  wait "$pid"; rc=$?; st=$(state)
  [ "$rc" = "$ee" ] && [ "$st" = "$es" ] && ok=1 || ok=0
  result "run-service: $name" "$ok" "exit=$rc state=[$st]"
}

echo "== run-service.sh: no signal"
rs "success (Docker health)"                 0 "$NEW" 0 ""
rs "first deploy"                            0 "$NEW" 1 ""
rs "failed docker run"                       1 "$OLD" 0 "" RUN_FAIL=1
rs "unhealthy"                               1 "$OLD" 0 "" NEW_HEALTH=unhealthy
rs "health timeout"                          1 "$OLD" 0 "" NEW_HEALTH=starting WAIT_S=4
echo "== run-service.sh: signal on the way in"
rs "INT during rename of old"              130 "$OLD" 0 "INT@0.5" SLOW_rename=1.5
rs "TERM during stop of old"               143 "$OLD" 0 "TERM@0.5" SLOW_stop=1.5
rs "INT during docker run"                 130 "$OLD" 0 "INT@1" RUN_SLEEP=3 NEW_HEALTH=starting
rs "TERM during docker run"                143 "$OLD" 0 "TERM@1" RUN_SLEEP=3 NEW_HEALTH=starting
rs "INT during health wait"                130 "$OLD" 0 "INT@3" NEW_HEALTH=starting WAIT_S=60
rs "TERM during health wait"               143 "$OLD" 0 "TERM@3" NEW_HEALTH=starting WAIT_S=60
rs "INT during docker logs (before rollback)" 130 "$OLD" 0 "INT@0.5" NEW_HEALTH=unhealthy SLOW_logs=1.5
rs "INT health wait, first deploy"         130 ""     1 "INT@3" NEW_HEALTH=starting WAIT_S=60
rs "INT during docker run, first deploy"   130 ""     1 "INT@1" RUN_SLEEP=3 NEW_HEALTH=starting
echo "== run-service.sh: signal during a rollback (ignored)"
rs "unhealthy: INT after rename back"        1 "$OLD" 0 "INT@1" NEW_HEALTH=unhealthy RENAME_BACK_SLEEP=2
rs "unhealthy: TERM during docker start"     1 "$OLD" 0 "TERM@1" NEW_HEALTH=unhealthy SLOW_start=2
rs "unhealthy: INT during rm of new"         1 "$OLD" 0 "INT@0.7" NEW_HEALTH=unhealthy SLOW_rm=1.5
rs "failed run: INT after rename back"       1 "$OLD" 0 "INT@1" RUN_FAIL=1 RENAME_BACK_SLEEP=2
rs "failed run: TERM during docker start"    1 "$OLD" 0 "TERM@1" RUN_FAIL=1 SLOW_start=2
rs "timeout: INT during docker start"        1 "$OLD" 0 "INT@5" NEW_HEALTH=starting WAIT_S=4 SLOW_start=2
echo "== run-service.sh: double signals"
rs "INT,INT: health wait, then rollback start" 130 "$OLD" 0 "INT@3 INT@4" NEW_HEALTH=starting WAIT_S=60 SLOW_start=2
rs "INT,TERM: health wait, then after rename back" 130 "$OLD" 0 "INT@3 TERM@4" NEW_HEALTH=starting WAIT_S=60 RENAME_BACK_SLEEP=2
rs "TERM,TERM: docker run, then rollback"  143 "$OLD" 0 "TERM@1 TERM@3.5" RUN_SLEEP=3 NEW_HEALTH=starting SLOW_start=2
rs "unhealthy: INT,TERM during rollback"     1 "$OLD" 0 "INT@1 TERM@2" NEW_HEALTH=unhealthy SLOW_start=3
echo "== run-service.sh: /healthz polling"
SECONDS=0
rs "/healthz answers while Docker says starting" 0 "$NEW" 0 "" NEW_HEALTH=starting NEW_HTTP=1 WAIT_S=60
[ "$SECONDS" -le 3 ] && ok=1 || ok=0; result "  ...and doesn't wait for Docker health" "$ok" "${SECONDS}s"
rs "/healthz never answers, starting -> timeout" 1 "$OLD" 0 "" NEW_HEALTH=starting WAIT_S=4
rs "HEALTH_URL empty: Docker health only"    0 "$NEW" 0 "" HEALTH_URL= NEW_HEALTH=healthy
echo "== run-service.sh: port check (L1)"
rs "port taken after old stopped -> refuse, restore" 1 "$OLD" 0 "" PORT_TAKEN=1
grep -q "something else holds the port" "$DS/out" && ! grep -q "docker run" "$DS/log" && ok=1 || ok=0
result "  ...message shown, docker run never called" "$ok" ""
rs "port taken on first deploy -> refuse"    1 ""     1 "" PORT_TAKEN=1
! grep -q "docker run" "$DS/log" && ok=1 || ok=0; result "  ...docker run never called" "$ok" ""
rs "old answered before stop: no false refusal" 0 "$NEW" 0 "" NEW_HTTP=1
echo "== run-service.sh: wall-clock WAIT_S (I1)"
SECONDS=0
# Each poll hangs 3 s (+2 s sleep). Wall clock: 3 s port pre-check + ~10 s = ~13 s. The old per-iteration counter
# (t += 2) needed 5 polls, ~25 s measured.
rs "hanging /healthz (3 s per poll), WAIT_S=10" 1 "$OLD" 0 "" CURL_HANG=3 NEW_HEALTH=starting WAIT_S=10
[ "$SECONDS" -le 18 ] && ok=1 || ok=0; result "  ...gives up within WAIT_S + one poll + pre-check" "$ok" "${SECONDS}s (old counter: ~25 s)"
echo "== run-service.sh: refusals"
newds; echo "old:img running healthy 1" > "$DS/c/autofill"; echo "x:img exited none 0" > "$DS/c/autofill-prev"
env PATH="$FAKE:$PATH" ENV_FILE="$DS/env" bash "$RS" new:img > "$DS/out" 2>&1; rc=$?
[ "$rc" = 1 ] && grep -q "docker rename autofill-prev autofill" "$DS/out" && [ "$(state)" = "$OLD autofill-prev=x:img:exited" ] && ok=1 || ok=0
result "run-service: leftover autofill-prev" "$ok" "exit=$rc state=[$(state)]"
newds; echo "old:img running healthy 1" > "$DS/c/autofill"
env PATH="$FAKE:$PATH" ENV_FILE="$DS/env" bash "$RS" missing:img > "$DS/out" 2>&1; rc=$?
[ "$rc" = 1 ] && [ "$(state)" = "$OLD" ] && ok=1 || ok=0; result "run-service: missing image" "$ok" "exit=$rc"
newds; env PATH="$FAKE:$PATH" ENV_FILE="$DS/nope" bash "$RS" new:img > "$DS/out" 2>&1; rc=$?
[ "$rc" = 1 ] && ok=1 || ok=0; result "run-service: missing env file" "$ok" "exit=$rc"

echo "== build-image.sh"
newds
git init -q --bare "$DS/origin.git"
git clone -q "$DS/origin.git" "$DS/repo" 2>/dev/null
mkdir -p "$DS/repo/deploy/jetson" "$DS/repo/app"; cp "$JET/build-image.sh" "$DS/repo/deploy/jetson/"
echo "FROM scratch" > "$DS/repo/Dockerfile.jetson"; echo "x" > "$DS/repo/app/main.py"
git -C "$DS/repo" -c user.name=t -c user.email=t@t add -A; git -C "$DS/repo" -c user.name=t -c user.email=t@t commit -qm init
git -C "$DS/repo" push -q origin HEAD:main 2>/dev/null; git -C "$DS/repo" fetch -q origin
sha=$(git -C "$DS/repo" rev-parse --short=7 HEAD)
cat > "$DS/docker" <<'D'
#!/bin/bash
ctx=${*: -1}; [ -f "$ctx/Dockerfile.jetson" ] && [ -d "$ctx/app" ] && echo "context ok"; echo "docker $*"
D
chmod +x "$DS/docker"
(cd "$DS/repo" && PATH="$DS:$PATH" bash deploy/jetson/build-image.sh origin/main) > "$DS/out" 2>&1; rc=$?
[ "$rc" = 0 ] && grep -q "context ok" "$DS/out" && grep -q -- "-t apotheke-autofill:jetson-$sha " "$DS/out" && ok=1 || ok=0
result "build-image: good ref -> jetson-<sha>" "$ok" "exit=$rc"
(cd "$DS/repo" && PATH="$DS:$PATH" bash deploy/jetson/build-image.sh no-such-ref) > "$DS/out" 2>&1; rc=$?
[ "$rc" = 1 ] && grep -q "unknown ref 'no-such-ref'" "$DS/out" && ok=1 || ok=0
result "build-image: unknown ref -> clear error" "$ok" "exit=$rc"

echo "== dl-cap.sh"
newds; env PATH="$FAKE:$PATH" sh "$JET/dl-cap.sh" > "$DS/out" 2>&1; rc=$?
[ "$rc" = 0 ] && [ "$(grep -c 'filter add' "$DS/tc.log")" = 2 ] && ok=1 || ok=0; result "dl-cap: apply (IPv4 + IPv6)" "$ok" "exit=$rc"
newds; env PATH="$FAKE:$PATH" FAIL_IP=1 sh "$JET/dl-cap.sh" > "$DS/out" 2>&1; rc=$?
[ "$rc" != 0 ] && ! grep -q ipv6 "$DS/tc.log" && ok=1 || ok=0; result "dl-cap: failed IPv4 filter fails the script" "$ok" "exit=$rc"
newds; env PATH="$FAKE:$PATH" sh "$JET/dl-cap.sh" stop > "$DS/out" 2>&1; rc=$?
[ "$rc" = 0 ] && ! grep -q add "$DS/tc.log" && ok=1 || ok=0; result "dl-cap: stop" "$ok" "exit=$rc"

echo
echo "passed=$PASS failed=$FAIL"
exit "$FAIL"
