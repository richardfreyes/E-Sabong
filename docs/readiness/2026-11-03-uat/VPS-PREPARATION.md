# Contabo UAT preparation — 3 November 2026

Status: administrator-supplied Gmail/ACME settings verified and the authorized workstation
tester /32 configured. Deployment readiness remains incomplete pending attended Docker
and firewall inventory. No containers were started; no images built, Docker networks/volumes/databases
created, migrations applied, DNS/firewall/permission changes made, or existing files deleted.

Approved release: `uat-20261103-rc.1`, commit
`cc7c39aa9e9e25ab2240bdda4b1ff270191746d6`.

Latest prerequisite recheck: both release checkouts remain clean at the approved SHA/tag.
The administrator's existing UAT mail values match `smtp.gmail.com:587`, STARTTLS true,
with ACME contact, SMTP user and sender `<owner-email>`. The existing SMTP password
is nonempty/non-placeholder; it was not displayed, copied, authenticated or used to send mail.
Gateway ACME email matches. Both env files remain deploy-owned mode 600.

The workstation's public IPv4 was independently confirmed by api.ipify.org,
checkip.amazonaws.com and the SSH client address seen by the VPS; all three matched.
Only `UAT_ALLOWED_IPS` was updated to that exact IPv4 /32 (reported to the owner separately).
The token and every other file byte were preserved, as were inode/permissions. Private
pre-update backup retained at `/home/deploy/uat-gateway-env-backup-q344so69/gateway.env`.
No credentials were regenerated. Docker/sudo/firewall access remains blocked by authentication.

Read-only inventory is now explicitly approved; no permanent privilege change is approved.
Computer-use access to Terminal was denied by its safety restriction, so the agent cannot
present/operate a secure Terminal password prompt. The owner was asked to authenticate
in their own Terminal using the bounded identity check below, never via chat. Their result
is pending; do not infer that access succeeded.

```bash
ssh -t -i ~/.ssh/contabo_esabong -o IdentitiesOnly=yes deploy@46.250.249.61 \
  'sudo -k -H -u deploy -g docker -- /usr/bin/id'
```

Current daemon-free VPS Compose/secret/isolation checks all passed. UAT-only Caddy syntax
and provisioning validation also passed using the existing local rehearsal Caddy process,
actual non-secret settings and synthetic gate credentials; container ID/start time were
unchanged and no container was created or started. No real VPS token was transferred.
Native VPS Caddy is absent, so server runtime/TLS acceptance remains a later stage.

## Completed initial VPS work (historical; current verification above)

- Inspected Ubuntu 24.04.5 LTS, x86_64, six CPUs; Docker CLI 29.9.2 and Compose v5.7.0.
  The Docker daemon's health/version could not be queried without privileged access.
- Approximately 11 GiB available RAM, 94 GB disk available; no swap configured.
- Only SSH listened publicly at inspection time. UAT DNS A resolves to `46.250.249.61`;
  no UAT AAAA response. Production DNS was not changed.
- Original `/opt/esabong` checkout remains at `cccee4db5237811bceff4a4fe05be757cdb9e680`;
  its tracked files are unchanged. It now reports only `?? gateway/` and `?? uat/`, the
  approved nested independent clones. Never clean these away from the parent checkout.
- Created `/opt/esabong/uat` and `/opt/esabong/gateway`, owned by `deploy`. Both are clean
  detached checkouts at the approved full SHA; annotated tag resolves to that same SHA.
- Created `/opt/esabong/uat/db/backups` with mode 700. No backup was deleted or replaced.
- Created private, git-ignored files with mode 600, owned by `deploy`:
  `/opt/esabong/uat/.env` and `/opt/esabong/gateway/infrastructure/gateway/.env`.
  Creation refused existing files; no existing credentials were overwritten.
- Generated fresh distinct database, Redis, JWT, MediaMTX, Fernet and gate credentials
  on the VPS using Python's cryptographic random generator. Values were never printed or
  copied to the workstation. SMTP credentials were not invented. These private env files
  must be backed up to an approved password manager through a secure operator workflow.
