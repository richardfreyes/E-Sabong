#!/usr/bin/env python3
"""Serve real origin configuration in disposable Caddy; assert Owner assets and SPA safety."""

import json
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True).strip()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="esb-owner-static-") as directory:
        fixture = Path(directory)
        for portal in ("owner", "admin", "player"):
            root = fixture / portal
            (root / "assets").mkdir(parents=True)
            (root / "index.html").write_text(f"<!doctype html><title>{portal}</title>")
            (root / "main.js").write_text(f"console.info('{portal} fixture');")
            (root / "theme-init.js").write_text("/* theme fixture */")
            (root / "styles.css").write_text("body { color: black; }")
            (root / "assets/logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
            # Hidden/map fixtures must remain inaccessible even when physically present.
            (root / "main.js.map").write_text("{}")
            (root / ".env").write_text("FIXTURE=not-a-secret")
        container = docker(
            "run", "-d", "--rm", "-p", "127.0.0.1::80",
            "-e", "PLATFORM_HOST=owner.test", "-e", "VIDEO_BROWSER_ORIGINS=",
            "-v", f"{ROOT / 'infrastructure/caddy'}:/etc/caddy:ro",
            "-v", f"{fixture}:/srv:ro", "caddy:2.10-alpine",
            "caddy", "run", "--config", "/etc/caddy/Caddyfile.origin", "--adapter", "caddyfile",
        )
        try:
            port = json.loads(docker("inspect", container))[0]["NetworkSettings"]["Ports"]["80/tcp"][0]["HostPort"]

            def get(path: str, host: str = "owner.test") -> tuple[int, str, str]:
                request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Host": host})
                try:
                    with urllib.request.urlopen(request, timeout=3) as response:
                        return response.status, response.headers.get_content_type(), response.read().decode()
                except urllib.error.HTTPError as error:
                    return error.code, "", ""

            for attempt in range(50):
                try:
                    if get("/owner/login")[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(0.1)
            for prefix, portal in (("/owner", "owner"), ("/admin", "admin"), ("", "player")):
                status, mime, body = get(prefix + "/deep/link")
                assert status == 200 and mime == "text/html" and f"<title>{portal}</title>" in body
                for asset, mime_prefix in (("main.js", "text/javascript"), ("theme-init.js", "text/javascript"), ("styles.css", "text/css"), ("assets/logo.svg", "image/svg+xml")):
                    status, mime, body = get(f"{prefix}/{asset}")
                    assert status == 200 and mime == mime_prefix and "<!doctype" not in body, (prefix, asset, status, mime)
                for forbidden in ("main.js.map", ".env", ".git/config"):
                    assert get(f"{prefix}/{forbidden}")[0] == 404, (prefix, forbidden)
            assert get("/owner/main.js", "tenant.test")[0] == 404
            assert get("/owner/login", "tenant.test")[0] == 404
            print("PASS: three portals' assets, deep links, map/hidden-file blocks, Owner host isolation")
        finally:
            docker("rm", "-f", container)


if __name__ == "__main__":
    main()
