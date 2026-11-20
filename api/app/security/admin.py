"""Operator commands for temporary blocks (``python -m app.cli security ...``).

  security blocks                         list active blocks (address, seconds left, rule, counts)
  security block <ip> [--minutes N] --reason "..."   block an address by hand (max 24 h)
  security unblock <ip>                   lift a block at once (rollback of a wrong block)

Every change is written to the platform audit log (actor ``edge-guard-cli``) and the security log.
"""

import ipaddress
import time

from app.core.config import get_settings
from app.core.database import platform_session
from app.modules.audit.models import ActorType
from app.modules.audit.service import Actor, RequestMeta, record
from app.security import events, store
from app.security.config import get_security_settings

ACTOR = Actor(ActorType.SYSTEM, None, "edge-guard-cli")


def _env() -> str:
    return get_security_settings().env_name or get_settings().environment


def _address(value: str) -> str:
    return str(ipaddress.ip_address(value.strip()))


async def _audit(action: str, ip: str, changes: dict) -> None:
    async with platform_session() as db:
        await record(
            db,
            actor=ACTOR,
            action=action,
            resource_type="client_address",
            resource_id=ip,
            changes=changes,
            meta=RequestMeta(ip=ip),
        )


async def show_blocks() -> None:
    rows = await store.list_blocks()
    if not rows:
        print("No active blocks.")
        return
    print(f"{'address':<40} {'seconds left':>12}  rule / reason")
    for r in rows:
        print(f"{r['ip']:<40} {r['seconds_left']:>12}  {r.get('rule', '?')} {r.get('reason', '')}".rstrip())


async def block(ip: str, minutes: int, reason: str) -> None:
    ip = _address(ip)
    if not 1 <= minutes <= 1440:
        raise SystemExit("--minutes must be 1-1440 (blocks are always temporary)")
    data = {"rule": "manual", "reason": reason[:200], "env": _env(), "at": int(time.time()), "block_seconds": minutes * 60}
    await store.set_block(ip, minutes * 60, data)
    events.emit("temporary_block", env=_env(), rule="manual", action="blocked", client_ip=ip, block_seconds=minutes * 60)
    await _audit("security.ip_blocked", ip, data)
    print(f"Blocked {ip} for {minutes} min. Lift it with: python -m app.cli security unblock {ip}")


async def unblock(ip: str) -> None:
    ip = _address(ip)
    removed = await store.clear_block(ip)
    events.emit("temporary_block", env=_env(), rule="manual", action="unblocked", client_ip=ip, existed=removed)
    await _audit("security.ip_unblocked", ip, {"existed": removed, "env": _env()})
    print(f"Unblocked {ip}." if removed else f"{ip} had no active block (strike count cleared).")
