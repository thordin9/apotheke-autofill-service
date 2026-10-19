#!/bin/bash
# Tests for deploy/jetson/*.sh with fake docker/curl/tc on PATH (no Docker, no network, no root).
# Usage: deploy/jetson/tests/run-tests.sh        (about 2 min; the signal cases use real timing)
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
# Runs a copy of the hook whose DL_CAP points at the repo's dl-cap.sh (or NMD_DLCAP), with the fake tc and systemctl.
# NMD_CAP=1: the cap is already in place (fake tc state has the ingress qdisc and both filters).
nmd() {
  local er=$1 i=$2 a=$3 rc n; shift 3
  newds
  if [ -n "${NMD_CAP:-}" ]; then touch "$DS/tc.qdisc"; printf 'ip\nipv6\n' > "$DS/tc.filters"; fi
  sed "s|^DL_CAP=/usr/local/sbin/dl-cap.sh .*|DL_CAP=${NMD_DLCAP:-$DC}|" "$NMD" > "$DS/hook"
  env PATH="$FAKE:$PATH" DL_CAP_HOOK_LOCK="$DS/hook.lock" DLCAP_SH="$DC" "$@" sh "$DS/hook" "$i" "$a" > "$DS/out" 2>&1; rc=$?
  n=$(grep -c "restart dl-cap.service" "$DS/systemctl.log" 2>/dev/null); n=${n:-0}
  [ "$rc" = 0 ] && [ "$n" = "$er" ] && ok=1 || ok=0
  result "nm-dispatcher: '$i' '$a'${NMD_CAP:+ cap ok}${NMD_DLCAP:+ no dl-cap.sh}${1:+ $*} -> $([ "$er" = 1 ] && echo restart || echo nothing)" "$ok" "exit=$rc restarts=$n"
}
newds; sed "s|^DL_CAP=/usr/local/sbin/dl-cap.sh .*|DL_CAP=$DC|" "$NMD" > "$DS/hook"
grep -qx "DL_CAP=$DC" "$DS/hook" && ok=1 || ok=0; result "nm-dispatcher: harness can point DL_CAP at the repo copy" "$ok" ""
# Cap missing: every handled event restarts.
for a in up connectivity-change dhcp4-change; do nmd 1 eth0 "$a"; done
nmd 1 "" connectivity-change          # NetworkManager sends connectivity-change with an empty interface (L5)
for a in down pre-up pre-down dhcp6-change hostname reapply vpn-up ""; do nmd 0 eth0 "$a"; done
for i in wlan0 eth1 lo eth0.5 docker0 ""; do nmd 0 "$i" up; done
for i in wlan0 docker0; do nmd 0 "$i" connectivity-change; nmd 0 "$i" dhcp4-change; done
# Cap in place: nothing to do for any handled event, `up` included (N1: `up` used to restart unconditionally).
NMD_CAP=1 nmd 0 eth0 up
NMD_CAP=1 nmd 0 eth0 up; grep -q "filter show dev eth0 parent ffff:" "$DS/tc.log" && ! grep -qE "qdisc|filter add" "$DS/tc.log" \
  && grep -q "eth0 up: cap in place on eth0, no restart" "$DS/logger.log" && ok=1 || ok=0
result "  ...up ran 'dl-cap.sh check' (read-only), logged the skip, cap untouched" "$ok" ""
NMD_CAP=1 nmd 0 eth0 dhcp4-change
NMD_CAP=1 nmd 0 "" connectivity-change
NMD_CAP=1 nmd 0 eth0 connectivity-change
NMD_CAP=1 nmd 0 eth0 dhcp4-change; grep -q "filter show dev eth0 parent ffff:" "$DS/tc.log" && ! grep -qE "qdisc|filter add" "$DS/tc.log" && ok=1 || ok=0
result "  ...dhcp4-change ran 'dl-cap.sh check' (read-only tc filter show) first" "$ok" ""
# dl-cap.sh not installed: check impossible, so restart (the safe side).
NMD_DLCAP=/nonexistent/dl-cap.sh NMD_CAP=1 nmd 1 eth0 dhcp4-change
# Disabled unit: nothing, whatever the event or cap state.
nmd 0 eth0 up UNIT_DISABLED=1
nmd 0 eth0 dhcp4-change UNIT_DISABLED=1
nmd 0 "" connectivity-change UNIT_DISABLED=1
nmd 1 eth0 up SYSTEMCTL_FAIL=1
nmd 1 eth0 dhcp4-change SYSTEMCTL_FAIL=1
newds; env PATH="$FAKE:$PATH" DL_CAP_HOOK_LOCK="$DS/hook.lock" sh "$NMD" eth0 up > "$DS/out" 2>&1
grep -qx "systemctl --no-block restart dl-cap.service" "$DS/systemctl.log" && ok=1 || ok=0
result "  ...restart is queued (--no-block), not waited for" "$ok" ""
# Ignored events/interfaces touch nothing: no systemctl call (not even is-enabled), no tc call, no lock file.
for ia in "eth0 down" "wlan0 up" "docker0 connectivity-change" "eth1 dhcp4-change" "eth0 pre-up"; do
  nmd 0 "${ia% *}" "${ia#* }"
  [ ! -e "$DS/systemctl.log" ] && [ ! -e "$DS/tc.log" ] && [ ! -e "$DS/hook.lock" ] && ok=1 || ok=0
  result "  ...'$ia' ignored: no systemctl, tc or lock" "$ok" ""
done
# A job already queued for dl-cap.service (its boot start waiting for network-online.target, or a restart queued by
# an earlier event): leave it, even with the cap missing; that start applies and verifies the cap.
nmd 0 eth0 up DLCAP_STATE=inactive DLCAP_JOB=7
nmd 0 "" connectivity-change DLCAP_STATE=active DLCAP_JOB=12
grep -q "already has job 12 queued" "$DS/logger.log" && ok=1 || ok=0; result "  ...logged 'already has job 12 queued'" "$ok" ""
# Stuck in a transition for longer than DL_CAP_HOOK_WAIT_S: give up after the wait, no restart, still exit 0.
SECONDS=0; nmd 0 eth0 up DLCAP_STATE=activating DL_CAP_HOOK_WAIT_S=2; t=$SECONDS
[ "$t" -ge 2 ] && [ "$t" -le 4 ] && grep -q "still activating after 2s" "$DS/logger.log" && ok=1 || ok=0
result "  ...gave up after DL_CAP_HOOK_WAIT_S=2 (${t}s), logged it" "$ok" ""
nmd 1 eth0 up DLCAP_STATE=failed   # a failed unit with no job and no cap: restart
nmd 1 eth0 up DLCAP_STATE=inactive # stopped (not disabled): the next event restarts it, as before

echo "== nm-dispatcher-90-dl-cap: concurrent events (QA #17 N1), with a systemd-like dl-cap job"
# settle: wait (max 10 s) for the simulated dl-cap start to finish.
settle() { local k=0; while [ -f "$DS/dlcap.pid" ] && kill -0 "$(cat "$DS/dlcap.pid")" 2>/dev/null && [ $k -lt 100 ]; do
  sleep 0.1; k=$((k+1)); done; }
