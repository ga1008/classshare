"""Exercise the repository's two game TLS vhosts on loopback with a disposable database and certificate."""

import argparse
import asyncio
import base64
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import multiprocessing
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.isolated_environment import (
    guard_dotenv_loading,
    guard_postgres_connections,
    isolate_sqlite_environment,
)


def certificate(directory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "LanShare disposable TLS probe")]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
        .sign(key, hashes.SHA256())
    )
    (directory / "test.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (directory / "test.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(hashlib.sha256(spki).digest()).decode("ascii")


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def quoted(path):
    return '"' + path.resolve().as_posix().replace('"', '\\"') + '"'


@contextmanager
def nginx(binary, temp, main_upstream, content_upstream, api_upstream, tls):
    executable = Path(shutil.copy2(binary, temp / binary.name))
    (temp / "logs").mkdir()
    (temp / "conf").mkdir()
    (temp / "temp").mkdir()
    edge = (ROOT / "nginx.conf").read_text(encoding="utf-8")
    content = (ROOT / "deployment/nginx-game-content.conf.example").read_text(
        encoding="utf-8"
    )
    api_source = (ROOT / "deployment/nginx-game-api-location.conf").read_text(
        encoding="utf-8"
    )
    if (
        "/etc/nginx/lanshare-optional/*.conf" not in edge
        or "http://game-api:8015" not in api_source
    ):
        raise RuntimeError(
            "The repository game API location and main vhost include must be enabled for this probe."
        )
    (temp / "conf/game-api.conf").write_text(
        api_source.replace("http://game-api:8015", api_upstream).replace(
            "/dev/null", os.devnull
        ),
        encoding="utf-8",
    )
    http = port()
    for source, replacement in {
        "listen 80;": f"listen 127.0.0.1:{http};",
        "listen 443 ssl;": f"listen 127.0.0.1:{tls} ssl;",
        "/etc/nginx/ssl/guardianangel.net.cn_bundle.crt": "test.crt",
        "/etc/nginx/ssl/guardianangel.net.cn.key": "test.key",
        "server_name guardianangel.net.cn www.guardianangel.net.cn;": "server_name 127.0.0.1;",
        "server app:8000;": f"server {urlsplit(main_upstream).netloc};",
        "/etc/nginx/lanshare-optional/*.conf": "conf/game-api.conf",
        "root /srv/lanshare;": f"root {quoted(ROOT)};",
        "/var/log/nginx/access.log": "logs/access.log",
    }.items():
        edge = edge.replace(source, replacement)
    for source, replacement in {
        "listen 80;": f"listen 127.0.0.1:{http};",
        "listen 443 ssl;": f"listen 127.0.0.1:{tls} ssl;",
        "games.example.edu": "localhost",
        "/etc/nginx/game-tls/fullchain.pem": "test.crt",
        "/etc/nginx/game-tls/privkey.pem": "test.key",
        "http://game-content:8014": content_upstream,
        "/var/log/nginx/access.log": "logs/access.log",
        "/dev/null": os.devnull,
    }.items():
        content = content.replace(source, replacement)
    listeners = re.findall(
        r"^\s*listen\s+([^;]+);", edge + "\n" + content, re.MULTILINE
    )
    if not listeners or any(not item.startswith("127.0.0.1:") for item in listeners):
        raise RuntimeError("The configured probe must listen on loopback only.")
    (temp / "conf/main-vhost.conf").write_text(edge, encoding="utf-8")
    (temp / "conf/content-vhost.conf").write_text(content, encoding="utf-8")
    mime = next(
        (
            p
            for p in (binary.parent / "conf/mime.types", Path("/etc/nginx/mime.types"))
            if p.is_file()
        ),
        None,
    )
    if not mime:
        raise RuntimeError(
            "The Nginx distribution must include its conf/mime.types file."
        )
    config = f"""daemon off;
master_process off;
worker_processes 1;
pid logs/nginx.pid;
error_log logs/error.log notice;
events {{ worker_connections 256; }}
http {{
    include {quoted(mime)};
    include conf/main-vhost.conf;
    include conf/content-vhost.conf;
}}
"""
    (temp / "nginx.conf").write_text(config, encoding="utf-8")
    cmd = [str(executable), "-p", temp.as_posix() + "/", "-c", "nginx.conf"]
    opts = {"cwd": temp, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    checked = subprocess.run([*cmd, "-t"], capture_output=True, text=True, **opts)
    if checked.returncode:
        log = temp / "logs/error.log"
        details = (
            log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        )
        raise RuntimeError(
            "Nginx rejected the loopback vhosts. " + checked.stderr + details
        )
    with (temp / "logs/process.log").open("wb") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=log, **opts)
        try:
            until = time.monotonic() + 8
            while True:
                if proc.poll() is not None:
                    raise RuntimeError(
                        "Nginx stopped before its loopback listener became ready."
                    )
                try:
                    with socket.create_connection(("127.0.0.1", tls), 0.25):
                        break
                except OSError:
                    if time.monotonic() >= until:
                        raise RuntimeError("Nginx loopback listener timed out.")
                    time.sleep(0.05)
            yield http
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(5)


def serve_backend(role, root, listener, started, stop, observations):
    """Each backend imports the real ASGI entry point against the same isolated fixture."""
    import importlib
    import uvicorn

    isolate_sqlite_environment(root)
    guard_dotenv_loading()
    guard_postgres_connections()
    os.environ["MAIN_DB_PATH"] = str(root / "classroom.db")
    if role == "classroom":
        os.environ["MINIGAMES_EXTERNAL_API"] = "1"
    apps = {
        "classroom": "classroom_app.app",
        "game-api": "classroom_app.minigame_api",
        "content": "classroom_app.minigame_content",
    }
    app = importlib.import_module(apps[role]).app

    async def measured(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        path = scope["path"]
        if path.startswith("/api/games/v1/"):
            kind = "gameApi"
        elif path == "/api/profile/ui-preferences":
            kind = "classroomApi"
        elif path == "/games" or path.startswith("/games/"):
            kind = "gamePage"
        elif path.startswith("/l/"):
            kind = "asset"
        else:
            kind = "other"
        names = {k for k, _ in scope["headers"]}
        observations.write(
            json.dumps([kind, b"cookie" in names, b"authorization" in names]) + "\n"
        )
        observations.flush()

        async def identify(message):
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message["headers"],
                    (b"x-minigame-probe-upstream", role.encode("ascii")),
                ]
            await send(message)

        return await app(scope, receive, identify)

    service = uvicorn.Server(
        uvicorn.Config(
            measured,
            log_level="error",
            access_log=False,
            lifespan="on" if role == "game-api" else "off",
        )
    )

    async def run():
        task = asyncio.create_task(service.serve(sockets=[listener]))
        while not service.started and not task.done():
            await asyncio.sleep(0.025)
        if service.started:
            started.set()
        while not stop.is_set() and not task.done():
            await asyncio.sleep(0.1)
        service.should_exit = True
        await task

    asyncio.run(run())


def backend_entry(role, root, listener, started, stop, observations):
    with listener, observations.open("x", encoding="utf-8") as output:
        serve_backend(role, root, listener, started, stop, output)


@contextmanager
def backend(role, root, temp):
    spawn = multiprocessing.get_context("spawn")
    started, stop = spawn.Event(), spawn.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        at = listener.getsockname()[1]
        process = spawn.Process(
            target=backend_entry,
            args=(role, root, listener, started, stop, temp / (role + ".jsonl")),
        )
        process.start()
        try:
            end = time.monotonic() + 25
            while not started.wait(0.1):
                if not process.is_alive() or time.monotonic() >= end:
                    raise RuntimeError(
                        f"{role} did not start on loopback (exit={process.exitcode})."
                    )
            yield f"http://127.0.0.1:{at}"
        finally:
            stop.set()
            process.join(8)
            if process.is_alive():
                process.terminate()
                process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
            process.close()


def inspect(binary, temp):
    import httpx
    from playwright.sync_api import sync_playwright

    sys.path.insert(0, str(ROOT / "tests"))
    import test_minigame_platform as fixture

    marker = "probe-" + uuid.uuid4().hex

    spki = certificate(temp)
    tls = port()
    main, asset = f"https://127.0.0.1:{tls}", f"https://localhost:{tls}"
    with ExitStack() as stack:
        f = fixture.GamePlatformTests()
        stack.callback(f.doCleanups)
        f.setUp()
        stack.enter_context(
            patch.dict(
                os.environ,
                {"MINIGAMES_APP_ORIGIN": main, "MINIGAMES_CONTENT_ORIGIN": asset},
            )
        )
        game = f.publish(True)
        main_upstream = stack.enter_context(backend("classroom", f.root, temp))
        api_process = stack.enter_context(ExitStack())
        api_upstream = api_process.enter_context(backend("game-api", f.root, temp))
        content_upstream = stack.enter_context(backend("content", f.root, temp))
        http = stack.enter_context(
            nginx(binary, temp, main_upstream, content_upstream, api_upstream, tls)
        )
        ctx = ssl.create_default_context(cafile=str(temp / "test.crt"))
        client = stack.enter_context(
            httpx.Client(verify=ctx, trust_env=False, timeout=10)
        )
        for path in ("/api/internal/live", "/l/" + marker):
            denied = client.get(f"http://localhost:{http}" + path)
            assert denied.status_code == 404 and "location" not in denied.headers
            assert "x-minigame-probe-upstream" not in denied.headers

        def signed(uid):
            claims = fixture.dependencies.decode_token_payload(f.tokens[uid])
            return {
                "Cookie": "access_token=" + f.tokens[uid],
                "Origin": main,
                "X-LanGame-CSRF": fixture.p.csrf_token(
                    {
                        "actor": claims["role"] + ":" + str(uid),
                        "session_id": claims["session_id"],
                    }
                ),
                "Idempotency-Key": uuid.uuid4().hex,
            }

        def write(uid, path, body):
            response = client.post(
                main + "/api/games/v1/" + path, headers=signed(uid), json=body
            )
            assert (
                response.status_code == 200
                and response.headers.get("x-minigame-probe-upstream") == "game-api"
            ), (path.split("/")[0], response.status_code)
            return response

        classroom = client.get(
            main + "/api/profile/ui-preferences",
            headers={"Cookie": "access_token=" + f.tokens[931]},
        )
        assert (
            classroom.status_code == 200
            and classroom.headers.get("x-minigame-probe-upstream") == "classroom"
        )
        hall = client.get(
            main + "/games", headers={"Cookie": "access_token=" + f.tokens[931]}
        )
        assert (
            hall.status_code == 200
            and hall.headers.get("x-minigame-probe-upstream") == "classroom"
        )
        grant = write(
            931,
            "games/" + game["id"] + "/launches",
            {"hostInstance": "https-test", "purpose": "play"},
        ).json()
        path = grant["contentEntryUrl"]
        headers = {"Cookie": "audit=" + marker, "Authorization": "Bearer " + marker}
        head = client.head(path, headers=headers)
        full = client.get(path, headers=headers)
        partial = client.get(path, headers={**headers, "Range": "bytes=0-9"})
        if (head.status_code, full.status_code, partial.status_code) != (200, 200, 206):
            raise AssertionError(
                f"Content edge HEAD/GET/RANGE returned {head.status_code}/{full.status_code}/{partial.status_code}; expected 200/200/206."
            )
        assert not head.content and int(head.headers["content-length"]) == len(
            full.content
        )
        assert (
            partial.content == full.content[:10]
            and partial.headers["content-range"] == f"bytes 0-9/{len(full.content)}"
        )
        assert (
            partial.headers["access-control-allow-origin"] == "null"
            and "sandbox allow-scripts" in full.headers["content-security-policy"]
        )
        assert (
            "set-cookie" not in full.headers
            and full.headers.get("x-minigame-probe-upstream") == "content"
        )
        renewed = write(
            931,
            "launches/" + grant["leaseId"] + "/renew",
            {"hostInstance": "https-test"},
        )
        assert renewed.status_code == 200
        pw = stack.enter_context(sync_playwright())
        browser = pw.chromium.launch(
            executable_path=f.browser_bin(),
            chromium_sandbox=os.name != "nt",
            args=["--ignore-certificate-errors-spki-list=" + spki],
        )
        stack.callback(browser.close)
        page_ctx = browser.new_context()
        page_ctx.add_cookies(
            [{"url": main, "name": "access_token", "value": f.tokens[931]}]
        )
        page = page_ctx.new_page()
        page.goto(main + "/games/" + game["slug"], wait_until="domcontentloaded")
        frame = page.frame_locator("iframe[sandbox]")
        frame.get_by_role("button", name="东", exact=True).click(timeout=15000)
        frame.get_by_text("沿地图向下航行", exact=False).wait_for()
        observed_origin = frame.locator("body").evaluate("()=>window.origin")
        assert (
            observed_origin == "null"
            and page.locator("iframe[sandbox]").get_attribute("sandbox")
            == "allow-scripts"
        )
        write(
            901,
            "admin/games/" + game["id"] + "/unlist",
            {"expectedRevision": game["revision"]},
        )
        after = client.get(path, headers=headers)
        assert after.status_code == 404
        page_ctx.close()
        api_process.close()
        unavailable = client.post(
            main + "/api/games/v1/launches/" + grant["leaseId"] + "/renew",
            headers=signed(931),
            json={"hostInstance": "https-test"},
        )
        assert (
            unavailable.status_code == 502
            and "x-minigame-probe-upstream" not in unavailable.headers
        ), unavailable.status_code
        live = client.get(main + "/api/internal/live")
        dashboard = client.get(
            main + "/dashboard",
            headers={"Cookie": "access_token=" + f.tokens[931]},
            follow_redirects=True,
        )
        assert live.status_code == 200 and dashboard.status_code == 200, (
            live.status_code,
            dashboard.status_code,
        )
        assert (
            live.headers.get("x-minigame-probe-upstream")
            == dashboard.headers.get("x-minigame-probe-upstream")
            == "classroom"
        )
        assert (
            "text/html" in dashboard.headers["content-type"]
            and "/login" not in dashboard.url.path
        )
        observations = {
            name: [
                json.loads(row)
                for row in (temp / (name + ".jsonl"))
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            for name in ("classroom", "game-api", "content")
        }
        assert (
            all(role == "gameApi" for role, _, _ in observations["game-api"])
            and observations["game-api"]
        )
        assert all(
            role not in {"gameApi", "asset"} for role, _, _ in observations["classroom"]
        )
        assert (
            all(
                role == "asset" and not cookie and not auth
                for role, cookie, auth in observations["content"]
            )
            and observations["content"]
        )
        logs = "\n".join(
            p.read_text(encoding="utf-8", errors="replace")
            for p in (temp / "logs").glob("*.log")
        )
        secrets = [
            urlsplit(path).path.split("/")[2],
            grant["leaseId"],
            marker,
            f.tokens[931],
        ]
        assert "/l/redacted" in logs, (
            "Expected redacted game content access lines in Nginx log."
        )
        assert all(
            any("/l/redacted" in line and f"port={number} " in line for line in logs.splitlines())
            for number in (http, tls)
        ), "Expected separate redacted HTTP and HTTPS content access lines."
        assert "/api/games/v1/launches/redacted" in logs, (
            "Expected a redacted platform lease renewal in Nginx log."
        )
        assert not any(value in logs for value in secrets), (
            "Nginx emitted a handle, lease identifier or credential in a log."
        )
        return {
            "nginx": subprocess.run(
                [str(temp / binary.name), "-v"],
                cwd=temp,
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stderr.strip(),
            "loopbackTLS": True,
            "backendProcesses": 3,
            "afterGameApiStop": {
                "gameLeaseRenewal": unavailable.status_code,
                "classroomLiveness": live.status_code,
                "studentHome": dashboard.status_code,
            },
            "observedRequestClasses": {
                name: sorted({r[0] for r in values})
                for name, values in observations.items()
            },
            "content": {
                "head": head.status_code,
                "get": full.status_code,
                "range": partial.status_code,
                "afterUnlist": after.status_code,
                "forwardedClientCredentials": False,
            },
            "opaqueFrameOrigin": observed_origin,
            "twoOriginSdkIntent": True,
            "leaseRenewal": renewed.status_code,
            "logRedaction": True,
            "businessDataUsed": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--nginx",
        type=Path,
        required=True,
        help="Trusted installed nginx executable (distribution with mime.types)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Optional JSON under this checkout's .codex-temp/evidence",
    )
    args = parser.parse_args()
    if not __debug__:
        parser.error("run with Python assertions enabled")
    binary = args.nginx.resolve(strict=True)
    if args.report and not args.report.resolve().is_relative_to(
        ROOT / ".codex-temp/evidence"
    ):
        parser.error("report must be below .codex-temp/evidence")
    scratch = ROOT / ".codex-temp/scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory(prefix="games-https-", dir=scratch) as workspace,
        ExitStack() as guards,
    ):
        temp = Path(workspace)
        isolate_sqlite_environment(temp)
        guard_dotenv_loading(guards)
        guard_postgres_connections(guards)
        guards.enter_context(patch.object(tempfile, "tempdir", str(temp)))
        guards.enter_context(
            patch.dict(
                os.environ,
                {
                    "TMP": str(temp),
                    "TMPDIR": str(temp),
                    "TEMP": str(temp),
                    "SECRET_KEY": secrets.token_urlsafe(48),
                },
            )
        )
        result = inspect(binary, temp)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
