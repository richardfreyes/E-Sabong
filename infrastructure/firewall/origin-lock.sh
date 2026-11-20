#!/usr/bin/env bash
# Origin lock for the Cloudflare cutover (esabong-origin-lock.nft). Manages ONLY the nftables table
# inet esabong_origin_lock; never the host table, the edge guard, Docker or Fail2Ban.
#
#   sudo ./origin-lock.sh check        parse the rules file (nft -c), change nothing
#   sudo ./origin-lock.sh apply        load in OBSERVE mode with Cloudflare's current ranges
#   sudo ./origin-lock.sh status       mode, counters, direct sources seen in the last hour
#   sudo ./origin-lock.sh allow <cidr> let an admin/monitoring IPv4 source reach 80/443 directly
#   sudo ./origin-lock.sh enforce      drop direct (non-Cloudflare) connections to 80/443
#   sudo ./origin-lock.sh observe      stop dropping at once (counting continues)
#   sudo ./origin-lock.sh refresh      reload Cloudflare's published ranges (keeps the mode)
#   sudo ./origin-lock.sh rollback     remove the table
#
# apply/enforce need owner approval, and enforce only after the Cloudflare cutover is verified and
# no verified custom domain points straight at the VPS (see the rules file).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULES="${RULES:-$HERE/esabong-origin-lock.nft}"
CF_IPS="${CF_IPS:-$HERE/../gateway/cloudflare-ips.sh}"
TABLE="inet esabong_origin_lock"
NFT=/usr/sbin/nft
die() { echo "ERROR: $*" >&2; exit 1; }
need_root() { [ "$(id -u)" -eq 0 ] || die "run with sudo"; }
have_table() { "$NFT" list table $TABLE >/dev/null 2>&1; }
enforcing() { "$NFT" list chain $TABLE direct 2>/dev/null | grep -qw drop; }

fill_cloudflare() {
  local line v4 v6
  line="$("$CF_IPS")" || die "could not fetch Cloudflare's ranges; nothing changed"
  line="${line#GATEWAY_TRUSTED_PROXIES=}"
  v4="$(tr ' ' '\n' <<< "$line" | grep -v ':' | paste -sd, -)"
  v6="$(tr ' ' '\n' <<< "$line" | grep ':' | paste -sd, -)"
  [ -n "$v4" ] && [ -n "$v6" ] || die "empty range list; nothing changed"
  "$NFT" flush set $TABLE cloudflare_v4
  "$NFT" flush set $TABLE cloudflare_v6
  "$NFT" add element $TABLE cloudflare_v4 "{ $v4 }"
  "$NFT" add element $TABLE cloudflare_v6 "{ $v6 }"
  echo "Cloudflare ranges loaded: $(tr ',' '\n' <<< "$v4" | wc -l | tr -d ' ') IPv4, $(tr ',' '\n' <<< "$v6" | wc -l | tr -d ' ') IPv6"
}

case "${1:-}" in
  check) need_root; "$NFT" -c -f "$RULES"; echo "OK: $RULES parses (nothing loaded)" ;;
  apply)
    need_root; "$NFT" -c -f "$RULES"; "$NFT" -f "$RULES"; fill_cloudflare
    echo "Loaded $TABLE in OBSERVE mode (nothing is dropped). Watch: sudo $0 status" ;;
  refresh) need_root; have_table || die "apply first"; fill_cloudflare ;;
  allow)
    need_root; [ -n "${2:-}" ] || die "usage: $0 allow <ipv4/cidr>"
    "$NFT" add element $TABLE allowed_v4 "{ $2 }"; echo "Allowed $2 directly (also record it in $RULES)." ;;
  enforce)
    need_root; have_table || die "apply first"
    "$NFT" list set $TABLE cloudflare_v4 | grep -q "192.0.2.255" && die "Cloudflare ranges not loaded"
    enforcing || "$NFT" add rule $TABLE direct drop
    echo "ENFORCING: direct HTTP/HTTPS to the origin is dropped. Undo at once: sudo $0 observe" ;;
  observe)
    need_root; have_table || die "not loaded"
    "$NFT" flush chain $TABLE direct
    "$NFT" add rule $TABLE direct counter comment '"direct to origin, not via Cloudflare (observe mode: counted only)"'
    echo "OBSERVE mode: counting only." ;;
  status)
    need_root
    if ! have_table; then echo "$TABLE: not loaded"; exit 0; fi
    if enforcing; then echo "mode: ENFORCE"; else echo "mode: observe"; fi
    "$NFT" list chain $TABLE direct | grep -E "counter" | sed 's/^[[:space:]]*/  /'
    echo "  direct IPv4 sources in the last hour: $("$NFT" -j list set $TABLE direct_seen_v4 | grep -o '"val"' | wc -l | tr -d ' ')" ;;
  rollback) need_root; "$NFT" delete table $TABLE 2>/dev/null || true; have_table && die "table still present"; echo "Removed $TABLE." ;;
  *) sed -n '2,15p' "$0"; exit 2 ;;
esac