cnt() { local c; c=$(grep -c "$@" 2>/dev/null); echo "${c:-0}"; }   # cnt [GREP-OPT] PATTERN FILE
# pair LOCK CAP "IF1 EV1" "IF2 EV2" [ENV...]: run two hook invocations at the same moment, then let dl-cap settle.
# Sets restarts= applies= killed= capok= (dl-cap.sh check exit 0 afterwards).
pair() {
  local lock=$1 cap=$2 e1=$3 e2=$4 p1 p2; shift 4
  newds
  if [ "$cap" = 1 ]; then touch "$DS/tc.qdisc"; printf 'ip\nipv6\n' > "$DS/tc.filters"; fi
  sed "s|^DL_CAP=/usr/local/sbin/dl-cap.sh .*|DL_CAP=$DC|" "$NMD" > "$DS/hook"
  # shellcheck disable=SC2086  # "IF EV" splits into the hook's two arguments; an empty IF is passed as ""
  { env PATH="$FAKE:$PATH" DL_CAP_HOOK_LOCK="$lock" DLCAP_SIM=1 DLCAP_SH="$DC" SHOW_SLEEP=0.3 "$@" \
      sh "$DS/hook" "${e1% *}" "${e1#* }" > "$DS/out1" 2>&1 & p1=$!
    env PATH="$FAKE:$PATH" DL_CAP_HOOK_LOCK="$lock" DLCAP_SIM=1 DLCAP_SH="$DC" SHOW_SLEEP=0.3 "$@" \
      sh "$DS/hook" "${e2% *}" "${e2#* }" > "$DS/out2" 2>&1 & p2=$!
    wait "$p1"; r1=$?; wait "$p2"; r2=$?; }
  settle
  cat "$DS/out1" "$DS/out2" > "$DS/out" 2>/dev/null; [ -f "$DS/logger.log" ] && sed 's/^/log: /' "$DS/logger.log" >> "$DS/out"
  [ -f "$DS/dlcap.events" ] && sed 's/^/event: /' "$DS/dlcap.events" >> "$DS/out"
  restarts=$(cnt "restart dl-cap.service" "$DS/systemctl.log"); applies=$(cnt "^apply$" "$DS/dlcap.events")
  killed=$(cnt "start killed" "$DS/dlcap.events")
  env PATH="$FAKE:$PATH" sh "$DC" check > /dev/null 2>&1 && capok=1 || capok=0
}
# Self-test: the fake reproduces N1. Two back-to-back restarts, as the old hook queued them, kill the first start.
newds; touch "$DS/tc.qdisc"; printf 'ip\nipv6\n' > "$DS/tc.filters"
for k in 1 2; do env PATH="$FAKE:$PATH" DLCAP_SIM=1 DLCAP_SH="$DC" systemctl --no-block restart dl-cap.service; sleep 0.3; done
settle; killed=$(cnt "start killed" "$DS/dlcap.events"); applies=$(cnt "^apply$" "$DS/dlcap.events")
[ "$killed" = 1 ] && [ "$applies" = 1 ] && ok=1 || ok=0
result "nm-dispatcher sim self-test: 2 quick restarts -> 1st start killed (N1)" "$ok" "killed=$killed applies=$applies"
# Two events at the same moment, cap missing: one restart, one apply, nothing killed, cap in place afterwards.
for ev in "eth0 up|eth0 up" "eth0 up| connectivity-change" "eth0 dhcp4-change| connectivity-change"; do
  pair "$TMPROOT/lock.$$" 0 "${ev%|*}" "${ev#*|}"; rm -f "$TMPROOT/lock.$$"
  [ "$r1$r2" = 00 ] && [ "$restarts" = 1 ] && [ "$applies" = 1 ] && [ "$killed" = 0 ] && [ "$capok" = 1 ] && ok=1 || ok=0
  result "nm-dispatcher: concurrent '${ev%|*}' + '${ev#*|}', no cap -> 1 apply, none killed" "$ok" \
    "exit=$r1,$r2 restarts=$restarts applies=$applies killed=$killed cap=$capok"
done
[ "$(cnt "restarting dl-cap.service" "$DS/logger.log")" = 1 ] && [ "$(cnt -E "already has job|cap in place" "$DS/logger.log")" = 1 ] \
  && ok=1 || ok=0
result "  ...the second event left the first's job alone (queued job, or waited + cap in place)" "$ok" ""
# The board's boot case: both events while the cap is already applied -> no restart at all, so no uncapped gap.
pair "$TMPROOT/lock.$$" 1 "eth0 up" " connectivity-change"; rm -f "$TMPROOT/lock.$$"
[ "$restarts" = 0 ] && [ "$applies" = 0 ] && [ "$killed" = 0 ] && [ "$capok" = 1 ] && ! grep -qE "qdisc|filter add" "$DS/tc.log" \
  && [ "$(cnt "cap in place on eth0, no restart" "$DS/logger.log")" = 2 ] && ok=1 || ok=0
result "nm-dispatcher: concurrent up + connectivity-change, cap in place -> 0 restarts" "$ok" \
  "restarts=$restarts applies=$applies killed=$killed cap=$capok"
# An event while a restart is in progress (deactivating/activating): wait for it, then check -> no second restart.
newds; sed "s|^DL_CAP=/usr/local/sbin/dl-cap.sh .*|DL_CAP=$DC|" "$NMD" > "$DS/hook"
env PATH="$FAKE:$PATH" DLCAP_SIM=1 DLCAP_SH="$DC" DLCAP_START_S=2 systemctl --no-block restart dl-cap.service
sleep 0.3; rm -f "$DS/dlcap.job"   # the job has started running (state activating), no longer just queued
SECONDS=0; env PATH="$FAKE:$PATH" DL_CAP_HOOK_LOCK="$DS/hook.lock" DLCAP_SIM=1 DLCAP_SH="$DC" sh "$DS/hook" eth0 up > "$DS/out" 2>&1; rc=$?; t=$SECONDS
settle; restarts=$(cnt "restart dl-cap.service" "$DS/systemctl.log"); killed=$(cnt "start killed" "$DS/dlcap.events")
[ "$rc" = 0 ] && [ "$restarts" = 1 ] && [ "$killed" = 0 ] && [ "$t" -ge 1 ] && grep -q "cap in place on eth0, no restart" "$DS/logger.log" \
  && ok=1 || ok=0
result "nm-dispatcher: up during a running start -> waits (${t}s), then no 2nd restart" "$ok" "restarts=$restarts killed=$killed"
# Without the lock (lock file can't be opened), the same concurrent pair races and the fake shows N1 again:
# evidence that the serialisation is what prevents it (the hook still exits 0 and the cap ends up in place).
pair /nonexistent-dir/lock 0 "eth0 up" " connectivity-change"
[ "$r1$r2" = 00 ] && [ "$restarts" = 2 ] && [ "$killed" = 1 ] && [ "$capok" = 1 ] && ok=1 || ok=0
result "nm-dispatcher sim self-test: unlocked pair races -> 2 restarts, 1 killed" "$ok" \
  "restarts=$restarts applies=$applies killed=$killed cap=$capok"
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

