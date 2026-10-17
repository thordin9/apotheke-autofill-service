#!/bin/bash
# Write the curl header file from AUTOFILL_TOKEN in the service env file, without echoing the token.
# Usage: sudo bench/make-auth-header.sh [/opt/autofill/service.env] [/opt/autofill/auth.hdr]
ENV_FILE=${1:-/opt/autofill/service.env}; HDR=${2:-/opt/autofill/auth.hdr}
umask 077
T=$(sed -n 's/^AUTOFILL_TOKEN=//p' "$ENV_FILE")
[ -n "$T" ] || { echo "AUTOFILL_TOKEN not found in $ENV_FILE" >&2; exit 1; }
printf 'Authorization: Bearer %s\n' "$T" > "$HDR"; unset T; chmod 600 "$HDR"; echo "wrote $HDR (mode 600)"