- `/opt/esabong/.env` and `/opt/esabong/prod/.env` are absent; no existing production
  credentials were available to compare. No production configuration was copied; all
  generated UAT infrastructure secrets and the gateway token are mutually distinct.
- Left unknown settings as visible placeholders or empty values. No fake contact email,
  SMTP provider, password, sender or tester IP was supplied.

## Prepared configuration and verification

`ESB_STACK=uat`, `ROOT_TENANT_SLUG=uat-demo`, `ENVIRONMENT=production`,
`BASE_DOMAIN=PLATFORM_HOST=uat.swcmain183.live`, synthetic data/test source enabled,
secure cookies enabled, tenant-header override disabled, and `GATEWAY_MODE=uat-only`.

Daemon-free `docker compose config` succeeded on the VPS for both projects. JSON output,
which contains secrets, was kept inside the checking process and was not printed or saved.
The existing isolation checker verified the UAT project, five volumes, three networks,
bind mounts and data-service URLs. PostgreSQL and Redis are separate UAT services with
internal networking and no host ports. `DB_NAME=esabong` is local to UAT's own container;
it is not a connection to the developer or production database.

The origin/API/worker/beat do not publish host ports. MediaMTX uses UAT ports 1945, 1946
and 8190, with 8190 also used inside the container. Pending an explicit broadcaster
allowlist, public plaintext RTMP is closed in the private UAT env:
`STREAM_RTMP_BIND=127.0.0.1`, `STREAM_RTMP_URL=`. RTMPS remains loopback-only.
WHIP over HTTPS is the proposed initial ingest path; an OBS broadcaster must also pass
the UAT gate, normally through its explicit public-IP allowlist.

Gateway Compose selects `Caddyfile.uat-only` and joins only `public` and `uat` networks.
It has no production network, production hostname/upstream, catch-all or on-demand TLS.
Tracked Caddy, Compose, deploy and 0016 migration files were verified byte-identical to
the approved release. The approved release's Caddy syntax/runtime validation remains
recorded in [REPORT.md](REPORT.md). No Caddy executable is installed on the VPS, and no
validator/gateway container was started during preparation; server runtime validation
therefore remains pending. File permissions, strong secret lengths, distinctness and
Fernet encoding passed the on-server checks.

The migration graph was inspected statically without importing migration code or connecting
to any database: base → 0001 → … → 0015 → **0016**, one head, 16 revisions. The first
inspection script missed annotated metadata assignments and failed its assertion; the
corrected inspection handled both assignment forms and passed. No migration defect found.

**Actual pending database plan is unknown.** Existing Docker volumes could not be listed;
an absent checkout does not prove an empty database. Review actual state before choosing
any migration range. Do not connect to production to infer UAT state.

## Administrator settings — previous missing values resolved

| File | Missing or unconfirmed |
|---|---|
| UAT `.env` | Actual ACME/Gmail user/sender/host values verified; SMTP password present and hidden |
| UAT `.env` | Port 587 and STARTTLS true verified against administrator-supplied values |
| Gateway `.env` | Actual ACME email verified; exact authorized workstation public IPv4 /32 configured |
| Optional public RTMP | Remains closed to public interfaces; broadcaster rules needed only if later enabled |

The gate token was retained unchanged. The explicit /32 allowlist is now configured;
the current public IP should be rechecked if the workstation changes network before testing.
Mail delivery and SMTP authentication remain deliberately untested. The historic updater
below is retained for future administrator changes; do not rerun it merely to regenerate values.
No production secret/config file is required just to deploy UAT-only mode.

## Exact command to update only administrator-supplied settings

First select a real UAT SMTP testing account and obtain its actual settings. The current
email worker uses `smtplib.SMTP` followed by `starttls()`; it does not implement implicit TLS
on port 465. This updater requires a confirmed STARTTLS endpoint rather than silently
disabling transport security. SMTP account/provider selection remains the administrator's
decision. No mail will be sent by this command.

Run the following in the administrator's own terminal, only once actual values are known.
All values are entered with hidden prompts, outside shell history and process arguments.
Do not run it in a terminal with input recording. Do not substitute credentials into the
script, paste them into chat, or source the env files as shell scripts.

