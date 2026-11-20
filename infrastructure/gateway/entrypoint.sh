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

# Upstream DDoS proxy trust (docs/security/README.md). Empty = trust nobody: the client address is
# always the TCP peer. Only public CIDRs of the proxy provider are accepted: trusting everyone, or
# a private/Docker range (IPv6 clients arrive from docker-proxy's 172.x address), would let any
# client choose its own address and dodge or poison the API's per-address limits.
for cidr in ${GATEWAY_TRUSTED_PROXIES:-}; do
  case "$cidr" in
    0.0.0.0/0 | ::/0 | 0.0.0.0 | ::) die "GATEWAY_TRUSTED_PROXIES must not contain $cidr (every client could forge its address)" ;;
  esac
  if printf '%s' "$cidr" | grep -Eq '^(10\.|127\.|169\.254\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.|f[cd][0-9a-f]{2}:|fe80:|::1)'; then
    die "GATEWAY_TRUSTED_PROXIES must not contain private or loopback range $cidr"
  fi
done
[ -n "${GATEWAY_TRUSTED_PROXIES:-}" ] || GATEWAY_TRUSTED_PROXIES=192.0.2.255/32   # TEST-NET: trusts nobody
case "${GATEWAY_CLIENT_IP_HEADER:=X-Forwarded-For}" in
  X-Forwarded-For | CF-Connecting-IP | True-Client-IP) ;;
  *) die "GATEWAY_CLIENT_IP_HEADER must be X-Forwarded-For, CF-Connecting-IP or True-Client-IP" ;;
esac
export GATEWAY_TRUSTED_PROXIES GATEWAY_CLIENT_IP_HEADER
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
