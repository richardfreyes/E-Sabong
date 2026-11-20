#!/usr/bin/env bash
# Is the origin address discoverable? Run from OUTSIDE the server. The address is read from a
# private file and never printed (output shows <origin>), so the check can run in shared logs.
#
#   ORIGIN_IP_FILE=~/.esabong-origin-ip infrastructure/scripts/origin-exposure-check.sh
#
# The file holds the origin IPv4 (and optionally IPv6) addresses, one per line, mode 600.
# Checks: every public hostname (A/AAAA via two public resolvers and Cloudflare's own
# nameserver) never returns the origin; TCP ports that must be closed; the certificate the
# origin presents without SNI. Exit 1 when anything is exposed. Read-only: one connection per port.
set -uo pipefail
file="${ORIGIN_IP_FILE:-$HOME/.esabong-origin-ip}"
[ -r "$file" ] || { echo "missing $file (one origin address per line, mode 600)"; exit 2; }
perm="$(stat -c %a "$file" 2>/dev/null || stat -f %Lp "$file")"
[ "$perm" = 600 ] || [ "$perm" = 400 ] || { echo "$file must be mode 600"; exit 2; }
ORIGINS=()   # portable (macOS bash 3.2 has no mapfile)
while IFS= read -r line; do line="${line// /}"; [ -n "$line" ] && [ "${line#\#}" = "$line" ] && ORIGINS+=("$line"); done < "$file"
[ "${#ORIGINS[@]}" -gt 0 ] || { echo "no address in $file"; exit 2; }
HOSTS=(swcmain183.live www.swcmain183.live uat.swcmain183.live swcmain185.live www.swcmain185.live)
# Ports that must not answer on the origin once the tunnel is live (22 is reported, not failed).
CLOSED=(80 443 1935 1936 1945 1946 8189 8190 2019 5432 6379 8000 8889 9997)
bad=0
mask() { local s="$1" o; for o in "${ORIGINS[@]}"; do s="${s//$o/<origin>}"; done; printf '%s' "$s"; }
is_origin() { local v o; for v in "$@"; do for o in "${ORIGINS[@]}"; do [ "$v" = "$o" ] && return 0; done; done; return 1; }

echo "== DNS (A/AAAA) for ${#HOSTS[@]} hostnames"
for h in "${HOSTS[@]}"; do
  for ns in 1.1.1.1 8.8.8.8 "$(dig +short NS "${h#www.}" | head -n1)"; do
    [ -n "$ns" ] || continue
    ans=()
    while IFS= read -r line; do [ -n "$line" ] && ans+=("$line"); done < <(dig +short A "$h" @"$ns"; dig +short AAAA "$h" @"$ns")
    if [ "${#ans[@]}" -gt 0 ] && is_origin "${ans[@]}"; then echo "EXPOSED  $h @$ns -> $(mask "${ans[*]}")"; bad=1; fi
  done
done
[ $bad = 0 ] && echo "ok: no hostname resolves to the origin"

for o in "${ORIGINS[@]}"; do
  [[ "$o" == *:* ]] && fam=-6 || fam=-4
  echo "== origin $( [ $fam = -6 ] && echo IPv6 || echo IPv4 ) <origin>: TCP ports"
  for p in "${CLOSED[@]}"; do
    if nc $fam -z -G 3 "$o" "$p" >/dev/null 2>&1 || nc $fam -z -w 3 "$o" "$p" >/dev/null 2>&1; then echo "OPEN     $p (must be closed)"; bad=1; fi
  done
  (nc $fam -z -G 3 "$o" 22 >/dev/null 2>&1 || nc $fam -z -w 3 "$o" 22 >/dev/null 2>&1) && echo "note     22 open (restrict to admin addresses)"
  for p in 443 1936; do
    target="$o"; [ $fam = -6 ] && target="[$o]"
    subj="$(echo | openssl s_client -connect "$target:$p" 2>/dev/null | openssl x509 -noout -subject 2>/dev/null)"
    [ -n "$subj" ] && { echo "EXPOSED  certificate on $p without SNI: $(mask "$subj")"; bad=1; }
  done
done
[ $bad = 0 ] && echo "RESULT: origin not discoverable by DNS, open ports or certificates" || echo "RESULT: EXPOSED (see above)"
exit $bad