```bash
ssh -t -i ~/.ssh/contabo_esabong -o IdentitiesOnly=yes deploy@46.250.249.61
set +x
python3 <<'PY'
import contextlib, fcntl, getpass, ipaddress, os, re, stat, tempfile
from pathlib import Path

def encode(value):
    if any(c in value for c in '\r\n\0'):
        raise ValueError('Multiline/NUL values are not supported')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$') + '"'

def patch(text, updates):
    lines = text.splitlines(keepends=True)
    for key, value in updates.items():
        positions = [i for i, line in enumerate(lines) if line.startswith(key + '=')]
        if len(positions) != 1:
            raise ValueError('Missing or duplicate setting: ' + key)
        lines[positions[0]] = key + '=' + encode(value) + '\n'
    return ''.join(lines)

def main():
    uat = Path('/opt/esabong/uat/.env')
    gateway = Path('/opt/esabong/gateway/infrastructure/gateway/.env')
    fields = ('ACME_EMAIL', 'SMTP_HOST', 'SMTP_PORT', 'SMTP_USER',
              'SMTP_PASSWORD', 'SMTP_USE_TLS', 'MAIL_FROM', 'UAT_ALLOWED_IPS')
    values = {key: getpass.getpass(key + ' (actual value, hidden): ') for key in fields}
    if any(not value or 'CHANGE_ME' in value for value in values.values()):
        raise ValueError('Supply all actual administrator settings; no placeholders')
    for value in values.values():
        encode(value)
    for key in ('ACME_EMAIL', 'MAIL_FROM'):
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', values[key]):
            raise ValueError('Supply a plain email address for ' + key)
    port = int(values['SMTP_PORT'])
    if not 1 <= port <= 65535 or port == 465 or values['SMTP_USE_TLS'].lower() != 'true':
        raise ValueError('This release needs a confirmed STARTTLS endpoint and SMTP_USE_TLS=true')
    values['SMTP_USE_TLS'] = 'true'
    networks = [ipaddress.ip_network(v, strict=True) for v in values['UAT_ALLOWED_IPS'].split()]
    if not networks or any(n.prefixlen == 0 or not n.network_address.is_global
                           or not n.broadcast_address.is_global for n in networks):
        raise ValueError('Tester access requires explicit public IPs/CIDRs')
    values['UAT_ALLOWED_IPS'] = ' '.join(str(n) for n in networks)
    changes = {
        uat: {k: values[k] for k in fields if k != 'UAT_ALLOWED_IPS'},
        gateway: {k: values[k] for k in ('ACME_EMAIL', 'UAT_ALLOWED_IPS')},
    }
    with contextlib.ExitStack() as stack:
        opened, originals, rendered = {}, {}, {}
        for path, updates in changes.items():
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)  # existing files only
            f = stack.enter_context(os.fdopen(fd, 'r+', encoding='utf-8', newline=''))
            fcntl.flock(f, fcntl.LOCK_EX)
            info = os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
                raise ValueError('Require existing deploy-owned regular mode-600 files')
            opened[path], originals[path] = f, f.read()
            rendered[path] = patch(originals[path], updates)
        backup = Path(tempfile.mkdtemp(prefix='uat-env-backup-', dir=Path.home()))
        for path, original in originals.items():
            name = 'uat.env' if path == uat else 'gateway.env'
            fd = os.open(backup / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
                f.write(original); f.flush(); os.fsync(f.fileno())
        for path, f in opened.items():
            f.seek(0)
            if f.read() != originals[path]:
                raise ValueError('File changed during review; no update attempted')
        for path, f in opened.items():
            f.seek(0); f.write(rendered[path]); f.truncate(); f.flush(); os.fsync(f.fileno())
        print('Only requested mail/contact/tester settings updated; infrastructure secrets retained.')
        print('Private pre-update backups retained at: ' + str(backup))

if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, EOFError):
        raise SystemExit('Update stopped. No values printed. Review private backups if writing had begun.')
PY
```

