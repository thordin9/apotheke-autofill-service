#!/bin/sh
# Cap ALL incoming traffic on eth0 at 30 Mbit/s, with no exemptions (LAN, SSH and service uploads included).
# The L4T R35 kernel has no IFB module, so this polices ingress: packets over the rate are dropped and TCP
# backs off to about 30 Mbit/s. Install as /usr/local/sbin/dl-cap.sh (mode 755); started by dl-cap.service and
# restarted by the NetworkManager hook nm-dispatcher-90-dl-cap when NetworkManager (re)configures eth0.
# Usage: dl-cap.sh        apply (idempotent), then verify; exits 1 if the cap isn't in place afterwards
#        dl-cap.sh check  exit 0 if the cap is in place, 1 if it is missing (changes nothing)
#        dl-cap.sh stop   remove
DEV=${DEV:-eth0}

# True if DEV's ingress qdisc carries a police filter for both IPv4 and IPv6. A missing qdisc, a failing
# `tc` or empty output all count as missing. Matches iproute2-ss200127 on the board, where `protocol ip`/`ipv6`
# is on the `filter` line and `police` is on a following `action` line (see tests/fixtures/tc-filter-show-eth0-ffff.txt).
has_cap() {
  tc filter show dev "$DEV" parent ffff: 2>/dev/null | awk '
    /^filter/ { p = ""; for (i = 1; i < NF; i++) if ($i == "protocol") p = $(i + 1) }
    /police/ && p != "" { seen[p] = 1 }
    END { exit !(seen["ip"] && seen["ipv6"]) }'
}

case "${1:-apply}" in
  check)
    if has_cap; then echo "dl-cap: 30 Mbit/s inbound cap is in place on $DEV"; exit 0; fi
    echo "dl-cap: 30 Mbit/s inbound cap is MISSING on $DEV (fix: systemctl restart dl-cap)" >&2; exit 1 ;;
  apply|stop) ;;
  *) echo "usage: dl-cap.sh [apply|check|stop]" >&2; exit 2 ;;
esac

tc qdisc del dev "$DEV" ingress 2>/dev/null   # may fail harmlessly (nothing to delete)
set -e                                       # from here on, any failed tc call fails the unit
[ "${1:-}" = "stop" ] && exit 0
tc qdisc add dev "$DEV" handle ffff: ingress
tc filter add dev "$DEV" parent ffff: protocol ip prio 2 u32 match u32 0 0 action police rate 30mbit burst 384k drop
tc filter add dev "$DEV" parent ffff: protocol ipv6 prio 3 u32 match u32 0 0 action police rate 30mbit burst 384k drop
# Verify: tc can accept the calls and the cap still be gone (e.g. NetworkManager reset eth0 in between). Fail
# the unit then, so Restart=on-failure tries again instead of reporting success with no cap.
if ! has_cap; then
  echo "dl-cap: police filters for ip and ipv6 not found on $DEV after applying; failing so systemd retries" >&2
  exit 1
fi
echo "dl-cap: 30 Mbit/s inbound cap applied on $DEV"
