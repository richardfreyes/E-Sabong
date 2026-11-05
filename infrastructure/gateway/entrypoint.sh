#!/bin/sh
# Gateway entrypoint: checks the UAT access settings, fills address-list defaults, derives the
# UAT sign-in hash, then runs, validates or reloads Caddy.   entrypoint.sh run|validate|reload
set -eu

die() { echo "gateway: $*" >&2; exit 64; }

printf '%s' "${UAT_ACCESS_TOKEN:-}" | grep -Eq '^[A-Za-z0-9]{32,}$' \
  || die "UAT_ACCESS_TOKEN must be 32+ letters/digits (openssl rand -hex 24)"
case "${UAT_ACCESS_TOKEN}" in CHANGE_ME*) die "UAT_ACCESS_TOKEN is still a placeholder" ;; esac

# The UAT site is public; this list is not used to admit visitors. Private ranges stay
# rejected so a proxy address (Docker shows outside clients as 172.x) can never be trusted.
for cidr in ${UAT_ALLOWED_IPS:-}; do
  case "$cidr" in
    0.0.0.0/0 | ::/0 | 0.0.0.0 | ::) die "UAT_ALLOWED_IPS must not contain $cidr (that opens UAT to everyone)" ;;
  esac
  if printf '%s' "$cidr" | grep -Eq '^(10\.|127\.|169\.254\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.|f[cd][0-9a-f]{2}:|fe80:|::1)'; then
    die "UAT_ALLOWED_IPS must not contain private or loopback range $cidr"
  fi
done
[ -n "${UAT_ALLOWED_IPS:-}" ] || UAT_ALLOWED_IPS=192.0.2.255/32      # TEST-NET: matches nobody
[ -n "${PROD_OWNER_ALLOWED_IPS:-}" ] || PROD_OWNER_ALLOWED_IPS='0.0.0.0/0 ::/0'
# Password hash for the /__uat/access sign-in (never printed). Minimum bcrypt cost on purpose:
# the token is 128+ random bits, so the cost adds no security, while a high cost would let
# anyone hammering the sign-in burn the gateway's CPU, which also serves production.
UAT_ACCESS_HASH="$(caddy hash-password --bcrypt-cost 4 --plaintext "$UAT_ACCESS_TOKEN")"
export UAT_ALLOWED_IPS PROD_OWNER_ALLOWED_IPS UAT_ACCESS_HASH

CONFIG="--config /etc/caddy/Caddyfile --adapter caddyfile"
case "${1:-run}" in
  run) exec caddy run $CONFIG ;;
  validate) exec caddy validate $CONFIG ;;
  reload) exec caddy reload $CONFIG ;;
  *) die "usage: entrypoint.sh run|validate|reload" ;;
esac