The existing env files are edited in place; they are not recreated, replaced, chmodded or
regenerated. Only the named settings are changed. Private pre-update backups are retained,
not deleted. The two writes are not a filesystem transaction: if an I/O failure interrupts
them, inspect the private backups and both files before proceeding. No secret rollback is
attempted automatically. The literal quoting was checked with synthetic values including
spaces, quotes, dollars and backslashes using Compose's daemon-free env parser. Docker's
[interpolation reference](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)
explains why secrets must not be left unquoted. Never print `compose config` or
`compose config --environment` against real env files: they contain secret values.

## Attended Docker execution proposal — not executed

`deploy` is UID 1001 and is not a Docker-group member. Socket permissions remain
`660 root:docker`. Docker container/volume/network inventory, firewall/UFW/iptables rules,
and the daemon state could not be read: direct access was denied and `sudo -n` required
a password. Their state is unknown, not verified empty or open. No credentials were requested
through chat, no authentication was bypassed and no policy change was attempted.

Proposed method for administrator review: a password-authenticated sudo session with
UID `deploy` and primary group `docker` **for that process tree only**:

```bash
# Administrator/operator types the sudo password in their own SSH terminal, not chat.
sudo -k -H -u deploy -g docker -- /usr/bin/bash --noprofile --norc
id -u                       # must be 1001, never 0
id -gn                      # must be docker
docker ps -a --format '{{.Names}} {{.Status}} {{.Ports}}'
docker volume ls
docker network ls
```

This is a proposal, not a successfully authenticated session. The VPS's installed sudo
help confirms the `-u`/`-g` options; Ubuntu's [sudo manual](https://manpages.ubuntu.com/manpages/noble/man8/sudo.8.html)
documents running commands with a selected user/group, subject to sudo policy. This needs
an administrator to authenticate and confirm identity and access on the real VPS.
No repository script change is proposed: the existing shell/Python child processes would
inherit Docker access while executing as UID `deploy`, so user-writable repository scripts
would not run as UID root. The guarded deployment scripts would remain unchanged.

Latest verification: sudo 1.9.15p5 and its user/group options are present. The noninteractive
identity-only probe (`sudo -n -u deploy -g docker -- /usr/bin/id`) was refused because a password
is required; it granted no Docker access. Thus syntax/installation support is verified, while
actual sudo policy authorization and resulting UID/GID remain unverified. The proposal is
not evidence of granted access. Read-only inventory is approved, but administrator
authentication is still required. Broader execution/deployment remains separately gated.

