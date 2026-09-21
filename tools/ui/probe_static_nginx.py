"""Exercise the repository nginx static locations with an isolated loopback server.

Requires an existing nginx binary and openssl. This tests the actual nginx engine
and publisher, not Docker mounts, Linux permissions, or a production deployment.
No application configuration, database, or production certificate is loaded.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tarfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.publish_static_assets import publish_static_assets, seed_legacy_vite_assets


def free_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument("--openssl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    nginx = args.nginx.resolve()
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Use a fresh probe output directory; existing evidence is not overwritten")
    output.mkdir(parents=True)
    (output / "logs").mkdir()
    (output / "temp").mkdir()
    source = ROOT / "static"
    public = output / "public"
    destination = public / "static"
    legacy_name = "previous-release-aBcD1234.js"
    legacy_content = b"/* synthetic pre-S0 Vite release */\n"
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        member = tarfile.TarInfo(legacy_name)
        member.size = len(legacy_content)
        archive.addfile(member, io.BytesIO(legacy_content))
    stream.seek(0)
    seeded = seed_legacy_vite_assets(stream, destination)
    published = publish_static_assets(source, destination)
    published_again = publish_static_assets(source, destination)
    manifest = json.loads((source / "assets/manifest.json").read_text(encoding="utf-8"))
    revision = manifest["revision"]
    upstream_requests: list[str] = []

    class MockApplication(BaseHTTPRequestHandler):
        def do_GET(self):
            upstream_requests.append(self.path)
            status = 404 if "missing" in self.path else 200
            content = json.dumps({"mock_upstream": True, "path": self.path}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), MockApplication)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    http_port, https_port = free_port(), free_port()
    while https_port == http_port:
        https_port = free_port()
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    cert, key = output / "probe.crt", output / "probe.key"
    subprocess.run([str(args.openssl.resolve()), "req", "-x509", "-newkey", "rsa:2048",
                    "-nodes", "-days", "1", "-subj", "/CN=localhost", "-keyout", str(key),
                    "-out", str(cert)], check=True, capture_output=True, creationflags=flags)
    original = (ROOT / "nginx.conf").read_text(encoding="utf-8")
    substitutions = {
        "server app:8000;": f"server 127.0.0.1:{upstream.server_port};",
        "listen 80;": f"listen 127.0.0.1:{http_port};",
        "listen 443 ssl;": f"listen 127.0.0.1:{https_port} ssl;",
        "/etc/nginx/ssl/guardianangel.net.cn_bundle.crt": f'"{cert.as_posix()}"',
        "/etc/nginx/ssl/guardianangel.net.cn.key": f'"{key.as_posix()}"',
        "root /srv/lanshare;": f'root "{public.as_posix()}";',
    }
    adapted = original
    for old, new in substitutions.items():
        if adapted.count(old) != 1:
            raise ValueError(f"Repository nginx configuration changed: {old}")
        adapted = adapted.replace(old, new)
    (output / "site.conf").write_text(adapted, encoding="utf-8")
    mime = nginx.parent / "conf/mime.types"
    config = f'''daemon off;
worker_processes 1;
pid logs/nginx.pid;
error_log logs/error.log info;
events {{ worker_connections 64; }}
http {{
    include "{mime.as_posix()}";
    default_type application/octet-stream;
    access_log logs/access.log;
    include "{(output / 'site.conf').as_posix()}";
}}
'''
    (output / "nginx.conf").write_text(config, encoding="utf-8")
    command = [str(nginx), "-p", output.as_posix() + "/", "-c", "nginx.conf"]
    validation = subprocess.run(command + ["-t"], capture_output=True, text=True, creationflags=flags)
    (output / "nginx-test.log").write_text(validation.stdout + validation.stderr, encoding="utf-8")
    validation.check_returncode()
    version = subprocess.run([str(nginx), "-V"], capture_output=True, text=True, creationflags=flags)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # Only the ephemeral loopback certificate above.
    checks = []

    def request(path, headers=None):
        connection = http.client.HTTPSConnection("127.0.0.1", https_port, context=context, timeout=5)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict((k.lower(), v) for k, v in response.getheaders()), response.read()
        finally:
            connection.close()

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)

    process = None
    try:
        with (output / "process.log").open("wb") as log:
            process = subprocess.Popen(command, stdout=log, stderr=log, creationflags=flags)
            deadline = time.monotonic() + 10
            while True:
                if process.poll() is not None:
                    raise RuntimeError("nginx exited during startup")
                try:
                    with socket.create_connection(("127.0.0.1", https_port), timeout=.1):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("nginx did not bind loopback")
                    time.sleep(.05)
            css = manifest["entries"]["css/tailwind-app.css"]
            status, headers, content = request("/static/" + css)
            check("native CSS content and MIME", status == 200 and content == (source / css).read_bytes()
                  and headers.get("content-type", "").startswith("text/css"))
            check("native CSS immutable", "immutable" in headers.get("cache-control", ""))
            check("native CSS bypasses upstream", not upstream_requests)
            status, zipped_headers, zipped = request("/static/" + css, {"Accept-Encoding": "gzip"})
            check("precompressed gzip content", status == 200 and zipped_headers.get("content-encoding") == "gzip"
                  and gzip.decompress(zipped) == content and zipped == (source / (css + ".gz")).read_bytes())
            check("gzip Vary", "Accept-Encoding" in zipped_headers.get("vary", ""))
            status, _, body = request("/static/" + css, {"If-None-Match": headers["etag"]})
            check("conditional immutable response", status == 304 and not body)
            vite = json.loads((source / "dist/manifest.json").read_text(encoding="utf-8"))
            current_js = next(entry["file"] for entry in vite.values() if entry.get("file", "").endswith(".js"))
            status, headers, content = request("/static/dist/" + current_js)
            check("current Vite direct delivery", status == 200 and "immutable" in headers.get("cache-control", "")
                  and content == (source / "dist" / current_js).read_bytes() and not upstream_requests)
            status, headers, content = request("/static/dist/assets/" + legacy_name)
            check("pre-S0 Vite survives publication", status == 200 and content == legacy_content
                  and "immutable" in headers.get("cache-control", "") and not upstream_requests)
            check("publication idempotent", seeded == 1 and published > 0 and published_again == 0)
            for path in ["/static/css/tailwind-app.css", "/static/assets/manifest.json",
                         "/static/dist/manifest.json", "/static/assets/not-a-hash/css/tailwind-app.css",
                         f"/static/assets/{revision}/missing.css", "/static/dist/assets/missing-aBcD1234.js"]:
                count = len(upstream_requests)
                status, headers, content = request(path)
                expected = 404 if "missing" in path else 200
                check("fallback " + path, status == expected and len(upstream_requests) == count + 1
                      and json.loads(content)["mock_upstream"] and "immutable" not in headers.get("cache-control", ""))
    finally:
        if process is not None and process.poll() is None:
            subprocess.run(command + ["-s", "quit"], capture_output=True, creationflags=flags, timeout=10)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Kill only this probe's process tree, never other nginx instances.
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                                   creationflags=flags, check=True)
                else:
                    process.kill()
                process.wait(timeout=5)
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=2)
        report = {"schema": 1, "scope": "local nginx engine, synthetic upstream; not Docker or production",
                  "source_config_sha256": hashlib.sha256(original.encode()).hexdigest(),
                  "nginx_version": version.stderr, "revision": revision,
                  "published": published, "checks": checks, "upstream_requests": upstream_requests,
                  "process_exited": process is None or process.poll() is not None,
                  "ports": [http_port, https_port, upstream.server_port]}
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"passed": len(checks), "report": str(output / "report.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
