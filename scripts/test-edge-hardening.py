#!/usr/bin/env python3
"""End-to-end test of the edge hardening: the real gateway Caddyfile + entrypoint in front of the
real origin Caddyfile + snippets, in disposable containers with an echo server as the API.

    python3 scripts/test-edge-hardening.py

Needs Docker and the images caddy:2.10-alpine and any image with python3 (ECHO_IMAGE, default
python:3.12-alpine). The only change to the configuration is a local CA instead of ACME. Nothing
touches a deployment. Checks:

  * client address: a forged X-Forwarded-For / X-Real-IP / Forwarded never reaches the API; the
    API sees exactly "<client>, <gateway>" and uvicorn's proxy handling resolves the real client
  * upstream-proxy mode (Cloudflare): CF-Connecting-IP is used only from trusted ranges
  * entrypoint refuses unsafe trust settings (everyone, private ranges, unknown header)
  * no Server / Via / Alt-Svc banners; security headers on portal responses
  * slow headers are cut at 15 s; oversized headers get 431; body ceilings per route (413)
  * portal builds never serve source maps, hidden files or Finder copies; .well-known still works
  * owner allowlist uses the verified client address
"""

# Disposable local fixtures; subprocess arguments are fixed.
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NET = "esb-edge-hardening"
PREFIX = "esb-edge-hard-"
HTTPS_PORT = 19443
ECHO_IMAGE = os.environ.get("ECHO_IMAGE", "python:3.12-alpine")
CADDY_IMAGE = "caddy:2.10-alpine"

ECHO = r"""
import json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def handle_any(self):
        n = int(self.headers.get("Content-Length") or 0)
        got = len(self.rfile.read(n)) if n else 0
        body = json.dumps({"port": self.server.server_port, "method": self.command, "path": self.path,
                           "headers": {k.lower(): v for k, v in self.headers.items()}, "body_len": got}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Server", "uvicorn")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_any
for port in (8000, 8081, 8888, 8889, 80):
    s = ThreadingHTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
threading.Event().wait()
"""

failures: list[str] = []


def check(ok: bool, label: str) -> None:
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        failures.append(label)


def sh(*args: str, check_rc: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, check=check_rc)


def curl(host: str, path: str, *extra: str) -> tuple[int, dict[str, str], str]:
    out = sh(
        "curl", "-sk", "-o", "-", "-D", "/dev/stderr", "--max-time", "20",
        "--resolve", f"{host}:{HTTPS_PORT}:127.0.0.1", *extra, f"https://{host}:{HTTPS_PORT}{path}",
        check_rc=False,
    )
    status, headers = 0, {}
    for line in out.stderr.splitlines():
        if line.startswith("HTTP/"):
            status, headers = int(line.split()[1]), {}
        elif ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
    return status, headers, out.stdout


def wait_ready(host: str) -> None:
    for _ in range(100):
        if curl(host, "/api/health")[0] == 200:
            return
        time.sleep(0.3)
    raise RuntimeError(f"gateway never answered for {host}")


def uvicorn_client(xff: str) -> str:
    """What uvicorn --proxy-headers --forwarded-allow-ips '*' sets as the client address."""
    sys.path.insert(0, str(ROOT / "api"))
    try:
        from uvicorn.middleware.proxy_headers import _TrustedHosts
    except ImportError:  # uvicorn not installed on this host: same rule, inlined
        return xff.split(",")[0].strip()
    return _TrustedHosts("*").get_trusted_client_address(xff)[0]


def gateway(name: str, caddyfile: Path, env: dict[str, str], entrypoint: bool = True) -> None:
    base = [
        "docker", "run", "-d", "--name", PREFIX + name, "--network", NET,
        "-p", f"127.0.0.1:{HTTPS_PORT}:443",
        "-v", f"{caddyfile}:/etc/caddy/Caddyfile:ro",
        "-v", f"{ROOT / 'infrastructure/gateway/entrypoint.sh'}:/etc/caddy/entrypoint.sh:ro",
    ]
    for k, v in env.items():
        base += ["-e", f"{k}={v}"]
    if entrypoint:
        sh(*base, "--entrypoint", "/bin/sh", CADDY_IMAGE, "/etc/caddy/entrypoint.sh", "run")
    else:
        sh(*base, CADDY_IMAGE, "caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")


GW_ENV = {
    "ACME_EMAIL": "ops@example.com",
    "PROD_HOST": "prod.test",
    "UAT_HOST": "uat.test",
    "UAT_ACCESS_TOKEN": "a" * 40,
    "PROD_OWNER_ALLOWED_IPS": "",
    "GATEWAY_TRUSTED_PROXIES": "",
    "GATEWAY_CLIENT_IP_HEADER": "",
}


