"""Redis-backed counters shared by every API worker (one Lua round trip per guarded request).

Keys (all expire on their own; nothing here is permanent):
  sec:block:<ip>                    temporary block, value = JSON reason, TTL = block length
  sec:strike:<ip>                   minutes in the strike window the address broke the flood ceiling
  sec:rl:<rule>:<key>:<minute>      fixed one-minute request counters
  sec:ws:<ip|user>:<key>            sorted set of open sockets (member = connection id, score = expiry)
"""

import json
import time
from typing import Any

from redis.asyncio import Redis

from app.core.redis import get_redis

BLOCK_PREFIX = "sec:block:"
STRIKE_PREFIX = "sec:strike:"
WS_MEMBER_TTL_SECONDS = 150

# KEYS[1] = block key, KEYS[2..] = counters. Returns {block ttl (-2 = none), count...}.
_COUNT = """
local out = {redis.call('TTL', KEYS[1])}
for i = 2, #KEYS do
  local c = redis.call('INCR', KEYS[i])
  if c == 1 then redis.call('EXPIRE', KEYS[i], ARGV[1]) end
  out[#out + 1] = c
end
return out
"""

# KEYS[1] = strike key, KEYS[2] = block key. ARGV: window, threshold, block seconds, reason, enforce.
# Returns {strikes, outcome}: 1 = block created, 2 = would block (monitor mode), 0 = nothing.
_STRIKE = """
local s = redis.call('INCR', KEYS[1])
if s == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
if s >= tonumber(ARGV[2]) then
  redis.call('DEL', KEYS[1])
  if ARGV[5] == '1' then
    if redis.call('SET', KEYS[2], ARGV[4], 'EX', ARGV[3], 'NX') then return {s, 1} end
    return {s, 0}
  end
  return {s, 2}
end
return {s, 0}
"""

# KEYS: block, handshakes/ip, handshakes/user, sockets/ip, sockets/user.
# ARGV: counter ttl, now, member expiry, member, has user (1/0).
# Returns {block ttl, handshakes ip, handshakes user, sockets ip, sockets user}.
_WS_OPEN = """
local out = {redis.call('TTL', KEYS[1]), 0, 0, 0, 0}
local function hs(k)
  local c = redis.call('INCR', k)
  if c == 1 then redis.call('EXPIRE', k, ARGV[1]) end
  return c
end
local function open(k)
  redis.call('ZREMRANGEBYSCORE', k, '-inf', ARGV[2])
  redis.call('ZADD', k, ARGV[3], ARGV[4])
  redis.call('EXPIRE', k, 300)
  return redis.call('ZCARD', k)
end
out[2] = hs(KEYS[2])
out[4] = open(KEYS[4])
if ARGV[5] == '1' then
  out[3] = hs(KEYS[3])
  out[5] = open(KEYS[5])
end
return out
"""

_scripts: dict[int, dict[str, Any]] = {}


def _script(redis: Redis, name: str, source: str) -> Any:
    per_client = _scripts.setdefault(id(redis), {})
    if name not in per_client:
        if len(_scripts) > 8:  # tests recreate the client; never grow without bound
            _scripts.clear()
            per_client = _scripts.setdefault(id(redis), {})
        per_client[name] = redis.register_script(source)
    return per_client[name]


def minute() -> int:
    return int(time.time() // 60)


def seconds_left_in_minute() -> int:
    return max(1, 60 - int(time.time() % 60))


async def count(block_key: str, counters: list[str]) -> list[int]:
    redis = get_redis()
    return [int(v) for v in await _script(redis, "count", _COUNT)(keys=[block_key, *counters], args=[65])]


async def strike(
    ip: str, *, window: int, threshold: int, block_seconds: int, reason: dict[str, Any], enforce: bool
) -> tuple[int, int]:
    redis = get_redis()
    result = await _script(redis, "strike", _STRIKE)(
        keys=[STRIKE_PREFIX + ip, BLOCK_PREFIX + ip],
        args=[window, threshold, block_seconds, json.dumps(reason, separators=(",", ":")), "1" if enforce else "0"],
    )
    return int(result[0]), int(result[1])


async def ws_open(ip: str, user: str | None, connection: str) -> list[int]:
    redis = get_redis()
    now = time.time()
    m = minute()
    keys = [
        BLOCK_PREFIX + ip,
        f"sec:rl:wshs-ip:{ip}:{m}",
        f"sec:rl:wshs-user:{user or '-'}:{m}",
        f"sec:ws:ip:{ip}",
        f"sec:ws:user:{user or '-'}",
    ]
    args = [65, now, now + WS_MEMBER_TTL_SECONDS, connection, "1" if user else "0"]
    return [int(v) for v in await _script(redis, "ws_open", _WS_OPEN)(keys=keys, args=args)]


async def ws_refresh(ip: str, user: str | None, connection: str) -> None:
    redis = get_redis()
    expiry = time.time() + WS_MEMBER_TTL_SECONDS
    async with redis.pipeline(transaction=False) as pipe:
        for key in (f"sec:ws:ip:{ip}", f"sec:ws:user:{user}" if user else None):
            if key:
                pipe.zadd(key, {connection: expiry}, xx=True)
                pipe.expire(key, 300)
        await pipe.execute()


async def ws_close(ip: str, user: str | None, connection: str) -> None:
    redis = get_redis()
    async with redis.pipeline(transaction=False) as pipe:
        pipe.zrem(f"sec:ws:ip:{ip}", connection)
        if user:
            pipe.zrem(f"sec:ws:user:{user}", connection)
        await pipe.execute()


# ------------------------------------------------------------------ operator commands (CLI)


async def list_blocks() -> list[dict[str, Any]]:
    redis = get_redis()
    rows: list[dict[str, Any]] = []
    async for key in redis.scan_iter(match=BLOCK_PREFIX + "*", count=500):
        raw, ttl = await redis.get(key), await redis.ttl(key)
        try:
            reason = json.loads(raw) if raw else {}
        except ValueError:
            reason = {"raw": raw}
        rows.append({"ip": key[len(BLOCK_PREFIX) :], "seconds_left": ttl, **reason})
    return sorted(rows, key=lambda r: r["ip"])


async def set_block(ip: str, seconds: int, reason: dict[str, Any]) -> None:
    await get_redis().set(BLOCK_PREFIX + ip, json.dumps(reason, separators=(",", ":")), ex=seconds)


async def clear_block(ip: str) -> bool:
    redis = get_redis()
    removed = await redis.delete(BLOCK_PREFIX + ip, STRIKE_PREFIX + ip)
    return bool(removed)
