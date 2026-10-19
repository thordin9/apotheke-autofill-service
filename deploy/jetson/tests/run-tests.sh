#!/bin/bash
# Tests for deploy/jetson/*.sh with fake docker/curl/tc on PATH (no Docker, no network, no root).
# Usage: deploy/jetson/tests/run-tests.sh        (about 1 min 30 s; the signal cases use real timing)
# Exit code: number of failed cases (0 = all passed).
set -u
set -m   # job control: background jobs keep default SIGINT handling, so the signal cases can send it
HERE=$(cd "$(dirname "$0")" && pwd)
JET=$(cd "$HERE/.." && pwd)
RS=$JET/run-service.sh
FAKE=$HERE/fake-bin
PASS=0; FAIL=0
# Every case's scratch dir lives under one parent, removed on exit (also on Ctrl-C / SIGTERM / SIGHUP).
TMPROOT=$(mktemp -d "${TMPDIR:-/tmp}/run-tests.XXXXXX") || { echo "cannot create a temp dir under ${TMPDIR:-/tmp}" >&2; exit 1; }
trap 'rm -rf "$TMPROOT"' EXIT
CASE_PID=""   # a case runs in its own process group (set -m): stop it before cleaning up, or it recreates its dir
# shellcheck disable=SC2317  # called from the traps
stop_case() { [ -n "$CASE_PID" ] && kill -TERM -- "-$CASE_PID" 2>/dev/null; [ -n "$CASE_PID" ] && wait "$CASE_PID" 2>/dev/null; exit "$1"; }
trap 'stop_case 130' INT
trap 'stop_case 143' TERM
trap 'stop_case 129' HUP   # terminal closed
OLD="autofill=old:img:running"; NEW="autofill=new:img:running"

