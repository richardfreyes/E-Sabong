#!/usr/bin/env bash
# Origin-side steps of the Cloudflare cutover (docs/security/cloudflare.md §9 B and C), run on the
# VPS as the deploy user. Each step backs up first, validates before switching, and can be undone.
#
#   cloudflare-origin.sh backup                       copy every file the steps touch to ~/esb-jobs/cf-backup-<ts>/
#   cloudflare-origin.sh app-config "<ip>[,<ip>...]"  set CUSTOM_DOMAIN_GATEWAY_IPS in prod + UAT .env, recreate
#                                                     api + worker, re-check custom domains, show their status
#   cloudflare-origin.sh gateway <candidate Caddyfile> install the Cloudflare trust gateway config, validate, `up`
#   cloudflare-origin.sh verify                       smoke + client-address checks through the local gateway
#   cloudflare-origin.sh rollback-gateway <backup dir> restore the gateway files from a backup and `up`
#   cloudflare-origin.sh rollback-app <backup dir>     restore both .env files from a backup and recreate api + worker
#
# Restarts: app-config recreates api + worker in each environment (~30-60 s each); gateway and
# rollback-gateway recreate the shared gateway (~1-3 s on 80/443 for BOTH environments; WebSockets
# reconnect once). Run only with owner approval and never during a live event.
set -euo pipefail
ROOT=/opt/esabong
GW="$ROOT/gateway/infrastructure/gateway"
GWS="$ROOT/gateway/infrastructure/scripts/gateway.sh"
REPO_GW="$ROOT/prod/infrastructure/gateway"     # main's entrypoint.sh + docker-compose.yml (prod-20261120.1)
die() { echo "cloudflare-origin: $*" >&2; exit 1; }
say() { echo "==> $*"; }

set_env() {  # set_env <file> <KEY> <value>: replace or append, keep mode 600
  local f="$1" k="$2" v="$3" tmp
  tmp="$(mktemp "${f}.XXXX")"
  awk -v k="$k" -v v="$v" -F= 'BEGIN{done=0} $1==k {print k"="v; done=1; next} {print} END{if(!done) print k"="v}' "$f" > "$tmp"
  chmod 600 "$tmp"; mv "$tmp" "$f"
}

wait_healthy() {  # wait_healthy <container>
  for _ in $(seq 1 60); do
    [ "$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$1" 2>/dev/null)" = healthy ] && return 0
    sleep 5
  done
  die "$1 is not healthy after 5 minutes"
}