echo "== wait-cuda.sh"
WC=$JET/wait-cuda.sh
FPY=$FAKE/fake-python3
# Helper: build a CUDA_TEST_ROOT with device nodes; optional sysfs readiness path.
mk_gpu() { # root [sysfs=nvgpu|gpu.0|gv11b|none]
  local r=$1 kind=${2:-nvgpu}
  mkdir -p "$r/dev"
  : > "$r/dev/nvhost-ctrl-gpu"; : > "$r/dev/nvmap"
  case "$kind" in
    nvgpu) mkdir -p "$r/sys/module/nvgpu" ;;
    gpu.0) mkdir -p "$r/sys/devices"; : > "$r/sys/devices/gpu.0" ;;
    gv11b) mkdir -p "$r/sys/class/devfreq/17000000.gv11b" ;;
    none) ;;
    *) echo "bad kind $kind" >&2; return 1 ;;
  esac
}
# wc_run [ENV...]: run wait-cuda.sh with the fake python3 (cuInit OK unless FAKE_CUINIT says otherwise).
wc_run() { env CUDA_TEST_ROOT="$DS/root" CUDA_PROBE_PYTHON="$FPY" "$@" sh "$WC" > "$DS/out" 2>&1; }
probes() { cat "$DS/probes" 2>/dev/null || echo 0; }
newds; mk_gpu "$DS/root" nvgpu
wc_run CUDA_WAIT_S=2; rc=$?
[ "$rc" = 0 ] && grep -qE 'device/sysfs: present' "$DS/out" && grep -qE 'cuInit probe #1: OK' "$DS/out" \
  && grep -qE '\+[0-9]+s\)' "$DS/out" && [ "$(probes)" = 1 ] && ok=1 || ok=0
result "wait-cuda: already ready (Ollama after CUDA) -> one cuInit probe, timestamped" "$ok" "exit=$rc probes=$(probes)"
# Device nodes alone are not enough (the board race).
newds; mk_gpu "$DS/root" none
SECONDS=0; wc_run CUDA_WAIT_S=2; rc=$?
[ "$rc" = 0 ] && grep -q "timed out" "$DS/out" && grep -q "device/sysfs: missing" "$DS/out" \
  && [ "$SECONDS" -ge 2 ] && [ "$(probes)" = 0 ] && ok=1 || ok=0
result "wait-cuda: device nodes only (no sysfs) -> timeout, exit 0" "$ok" "exit=$rc ${SECONDS}s"
# Ollama before CUDA: nodes appear after ~1 s.
newds; mkdir -p "$DS/root"
( sleep 1; mk_gpu "$DS/root" gv11b ) &
SECONDS=0; wc_run CUDA_WAIT_S=5; rc=$?
[ "$rc" = 0 ] && grep -qE 'device/sysfs: present' "$DS/out" && grep -qE 'cuInit probe #1: OK' "$DS/out" \
  && [ "$SECONDS" -ge 1 ] && [ "$SECONDS" -le 5 ] && ok=1 || ok=0
result "wait-cuda: ready appears after 1s (Ollama before CUDA)" "$ok" "exit=$rc ${SECONDS}s"
# Timeout validation: bad values fall back to 60 but we use a ready root so it returns immediately;
# the warning must mention the bad value. (0 is valid — skip — and is tested next.)
newds; mk_gpu "$DS/root" nvgpu
for w in abc -5 010 1.5 1000000; do
  wc_run CUDA_WAIT_S="$w"; rc=$?
  [ "$rc" = 0 ] && grep -q "CUDA_WAIT_S must be a whole number of seconds from 0 to 150" "$DS/out" && grep -qE 'cuInit probe #1: OK|GPU ready' "$DS/out" && ok=1 || ok=0
  result "wait-cuda: CUDA_WAIT_S='$w' -> warn, use 60, still exit 0" "$ok" "exit=$rc"
done
# CUDA_WAIT_S=0: skip the wait entirely (including cuInit). Used by QA to reproduce the old race.
newds; mk_gpu "$DS/root" none   # no device nodes either — still must exit at once
SECONDS=0; wc_run CUDA_WAIT_S=0; rc=$?
[ "$rc" = 0 ] && [ "$SECONDS" -le 1 ] && grep -q "CUDA_WAIT_S=0; skipping the wait" "$DS/out" \
  && ! grep -qE 'device/sysfs|cuInit probe' "$DS/out" && [ "$(probes)" = 0 ] && ok=1 || ok=0
result "wait-cuda: CUDA_WAIT_S=0 -> skip entirely (no device/cuInit probes), exit 0" "$ok" "exit=$rc ${SECONDS}s probes=$(probes)"
newds; mk_gpu "$DS/root" nvgpu
wc_run CUDA_WAIT_S=0; rc=$?
[ "$rc" = 0 ] && grep -q "CUDA_WAIT_S=0; skipping the wait" "$DS/out" && [ "$(probes)" = 0 ] && ok=1 || ok=0
result "wait-cuda: CUDA_WAIT_S=0 with nodes present -> still skips cuInit" "$ok" "exit=$rc probes=$(probes)"
# L1: a wait longer than the drop-in's TimeoutStartSec allows is clamped to 150.
for w in 151 999999; do
  wc_run CUDA_WAIT_S="$w"; rc=$?
  [ "$rc" = 0 ] && grep -q "CUDA_WAIT_S=$w is above 150" "$DS/out" && grep -q "using 150" "$DS/out" && grep -qE 'cuInit probe #1: OK' "$DS/out" && ok=1 || ok=0
  result "wait-cuda: CUDA_WAIT_S=$w -> clamped to 150 (L1)" "$ok" "exit=$rc"
done
wc_run CUDA_WAIT_S=150; rc=$?
[ "$rc" = 0 ] && ! grep -q "above 150" "$DS/out" && ok=1 || ok=0; result "wait-cuda: CUDA_WAIT_S=150 -> accepted as is" "$ok" "exit=$rc"
# Real timeout path with short wait (nodes never appear).
newds; mkdir -p "$DS/root"
SECONDS=0; wc_run CUDA_WAIT_S=2; rc=$?
[ "$rc" = 0 ] && grep -q "timed out after 2s" "$DS/out" && ok=1 || ok=0
result "wait-cuda: nothing appears, CUDA_WAIT_S=2 -> timeout exit 0" "$ok" "exit=$rc ${SECONDS}s"
echo "== wait-cuda.sh: cuInit gate (M2)"
# Nodes and sysfs present (as on the bad boot), cuInit fails twice, then succeeds: a fresh probe process each time.
newds; mk_gpu "$DS/root" gv11b
SECONDS=0; wc_run CUDA_WAIT_S=10 FAKE_CUINIT_FAIL_N=2; rc=$?
[ "$rc" = 0 ] && [ "$(probes)" = 3 ] && grep -q "cuInit probe #3: OK" "$DS/out" \
  && [ "$(grep -c 'cuInit probe #' "$DS/out")" = 3 ] \
  && grep -q "cuInit probe #1: not ready yet" "$DS/out" && grep -q "cuInit=100" "$DS/out" \
  && grep -q "cuInit=0 cuDeviceGetCount=0 devices=1" "$DS/out" \
  && grep -qE '\+[0-9]+s\)' "$DS/out" && [ "$SECONDS" -ge 2 ] && [ "$SECONDS" -le 5 ] && ok=1 || ok=0
