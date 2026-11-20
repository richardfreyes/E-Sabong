#!/usr/bin/env python3
"""Traffic baseline and anomaly report from Caddy JSON access logs (gateway or origin).

    docker logs --since 1h esabong-gateway-gateway-1 2>&1 | python3 infrastructure/monitoring/traffic-report.py
    ... | python3 traffic-report.py --anonymize          # share-safe: addresses hashed
    ... | python3 traffic-report.py --json > base.json    # machine-readable baseline

Read-only. Every request is classified first (HLS, WebRTC signalling, WebSocket, health, auth,
declarator, API read/write, media, static), and only then compared with thresholds, so a viewer
fetching 25 HLS parts a second or a Declarator desk polling is never reported as an attacker.
A source is flagged only for patterns real portals do not produce:

  * API (non-HLS, non-health) requests per minute above --api-per-min
  * many 401/403 answers (credential guessing) above --auth-fail-per-min
  * many distinct 404 paths (scanning) above --scan-404-paths
  * probe paths no portal requests (/.env, /wp-admin, *.php, /.git, ...)
  * WebSocket handshakes per minute above --ws-per-min (reconnect loops)
  * any 429 from the edge guard (who is being limited, and whether it is a real user pattern)

Exit status: 0 = nothing flagged, 1 = at least one source flagged (for cron/alerting).
"""

import argparse
import collections
import hashlib
import json
import re
import sys
from typing import Any

PROBE = re.compile(
    r"(?i)(/\.env|/\.git|/wp-(admin|login|content)|\.php\b|/phpmyadmin|/cgi-bin|/actuator|/server-status|/\.aws|/config\.json|/vendor/|/boaform|/HNAP1)"
)


def classify(path: str, method: str, upgrade: bool) -> str:
    if upgrade or path.startswith("/api/ws"):
        return "websocket"
    if path.startswith("/stream/hls/"):
        return "hls"
    if path.startswith("/stream/"):
        return "webrtc_signal"
    if path in ("/api/health", "/api/ready"):
        return "health"
    if path.startswith(("/api/v1/auth/", "/api/v1/platform/auth/")):
        return "auth"
    if path.startswith("/api/v1/declarator/"):
        return "declarator"
    if path.startswith("/api/media/"):
        return "media"
    if path.startswith("/api/"):
        return "api_write" if method not in ("GET", "HEAD", "OPTIONS") else "api_read"
    return "static"


