#!/usr/bin/env bash
# Cloudflare's published proxy ranges, validated, for the gateway trust setting and firewall sets.
#
#   infrastructure/gateway/cloudflare-ips.sh            print GATEWAY_TRUSTED_PROXIES=<ranges>
#   infrastructure/gateway/cloudflare-ips.sh --nft4     print IPv4 ranges as an nftables element list
#   infrastructure/gateway/cloudflare-ips.sh --check    compare with GATEWAY_TRUSTED_PROXIES in ./.env (exit 1 if different)
#
# Source: https://api.cloudflare.com/client/v4/ips (public, no credentials). Refuses an answer that is
# not a successful API response, has too few ranges, or contains anything that is not a public CIDR,
# so a bad download can never make the gateway trust everyone. Changes nothing by itself.
set -euo pipefail
URL="https://api.cloudflare.com/client/v4/ips"
die() { echo "cloudflare-ips: $*" >&2; exit 1; }
json="$(curl -fsS --max-time 20 "$URL")" || die "download failed"
ranges="$(printf '%s' "$json" | python3 -c '
import ipaddress, json, sys
d = json.load(sys.stdin)
if not d.get("success"):
    sys.exit("API answer not successful")
v4, v6 = d["result"]["ipv4_cidrs"], d["result"]["ipv6_cidrs"]
if len(v4) < 8 or len(v6) < 4:
    sys.exit("suspiciously few ranges")
for c in v4 + v6:
    n = ipaddress.ip_network(c, strict=True)
    if not n.is_global or n.prefixlen < 8:
        sys.exit(f"refusing range {c}")
print(" ".join(v4), "|", " ".join(v6))
')" || die "invalid range list"
v4="${ranges%% |*}"; v6="${ranges##*| }"
case "${1:-}" in
  "") echo "GATEWAY_TRUSTED_PROXIES=$v4 $v6" ;;
  --nft4) echo "{ $(echo "$v4" | sed 's/ /, /g') }" ;;
  --check)
    current="$(awk -F= '/^GATEWAY_TRUSTED_PROXIES=/ {sub(/^GATEWAY_TRUSTED_PROXIES=/, ""); print}' .env 2>/dev/null)"
    want="$v4 $v6"
    if [ "$(tr ' ' '\n' <<< "$current" | sort)" = "$(tr ' ' '\n' <<< "$want" | sort)" ]; then echo "up to date"; else echo "differs from Cloudflare's current list"; exit 1; fi ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