result "wait-cuda: cuInit fails twice then OK -> 3 timestamped probes" "$ok" "exit=$rc probes=$(probes) ${SECONDS}s"
# Every probe is timestamped and numbered; device/sysfs status is logged too (QA: "did cuInit ever fail early?").
newds; mk_gpu "$DS/root" nvgpu
wc_run CUDA_WAIT_S=5 FAKE_CUINIT_FAIL_N=1; rc=$?
n_dev=$(grep -cE 'device/sysfs: (present|missing)' "$DS/out")
n_probe=$(grep -cE 'cuInit probe #[0-9]+:' "$DS/out")
ts_ok=$(awk '/wait-cuda: /{if ($0 ~ /[0-9]{4}-[0-9]{2}-[0-9]{2}T/ && $0 ~ /\+[0-9]+s\)/) c++} END{print c+0}' "$DS/out")
[ "$rc" = 0 ] && [ "$n_dev" -ge 1 ] && [ "$n_probe" = 2 ] && [ "$ts_ok" -ge 3 ] \
  && grep -q "cuInit probe #1: not ready yet" "$DS/out" && grep -q "cuInit probe #2: OK" "$DS/out" && ok=1 || ok=0
result "wait-cuda: every probe and device check is timestamped (+Ns)" "$ok" "dev=$n_dev probes=$n_probe ts=$ts_ok"
# cuInit 0 but no device counts as not ready.
newds; mk_gpu "$DS/root" nvgpu
SECONDS=0; wc_run CUDA_WAIT_S=2 FAKE_CUINIT=nodev; rc=$?
[ "$rc" = 0 ] && grep -q "timed out after 2s" "$DS/out" && grep -q "devices=0" "$DS/out" && ok=1 || ok=0
result "wait-cuda: cuInit 0 but 0 devices -> not ready, timeout exit 0" "$ok" "exit=$rc probes=$(probes) ${SECONDS}s"
# cuInit never succeeds -> exit 0 at the deadline (never fails the unit).
newds; mk_gpu "$DS/root" nvgpu
SECONDS=0; wc_run CUDA_WAIT_S=3 FAKE_CUINIT=fail; rc=$?
[ "$rc" = 0 ] && grep -q "timed out after 3s" "$DS/out" && [ "$SECONDS" -ge 3 ] && [ "$SECONDS" -le 5 ] \
  && [ "$(probes)" -ge 3 ] && ok=1 || ok=0
result "wait-cuda: cuInit never OK, CUDA_WAIT_S=3 -> timeout exit 0" "$ok" "exit=$rc probes=$(probes) ${SECONDS}s"
# A hanging probe is killed by its timeout, capped at the time left, so the total stays near CUDA_WAIT_S.
newds; mk_gpu "$DS/root" nvgpu
SECONDS=0; wc_run CUDA_WAIT_S=3 FAKE_CUINIT=hang; rc=$?
[ "$rc" = 0 ] && grep -q "cuInit probe #1: timed out" "$DS/out" && grep -q "timed out after 3s" "$DS/out" \
  && [ "$SECONDS" -le 7 ] && ok=1 || ok=0
result "wait-cuda: probe hangs -> killed by timeout, exit 0 within the wait" "$ok" "exit=$rc ${SECONDS}s"
# Default per-probe timeout is 10 s.
# shellcheck disable=SC2016  # literal $ for the stub / pattern
grep -qx 'PROBE_TIMEOUT_S=10' "$WC" && grep -q 'timeout -k 2 "\$1"' "$WC" && ok=1 || ok=0
result "wait-cuda: per-probe timeout is 10 s (timeout -k 2)" "$ok" ""
# I1: CUDA comes up exactly at the deadline -> the last probe at the deadline still sees it.
newds; mk_gpu "$DS/root" nvgpu
wc_run CUDA_WAIT_S=2 FAKE_CUINIT_OK_AFTER=$(( $(date +%s) + 2 )); rc=$?
[ "$rc" = 0 ] && grep -qE 'cuInit probe #[0-9]+: OK' "$DS/out" && ! grep -q "timed out" "$DS/out" && ok=1 || ok=0
result "wait-cuda: cuInit OK right at the deadline -> ready, not timed out (I1)" "$ok" "exit=$rc probes=$(probes)"
# python3 missing -> log and fall back to the device checks.
newds; mk_gpu "$DS/root" nvgpu
wc_run CUDA_WAIT_S=2 CUDA_PROBE_PYTHON=/nonexistent/python3; rc=$?
[ "$rc" = 0 ] && grep -q "/nonexistent/python3 not found; skipping the cuInit check" "$DS/out" && ok=1 || ok=0
result "wait-cuda: python3 missing -> device checks only, exit 0" "$ok" "exit=$rc"
# libcuda missing (probe exit 3) -> log and fall back, one probe only.
newds; mk_gpu "$DS/root" nvgpu
wc_run CUDA_WAIT_S=5 FAKE_CUINIT=nolib; rc=$?
[ "$rc" = 0 ] && grep -q "cuInit probe #1: unavailable (libcuda.so.1 not loadable)" "$DS/out" && [ "$(probes)" = 1 ] && ok=1 || ok=0
result "wait-cuda: libcuda missing -> device checks only, exit 0" "$ok" "exit=$rc probes=$(probes)"
# The real probe with the box's python3: no libcuda here, so it must report "unavailable" (exit 3), not crash.
if command -v python3 >/dev/null 2>&1 && ! python3 -c 'import ctypes; ctypes.CDLL("libcuda.so.1")' 2>/dev/null; then
  newds; mk_gpu "$DS/root" nvgpu
  env CUDA_TEST_ROOT="$DS/root" CUDA_WAIT_S=5 sh "$WC" > "$DS/out" 2>&1; rc=$?
  [ "$rc" = 0 ] && grep -q "cuInit probe #1: unavailable (libcuda.so.1 not loadable)" "$DS/out" && ok=1 || ok=0
  result "wait-cuda: real python3 probe without libcuda -> unavailable, exit 0" "$ok" "exit=$rc"
fi
# The real probe code with the real python3 against a stub libcuda.so.1 (built here; needs a C compiler):
# cuInit 0 + 1 device -> ready; cuInit 100 -> not ready; cuInit 0 + 0 devices -> not ready.
if command -v python3 >/dev/null 2>&1 && command -v cc >/dev/null 2>&1; then
  newds; mk_gpu "$DS/root" nvgpu; mkdir -p "$DS/lib"
  printf '#include <stdlib.h>\nint cuInit(unsigned f){const char*e=getenv("STUB_INIT");(void)f;return e?atoi(e):0;}\nint cuDeviceGetCount(int*n){const char*e=getenv("STUB_N");*n=e?atoi(e):1;return 0;}\n' > "$DS/lib/c.c"
  if cc -shared -fPIC -o "$DS/lib/libcuda.so.1" "$DS/lib/c.c" 2>"$DS/out"; then
    for v in "0 1 0 cuInit=0 cuDeviceGetCount=0 devices=1" "100 1 1 cuInit=100" "0 0 1 devices=0"; do
      read -r si sn to want <<< "$v"
      env LD_LIBRARY_PATH="$DS/lib" STUB_INIT="$si" STUB_N="$sn" CUDA_TEST_ROOT="$DS/root" CUDA_WAIT_S=1 sh "$WC" > "$DS/out" 2>&1; rc=$?
      if [ "$to" = 0 ]; then grep -qE 'cuInit probe #1: OK' "$DS/out" && grep -q "$want" "$DS/out" && ok=1 || ok=0
      else grep -q "timed out after 1s" "$DS/out" && grep -q "$want" "$DS/out" && ok=1 || ok=0; fi
      [ "$rc" = 0 ] || ok=0
      result "wait-cuda: real probe, stub libcuda cuInit=$si devices=$sn -> $([ "$to" = 0 ] && echo ready || echo timeout)" "$ok" "exit=$rc"
    done
  fi