cmd_backup() {
  local dir; dir="$HOME/esb-jobs/cf-backup-$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$dir"; chmod 700 "$dir"
  cp -p "$ROOT/prod/.env" "$dir/prod.env"; cp -p "$ROOT/uat/.env" "$dir/uat.env"
  for f in Caddyfile entrypoint.sh docker-compose.yml .env; do cp -p "$GW/$f" "$dir/gateway.$f"; done
  docker exec esabong-gateway-gateway-1 cat /etc/caddy/Caddyfile > "$dir/gateway.running-Caddyfile"
  (cd "$dir" && sha256sum -- * > SHA256SUMS); chmod 600 "$dir"/*
  echo "$dir"
}

cmd_app_config() {
  local ips="${1:?usage: app-config <ip>[,<ip>...]}"
  python3 -c 'import ipaddress,sys; [ipaddress.ip_address(x.strip()) for x in sys.argv[1].split(",")]' "$ips" \
    || die "not a comma-separated IP list: $ips"
  for env in prod uat; do
    say "$env: CUSTOM_DOMAIN_GATEWAY_IPS=$ips"
    set_env "$ROOT/$env/.env" CUSTOM_DOMAIN_GATEWAY_IPS "$ips"
    (cd "$ROOT/$env" && docker compose up -d api worker)
    wait_healthy "esabong-$env-api-1"; wait_healthy "esabong-$env-worker-1"
    (cd "$ROOT/$env" && docker compose exec -T api printenv CUSTOM_DOMAIN_GATEWAY_IPS < /dev/null)
    say "$env: re-checking custom domains now"
    (cd "$ROOT/$env" && docker compose exec -T worker celery -A app.workers.celery_app call domains.recheck_pending < /dev/null) >/dev/null
    sleep 20
    (cd "$ROOT/$env" && docker compose exec -T postgres sh -c "psql -U \"\$POSTGRES_USER\" -d esabong -At -F' ' -c \"select kind, verification_status, ssl_status, coalesce(connection_error,'-') from tenant_domains where kind='custom' and enabled\"" < /dev/null)
  done
  echo "Every custom domain above must read: custom verified active -"
}

cmd_gateway() {
  local cand="${1:?usage: gateway <candidate Caddyfile>}" ranges
  [ -f "$cand" ] || die "candidate not found: $cand"
  grep -q 'trusted_proxies static {$GATEWAY_TRUSTED_PROXIES}' "$cand" || die "candidate lacks the trust setting"
  [ -x "$HOME/esb-jobs/cloudflare-ips.sh" ] || die "copy infrastructure/gateway/cloudflare-ips.sh to ~/esb-jobs/ first"
  ranges="$("$HOME/esb-jobs/cloudflare-ips.sh")"; ranges="${ranges#GATEWAY_TRUSTED_PROXIES=}"
  [ "$(wc -w <<< "$ranges")" -ge 12 ] || die "too few Cloudflare ranges"
  say "installing gateway files in place (same inodes, so the running container sees them)"
  cat "$cand" > "$GW/Caddyfile"
  cat "$REPO_GW/entrypoint.sh" > "$GW/entrypoint.sh"
  cat "$REPO_GW/docker-compose.yml" > "$GW/docker-compose.yml"
  set_env "$GW/.env" GATEWAY_TRUSTED_PROXIES "$ranges"
  set_env "$GW/.env" GATEWAY_CLIENT_IP_HEADER CF-Connecting-IP
  say "validating (no change to the running gateway yet)"
  "$GWS" validate || die "validation failed: restore with rollback-gateway <backup dir>"
  say "recreating the gateway (~1-3 s on 80/443)"
  "$GWS" up
  docker exec esabong-gateway-gateway-1 sh -c 'grep -c read_header /etc/caddy/Caddyfile; printenv GATEWAY_CLIENT_IP_HEADER'
}

cmd_verify() {
  local fail=0 h p code
  for h in swcmain183.live uat.swcmain183.live swcmain185.live; do
    for p in / /api/ready /api/v1/public/tenant; do
      code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 15 --resolve "$h:443:127.0.0.1" "https://$h$p" || true)"
      printf '  %-22s %-24s %s\n' "$h" "$p" "$code"; [ "$code" = 200 ] || fail=1
    done
    code="$(curl -sk -o /dev/null -w '%{http_code}' --http1.1 --max-time 6 --resolve "$h:443:127.0.0.1" \
      -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' \
      -H 'Sec-WebSocket-Key: ZXNhYm9uZy1tb25pdG9yIQ==' "https://$h/api/ws" || true)"  # 101 keeps the socket open until --max-time
    printf '  %-22s %-24s %s\n' "$h" "/api/ws upgrade" "$code"; [ "$code" = 101 ] || fail=1
  done
  local rid; rid="cfverify$(date +%s)abc"
  curl -sk -o /dev/null --max-time 10 --resolve "swcmain183.live:443:127.0.0.1" -H "X-Request-ID: $rid" \
    -H 'X-Forwarded-For: 203.0.113.77' -H 'CF-Connecting-IP: 203.0.113.78' https://swcmain183.live/api/v1/public/tenant
  sleep 2
  local seen; seen="$(cd "$ROOT/prod" && docker compose logs --since 2m --no-log-prefix api 2>/dev/null | grep "$rid" | grep -oE '"client_ip": "[^"]+"' | head -1)"
  echo "  forged headers from a non-Cloudflare peer -> API saw $seen (must not be 203.0.113.x)"
  case "$seen" in *203.0.113.*|"") fail=1 ;; esac
  [ "$fail" = 0 ] && echo "VERIFY PASSED" || { echo "VERIFY FAILED"; return 1; }
}

cmd_rollback_gateway() {
  local dir="${1:?usage: rollback-gateway <backup dir>}"
  for f in Caddyfile entrypoint.sh docker-compose.yml .env; do cat "$dir/gateway.$f" > "$GW/$f"; done
  "$GWS" up
  echo "gateway restored from $dir"
}

cmd_rollback_app() {
  local dir="${1:?usage: rollback-app <backup dir>}"
  for env in prod uat; do
    cat "$dir/$env.env" > "$ROOT/$env/.env"
    (cd "$ROOT/$env" && docker compose up -d api worker)
  done
  echo "app config restored from $dir"
}

case "${1:-}" in
  backup) cmd_backup ;;
  app-config) shift; cmd_app_config "$@" ;;
  gateway) shift; cmd_gateway "$@" ;;
  verify) cmd_verify ;;
  rollback-gateway) shift; cmd_rollback_gateway "$@" ;;
  rollback-app) shift; cmd_rollback_app "$@" ;;
  *) sed -n '2,16p' "$0"; exit 2 ;;
esac
