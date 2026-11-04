#!/usr/bin/env bash
# Shared HTTPS gateway of the Contabo layout (infrastructure/gateway/). Run from a dedicated
# gateway checkout (e.g. /opt/esabong/gateway for a UAT-only first deployment) as the deploy user.
# GATEWAY_MODE=uat-only creates only the UAT link and exposes only the UAT host; dual mode
# enables production routing and needs separate approval. The gateway is its own Compose project
# (esabong-gateway): starting, stopping or reloading it never touches UAT or production, and
# deploying either environment never touches it.
#
#   infrastructure/scripts/gateway.sh networks   create the per-environment private networks
#                                                (esabong-prod-gateway, esabong-uat-gateway;
#                                                internal = no egress). Idempotent.
#   infrastructure/scripts/gateway.sh validate   check .env and the Caddyfile (no restart)
#   infrastructure/scripts/gateway.sh up         networks + validate + start/update the gateway
#   infrastructure/scripts/gateway.sh reload     validate, then apply a changed Caddyfile without
#                                                dropping connections. A changed .env (allowlist,
#                                                token) needs `up`, which recreates the container
#                                                (a few seconds without HTTPS for both environments).
#   infrastructure/scripts/gateway.sh ps|logs|config|down
set -euo pipefail
cd "$(dirname "$0")/../gateway"

die() { echo "gateway: $*" >&2; exit 1; }
. ../../db/scripts/lib-env.sh

[ -f .env ] || die "infrastructure/gateway/.env missing (copy .env.example and fill it in)"
[ "$(stat -c %a .env 2>/dev/null || stat -f %Lp .env)" = "600" ] || die "infrastructure/gateway/.env must be mode 600"
OVERRIDES="$(shell_overrides .env | tr '\n' ' ')"
[ -z "$OVERRIDES" ] || die "exported shell variables override .env: ${OVERRIDES}(unset them first)"

# Read from the guarded file, never from an exported override. Existing installations
# without this setting retain their dual routing; new templates default to UAT only.
MODE="$(awk -F= '/^GATEWAY_MODE=/ {v=$2} END {print v}' .env)"
COMPOSE_ARGS=(-f docker-compose.yml)
case "$MODE" in
  uat-only) COMPOSE_ARGS+=(-f docker-compose.uat-only.yml) ;;
  dual|'') ;;
  *) die "GATEWAY_MODE must be uat-only or dual" ;;
esac
compose() { docker compose --env-file .env "${COMPOSE_ARGS[@]}" "$@"; }

networks() {
  local stack net internal
  local stacks="prod uat"
  [ "$MODE" != uat-only ] || stacks=uat
  for stack in $stacks; do
    net="esabong-$stack-gateway"
    if ! docker network inspect "$net" > /dev/null 2>&1; then
      docker network create --internal --label "com.esabong.stack=$stack" "$net" > /dev/null
      echo "created network $net (internal)"
    fi
    internal="$(docker network inspect --format '{{.Internal}}' "$net")"
    [ "$internal" = "true" ] || die "network $net exists but is not internal; recreate it with --internal"
  done
}

validate() {
  local out
  compose config --quiet
  out="$(compose run --rm --no-deps -T gateway validate 2>&1)" || { printf '%s\n' "$out" | tail -n 20 >&2; die "Caddyfile or UAT access settings are invalid"; }
  echo "gateway configuration valid"
}

case "${1:-}" in
  networks) networks ;;
  validate) networks; validate ;;
  up)
    networks
    validate
    compose up -d --wait
    compose ps
    ;;
  reload)
    validate
    compose exec -T gateway /bin/sh /etc/caddy/entrypoint.sh reload
    echo "gateway reloaded"
    ;;
  ps) compose ps ;;
  logs) shift; compose logs "$@" ;;
  config) compose config ;;
  down) compose down ;;
  *)
    echo "usage: $0 networks|validate|up|reload|ps|logs|config|down" >&2
    exit 2
    ;;
esac