fi
# Not root: the probe runs directly (ExecStartPre already runs as User=ollama), no runuser.
newds; mk_gpu "$DS/root" nvgpu; mkdir -p "$DS/bin"
# shellcheck disable=SC2016  # literal $ for the stub / pattern
printf '#!/bin/sh\necho "runuser $*" >> "$DS/runuser.log"; exit 99\n' > "$DS/bin/runuser"
# shellcheck disable=SC2016  # literal $ for the stub / pattern
printf '#!/bin/sh\n[ "$1" = -u ] && { echo 1000; exit 0; }\nexit 0\n' > "$DS/bin/id"; chmod +x "$DS/bin/"*
wc_run PATH="$DS/bin:$PATH" CUDA_WAIT_S=2; rc=$?
[ "$rc" = 0 ] && grep -q "cuInit probe #1: OK" "$DS/out" && [ ! -e "$DS/runuser.log" ] && ok=1 || ok=0
result "wait-cuda: not root -> probe runs as the unit's user, no runuser" "$ok" "exit=$rc"
# Root: the probe runs as the ollama user through runuser.
newds; mk_gpu "$DS/root" nvgpu; mkdir -p "$DS/bin"
# shellcheck disable=SC2016  # literal $DS / $* for the stub
printf '#!/bin/sh\necho "runuser $*" >> "$DS/runuser.log"\n[ "$1" = -u ] && [ "$3" = -- ] || exit 98\nshift 3; exec "$@"\n' > "$DS/bin/runuser"
# shellcheck disable=SC2016  # literal $ for the stub / pattern
printf '#!/bin/sh\n[ "$1" = -u ] && { echo 0; exit 0; }\n[ "$1" = ollama ] && exit 0\nexit 1\n' > "$DS/bin/id"; chmod +x "$DS/bin/"*
wc_run PATH="$DS/bin:$PATH" CUDA_WAIT_S=2; rc=$?
[ "$rc" = 0 ] && grep -q "cuInit probe #1: OK" "$DS/out" && grep -q "^runuser -u ollama -- $FPY -c" "$DS/runuser.log" && ok=1 || ok=0
result "wait-cuda: root -> probe via runuser -u ollama" "$ok" "exit=$rc $(head -c 60 "$DS/runuser.log" 2>/dev/null)"
# Root, but no ollama user: warn once and probe as root (still bounded, still exit 0).
newds; mk_gpu "$DS/root" nvgpu; mkdir -p "$DS/bin"
# shellcheck disable=SC2016  # literal $ for the stub / pattern
printf '#!/bin/sh\necho "runuser $*" >> "$DS/runuser.log"; exit 99\n' > "$DS/bin/runuser"
# shellcheck disable=SC2016  # literal $ for the stub / pattern
printf '#!/bin/sh\n[ "$1" = -u ] && { echo 0; exit 0; }\nexit 1\n' > "$DS/bin/id"; chmod +x "$DS/bin/"*
wc_run PATH="$DS/bin:$PATH" CUDA_WAIT_S=10 FAKE_CUINIT_FAIL_N=1; rc=$?
[ "$rc" = 0 ] && grep -qE 'cuInit probe #[0-9]+: OK' "$DS/out" && [ "$(grep -c "cannot switch to 'ollama'" "$DS/out")" = 1 ] && [ ! -e "$DS/runuser.log" ] && ok=1 || ok=0
result "wait-cuda: root without an ollama user -> warn once, probe as root" "$ok" "exit=$rc"
echo "== ollama unit files (L1, L4, M2)"
DROPIN=$JET/ollama-wait-cuda.conf; GCU=$JET/ollama-gpu-check.service
grep -qx 'After=nv.service nvpmodel.service' "$DROPIN" && grep -qx 'TimeoutStartSec=180' "$DROPIN" \
  && grep -qx 'ExecStartPre=/usr/local/sbin/wait-cuda.sh' "$DROPIN" && ok=1 || ok=0
result "drop-in: After=nv.service nvpmodel.service, TimeoutStartSec=180, unprivileged ExecStartPre" "$ok" ""
mw=$(sed -n 's/^MAX_WAIT=//p' "$WC"); pt=$(sed -n 's/^PROBE_TIMEOUT_S=//p' "$WC")
[ -n "$mw" ] && [ -n "$pt" ] && [ $((mw + pt + 2)) -lt 180 ] && ok=1 || ok=0
result "drop-in: CUDA_WAIT_S cap + one probe + kill grace < TimeoutStartSec" "$ok" "max=$mw probe=$pt"
grep -qx 'Requisite=ollama.service' "$GCU" && grep -qx 'After=ollama.service' "$GCU" && ! grep -q '^Wants=' "$GCU" \
  && grep -qx 'TimeoutStartSec=600' "$GCU" && ok=1 || ok=0
result "gpu-check unit: Requisite+After ollama, no Wants=, TimeoutStartSec=600" "$ok" ""
gw=$(sed -n 's/^DEFAULT_WAIT=//p' "$JET/ollama-gpu-check.sh"); gd=$(sed -n 's/^DEFAULT_DELAY=//p' "$JET/ollama-gpu-check.sh")
# One restart = Ollama's stop (systemd's default stop timeout, 90 s) + its start (TimeoutStartSec=180): 520 s.
[ $((2 * gw + gd + 90 + 180)) -lt 600 ] && grep -q '= 520 s worst' "$GCU" && ok=1 || ok=0
result "gpu-check unit: 2 x wait + delay + one restart (stop + start) < TimeoutStartSec, comment says 520 s" "$ok" "wait=$gw delay=$gd"
# The documented overrun of the last probe (time left, at least 1 s, + 2 s kill grace) stays inside the drop-in.
[ $((mw + 1 + 2)) -lt 180 ] && grep -q 'at most' "$DROPIN" && grep -q '3 s' "$DROPIN" && grep -q 'kill' "$DROPIN" \
  && grep -q 'timeout -k 2 ' "$WC" && ok=1 || ok=0
result "drop-in: comment's 3 s overrun matches wait-cuda (min 1 s + 2 s kill grace)" "$ok" "max=$mw"
# Forced-race boot (R1b) in docs/deploy-jetson.md: no empty After= (systemd ignores it in a drop-in); the
# tmpfiles entries and the drop-in carry exactly the one-boot env, and the drop-in deletes it after the first start.
DOC=$JET/../../docs/deploy-jetson.md
doc_heredoc() { sed -n "\|^cat > $1 <<'EOF'\$|,/^EOF\$/p" "$DOC" | sed '1d;$d'; }
R1B_TMP=$(doc_heredoc /etc/tmpfiles.d/zz-force-race.conf)
R1B_DROP=$(doc_heredoc /etc/systemd/system/ollama.service.d/zz-force-race.conf)
! grep -qx 'After=' "$DOC" \
  && printf '%s\n' "$R1B_TMP" | grep -qx 'f /run/ollama-force-race-cpu.env 0644 root root - CUDA_VISIBLE_DEVICES=-1' \
  && printf '%s\n' "$R1B_TMP" | grep -qx 'f /run/ollama-force-race-nowait.env 0644 root root - CUDA_WAIT_S=0' \
  && printf '%s\n' "$R1B_DROP" | grep -qx 'EnvironmentFile=-/run/ollama-force-race-cpu.env' \
  && printf '%s\n' "$R1B_DROP" | grep -qx 'EnvironmentFile=-/run/ollama-force-race-nowait.env' \
  && printf '%s\n' "$R1B_DROP" | grep -qx 'ExecStartPost=+/bin/rm -f /run/ollama-force-race-cpu.env /run/ollama-force-race-nowait.env' \
  && grep -q 'rm /etc/tmpfiles.d/zz-force-race.conf /etc/systemd/system/ollama.service.d/zz-force-race.conf' "$DOC" \
  && ok=1 || ok=0