state() { local o="" f; for f in "$DS"/c/*; do [ -e "$f" ] && o="$o $(basename "$f")=$(cut -d' ' -f1,2 "$f" | tr ' ' :)"; done; echo "${o# }"; }
result() { # name ok detail
  if [ "$2" = 1 ]; then PASS=$((PASS+1)); printf 'PASS  %-58s %s\n' "$1" "$3"
  else FAIL=$((FAIL+1)); printf 'FAIL  %-58s %s\n' "$1" "$3"; sed 's/^/      | /' "$DS/out"; fi; }
newds() { DS=$(mktemp -d "$TMPROOT/ds.XXXXXX"); export DS; mkdir -p "$DS/c"; touch "$DS/env"; }

# rs NAME EXP_EXIT EXP_STATE FIRST SIGNALS [ENV...]
#   FIRST=1: no previous container. SIGNALS: space-separated SIG@seconds, e.g. "INT@3 TERM@4", or "".
rs() {
  local name=$1 ee=$2 es=$3 first=$4 sigs=$5 t=0 s sig d pid rc st; shift 5
  newds
  [ "$first" = 1 ] || echo "old:img running healthy 1" > "$DS/c/autofill"
  env PATH="$FAKE:$PATH" ENV_FILE="$DS/env" "$@" bash "$RS" new:img > "$DS/out" 2>&1 &
  pid=$!; CASE_PID=$pid
  for s in $sigs; do sig=${s%@*}; d=${s#*@}
    # sleep as a background job + wait: with set -m a foreground sleep would get its own process group and the
    # terminal, so Ctrl-C would only kill the sleep; this way the harness stays in the foreground and gets it.
    sleep "$(awk -v a="$d" -v b="$t" 'BEGIN{print a-b}')" & wait $!
    t=$d; kill -"$sig" "$pid" 2>/dev/null; done
  wait "$pid"; rc=$?; CASE_PID=""; st=$(state)
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
echo "== run-service.sh: bind-error hint (I1 of #14)"
rs "unhealthy, logs say address in use -> hint" 1 "$OLD" 0 "" NEW_HEALTH=unhealthy \
  LOGS_TEXT="ERROR: [Errno 98] error while attempting to bind on address ('0.0.0.0', 8080): address already in use"
grep -qF "ss -ltnp 'sport = :8080'" "$DS/out" && grep -q "address already in use" "$DS/out" && ok=1 || ok=0
result "  ...logs and the ss hint are shown" "$ok" ""
rs "unhealthy, other error -> no hint"         1 "$OLD" 0 "" NEW_HEALTH=unhealthy LOGS_TEXT="boom"
! grep -q "ss -ltnp" "$DS/out" && ok=1 || ok=0; result "  ...no hint" "$ok" ""
echo "== run-service.sh: WAIT_S validation"
for w in abc 1.5 0 -5 010 " 5" "5s" 9223372036854775808; do
  rs "WAIT_S='$w' -> refused, nothing touched" 1 "$OLD" 0 "" WAIT_S="$w"
  grep -q "WAIT_S must be a positive whole number" "$DS/out" && [ ! -e "$DS/log" ] && ok=1 || ok=0
  result "  ...clear message, docker never called" "$ok" ""
done
rs "WAIT_S=1 (smallest valid) -> times out, rolls back" 1 "$OLD" 0 "" NEW_HEALTH=starting WAIT_S=1
grep -q "not healthy after 1s" "$DS/out" && ok=1 || ok=0; result "  ...normal timeout message" "$ok" ""
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
DC=$JET/dl-cap.sh
dc() { env PATH="$FAKE:$PATH" "$@" > "$DS/out" 2>&1; }   # dc [ENV...] sh "$DC" [ARG]; exit code in $?
newds; dc sh "$DC"; rc=$?
[ "$rc" = 0 ] && [ "$(grep -c 'filter add' "$DS/tc.log")" = 2 ] && grep -q "filter show dev eth0 parent ffff:" "$DS/tc.log" \
  && ok=1 || ok=0; result "dl-cap: apply (IPv4 + IPv6), then verified" "$ok" "exit=$rc"
dc sh "$DC"; rc=$?
[ "$rc" = 0 ] && [ "$(tr '\n' ' ' < "$DS/tc.filters")" = "ip ipv6 " ] && ok=1 || ok=0
result "dl-cap: apply again (idempotent)" "$ok" "exit=$rc filters=[$(tr '\n' ' ' < "$DS/tc.filters")]"
newds; dc FAIL_IP=1 sh "$DC"; rc=$?
[ "$rc" != 0 ] && ! grep -q ipv6 "$DS/tc.log" && ok=1 || ok=0; result "dl-cap: failed IPv4 filter fails the script" "$ok" "exit=$rc"
newds; dc sh "$DC" stop; rc=$?
[ "$rc" = 0 ] && ! grep -q add "$DS/tc.log" && ok=1 || ok=0; result "dl-cap: stop" "$ok" "exit=$rc"
echo "== dl-cap.sh: verify after apply"
for knob in DROP_FILTERS=1 DROP_IPV6=1 NO_POLICE=1 SHOW_FAIL=1; do
  newds; dc "$knob" sh "$DC"; rc=$?
  [ "$rc" = 1 ] && grep -q "not found on eth0 after applying" "$DS/out" && ok=1 || ok=0
  result "dl-cap: $knob: tc calls succeed, cap not there -> exit 1" "$ok" "exit=$rc"
done
echo "== dl-cap.sh check"
newds; dc sh "$DC" check; rc=$?
[ "$rc" = 1 ] && grep -q MISSING "$DS/out" && ok=1 || ok=0; result "dl-cap check: no qdisc -> 1" "$ok" "exit=$rc"
dc sh "$DC"; : > "$DS/tc.log"; dc sh "$DC" check; rc=$?
[ "$rc" = 0 ] && grep -q "in place on eth0" "$DS/out" && ok=1 || ok=0; result "dl-cap check: after apply -> 0" "$ok" "exit=$rc"
[ "$(grep -c 'filter show' "$DS/tc.log")" -ge 1 ] && [ "$(grep -vc 'filter show' "$DS/tc.log")" = 0 ] && ok=1 || ok=0
result "  ...check only reads (no add/del)" "$ok" "$(wc -l < "$DS/tc.log") tc call(s)"
rm -f "$DS/tc.qdisc" "$DS/tc.filters"   # NetworkManager reset eth0 after dl-cap ran (the reboot case)
dc sh "$DC" check; rc=$?; [ "$rc" = 1 ] && ok=1 || ok=0; result "dl-cap check: qdisc gone after apply -> 1" "$ok" "exit=$rc"
newds; dc DROP_IPV6=1 sh "$DC"; dc sh "$DC" check; rc=$?
[ "$rc" = 1 ] && ok=1 || ok=0; result "dl-cap check: IPv4 filter only -> 1" "$ok" "exit=$rc"
newds; dc sh "$DC"; dc NO_POLICE=1 sh "$DC" check; rc=$?
[ "$rc" = 1 ] && ok=1 || ok=0; result "dl-cap check: filters without police -> 1" "$ok" "exit=$rc"
newds; dc sh "$DC"; dc SHOW_FAIL=1 sh "$DC" check; rc=$?
[ "$rc" = 1 ] && ok=1 || ok=0; result "dl-cap check: tc filter show fails -> 1" "$ok" "exit=$rc"
newds; dc sh "$DC"; dc sh "$DC" stop; dc sh "$DC" check; rc=$?
[ "$rc" = 1 ] && ok=1 || ok=0; result "dl-cap check: after stop -> 1" "$ok" "exit=$rc"
newds; dc sh "$DC" bogus; rc=$?
[ "$rc" = 2 ] && grep -q usage "$DS/out" && [ ! -e "$DS/tc.log" ] && ok=1 || ok=0
result "dl-cap: unknown argument -> usage, exit 2, no tc call" "$ok" "exit=$rc"

echo "== nm-dispatcher-90-dl-cap"
NMD=$JET/nm-dispatcher-90-dl-cap
# nmd EXP_RESTART IFACE ACTION [ENV...]: EXP_RESTART=1 if dl-cap.service must be restarted. Always exits 0.
nmd() {
  local er=$1 i=$2 a=$3 rc n; shift 3
  newds; env PATH="$FAKE:$PATH" "$@" sh "$NMD" "$i" "$a" > "$DS/out" 2>&1; rc=$?
  n=$(grep -c "restart dl-cap.service" "$DS/systemctl.log" 2>/dev/null); n=${n:-0}
  [ "$rc" = 0 ] && [ "$n" = "$er" ] && ok=1 || ok=0
  result "nm-dispatcher: '$i' '$a'${1:+ $*} -> $([ "$er" = 1 ] && echo restart || echo nothing)" "$ok" "exit=$rc restarts=$n"
}
for a in up connectivity-change dhcp4-change; do nmd 1 eth0 "$a"; done
for a in down pre-up pre-down dhcp6-change hostname reapply vpn-up ""; do nmd 0 eth0 "$a"; done
for i in wlan0 eth1 lo eth0.5 docker0 ""; do nmd 0 "$i" up; done
nmd 0 eth0 up UNIT_DISABLED=1
nmd 1 eth0 up SYSTEMCTL_FAIL=1
newds; env PATH="$FAKE:$PATH" sh "$NMD" eth0 up > "$DS/out" 2>&1
grep -qx "systemctl --no-block restart dl-cap.service" "$DS/systemctl.log" && ok=1 || ok=0
result "  ...restart is queued (--no-block), not waited for" "$ok" ""
# shellcheck disable=SC2016  # a literal ${DEV:-...} in the sed pattern
d1=$(sed -n 's/^DEV=\${DEV:-\([^}]*\)}.*/\1/p' "$DC"); d2=$(sed -n 's/^DEV=\([^ ]*\).*/\1/p' "$NMD")
[ -n "$d1" ] && [ "$d1" = "$d2" ] && ok=1 || ok=0; result "nm-dispatcher: interface matches DEV in dl-cap.sh" "$ok" "dl-cap=$d1 hook=$d2"

echo "== dl-cap.sh: real board tc filter show fixture"
# Verbatim iproute2-ss200127 output from the board (police on a separate line from protocol ip/ipv6).
newds
FIX=$HERE/fixtures/tc-filter-show-eth0-ffff.txt
[ -f "$FIX" ] || { result "dl-cap fixture: file missing" 0 "$FIX"; }
# Stub tc that only serves filter show from the fixture (no FAKE tc).
cat > "$DS/tc" <<T
#!/bin/sh
echo "tc \$*" >> "\$DS/tc.log"
case "\$*" in "filter show"*) cat "$FIX" ;; *) echo "stub tc: \$*">&2; exit 2 ;; esac
T
chmod +x "$DS/tc"
env PATH="$DS:$PATH" DS="$DS" sh "$DC" check > "$DS/out" 2>&1; rc=$?
[ "$rc" = 0 ] && grep -q "in place on eth0" "$DS/out" && ok=1 || ok=0
result "dl-cap check: verbatim board tc fixture -> 0" "$ok" "exit=$rc"
# Fake tc emit format matches the fixture's shape (protocol line, then police on its own line).
newds; dc sh "$DC" >/dev/null; : > "$DS/tc.log"
dc sh "$DC" check >/dev/null
# Compare structural tokens: both have "filter protocol ip" and a later "police" before ipv6's police.
fake_show=$("$FAKE/tc" filter show dev eth0 parent ffff: 2>/dev/null || true)
echo "$fake_show" | awk '/^filter protocol ip /{ip=1} ip && /police/{pol=1; exit} END{exit !(ip && pol)}' && ok=1 || ok=0
result "dl-cap fake tc: police is on a separate line from protocol ip" "$ok" ""
echo "$fake_show" | awk '/^filter protocol ipv6 /{v=1} v && /police/{pol=1; exit} END{exit !(v && pol)}' && ok=1 || ok=0
result "dl-cap fake tc: police is on a separate line from protocol ipv6" "$ok" ""
# USE_FIXTURE=1 serves the file verbatim through the fake.
newds; dc USE_FIXTURE=1 sh "$DC" check > "$DS/out" 2>&1; rc=$?
[ "$rc" = 0 ] && ok=1 || ok=0; result "dl-cap check: USE_FIXTURE=1 (verbatim) -> 0" "$ok" "exit=$rc"

echo
echo "passed=$PASS failed=$FAIL"
exit "$FAIL"
