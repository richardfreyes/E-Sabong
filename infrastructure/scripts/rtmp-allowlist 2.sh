#!/usr/bin/env bash
# Allow RTMP ingest (published by the mediamtx container) only from the broadcaster's
# addresses. Docker-published ports bypass UFW, so the rule lives in Docker's DOCKER-USER chain.
#
#   sudo RTMP_ALLOWED_SOURCES="203.0.113.5,198.51.100.0/24" infrastructure/scripts/rtmp-allowlist.sh apply
#   sudo infrastructure/scripts/rtmp-allowlist.sh remove
#   sudo infrastructure/scripts/rtmp-allowlist.sh show
#
# STREAM_RTMP_PORT selects the HOST port (production 1935, UAT 1945 in the Contabo layout).
# Each port gets its own chain (ESABONG-RTMP-<port>), so applying or removing one
# environment's allowlist never changes the other's. The match uses the connection's original
# destination port: in DOCKER-USER the packet is already DNAT-ed to the container port (1935
# for both environments), so --dport would hit both.
#
# Re-applied at boot by infrastructure/systemd/esabong-rtmp-allowlist@.service (reads
# /opt/esabong/<stack>/.rtmp-allowlist.env). Not needed when RTMP is closed
# (STREAM_RTMP_BIND=127.0.0.1, WHIP-only ingest). WebRTC and HTTPS stay open to everyone.
set -euo pipefail
PORT="${STREAM_RTMP_PORT:-1935}"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] || { echo "STREAM_RTMP_PORT must be a port number" >&2; exit 2; }
CHAIN="ESABONG-RTMP-$PORT"
MATCH=(-p tcp -m conntrack --ctorigdstport "$PORT" --ctdir ORIGINAL)

ensure_chain() {
  iptables -N "$CHAIN" 2>/dev/null || iptables -F "$CHAIN"
  iptables -C DOCKER-USER "${MATCH[@]}" -j "$CHAIN" 2>/dev/null \
    || iptables -I DOCKER-USER 1 "${MATCH[@]}" -j "$CHAIN"
}

case "${1:-}" in
  apply)
    [ -n "${RTMP_ALLOWED_SOURCES:-}" ] || { echo "Set RTMP_ALLOWED_SOURCES (comma-separated IPs/CIDRs)" >&2; exit 1; }
    iptables -L DOCKER-USER -n >/dev/null 2>&1 || { echo "DOCKER-USER chain missing: start Docker first" >&2; exit 1; }
    ensure_chain
    iptables -A "$CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    IFS=',' read -ra SOURCES <<< "$RTMP_ALLOWED_SOURCES"
    for src in "${SOURCES[@]}"; do
      iptables -A "$CHAIN" -s "$(echo "$src" | tr -d ' ')" -j RETURN
    done
    iptables -A "$CHAIN" -j DROP
    echo "RTMP $PORT allowed only from: $RTMP_ALLOWED_SOURCES"
    ;;
  remove)
    iptables -D DOCKER-USER "${MATCH[@]}" -j "$CHAIN" 2>/dev/null || true
    iptables -F "$CHAIN" 2>/dev/null || true
    iptables -X "$CHAIN" 2>/dev/null || true
    echo "RTMP allowlist removed"
    ;;
  show)
    iptables -L DOCKER-USER -n --line-numbers
    iptables -L "$CHAIN" -n --line-numbers 2>/dev/null || echo "(no $CHAIN chain)"
    ;;
  *)
    echo "usage: $0 apply|remove|show" >&2
    exit 2
    ;;
esac