def pct(values: list[int], q: float) -> int:
    if not values:
        return 0
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api-per-min", type=int, default=600, help="API requests/min from one address (baseline max 109)")
    ap.add_argument("--auth-fail-per-min", type=int, default=30, help="401/403 answers/min to one address (baseline max 8)")
    ap.add_argument("--scan-404-paths", type=int, default=25, help="distinct 404 paths from one address in the window")
    ap.add_argument("--ws-per-min", type=int, default=30, help="WebSocket handshakes/min from one address (baseline max 6)")
    ap.add_argument("--anonymize", action="store_true", help="print hashed addresses only")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args()

    def label(ip: str) -> str:
        return "ip#" + hashlib.sha256(ip.encode()).hexdigest()[:10] if args.anonymize else ip

    per_min: collections.Counter[int] = collections.Counter()
    bytes_min: collections.Counter[int] = collections.Counter()
    status: collections.Counter[int] = collections.Counter()
    classes: collections.Counter[str] = collections.Counter()
    ip_min_cls: collections.Counter[tuple[str, int, str]] = collections.Counter()
    ip_min_fail: collections.Counter[tuple[str, int]] = collections.Counter()
    ip_404: dict[str, set[str]] = collections.defaultdict(set)
    ip_probe: collections.Counter[str] = collections.Counter()
    ip_429: collections.Counter[str] = collections.Counter()
    ip_total: collections.Counter[str] = collections.Counter()
    lines = 0
    for raw in sys.stdin:
        i = raw.find("{")
        if i < 0:
            continue
        try:
            e = json.loads(raw[i:])
        except ValueError:
            continue
        r = e.get("request")
        if not isinstance(r, dict):
            continue
        lines += 1
        ip = r.get("client_ip") or r.get("remote_ip") or "?"
        path = str(r.get("uri", "")).split("?", 1)[0]
        method = r.get("method", "")
        upgrade = any(str(v).lower() == "websocket" for v in (r.get("headers") or {}).get("Upgrade", []))
        cls = classify(path, method, upgrade)
        minute = int(float(e.get("ts", 0)) // 60)
        st = int(e.get("status", 0) or 0)
        per_min[minute] += 1
        bytes_min[minute] += int(e.get("size", 0) or 0)
        status[st] += 1
        classes[cls] += 1
        ip_total[ip] += 1
        ip_min_cls[(ip, minute, cls)] += 1
        if st in (401, 403) and cls != "health":
            ip_min_fail[(ip, minute)] += 1
        if st == 404:
            ip_404[ip].add(path[:120])
        if st == 429:
            ip_429[ip] += 1
        if PROBE.search(path):
            ip_probe[ip] += 1

    api_classes = ("api_read", "api_write", "auth", "declarator", "media")
    api_ip_min: collections.Counter[tuple[str, int]] = collections.Counter()
    ws_ip_min: collections.Counter[tuple[str, int]] = collections.Counter()
    per_class_ip_min: dict[str, list[int]] = collections.defaultdict(list)
    for (ip, minute, cls), n in ip_min_cls.items():
        per_class_ip_min[cls].append(n)
        if cls in api_classes:
            api_ip_min[(ip, minute)] += n
        if cls == "websocket":
            ws_ip_min[(ip, minute)] += n

    flags: list[dict[str, Any]] = []

    def flag(ip: str, rule: str, value: int, limit: int | None = None) -> None:
        flags.append({"source": label(ip), "rule": rule, "value": value, "limit": limit, "total_requests": ip_total[ip]})

    worst: dict[tuple[str, str], int] = {}
    for (ip, _), n in api_ip_min.items():
        if n > args.api_per_min:
            worst[(ip, "api_requests_per_min")] = max(n, worst.get((ip, "api_requests_per_min"), 0))
    for (ip, _), n in ip_min_fail.items():
        if n > args.auth_fail_per_min:
            worst[(ip, "auth_failures_per_min")] = max(n, worst.get((ip, "auth_failures_per_min"), 0))
    for (ip, _), n in ws_ip_min.items():
        if n > args.ws_per_min:
            worst[(ip, "websocket_handshakes_per_min")] = max(n, worst.get((ip, "websocket_handshakes_per_min"), 0))
    limits = {"api_requests_per_min": args.api_per_min, "auth_failures_per_min": args.auth_fail_per_min,
              "websocket_handshakes_per_min": args.ws_per_min}
    for (ip, rule), n in worst.items():
        flag(ip, rule, n, limits[rule])
    for ip, paths in ip_404.items():
        if len(paths) > args.scan_404_paths:
            flag(ip, "distinct_404_paths", len(paths), args.scan_404_paths)
    for ip, n in ip_probe.items():
        flag(ip, "probe_paths", n)
    for ip, n in ip_429.items():
        flag(ip, "rate_limited_429", n)
    flags.sort(key=lambda f: (-f["value"], f["rule"]))

    report = {
        "requests": lines,
        "distinct_sources": len(ip_total),
        "minutes_with_traffic": len(per_min),
        "requests_per_min": {"p50": pct(list(per_min.values()), 0.5), "p99": pct(list(per_min.values()), 0.99),
                             "max": max(per_min.values(), default=0)},
        "egress_bytes_per_min_max": max(bytes_min.values(), default=0),
        "status": dict(sorted(status.items())),
        "classes": dict(classes.most_common()),
        "per_source_per_min_by_class": {
            c: {"p50": pct(v, 0.5), "p99": pct(v, 0.99), "max": max(v)} for c, v in sorted(per_class_ip_min.items())
        },
        "api_per_source_per_min_max": max(api_ip_min.values(), default=0),
        "flags": flags,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"requests={lines} sources={len(ip_total)} minutes={len(per_min)} req/min p50={report['requests_per_min']['p50']} "
              f"p99={report['requests_per_min']['p99']} max={report['requests_per_min']['max']}")
        print("status:", report["status"])
        print("classes:", report["classes"])
        for c, s in report["per_source_per_min_by_class"].items():
            print(f"  per source/min [{c:13}] p50={s['p50']:<5} p99={s['p99']:<5} max={s['max']}")
        print(f"API per source/min max (HLS, health excluded): {report['api_per_source_per_min_max']}")
        if flags:
            print(f"\nFLAGGED ({len(flags)}):")
            for f in flags[:50]:
                limit = f" (threshold {f['limit']})" if f["limit"] is not None else ""
                print(f"  {f['source']:<42} {f['rule']:<30} {f['value']}{limit}  total={f['total_requests']}")
        else:
            print("\nNothing flagged.")
    return 1 if flags else 0


if __name__ == "__main__":
    sys.exit(main())
