#!/usr/bin/env bash
# E-Sabong host firewall helper (Contabo VPS). Manages only nftables table inet esabong_host;
# never flushes the ruleset, never edits Docker's tables/chains or Fail2Ban's table, and
# never touches UFW, sshd, users, Docker group membership or the Docker daemon.
#
#   sudo ./firewall.sh inventory          read-only inventory + private backup (no rule change)
#   sudo ./firewall.sh check              dry-run parse of esabong-host.nft (nft -c, no change)
#   sudo ./firewall.sh apply [MINUTES]    arm automatic rollback (default 5 min), then load table
#   sudo ./firewall.sh confirm            cancel the armed rollback (after a NEW SSH session works)
#   sudo ./firewall.sh persist            install + enable esabong-host-firewall.service (reboot-safe)
#   sudo ./firewall.sh rollback           remove the table (and the boot unit), restoring today's state
#   sudo ./firewall.sh status             show table, rollback timer and Fail2Ban state
#
# apply/confirm/persist/rollback CHANGE the firewall: Stage 2 only, after explicit approval.
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULES="${RULES:-$HERE/esabong-host.nft}"
TABLE="inet esabong_host"
TIMER_UNIT="esabong-fw-rollback"
BOOT_UNIT="esabong-host-firewall.service"
INSTALL_DIR=/etc/esabong/firewall
BACKUP_ROOT=/root/esabong-firewall-backups
NFT=/usr/sbin/nft

die() { echo "ERROR: $*" >&2; exit 1; }
need_root() { [ "$(id -u)" -eq 0 ] || die "run with sudo"; }
have_table() { "$NFT" list table $TABLE >/dev/null 2>&1; }
latest_backup() { ls -1d "$BACKUP_ROOT"/2* 2>/dev/null | tail -1; }

# Every table except ours, without counters (stateless), for before/after comparison.
foreign_tables() {
  "$NFT" list tables | while read -r _ fam name; do
    [ "$fam $name" = "inet esabong_host" ] && continue
    "$NFT" -s list table "$fam" "$name"
  done
}

ssh_client_ip() { awk '{print $1}' <<<"${SSH_CLIENT:-${SUDO_SSH_CLIENT:-}}"; }