result "docs: forced-race boot R1b (tmpfiles env, self-cleaning drop-in, removal), no empty After=" "$ok" ""
# systemd's own parser, where available (CI's ubuntu-latest has it). Paths rewritten to existing stubs.
if command -v systemd-analyze >/dev/null 2>&1; then
  newds; mkdir -p "$DS/u/ollama.service.d"
  printf '#!/bin/sh\nexit 0\n' > "$DS/stub"; chmod +x "$DS/stub"
  printf '[Unit]\nDescription=stub ollama\n[Service]\nExecStart=%s\n' "$DS/stub" > "$DS/u/ollama.service"
  sed "s|/usr/local/sbin/[a-z-]*\.sh|$DS/stub|" "$DROPIN" > "$DS/u/ollama.service.d/ollama-wait-cuda.conf"
  sed "s|/usr/local/sbin/[a-z-]*\.sh|$DS/stub|" "$GCU" > "$DS/u/ollama-gpu-check.service"
  (cd "$DS/u" && SYSTEMD_UNIT_PATH="$DS/u:" systemd-analyze verify --man=no ollama-gpu-check.service ollama.service) > "$DS/out" 2>&1; rc=$?
  ! grep -qiE 'unknown (key|section)|invalid|failed to parse|bad' "$DS/out" && ok=1 || ok=0
  result "systemd-analyze verify: gpu-check unit and drop-in parse cleanly" "$ok" "exit=$rc"
  # The documented R1b drop-in parses too, together with the wait-cuda drop-in.
  printf '%s\n' "$R1B_DROP" > "$DS/u/ollama.service.d/zz-force-race.conf"
  (cd "$DS/u" && SYSTEMD_UNIT_PATH="$DS/u:" systemd-analyze verify --man=no ollama.service) > "$DS/out" 2>&1; rc=$?
  [ -s "$DS/u/ollama.service.d/zz-force-race.conf" ] && ! grep -qiE 'unknown (key|section)|invalid|failed to parse|bad' "$DS/out" && ok=1 || ok=0
  result "systemd-analyze verify: docs' R1b zz-force-race.conf drop-in parses cleanly" "$ok" "exit=$rc"
  rm "$DS/u/ollama.service.d/zz-force-race.conf"
  # Negative control: the same check catches a misspelt key (so a clean run means something).
  echo 'Requisitee=ollama.service' >> "$DS/u/ollama-gpu-check.service"
  (cd "$DS/u" && SYSTEMD_UNIT_PATH="$DS/u:" systemd-analyze verify --man=no ollama-gpu-check.service) > "$DS/out" 2>&1
  grep -qE "Unknown key( name)? 'Requisitee'" "$DS/out" && ok=1 || ok=0
  result "systemd-analyze verify: negative control catches a misspelt key" "$ok" ""
fi

echo "== ollama-gpu-check.sh"
OG=$JET/ollama-gpu-check.sh
# Every forbidden call from any gpu-check case also lands here, so the final assertion covers all of them (I2).
ALL_MODEL_CALLS=$TMPROOT/gpu-check-model-calls.log; : > "$ALL_MODEL_CALLS"; OG_CASES=0
# Strict curl/ollama on PATH ahead of fakes: any model pull/generate fails the case.
strict_bins() {
  mkdir -p "$DS/strict"
  cat > "$DS/strict/curl" <<C
#!/bin/sh
echo "curl \$*" >> "\$DS/curl.log"
case "\$*" in */api/generate*|*/api/pull*|*/api/chat*) echo "curl \$*" >> "$ALL_MODEL_CALLS"; echo "FORBIDDEN: \$*" >&2; exit 99 ;; esac
exit 0
C
  cat > "$DS/strict/ollama" <<O
