#!/usr/bin/env bash
# E-Sabong edge guard (per-source connection limits on 80/443). Manages ONLY the nftables table
# inet esabong_edge_guard (esabong-edge-guard.nft); never the host table, Docker or Fail2Ban.
#
#   sudo ./edge-guard.sh check        parse the rules file (nft -c), change nothing
#   sudo ./edge-guard.sh apply        load the table in OBSERVE mode (counts, never drops)
#   sudo ./edge-guard.sh enforce      reset connections from sources over a limit
#   sudo ./edge-guard.sh observe      back to counting only (immediate)
#   sudo ./edge-guard.sh status       mode, counters, number of current offenders
#   sudo ./edge-guard.sh offenders    addresses over a limit in the last 15 minutes
#   sudo ./edge-guard.sh exempt <cidr>  never limit this address/range (until reload)
#   sudo ./edge-guard.sh rollback     remove the table (= the state before apply)
#
# apply/enforce change live traffic handling: run them only with owner approval, after reading
# `status` in observe mode for at least one busy event (docs/security/README.md).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULES="${RULES:-$HERE/esabong-edge-guard.nft}"
TABLE="inet esabong_edge_guard"
NFT=/usr/sbin/nft

die() { echo "ERROR: $*" >&2; exit 1; }
need_root() { [ "$(id -u)" -eq 0 ] || die "run with sudo"; }
have_table() { "$NFT" list table $TABLE >/dev/null 2>&1; }
enforcing() { "$NFT" list chain $TABLE over_limit 2>/dev/null | grep -q "reject"; }

case "${1:-}" in
  check)
    need_root; "$NFT" -c -f "$RULES"; echo "OK: $RULES parses (nothing loaded). sha256: $(sha256sum "$RULES" | cut -d' ' -f1)" ;;
  apply)
    need_root; "$NFT" -c -f "$RULES"; "$NFT" -f "$RULES"
    echo "Loaded $TABLE in OBSERVE mode (nothing is dropped). Watch: sudo $0 status" ;;
  enforce)
    need_root; have_table || die "apply first"
    enforcing || "$NFT" add rule $TABLE over_limit reject with tcp reset
    echo "ENFORCING: sources over a limit now get a TCP reset. Undo at once with: sudo $0 observe" ;;
  observe)
    need_root; have_table || die "not loaded"
    "$NFT" flush chain $TABLE over_limit
    "$NFT" add rule $TABLE over_limit add @offenders_v4 '{ ip saddr }'
    "$NFT" add rule $TABLE over_limit counter comment '"over a limit (observe mode: counted only)"'
    echo "OBSERVE mode: counting only." ;;
  status)
    need_root
    if ! have_table; then echo "$TABLE: not loaded"; exit 0; fi
    if enforcing; then echo "mode: ENFORCE"; else echo "mode: observe"; fi
    "$NFT" list chain $TABLE web_new | grep -E "counter|comment" | sed 's/^[[:space:]]*/  /'
    "$NFT" list chain $TABLE over_limit | grep counter | sed 's/^[[:space:]]*/  /'
    # Elements of a timeout set render as {"elem": {"val": <address>, ...}}: count the values.
    echo "  offenders (15 min): $("$NFT" -j list set $TABLE offenders_v4 | grep -o '"val"' | wc -l | tr -d ' ')" ;;
  offenders)
    need_root; "$NFT" list set $TABLE offenders_v4 ;;
  exempt)
    need_root; [ -n "${2:-}" ] || die "usage: $0 exempt <ip/cidr>"
    "$NFT" add element $TABLE exempt_v4 "{ $2 }"; echo "Exempted $2 (also add it to $RULES so a reload keeps it)." ;;
  rollback)
    need_root; "$NFT" delete table $TABLE 2>/dev/null || true
    have_table && die "table still present"; echo "Removed $TABLE." ;;
  *) sed -n '2,16p' "$0"; exit 2 ;;
esac
