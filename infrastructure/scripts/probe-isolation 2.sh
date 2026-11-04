#!/usr/bin/env bash
# Read-only network isolation probe for the Contabo layout. From a UAT container, tries to open
# TCP connections to every production container on every address it has (and the reverse).
# Expected on the server: every line "blocked". Run after both environments are up:
#
#   infrastructure/scripts/probe-isolation.sh
#
# Docker Engine on Linux drops traffic between different bridge networks. Some desktop
# runtimes (e.g. OrbStack) route between them, so a workstation result is not evidence.
set -euo pipefail

probe() {  # probe <from-container> <to-project>
  local from="$1" project="$2" name ip port fails=0
  for name in $(docker ps --filter "label=com.docker.compose.project=$project" --format '{{.Names}}'); do
    port="$(docker inspect -f '{{range $p, $_ := .Config.ExposedPorts}}{{$p}} {{end}}' "$name" | tr ' ' '\n' | grep -m1 tcp | cut -d/ -f1)"
    port="${port:-80}"
    for ip in $(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$name"); do
      if docker exec "$from" python3 -c "import socket,sys; socket.create_connection(('$ip', $port), timeout=3)" 2>/dev/null; then
        echo "REACHABLE  $from -> $name $ip:$port"; fails=$((fails + 1))
      else
        echo "blocked    $from -> $name $ip:$port"
      fi
    done
  done
  return "$fails"
}

docker inspect esabong-uat-api-1 esabong-prod-api-1 > /dev/null 2>&1 || { echo "both esabong-uat-api-1 and esabong-prod-api-1 must be running" >&2; exit 2; }
status=0
probe esabong-uat-api-1 esabong-prod || status=1
probe esabong-prod-api-1 esabong-uat || status=1
[ "$status" = 0 ] && echo "PASS: no cross-environment connection possible" || echo "FAIL: environments can reach each other (see REACHABLE lines)"
exit "$status"
