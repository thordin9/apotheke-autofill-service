#!/bin/sh
# Cap ALL incoming traffic on eth0 at 30 Mbit/s, with no exemptions (LAN, SSH and service uploads included).
# The L4T R35 kernel has no IFB module, so this polices ingress: packets over the rate are dropped and TCP
# backs off to about 30 Mbit/s. Install as /usr/local/sbin/dl-cap.sh (mode 755); started by dl-cap.service.
# Usage: dl-cap.sh        apply (idempotent)
#        dl-cap.sh stop   remove
DEV=${DEV:-eth0}
tc qdisc del dev $DEV ingress 2>/dev/null
[ "$1" = "stop" ] && exit 0
tc qdisc add dev $DEV handle ffff: ingress
tc filter add dev $DEV parent ffff: protocol ip prio 2 u32 match u32 0 0 action police rate 30mbit burst 384k drop
tc filter add dev $DEV parent ffff: protocol ipv6 prio 3 u32 match u32 0 0 action police rate 30mbit burst 384k drop