#!/bin/sh
echo "ollama \$*" >> "\$DS/ollama.log"
echo "ollama \$*" >> "$ALL_MODEL_CALLS"
echo "FORBIDDEN ollama call: \$*" >&2
exit 99
O
  chmod +x "$DS/strict/curl" "$DS/strict/ollama"
}
no_model_calls() {
  if [ -s "$DS/ollama.log" ] 2>/dev/null; then return 1; fi
  if [ -f "$DS/curl.log" ] && grep -qE '/api/(generate|pull|chat)' "$DS/curl.log"; then return 1; fi
  return 0
}
og() { # og [ENV...] sh "$OG" [args]; uses FAKE + strict bins. Restart delay 0 unless the case sets it.
  OG_CASES=$((OG_CASES + 1))
  [ -x "$DS/strict/ollama" ] || { echo "og: strict bins missing in $DS" >> "$ALL_MODEL_CALLS"; }
  env PATH="$DS/strict:$FAKE:$PATH" OLLAMA_GPU_CHECK_RESTART_DELAY_S=0 "$@" > "$DS/out" 2>&1
}
CUDA_LINE='msg="inference compute" id=CUDA0 library=CUDA compute=7.2 name=CUDA0 description=Xavier'
CPU_LINE='msg="inference compute" id=cpu library=cpu'
# check: CUDA -> 0
newds; strict_bins
echo "$CUDA_LINE" > "$DS/journal.txt"
og OLLAMA_GPU_CHECK_MARKER="$DS/marker" OLLAMA_GPU_CHECK_WAIT_S=2 sh "$OG" check; rc=$?
[ "$rc" = 0 ] && grep -q "library=CUDA" "$DS/out" && no_model_calls && ok=1 || ok=0
result "ollama-gpu-check check: library=CUDA -> 0" "$ok" "exit=$rc"
# check: CPU -> 1
newds; strict_bins
echo "$CPU_LINE" > "$DS/journal.txt"
og OLLAMA_GPU_CHECK_MARKER="$DS/marker" OLLAMA_GPU_CHECK_WAIT_S=2 sh "$OG" check; rc=$?
[ "$rc" = 1 ] && grep -q "CPU-only" "$DS/out" && ok=1 || ok=0
result "ollama-gpu-check check: library=cpu -> 1" "$ok" "exit=$rc"
# check: empty journal -> 1
newds; strict_bins; : > "$DS/journal.txt"
og OLLAMA_GPU_CHECK_MARKER="$DS/marker" OLLAMA_GPU_CHECK_WAIT_S=2 sh "$OG" check; rc=$?
[ "$rc" = 1 ] && ok=1 || ok=0; result "ollama-gpu-check check: no compute line -> 1" "$ok" "exit=$rc"
# D4: not root and nothing readable -> the message says it needs root / the journal group.
newds; strict_bins; : > "$DS/journal.txt"
# shellcheck disable=SC2016  # literal $ for the stub / pattern
printf '#!/bin/sh\n[ "$1" = -u ] && { echo 1000; exit 0; }\nexit 0\n' > "$DS/strict/id"; chmod +x "$DS/strict/id"
og OLLAMA_GPU_CHECK_MARKER="$DS/marker" sh "$OG" check; rc=$?
[ "$rc" = 1 ] && grep -q "needs root or the systemd-journal group" "$DS/out" && ok=1 || ok=0
result "ollama-gpu-check check: not root, no lines -> hint: needs root/journal (D4)" "$ok" "exit=$rc"
# L2: the board's exact lines (types.go:60 CPU at 18:22, types.go:42 GPU at 19:28); keys on library=, not source=.
BOARD_CPU='time=2026-10-19T18:22:10.715+03:00 level=INFO source=types.go:60 msg="inference compute" id=cpu library=cpu compute="" name=cpu description=cpu libdirs=ollama driver="" pci_id="" type="" total="6.8 GiB" available="5.1 GiB"'
BOARD_GPU='time=2026-10-19T19:28:41.102+03:00 level=INFO source=types.go:42 msg="inference compute" id=GPU-00000000-0000-0000-0000-000000000000 library=CUDA compute=7.2 name=CUDA0 description=Xavier libdirs=ollama,cuda_jetpack5 driver=11.4 pci_id="" type=iGPU total="6.8 GiB" available="5.1 GiB"'
# gc_check NAME EXP_EXIT LINE...: one start's journal with these lines, then `check`.
gc_check() {
  local name=$1 ee=$2; shift 2
  newds; strict_bins; printf '%s\n' "$@" > "$DS/journal.txt"
  og OLLAMA_GPU_CHECK_MARKER="$DS/marker" sh "$OG" check; rc=$?
  [ "$rc" = "$ee" ] && no_model_calls && ok=1 || ok=0; result "ollama-gpu-check check: $name -> $ee" "$ok" "exit=$rc"
}
gc_check "board CPU line (source=types.go:60)" 1 "$BOARD_CPU"
gc_check "board GPU line (source=types.go:42)" 0 "$BOARD_GPU"
gc_check "board GPU line with source= swapped to :60" 0 "${BOARD_GPU/types.go:42/types.go:60}"
gc_check "board CPU line with source= swapped to :42" 1 "${BOARD_CPU/types.go:60/types.go:42}"
gc_check "GPU line then CPU line in one start (L2)" 0 "$BOARD_GPU" "$BOARD_CPU"
gc_check "CPU line then GPU line in one start" 0 "$BOARD_CPU" "$BOARD_GPU"
gc_check "lower-case library=cuda (L2)" 0 "${CUDA_LINE/library=CUDA/library=cuda}"
gc_check "quoted library=\"CUDA\" (L2)" 0 "${CUDA_LINE/library=CUDA/library=\"CUDA\"}"
gc_check "library=CUDA at end of line" 0 'msg="inference compute" id=CUDA0 library=CUDA'
gc_check "library=cudart_stub is not CUDA" 1 'msg="inference compute" id=x library=cudart_stub'
gc_check "CUDA only in description=, library=cpu" 1 'msg="inference compute" id=cpu library=cpu description="no CUDA"'
gc_check "two starts in one journal: CUDA, then a start with cpu" 1 'msg="server config"' "$CUDA_LINE" 'msg="server config"' "$CPU_LINE"
gc_check "two starts in one journal: cpu, then a start with CUDA" 0 'msg="server config"' "$CPU_LINE" 'msg="server config"' "$CUDA_LINE"
# L3: scoped by _SYSTEMD_INVOCATION_ID. Previous start CUDA, current start CPU, both in the same second.
newds; strict_bins
printf '%s\n' "$CUDA_LINE" '# --- restart ---' "$CPU_LINE" > "$DS/journal.txt"; echo 1 > "$DS/ollama.restarts"
og OLLAMA_GPU_CHECK_MARKER="$DS/marker" SINCE_SAME_SECOND=1 sh "$OG" check; rc=$?
[ "$rc" = 1 ] && grep -q "_SYSTEMD_INVOCATION_ID=a1b2c3d4e5f60718293a4b5c6d7e8f91" "$DS/journalctl.log" && ! grep -q -- "--since" "$DS/journalctl.log" && ok=1 || ok=0
result "ollama-gpu-check check: InvocationID scopes to this start (old CUDA line ignored, L3)" "$ok" "exit=$rc"
newds; strict_bins
printf '%s\n' "$CPU_LINE" '# --- restart ---' "$CUDA_LINE" > "$DS/journal.txt"; echo 1 > "$DS/ollama.restarts"
og OLLAMA_GPU_CHECK_MARKER="$DS/marker" INVOCATION_ID_EMPTY=1 sh "$OG" check; rc=$?
[ "$rc" = 0 ] && grep -q -- "--since Mon 2026-10-19" "$DS/journalctl.log" && ok=1 || ok=0
result "ollama-gpu-check check: no InvocationID -> falls back to --since ActiveEnterTimestamp" "$ok" "exit=$rc"
# apply: already CUDA -> no restart
newds; strict_bins
echo "$CUDA_LINE" > "$DS/journal.txt"
og OLLAMA_GPU_CHECK_MARKER="$DS/marker" OLLAMA_GPU_CHECK_WAIT_S=2 sh "$OG"; rc=$?
[ "$rc" = 0 ] && [ ! -e "$DS/ollama.restarts" ] && [ ! -e "$DS/marker" ] && no_model_calls && ok=1 || ok=0
result "ollama-gpu-check apply: already CUDA -> no restart" "$ok" "exit=$rc"
# M1: the compute line shows up late (Ollama's discovery can take 30 s + 30 s); polling picks it up.
newds; strict_bins; : > "$DS/journal.txt"
( sleep 3; echo "$CUDA_LINE" >> "$DS/journal.txt" ) &
SECONDS=0; og OLLAMA_GPU_CHECK_MARKER="$DS/marker" OLLAMA_GPU_CHECK_WAIT_S=8 sh "$OG"; rc=$?; wait
[ "$rc" = 0 ] && grep -q "GPU already in use" "$DS/out" && [ ! -e "$DS/ollama.restarts" ] && [ "$SECONDS" -ge 3 ] && ok=1 || ok=0
result "ollama-gpu-check apply: compute line after 3 s -> found, no restart (M1)" "$ok" "exit=$rc ${SECONDS}s"
grep -qx 'DEFAULT_WAIT=120' "$OG" && ok=1 || ok=0; result "ollama-gpu-check: default wait for the compute line is 120 s (M1)" "$ok" ""
# apply: CPU, restart brings CUDA (Ollama before CUDA recovery)
newds; strict_bins
echo "$CPU_LINE" > "$DS/journal.txt"
og OLLAMA_GPU_CHECK_MARKER="$DS/marker" OLLAMA_GPU_CHECK_WAIT_S=2 RESTART_BRINGS_CUDA=1 sh "$OG"; rc=$?
[ "$rc" = 0 ] && [ "$(cat "$DS/ollama.restarts")" = 1 ] && [ -e "$DS/marker" ] \
  && grep -q "after restart, library=CUDA" "$DS/out" && no_model_calls && ok=1 || ok=0