cmd_inventory() {
  need_root
  local stamp dir
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  dir="$BACKUP_ROOT/$stamp"
  mkdir -p "$dir"; chmod 700 "$BACKUP_ROOT" "$dir"
  {
    "$NFT" list ruleset                  > "$dir/nft-ruleset.nft"
    "$NFT" -s list ruleset               > "$dir/nft-ruleset-stateless.nft"
    foreign_tables                       > "$dir/foreign-tables.nft"
    iptables-save                        > "$dir/iptables-nft-v4.rules"
    ip6tables-save                       > "$dir/iptables-nft-v6.rules"
    { iptables-legacy-save 2>/dev/null; ip6tables-legacy-save 2>/dev/null; } > "$dir/iptables-legacy.rules" || true
    ufw status verbose                   > "$dir/ufw-status.txt" 2>&1 || true
    tar -C / -czf "$dir/ufw-config.tgz" etc/ufw etc/default/ufw
    cp -p /etc/nftables.conf "$dir/" 2>/dev/null || true
    systemctl is-active ufw nftables fail2ban docker > "$dir/services.txt" 2>&1 || true
    fail2ban-client status               > "$dir/fail2ban-status.txt" 2>&1 || true
    fail2ban-client status sshd          > "$dir/fail2ban-sshd.txt" 2>&1 || true
    tar -C / -czf "$dir/fail2ban-config.tgz" etc/fail2ban
    docker ps -a --no-trunc --format '{{.ID}} {{.Names}} {{.Image}} {{.Status}} {{.Ports}}' > "$dir/docker-ps.txt"
    docker network ls --no-trunc         > "$dir/docker-networks.txt"
    docker network ls -q | xargs -r docker network inspect \
      --format '{{.Name}} driver={{.Driver}} ipv6={{.EnableIPv6}} internal={{.Internal}} subnets={{range .IPAM.Config}}{{.Subnet}} {{end}}' \
      > "$dir/docker-network-detail.txt"
    docker volume ls                     > "$dir/docker-volumes.txt"
    docker info --format 'firewall={{.FirewallBackend}} ' > "$dir/docker-firewall.txt" 2>/dev/null || true
    ls -l /etc/docker/                   > "$dir/docker-etc.txt" 2>&1 || true
    ss -H -lntup                         > "$dir/listeners.txt"
    { ip -br addr; ip route; ip -6 route; } > "$dir/addresses-routes.txt"
    sysctl net.ipv4.ip_forward net.ipv6.conf.all.forwarding net.ipv6.conf.all.disable_ipv6 > "$dir/sysctl.txt"
    sshd -T 2>/dev/null | grep -E '^(port|listenaddress|permitrootlogin|passwordauthentication|pubkeyauthentication|kbdinteractiveauthentication|allowusers|allowgroups|maxauthtries) ' > "$dir/sshd-effective.txt" || true
    grep -hE 'dhcp4|dhcp6|accept-ra' /etc/netplan/*.yaml > "$dir/netplan-dhcp.txt" 2>/dev/null || true
  }
  (cd "$dir" && sha256sum -- * > SHA256SUMS)
  chmod -R go-rwx "$dir"

  echo "Backup (private, root-only): $dir"
  echo "== nft tables";            "$NFT" list tables
  echo "== base chains (hook/priority/policy)"
  "$NFT" list chains | grep -E 'table|hook' | sed 's/^[[:space:]]*//'
  echo "== DOCKER-USER (v4)";      iptables -S DOCKER-USER 2>&1 || true
  echo "== DOCKER-USER (v6)";      ip6tables -S DOCKER-USER 2>&1 || true
  echo "== policies";              iptables -S | grep -E '^-P'; ip6tables -S | grep -E '^-P'
  echo "== legacy iptables rules"; grep -c '^-A' "$dir/iptables-legacy.rules" || true
  echo "== esabong_host present";  have_table && echo yes || echo no
  echo "== ufw";                   head -3 "$dir/ufw-status.txt"
  echo "== services";              paste -sd' ' "$dir/services.txt"
  echo "== fail2ban jails";        grep -E 'Jail list' "$dir/fail2ban-status.txt" || cat "$dir/fail2ban-status.txt"
  echo "== fail2ban sshd counts (addresses not printed)"
  grep -E 'Currently (failed|banned)|Total (failed|banned)' "$dir/fail2ban-sshd.txt" || true
  echo "== docker containers";     wc -l < "$dir/docker-ps.txt"; cut -d' ' -f2- "$dir/docker-ps.txt"
  echo "== docker networks";       cat "$dir/docker-network-detail.txt"
  echo "== docker volumes";        tail -n +2 "$dir/docker-volumes.txt" | wc -l
  echo "== docker firewall";       cat "$dir/docker-firewall.txt"; echo; cat "$dir/docker-etc.txt"
  echo "== listeners";             cat "$dir/listeners.txt"
  echo "== sshd effective";        cat "$dir/sshd-effective.txt"
  echo "== netplan dhcp/ra";       cat "$dir/netplan-dhcp.txt"
  echo "== rules file sha256";     sha256sum "$RULES"
}

cmd_check() {
  need_root
  "$NFT" -c -f "$RULES"
  echo "OK: $RULES parses (nothing loaded). sha256: $(sha256sum "$RULES" | cut -d' ' -f1)"
}

cmd_apply() {
  need_root
  local minutes="${1:-5}" ip backup before after
  [[ "$minutes" =~ ^[0-9]+$ ]] && [ "$minutes" -ge 2 ] && [ "$minutes" -le 30 ] || die "MINUTES must be 2-30"
  [ -n "${EXPECTED_SHA256:-}" ] || die "set EXPECTED_SHA256 to the reviewed esabong-host.nft hash"
  [ "$(sha256sum "$RULES" | cut -d' ' -f1)" = "$EXPECTED_SHA256" ] || die "rules file differs from the reviewed version"
  have_table && die "table $TABLE already loaded — run rollback or confirm first"
  backup="$(latest_backup)"; [ -n "$backup" ] || die "no backup: run inventory first"
  [ $(( $(date +%s) - $(stat -c %Y "$backup") )) -lt 86400 ] || die "latest backup is older than 24 h: run inventory again"
  systemctl is-active --quiet fail2ban || die "fail2ban is not active"
  systemctl is-active --quiet docker   || die "docker is not active"
  ufw status | grep -q 'Status: inactive' || die "UFW is active: this design assumes UFW stays disabled"

  ip="$(ssh_client_ip)"
  [ -n "$ip" ] || die "pass this session's address: sudo SUDO_SSH_CLIENT=\"\$SSH_CLIENT\" EXPECTED_SHA256=<hash> $0 apply"
  grep -Eq "elements = \{[^}]*\b${ip//./\\.}/32\b" "$RULES" \
    || die "this session's client IP $ip is not in admin_v4: add it to the file (and re-review) first"

  "$NFT" -c -f "$RULES"

  systemctl reset-failed "$TIMER_UNIT.timer" "$TIMER_UNIT.service" 2>/dev/null || true
  systemd-run --quiet --unit="$TIMER_UNIT" --on-active="${minutes}min" --timer-property=AccuracySec=1s \
    --description="E-Sabong firewall automatic rollback" \
    "$NFT" delete table $TABLE
  systemctl is-active --quiet "$TIMER_UNIT.timer" || die "rollback timer did not arm; nothing changed"

  before="$(foreign_tables | sha256sum)"
  foreign_tables > "$backup/foreign-tables.before-apply.nft"
  "$NFT" -f "$RULES"
  after="$(foreign_tables | sha256sum)"
  foreign_tables > "$backup/foreign-tables.after-apply.nft"

  echo "Loaded $TABLE. Automatic rollback in $minutes min:"
  systemctl list-timers "$TIMER_UNIT.timer" --no-pager | sed -n 1,2p
  if [ "$before" = "$after" ]; then
    echo "Docker/Fail2Ban tables unchanged by the load."
  else
    echo "NOTE: other tables changed during the load (a Fail2Ban ban/unban is normal); diff:"
    diff "$backup/foreign-tables.before-apply.nft" "$backup/foreign-tables.after-apply.nft" | head -20 || true
  fi
  echo "Now open a NEW SSH session from $ip. Only if it works: sudo $0 confirm"
}

cmd_confirm() {
  need_root
  have_table || die "$TABLE is not loaded (rollback already ran?) — nothing to confirm"
  systemctl stop "$TIMER_UNIT.timer" 2>/dev/null || true
  systemctl is-active --quiet "$TIMER_UNIT.timer" && die "rollback timer still active"
  echo "Rollback timer cancelled; $TABLE stays loaded (until reboot unless 'persist' is run)."
}

cmd_persist() {
  need_root
  have_table || die "load and confirm first"
  [ -n "${EXPECTED_SHA256:-}" ] && [ "$(sha256sum "$RULES" | cut -d' ' -f1)" = "$EXPECTED_SHA256" ] \
    || die "set EXPECTED_SHA256 to the reviewed hash"
  install -d -m 700 -o root -g root "$INSTALL_DIR"
  install -m 600 -o root -g root "$RULES" "$INSTALL_DIR/esabong-host.nft"
  install -m 644 -o root -g root "$HERE/$BOOT_UNIT" "/etc/systemd/system/$BOOT_UNIT"
  systemctl daemon-reload
  systemctl enable "$BOOT_UNIT"
  systemctl start "$BOOT_UNIT"   # idempotent reload of the same table
  systemctl is-active "$BOOT_UNIT"
}

cmd_rollback() {
  need_root
  systemctl stop "$TIMER_UNIT.timer" 2>/dev/null || true
  if systemctl is-enabled --quiet "$BOOT_UNIT" 2>/dev/null; then
    systemctl disable --now "$BOOT_UNIT"   # ExecStop deletes the table
  fi
  "$NFT" delete table $TABLE 2>/dev/null || true
  have_table && die "table still present"
  echo "Removed $TABLE. Docker, Fail2Ban and UFW state were not touched."
}

cmd_status() {
  need_root
  if have_table; then "$NFT" list table $TABLE; else echo "$TABLE: not loaded"; fi
  systemctl list-timers "$TIMER_UNIT.timer" --no-pager 2>/dev/null | sed -n 1,2p
  systemctl is-enabled "$BOOT_UNIT" 2>/dev/null || echo "$BOOT_UNIT: not installed"
  fail2ban-client status sshd 2>/dev/null | grep -E 'Currently (failed|banned)' || true
}

case "${1:-}" in
  inventory) cmd_inventory ;;
  check)     cmd_check ;;
  apply)     shift; cmd_apply "$@" ;;
  confirm)   cmd_confirm ;;
  persist)   cmd_persist ;;
  rollback)  cmd_rollback ;;
  status)    cmd_status ;;
  *) sed -n '2,15p' "$0"; exit 2 ;;
esac
