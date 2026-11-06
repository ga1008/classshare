"""Prepare a loopback-only native copy of the production Nginx config.

Uses an explicitly installed portable test binary, ephemeral self-signed TLS,
and the same built asset graph. Windows results do not certify Linux capacity.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import ipaddress
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    args = parser.parse_args()
    prefix = args.prefix.resolve(strict=True)
    temporary = (ROOT / ".codex-temp").resolve()
    if prefix == temporary or not prefix.is_relative_to(temporary) or not (prefix / "nginx.exe").is_file():
        raise ValueError("Use an explicit portable test installation under .codex-temp")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "capacity-synthetic.local")])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
            .not_valid_after(now+timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    (prefix / "logs").mkdir(exist_ok=True)
    (prefix / "capacity-test.key").write_bytes(key.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    (prefix / "capacity-test.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    source = (ROOT / "nginx.conf").read_text(encoding="utf-8")
    # Ordinary HTTP reuses its pool; upgraded sockets use a distinct fresh
    # connection pool. Derive both exact same-backend upstreams for this lab.
    assert source.count("upstream lanshare_app {") == 1
    assert source.count("upstream lanshare_realtime {") == 1
    assert source.count("server app:8000;") == 2
    source = source.replace("server app:8000;", "server 127.0.0.1:18767;")
    source = source.replace("listen 80;", "listen 127.0.0.1:18768 backlog=2048;")
    source = source.replace("listen 443 ssl;", "listen 127.0.0.1:18769 ssl backlog=2048;")
    source = source.replace("/etc/nginx/ssl/guardianangel.net.cn_bundle.crt", (prefix / "capacity-test.crt").as_posix())
    source = source.replace("/etc/nginx/ssl/guardianangel.net.cn.key", (prefix / "capacity-test.key").as_posix())
    source = source.replace("/srv/lanshare", ROOT.as_posix())
    source = source.replace("/var/log/nginx/access.log", (prefix / "logs/capacity-access.log").as_posix())
    config = "worker_processes 1;\npid logs/capacity-nginx.pid;\nevents { use poll; worker_connections 2048; }\nhttp {\ninclude conf/mime.types;\n" + source + "\n}\n"
    target = prefix / "capacity-nginx.conf"
    target.write_text(config, encoding="utf-8")
    subprocess.run([str(prefix / "nginx.exe"), "-p", prefix.as_posix()+"/", "-c", target.as_posix(), "-t"],
                   cwd=prefix, check=True, timeout=15)
    print("Validated loopback-only Nginx config on ports 18768/18769; ephemeral TLS; production unchanged")


if __name__ == "__main__":
    main()