This grants **root-equivalent Docker power** for the authorized session and descendants;
it is not least privilege or containment of a compromised `deploy` account. Docker explains
the group trust boundary in its [post-installation documentation](https://docs.docker.com/engine/install/linux-postinstall/).
Use only with explicit administrator acceptance, trusted reviewed release files, and an
attended operator. Never treat closing the shell as revoking access from surviving children;
inspect any retained processes when the session ends. No permanent group membership,
world-writable socket, socket ACL, passwordless sudo rule or root-owned wrapper is installed.
Rootful Docker remains a host-level trust boundary between UAT and later production.

Read-only privileged firewall checks can be run separately by the attended administrator:

```bash
sudo ufw status verbose
sudo iptables -S
sudo ip6tables -S
sudo nft list ruleset
```

Prepared read-only inventory after attended access is approved (execute the Docker portion
in the approved UID-deploy/GID-docker shell above, not as UID root):

```bash
id
docker version
docker compose version
docker ps -a --no-trunc --format '{{.ID}} {{.Names}} {{.Image}} {{.Status}} {{.Ports}}'
docker volume ls
docker network ls
ss -lntup
df -h /opt/esabong
free -h
git -C /opt/esabong rev-parse HEAD
git -C /opt/esabong status --short
git -C /opt/esabong/uat rev-parse HEAD
git -C /opt/esabong/gateway rev-parse HEAD
```

For the current read-only stage, prefer this bounded Docker inventory command over opening
an unrestricted Docker-enabled interactive shell. Run it on the VPS in the owner's own
terminal after the identity probe succeeds; enter sudo's password only at its hidden prompt:

```bash
sudo -k -H -u deploy -g docker -- /usr/bin/bash --noprofile --norc -c '
set -eu
test "$(id -u)" -eq 1001
test "$(id -gn)" = docker
id
docker version
docker ps -a --no-trunc --format "{{.ID}} {{.Names}} {{.Image}} {{.Status}} {{.Ports}}"
docker image ls --digests
docker volume ls
docker network ls
'
```

Run the existing read-only UFW/iptables/ip6tables/nft queries separately with attended sudo.
No repository code executes as root. Authentication in the owner's terminal does not
authorize another agent SSH session; do not transfer sudo timestamps or credentials.

Inspect names `esabong-uat`, `esabong-gateway`, `esabong-uat_*`,
`esabong-gateway_*`, and `esabong-uat-gateway` for collisions, including stopped
containers and unused volumes/networks. Do not run `inspect` without a restricted format,
`docker info` dumps, Compose config output, or container logs for sharing: they can contain
private configuration. The commands above neither start nor remove resources. Firewall
queries use system utilities directly with attended sudo; no repository scripts run as root.

Do not paste env files, private keys, tokens, secrets or unredacted Docker inspect/config
output into chat. Ordinary sudo authentication in a different terminal does not authorize
the agent's noninteractive terminal, and no sudo timestamp transfer is proposed.

## Ports and conflicts still to verify

Latest VPS resource check: approximately 11 GiB available RAM, 94 GB free disk, no swap;
only SSH listens publicly. No listeners were observed on 80/443, 8190 TCP/UDP, or loopback 1945/1946. Listening-port
inspection does not prove firewall, provider ingress or future Docker publishing readiness.
Full stopped-container, volume-name and network-name conflict checks need attended access.
Do not overwrite or delete a pre-existing UAT resource if discovered. UAT will need 80/443
and 8190 TCP/UDP externally reachable; keep 1945/1946 private with the prepared settings.
No firewall rule was changed in this stage. Public TLS issuance, SMTP delivery, OBS and the
eventual Linux cross-environment isolation probe remain unverified.

| Port | Proposed UAT use | Preparation status |
|---|---|---|
| TCP 80 | ACME HTTP challenge and HTTPS redirect | no listener; ingress/firewall unknown |
| TCP 443 | restricted portals, APIs, WS, WHIP/WHEP and HLS | no listener; ingress/firewall unknown |
| UDP/TCP 8190 | authenticated UAT WebRTC media | no listener; ingress/firewall unknown |
| TCP 1945/1946 | optional RTMP/RTMPS | loopback bind retained; no public opening proposed |
| 5432/6379/8888/8889/9997/9998 | data and media control/internal HTTP | never publish publicly |
| 1935/1936/8189 | production media | do not open or configure in this stage |

## Firewall — Stage 1 (design, staging, verification; NOT applied)

Superseded the earlier conditional UFW proposal: UFW must stay disabled on this host (it does not
filter Docker-published ports, and its rules would sit beside Docker's iptables-nft tables).
Design, exact rules, rollback and verification: [deployment-contabo.md §1a](../../deployment-contabo.md).

Unprivileged recheck on 3 Nov 2026 20:05 UTC (`deploy`, no sudo; `sudo -n` still needs a password):

| Item | Finding |
|---|---|
| SSH | key auth works from `203.0.113.10` (`SSH_CLIENT`); two open `deploy` sessions; `ssh.socket` activation; listens `0.0.0.0:22` **and `[::]:22`**; `PasswordAuthentication no`; root-login setting: review with `sshd -T` (details in private notes) |
| Listeners | only SSH plus systemd-resolved on loopback; no 80/443/8190/1945/1946 |
| IPv6 | **global** `2001:db8::1/64`, default route via `fe80::1`; IPv6 forwarding 0. With INPUT ACCEPT, SSH is reachable over IPv6 today |
| UFW | installed, `ENABLED=no` (the systemd oneshot shows active/exited — not an active firewall); defaults in `/etc/default/ufw`: input DROP, IPV6=yes |
| nftables | `nftables.service` inactive/disabled; `/etc/nftables.conf` begins with `flush ruleset` — must never be enabled |
| iptables | 1.8.10 via `xtables-nft-multi` (iptables-nft); no persistent-iptables packages |
| Fail2Ban | active; `jail.d/defaults-debian.conf`: banaction `nftables`, backend systemd, `[sshd]` enabled |
| Docker | 29.9.2 active, Compose v5.7.0; no `/etc/docker/daemon.json` (defaults: iptables, userland-proxy); `docker0` down. Containers/networks/volumes need attended inventory |
| Tools | `systemd-run` present (rollback timer); `at` absent |
| Checkouts | `/opt/esabong` cccee4d clean; `uat` and `gateway` cc7c39a clean |
| UAT media | `STREAM_WEBRTC_PORT=8190`, `STREAM_PUBLIC_HOSTS=46.250.249.61`, no STUN/TURN, RTMP/RTMPS bound `127.0.0.1:1945/1946`, `COMPOSE_PROFILES=streaming` |

Not verifiable by the agent (need the owner): full nft/iptables ruleset, `DOCKER-USER`, Fail2Ban ban
state, Docker inventory, effective `sshd -T`, netplan, Contabo VNC console and Contabo panel firewall.

Staged (no rule change): `/home/deploy/esabong-firewall/` mode 700 with `esabong-host.nft`
(sha256 `c21809c22b55fa7fdff35b231fdf416fc9258f28f7ca9709acb5d43e95e9d2fd`), `firewall.sh`
(`2b76155b29e660356e74440096720efe8cdc6a18fbc17539e1114119c60388bd`) and
`esabong-host-firewall.service` (`729f7cc0f95f08fadb424fbe501a20a9ad55b44a5c2cd250d954a1fd27f55ea6`).
These hashes are of the server-side copies (real administrator /32). The committed `esabong-host.nft`
uses a placeholder address, so its hash differs by design.
Owner's next step (own terminal; password only at sudo's hidden prompt):

```bash
ssh -t -i ~/.ssh/contabo_esabong -o IdentitiesOnly=yes deploy@46.250.249.61 \
  'cd ~/esabong-firewall && sha256sum * && sudo -k ./firewall.sh inventory && sudo ./firewall.sh check'
```

Local verification (throwaway privileged Ubuntu 24.04 container on its own network; emulated Docker
iptables-nft FORWARD/`DOCKER-USER`/DNAT into a separate namespace and a Fail2Ban-style `inet f2b-table`;
removed afterwards): `nft -c` parse, load, idempotent reload, other tables byte-identical after load,
**an already-open TCP session to 22 survived the load**, new non-admin SSH blocked, admin SSH allowed after
`add element`, published 5432 and 1935 blocked, host-only port blocked, published TCP 443 and UDP 8190
allowed, rollback restored the stateless ruleset exactly (20/20 checks). ShellCheck clean;
`systemd-analyze verify` clean. Not tested: real Docker daemon, real Fail2Ban, IPv6 behaviour, systemd-run
timer, the VPS itself.

## Next commands — after missing settings/access and separate deployment approval

Do **not** execute these under the preparation-only approval. Within an administrator-approved
Docker-enabled session as UID `deploy`, first inspect actual resources as above and confirm
the exact approved tag/SHAs and completed private config. Then:

```bash
cd /opt/esabong/gateway
infrastructure/scripts/gateway.sh networks   # creates UAT link only; no production network
infrastructure/scripts/gateway.sh validate   # transient validator; not run during prep

cd /opt/esabong/uat
infrastructure/scripts/deploy.sh uat-20261103-rc.1
```

The guarded deploy builds UAT images and starts UAT PostgreSQL/Redis before inspecting the
actual database revision. On a pending revision it stops without migrating. Review the exact
pending range it prints. A genuinely new empty UAT DB needs all revisions 0001–0016; an
existing database needs only its actual pending revisions, which remain unknown today.

Only after separate approval of that exact range, the environment-specific migration command is:

```bash
cd /opt/esabong/uat
APPROVED_MIGRATIONS=uat:0016 infrastructure/scripts/deploy.sh uat-20261103-rc.1
```

This approval is not granted by preparing the `.env` files. Do not use `prod:0016`, start
production, or run a bare Compose startup that bypasses the guarded flow. Account seeding,
gateway startup/public HTTPS and acceptance tests follow their own approved steps in
[REPORT.md](REPORT.md); none was executed here.
