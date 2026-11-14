#!/usr/bin/env python3
"""Smoke-test the real gateway's custom-host handlers using disposable mock origins.

Run locally with Docker available: python3 scripts/test-custom-domain-gateway.py
No certificate issuance, application credentials or running deployment is involved.
"""

# Synthetic HTTP fixtures must be reachable from Docker; subprocess arguments and URLs are fixed.
# ruff: noqa: S104, S310, S603, S607
import json
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPROVED = {"prod.custom.example": "prod", "uat.custom.example": "uat"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def respond(self):
        if self.server.approval:
            from urllib.parse import parse_qs, urlsplit

            host = parse_qs(urlsplit(self.path).query).get("domain", [""])[0]
            assert not self.headers.get("Authorization") and not self.headers.get("Cookie")
            self.send_response(200 if APPROVED.get(host) == self.server.env else 404)
            self.end_headers()
            return
        body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
        result = json.dumps(
            {
                "env": self.server.env,
                "method": self.command,
                "path": self.path,
                "body": body,
                "host": self.headers.get("Host"),
                "auth": self.headers.get("Authorization"),
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(result)))
        self.end_headers()
        self.wfile.write(result)


def request(host, path="/api/v1/auth/login?probe=yes", method="POST"):
    req = urllib.request.Request(
        "http://127.0.0.1:19080" + path,
        data=b'{"probe":true}' if method == "POST" else None,
        method=method,
        headers={"Host": host, "Authorization": "Bearer smoke-token", "Cookie": "test=smoke"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, None


def main():
    source = (ROOT / "infrastructure/gateway/Caddyfile").read_text()
    snippets = source[
        source.index("(gw-log)") : source.index("# ------------------------------------------------------------------ production")
    ]
    custom = source[source.index("https:// {") : source.index("# Caddy's private certificate approval broker")]
    custom = custom.replace("https:// {", ":8080 {", 1)
    custom = re.sub(r"\s*tls \{\s*on_demand\s*\}", "", custom, count=1)
    config = "{\n auto_https off\n admin off\n}\n" + snippets + custom
    for env, app_port, approval_port in [("prod", 19380, 19381), ("uat", 19480, 19481)]:
        config = config.replace(f"esabong-{env}-origin:8081", f"host.docker.internal:{approval_port}")
        # The to-origin snippet inserts :80 after its argument.
        config = config.replace(f"import to-origin esabong-{env}-origin", f"import smoke-{env}")
        config = config.replace(
            "(gw-log)",
            f"(smoke-{env}) {{\n reverse_proxy host.docker.internal:{app_port} {{\n flush_interval -1\n }}\n}}\n(gw-log)",
            1,
        )
    servers = []
    name = "esabong-domain-gateway-smoke"
    try:
        for env, ports in [("prod", (19380, 19381)), ("uat", (19480, 19481))]:
            for index, port in enumerate(ports):
                server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
                server.env, server.approval = env, bool(index)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                servers.append(server)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Caddyfile"
            path.write_text(config)
            subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    name,
                    "-p",
                    "127.0.0.1:19080:8080",
                    "-v",
                    f"{path}:/etc/caddy/Caddyfile:ro",
                    "caddy:2.10-alpine",
                ],
                check=True,
                capture_output=True,
            )
            for _ in range(30):
                try:
                    status, _ = request("unknown.example")
                    break
                except OSError:
                    time.sleep(0.2)
            for env in ("prod", "uat"):
                host = env + ".custom.example"
                status, result = request(host)
                assert status == 200, (host, status)
                assert result == {
                    "env": env,
                    "method": "POST",
                    "path": "/api/v1/auth/login?probe=yes",
                    "body": '{"probe":true}',
                    "host": host,
                    "auth": "Bearer smoke-token",
                }, result
                for path in ("/owner", "/owner/", "/api/v1/platform/tenants"):
                    assert request(host, path, "GET")[0] == 404
                assert request(host, "/stream/hls/live/demo/index.m3u8", "GET")[1]["env"] == env
            assert request("unknown.example")[0] == 404
            del APPROVED["prod.custom.example"]
            assert request("prod.custom.example")[0] == 404
            print(
                "PASS: prod/UAT selection, original method/body/query/auth, stream routing, "
                "owner denial, unknown and disabled hosts"
            )
    except BaseException:
        print(subprocess.run(["docker", "logs", name], capture_output=True, text=True).stderr)
        raise
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        for server in servers:
            server.shutdown()


if __name__ == "__main__":
    main()