result "ollama-gpu-check apply: CPU then CUDA after one restart" "$ok" "exit=$rc restarts=$(cat "$DS/ollama.restarts" 2>/dev/null)"
# L3: same, with the real --since's same-second overlap simulated; InvocationID still reads only the new start.
newds; strict_bins
echo "$CPU_LINE" > "$DS/journal.txt"
og OLLAMA_GPU_CHECK_MARKER="$DS/marker" OLLAMA_GPU_CHECK_WAIT_S=2 SINCE_SAME_SECOND=1 RESTART_BRINGS_CUDA=1 sh "$OG"; rc=$?
[ "$rc" = 0 ] && grep -q "after restart, library=CUDA" "$DS/out" && ! grep -q "still not" "$DS/out" && ok=1 || ok=0
result "ollama-gpu-check apply: restart in the same second -> 'after restart, library=CUDA' (L3)" "$ok" "exit=$rc"
# Restart delay: waits before the single restart, logged.
newds; strict_bins
echo "$CPU_LINE" > "$DS/journal.txt"
SECONDS=0; og OLLAMA_GPU_CHECK_MARKER="$DS/marker" OLLAMA_GPU_CHECK_WAIT_S=2 OLLAMA_GPU_CHECK_RESTART_DELAY_S=2 RESTART_BRINGS_CUDA=1 sh "$OG"; rc=$?
[ "$rc" = 0 ] && grep -q "waiting 2s before the single restart" "$DS/out" && [ "$SECONDS" -ge 2 ] && [ -e "$DS/marker" ] \
  && [ "$(cat "$DS/ollama.restarts")" = 1 ] && ok=1 || ok=0
result "ollama-gpu-check apply: OLLAMA_GPU_CHECK_RESTART_DELAY_S=2 -> waits, then one restart" "$ok" "exit=$rc ${SECONDS}s"
grep -qx 'DEFAULT_DELAY=10' "$OG" && ok=1 || ok=0; result "ollama-gpu-check: default restart delay is 10 s" "$ok" ""
for d in abc -1 010 1.5 301 1000; do
  newds; strict_bins; echo "$CUDA_LINE" > "$DS/journal.txt"
  og OLLAMA_GPU_CHECK_MARKER="$DS/marker" OLLAMA_GPU_CHECK_WAIT_S=2 OLLAMA_GPU_CHECK_RESTART_DELAY_S="$d" sh "$OG"; rc=$?
  [ "$rc" = 0 ] && grep -q "OLLAMA_GPU_CHECK_RESTART_DELAY_S must be a whole number of seconds from 0 to 300, got '$d'; using 10" "$DS/out" && ok=1 || ok=0
  result "ollama-gpu-check: RESTART_DELAY_S='$d' -> warn, use 10" "$ok" "exit=$rc"
done
# apply: really no GPU — one restart, still CPU, no loop; second apply does not restart again
newds; strict_bins
echo "$CPU_LINE" > "$DS/journal.txt"
og OLLAMA_GPU_CHECK_MARKER="$DS/marker" OLLAMA_GPU_CHECK_WAIT_S=2 sh "$OG"; rc=$?
[ "$rc" = 0 ] && [ "$(cat "$DS/ollama.restarts")" = 1 ] && [ -e "$DS/marker" ] \
  && grep -q "not restarting again" "$DS/out" && ok=1 || ok=0
result "ollama-gpu-check apply: really-no-GPU, one restart then stop" "$ok" "exit=$rc"
og OLLAMA_GPU_CHECK_MARKER="$DS/marker" OLLAMA_GPU_CHECK_WAIT_S=2 sh "$OG"; rc=$?
[ "$rc" = 0 ] && [ "$(cat "$DS/ollama.restarts")" = 1 ] && grep -q "already restarted once" "$DS/out" && ok=1 || ok=0
result "ollama-gpu-check apply: second call same boot -> no second restart" "$ok" "exit=$rc restarts=$(cat "$DS/ollama.restarts")"
# apply: ollama inactive -> nothing
newds; strict_bins
og OLLAMA_GPU_CHECK_MARKER="$DS/marker" OLLAMA_INACTIVE=1 OLLAMA_GPU_CHECK_WAIT_S=2 sh "$OG"; rc=$?
[ "$rc" = 0 ] && [ ! -e "$DS/ollama.restarts" ] && grep -q "not active" "$DS/out" && ok=1 || ok=0
result "ollama-gpu-check apply: ollama inactive -> no restart" "$ok" "exit=$rc"
# apply: restart fails -> marker kept, exit 0, no loop
newds; strict_bins
echo "$CPU_LINE" > "$DS/journal.txt"
og OLLAMA_GPU_CHECK_MARKER="$DS/marker" OLLAMA_GPU_CHECK_WAIT_S=2 SYSTEMCTL_FAIL=1 sh "$OG"; rc=$?
[ "$rc" = 0 ] && [ -e "$DS/marker" ] && [ ! -e "$DS/ollama.restarts" ] && grep -q "restart ollama.service failed" "$DS/out" && ok=1 || ok=0
result "ollama-gpu-check apply: restart fails -> marker, exit 0" "$ok" "exit=$rc"
# unknown arg
newds; strict_bins
og OLLAMA_GPU_CHECK_MARKER="$DS/marker" sh "$OG" bogus; rc=$?
[ "$rc" = 2 ] && grep -q usage "$DS/out" && ok=1 || ok=0
result "ollama-gpu-check: unknown argument -> usage, exit 2" "$ok" "exit=$rc"
# WAIT_S validation warning
newds; strict_bins
echo "$CUDA_LINE" > "$DS/journal.txt"
og OLLAMA_GPU_CHECK_MARKER="$DS/marker" OLLAMA_GPU_CHECK_WAIT_S=abc sh "$OG" check; rc=$?
[ "$rc" = 0 ] && grep -q "OLLAMA_GPU_CHECK_WAIT_S must be a positive whole number" "$DS/out" && ok=1 || ok=0
result "ollama-gpu-check: bad OLLAMA_GPU_CHECK_WAIT_S -> warn, still works" "$ok" "exit=$rc"
# No inference line within wait -> exit 0 without restart (don't flail)
newds; strict_bins; : > "$DS/journal.txt"
SECONDS=0
og OLLAMA_GPU_CHECK_MARKER="$DS/marker" OLLAMA_GPU_CHECK_WAIT_S=2 sh "$OG"; rc=$?
[ "$rc" = 0 ] && [ ! -e "$DS/ollama.restarts" ] && grep -q "no inference compute line" "$DS/out" && ok=1 || ok=0
result "ollama-gpu-check apply: no compute line after wait -> exit 0, no restart" "$ok" "exit=$rc ${SECONDS}s"
# I2: no model API call or ollama CLI call in ANY gpu-check case above (aggregate log, written by every strict bin).
[ ! -s "$ALL_MODEL_CALLS" ] && [ "$OG_CASES" -ge 30 ] && ok=1 || ok=0
result "ollama-gpu-check: no /api/generate|/api/pull|/api/chat and no ollama CLI in all $OG_CASES cases (I2)" "$ok" "$(head -c 200 "$ALL_MODEL_CALLS")"
# ...and the aggregate really catches a call: a strict bin called once writes to it.
newds; strict_bins; saved=$ALL_MODEL_CALLS
"$DS/strict/ollama" run qwen >/dev/null 2>&1; "$DS/strict/curl" -s localhost:11434/api/generate >/dev/null 2>&1
[ "$(wc -l < "$saved")" = 2 ] && ok=1 || ok=0; : > "$saved"
result "ollama-gpu-check: the aggregate log records forbidden calls (self-test)" "$ok" ""

echo
echo "passed=$PASS failed=$FAIL"
exit "$FAIL"