def slow_headers_cut(host: str) -> float:
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    raw = socket.create_connection(("127.0.0.1", HTTPS_PORT), timeout=40)
    tls = ctx.wrap_socket(raw, server_hostname=host)
    started = time.monotonic()
    tls.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\n".encode())
    tls.settimeout(2)
    try:
        while time.monotonic() - started < 35:
            try:
                tls.sendall(b"X-Slow: 1\r\n")  # trickle a header line every 2 s, never finish
                if tls.recv(1024) == b"":
                    break
            except TimeoutError:
                continue
            except (OSError, ssl.SSLError):
                break
            else:
                break  # the server answered (408/400) and is closing
    finally:
        tls.close()
    return time.monotonic() - started


def main() -> None:
    for image in (CADDY_IMAGE, ECHO_IMAGE):
        if sh("docker", "image", "inspect", image, check_rc=False).returncode:
            sh("docker", "pull", image)
    cleanup()
    sh("docker", "network", "create", NET)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            echo = tmpdir / "echo.py"
            echo.write_text(ECHO)
            # The "API" (also the UAT origin and MediaMTX): echoes what reached it.
            sh("docker", "run", "-d", "--name", PREFIX + "api", "--network", NET, "--network-alias", "api",
               "--network-alias", "mediamtx", "--network-alias", "esabong-uat-origin",
               "-v", f"{echo}:/echo.py:ro", "--entrypoint", "python3", ECHO_IMAGE, "/echo.py")
            # Production origin: the real Caddyfile.origin + snippets with a portal build fixture.
            srv = tmpdir / "srv"
            for portal in ("player", "admin", "owner"):
                (srv / portal / ".well-known").mkdir(parents=True)
                (srv / portal / "index.html").write_text("<!doctype html><title>t</title>")
                (srv / portal / "main-ABCD1234.js").write_text("console.log(1)")
                (srv / portal / "main-ABCD1234.js.map").write_text("{}")
                (srv / portal / ".env").write_text("SECRET=1")
                (srv / portal / "chunk 2.js").write_text("dup")
                (srv / portal / ".well-known" / "security.txt").write_text("Contact: mailto:x@example.com")
            (srv / "media").mkdir()
            sh("docker", "run", "-d", "--name", PREFIX + "origin", "--network", NET,
               "--network-alias", "esabong-prod-origin",
               "-e", "PLATFORM_HOST=owner.test", "-e", "VIDEO_BROWSER_ORIGINS=",
               "-v", f"{ROOT / 'infrastructure/caddy/Caddyfile.origin'}:/etc/caddy/Caddyfile.origin:ro",
               "-v", f"{ROOT / 'infrastructure/caddy/snippets.caddy'}:/etc/caddy/snippets.caddy:ro",
               "-v", f"{srv}:/srv:ro",
               CADDY_IMAGE, "caddy", "run", "--config", "/etc/caddy/Caddyfile.origin", "--adapter", "caddyfile")

            # GATEWAY_CADDYFILE: test another gateway file (e.g. a reload candidate) with the same checks.
            source = Path(os.environ.get("GATEWAY_CADDYFILE", ROOT / "infrastructure/gateway/Caddyfile")).read_text()
            print(f"gateway file: {os.environ.get('GATEWAY_CADDYFILE', 'infrastructure/gateway/Caddyfile')}")
            assert "{\n\temail {$ACME_EMAIL}" in source
            config = tmpdir / "Caddyfile"
            config.write_text(source.replace("{\n\temail {$ACME_EMAIL}", "{\n\tlocal_certs\n\tskip_install_trust\n\temail {$ACME_EMAIL}", 1))

            # ---------------------------------------------------------------- default mode
            gateway("gw", config, GW_ENV)
            wait_ready("prod.test")

            status, headers, body = curl(
                "prod.test", "/api/v1/echo?token=secret",
                "-H", "X-Forwarded-For: 6.6.6.6", "-H", "X-Real-IP: 6.6.6.7", "-H", "Forwarded: for=6.6.6.8",
            )
            seen = json.loads(body)["headers"]
            xff = seen.get("x-forwarded-for", "")
            hops = [h.strip() for h in xff.split(",")]
            check(status == 200 and "6.6.6" not in json.dumps(seen), "forged X-Forwarded-For/X-Real-IP/Forwarded never reach the API")
            check(len(hops) == 2, f"API sees exactly client + gateway hop (got {xff!r})")
            client = hops[0]
            check(uvicorn_client(xff) == client and not client.startswith("6.6.6"), f"uvicorn resolves the verified client ({client})")
            check("server" not in headers and "via" not in headers, "no Server/Via banner on API responses")
            check("alt-svc" not in headers, "HTTP/3 is not advertised (UDP 443 is closed)")

            status, headers, _ = curl("prod.test", "/")
            check(status == 200 and headers.get("x-frame-options") == "DENY", "portal served with X-Frame-Options DENY")
            check(headers.get("x-content-type-options") == "nosniff" and "content-security-policy" in headers, "portal keeps CSP/nosniff")
            check("server" not in headers and "via" not in headers, "no Server/Via banner on portal responses")
            check(curl("prod.test", "/main-ABCD1234.js")[0] == 200, "hashed bundle still served")
            check(curl("prod.test", "/main-ABCD1234.js.map")[0] == 404, "source map not served")
            check(curl("prod.test", "/.env")[0] == 404, "hidden file not served")
            check(curl("prod.test", "/chunk%202.js")[0] == 404, "Finder duplicate not served")
            check(curl("prod.test", "/.well-known/security.txt")[0] == 200, ".well-known still served")
            probes = ("/wp-admin/install.php", "/xmlrpc.php", "/.git/config", "/.env.production", "/missing.js.map", "/x%202.js")
            got = {p: curl("prod.test", p)[0] for p in probes}
            check(set(got.values()) == {404}, f"nonexistent probe paths answer 404, not the portal shell {got}")
            check(curl("prod.test", "/")[0] == 200 and curl("prod.test", "/index.html")[0] == 200, "portal shell itself still served")
            check(curl("prod.test", "/events/123/live")[0] == 200, "deep portal routes still fall back to the SPA")
            check(curl("prod.test", "/admin/main-ABCD1234.js.map")[0] == 404, "admin portal: source map not served")
            check(curl("prod.test", "/api/v1/internal/tls-ask")[0] == 404, "internal API routes stay unreachable")

            big = tmpdir / "big.bin"
            big.write_bytes(b"0" * (11 * 1024 * 1024))
            check(curl("prod.test", "/api/v1/upload", "-X", "POST", "--data-binary", f"@{big}")[0] == 413, "API body > 10 MB refused (413)")
            status, _, body = curl("prod.test", "/api/v1/upload", "-X", "POST", "--data-binary", "x" * 2000)
            check(status == 200 and json.loads(body)["body_len"] == 2000, "normal API body passes intact")
            sdp = tmpdir / "sdp.bin"
            sdp.write_bytes(b"v" * (300 * 1024))
            whep = "/stream/webrtc/live/abcdefghijklmnopqrstuvwx/whep"
            check(curl("prod.test", whep, "-X", "POST", "--data-binary", f"@{sdp}")[0] == 413, "stream signalling body > 256 KB refused")
            status, _, body = curl("prod.test", whep, "-X", "POST", "--data-binary", "v=0" * 300)
            check(status == 200 and json.loads(body)["port"] == 8889, "normal WHEP offer reaches MediaMTX")
            status, _, body = curl("prod.test", "/stream/hls/live/abcdefghijklmnopqrstuvwx/index.m3u8")
            check(status == 200 and json.loads(body)["port"] == 8888, "HLS playlist reaches MediaMTX untouched")
            status, _, _ = curl("prod.test", "/api/v1/x", "--http1.1", "-H", "X-Big: " + "h" * (70 * 1024))
            check(status == 431, f"header block > 64 KB refused (431, got {status})")
            status, _, body = curl("shop.customer.example", "/api/v1/echo", "-H", "X-Forwarded-For: 6.6.6.6")
            check(status == 200 and "6.6.6" not in body and json.loads(body)["headers"].get("host", "").startswith("shop.customer.example"),
                  f"approved custom domain (on-demand TLS) routed with the verified client address ({status})")
            status, _, body = curl("uat.test", "/api/v1/echo", "-H", "X-Forwarded-For: 6.6.6.6")
            check(status == 200 and "6.6.6" not in body, "UAT: forged X-Forwarded-For dropped too")
            took = slow_headers_cut("prod.test")
            check(13 <= took <= 25, f"slow (slowloris) headers cut by the server after {took:.1f} s")
            ws = curl("prod.test", "/api/ws", "--http1.1", "-H", "Connection: Upgrade", "-H", "Upgrade: websocket",
                      "-H", "Sec-WebSocket-Version: 13", "-H", "Sec-WebSocket-Key: ZXNhYm9uZy1tb25pdG9yIQ==")
            check(ws[0] == 200 and json.loads(ws[2])["headers"].get("upgrade") == "websocket", "WebSocket upgrade still forwarded to the API")
            sh("docker", "rm", "-f", PREFIX + "gw")

            # ---------------------------------------------------------------- owner allowlist
            gateway("gw", config, {**GW_ENV, "PROD_OWNER_ALLOWED_IPS": "203.0.113.9/32"})
            wait_ready("prod.test")
            check(curl("prod.test", "/owner/", "-H", "X-Forwarded-For: 203.0.113.9")[0] == 403,
                  "owner allowlist cannot be bypassed with a forged X-Forwarded-For")
            sh("docker", "rm", "-f", PREFIX + "gw")

            # ---------------------------------------------------------------- entrypoint validation
            for bad, why in (
                ({"GATEWAY_TRUSTED_PROXIES": "0.0.0.0/0"}, "trust everyone"),
                ({"GATEWAY_TRUSTED_PROXIES": "172.16.0.0/12"}, "trust a private range"),
                ({"GATEWAY_CLIENT_IP_HEADER": "X-Client"}, "unknown client header"),
            ):
                args = ["docker", "run", "--rm", "--entrypoint", "/bin/sh"]
                for k, v in {**GW_ENV, **bad}.items():
                    args += ["-e", f"{k}={v}"]
                args += ["-v", f"{config}:/etc/caddy/Caddyfile:ro",
                         "-v", f"{ROOT / 'infrastructure/gateway/entrypoint.sh'}:/etc/caddy/entrypoint.sh:ro",
                         CADDY_IMAGE, "/etc/caddy/entrypoint.sh", "validate"]
                rc = subprocess.run(args, check=False, capture_output=True, text=True).returncode
                check(rc == 64, f"entrypoint refuses to {why}")
            args = ["docker", "run", "--rm", "--entrypoint", "/bin/sh"]
            for k, v in {**GW_ENV, "GATEWAY_TRUSTED_PROXIES": "173.245.48.0/20 2400:cb00::/32",
                         "GATEWAY_CLIENT_IP_HEADER": "CF-Connecting-IP"}.items():
                args += ["-e", f"{k}={v}"]
            args += ["-v", f"{config}:/etc/caddy/Caddyfile:ro",
                     "-v", f"{ROOT / 'infrastructure/gateway/entrypoint.sh'}:/etc/caddy/entrypoint.sh:ro",
                     CADDY_IMAGE, "/etc/caddy/entrypoint.sh", "validate"]
            check(subprocess.run(args, check=False, capture_output=True, text=True).returncode == 0, "entrypoint accepts Cloudflare ranges")

            # ---------------------------------------------------------------- upstream proxy mode
            # The test client reaches the gateway from Docker's private host address, which the real
            # entrypoint (rightly) refuses to trust; run Caddy directly with that range trusted.
            peer = client.rsplit(".", 1)[0] + ".0/24" if "." in client else client + "/128"
            env = {**GW_ENV, "GATEWAY_TRUSTED_PROXIES": peer, "GATEWAY_CLIENT_IP_HEADER": "CF-Connecting-IP",
                   "UAT_ALLOWED_IPS": "192.0.2.255/32", "PROD_OWNER_ALLOWED_IPS": "0.0.0.0/0 ::/0"}
            gateway("gw", config, env, entrypoint=False)
            wait_ready("prod.test")
            _, _, body = curl("prod.test", "/api/v1/echo", "-H", "CF-Connecting-IP: 198.51.100.23", "-H", "X-Forwarded-For: 6.6.6.6")
            xff = json.loads(body)["headers"].get("x-forwarded-for", "")
            check(xff.split(",")[0].strip() == "198.51.100.23" and "6.6.6" not in xff,
                  f"from a trusted proxy the visitor address comes from CF-Connecting-IP ({xff!r})")
            check(uvicorn_client(xff) == "198.51.100.23", "uvicorn resolves the visitor behind the proxy")
            sh("docker", "rm", "-f", PREFIX + "gw")
            env["GATEWAY_TRUSTED_PROXIES"] = "203.0.113.0/24"  # the test client is NOT this proxy
            gateway("gw", config, env, entrypoint=False)
            wait_ready("prod.test")
            _, _, body = curl("prod.test", "/api/v1/echo", "-H", "CF-Connecting-IP: 198.51.100.23")
            xff = json.loads(body)["headers"].get("x-forwarded-for", "")
            check("198.51.100.23" not in xff, "a direct-to-origin client cannot claim an address with CF-Connecting-IP")
    except BaseException:
        for name in ("gw", "origin", "api"):
            logs = sh("docker", "logs", "--tail", "30", PREFIX + name, check_rc=False)
            print(f"--- {name}\n{logs.stdout[-3000:]}{logs.stderr[-3000:]}")
        raise
    finally:
        cleanup()
    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
    sys.exit(1 if failures else 0)


def cleanup() -> None:
    for name in ("gw", "origin", "api"):
        sh("docker", "rm", "-f", PREFIX + name, check_rc=False)
    sh("docker", "network", "rm", NET, check_rc=False)


if __name__ == "__main__":
    main()
