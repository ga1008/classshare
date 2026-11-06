"""Explicit client-only attachment to an owned synthetic Linux capacity lab.

No application import, database access, arbitrary DSN or runtime discovery.
The Linux controller remains responsible for final DB/image/source verification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import socket
from urllib.parse import urlsplit


IDENTITY_PATH = "/api/internal/capacity-lab-identity"
CONFIG_KEYS = frozenset({"lq_families", "threadpool_tokens", "postgres_pool_max", "uvicorn_limit_concurrency",
    "workers", "host", "backlog", "timeout_keep_alive", "ws_ping_interval", "ws_ping_timeout",
    "cpu_limit", "memory_limit_bytes"})
ARTIFACT_NAMES = frozenset({"fixture.json", "capacity-social.json", "capacity-reading.json"})
HEX = re.compile(r"[a-f0-9]{64}")


class LabManifestError(ValueError):
    pass


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def source_hashes(root):
    paths = [*sorted((root / "classroom_app").rglob("*.py")), root / "scheduled_task_dispatcher.py"]
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _json(path, *, expected_sha256=None, with_sha=False):
    if not path.is_file() or path.stat().st_size > 2_000_000:
        raise LabManifestError("Missing or oversized lab artifact")
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise LabManifestError("Duplicate lab JSON field")
            result[key] = value
        return result
    def invalid_constant(_value):
        raise LabManifestError("Nonfinite lab JSON number")
    try:
        with path.open("rb") as stream:
            raw = stream.read(2_000_001)
        if len(raw) > 2_000_000:
            raise LabManifestError("Oversized lab artifact")
        digest = hashlib.sha256(raw).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise LabManifestError("Lab artifact changed after fingerprint verification")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid_constant)
        return (value, digest) if with_sha else value
    except (UnicodeError, json.JSONDecodeError):
        raise LabManifestError("Invalid lab JSON") from None


def _owned(path, parent):
    try:
        result = Path(path).resolve(strict=True)
    except (OSError, TypeError, ValueError):
        raise LabManifestError("Lab artifact path is unavailable") from None
    if result == parent or not result.is_relative_to(parent):
        raise LabManifestError("Lab artifacts must stay inside the owned client runtime")
    return result


def _digest_map(value):
    if not isinstance(value, dict) or not value:
        raise LabManifestError("Lab source hashes are required")
    for name, digest in value.items():
        if not isinstance(name, str):
            raise LabManifestError("Invalid lab source hash path")
        path = PurePosixPath(name)
        if ("\\" in name or path.is_absolute() or ".." in path.parts
                or not (name == "scheduled_task_dispatcher.py" or name.startswith("classroom_app/") and name.endswith(".py"))
                or not isinstance(digest, str) or HEX.fullmatch(digest) is None):
            raise LabManifestError("Invalid lab source hash entry")
    return dict(value)


@dataclass(repr=False)
class LabManifest:
    path: Path
    runtime: Path
    root: Path
    base_url: str
    ca_file: Path
    release_id: str
    expected_identity: dict
    client_sources: dict
    server_sources: dict
    server_config: dict
    identifiers: dict
    fixture: dict = field(repr=False)
    jwt_secret: str = field(repr=False)
    file_hashes: dict = field(repr=False)

    def validate_response(self, response):
        if response.headers.get("x-lanshare-release") != self.release_id:
            raise LabManifestError("Lab response release mismatch")

    def validate_health(self, health):
        if (not isinstance(health, dict) or health.get("release_id") != self.release_id
                or health.get("service") != "main" or health.get("status") != "ok"):
            raise LabManifestError("Lab health identity mismatch")

    async def verify_identity(self, client):
        response = await client.get(IDENTITY_PATH)
        if response.status_code != 200:
            raise LabManifestError("Lab identity endpoint unavailable")
        body = response.json()
        if not isinstance(body, dict) or any(type(body.get(key)) is not type(value) or body.get(key) != value
                                              for key, value in self.expected_identity.items()):
            raise LabManifestError("Lab identity does not match the explicit controller manifest")
        health = await client.get("/api/internal/health")
        if health.status_code != 200:
            raise LabManifestError("Lab health endpoint is not ready")
        self.validate_response(health)
        self.validate_health(health.json())

    def artifacts_unchanged(self):
        return all(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
                   for path, digest in self.file_hashes.items())

    def socket_options(self):
        return {"lab_manifest": self.path, "lab_manifest_sha256": self.file_hashes[self.path],
                "lab_nonce": self.expected_identity["lab_nonce"]}

    def evidence(self):
        return {"mode": "linux_lab_attach_only", "lab_nonce": self.expected_identity["lab_nonce"],
                "release_id": self.release_id, "manifest_sha256": self.file_hashes[self.path],
                "tls_ca_sha256": self.file_hashes[self.ca_file], "base_url": self.base_url,
                "expected_identity": dict(self.expected_identity), "client_source_sha256": dict(self.client_sources),
                "server_source_sha256": dict(self.server_sources), "server_config": dict(self.server_config),
                "database_accessed_by_client": False, "controller_final_verification_required": True,
                "server_source_evidence": "Controller-published snapshot; not a client verification of Linux files",
                "source_changed_scope": "Client export against manifest; Linux final source verification belongs to controller",
                "scope": "Client load evidence only; Linux controller must separately verify final DB, image, source and resource state"}


def load_lab_manifest(args, root):
    if args.action != "run" or args.users != 200 or getattr(args, "cluster_dir", None) is not None or getattr(args, "pg_port", None) is not None:
        raise LabManifestError("Lab attachment requires run, exactly 200 users and no native DB options")
    if getattr(args, "proxy_port", 0) or getattr(args, "http_port", 18767) != 18767:
        raise LabManifestError("Lab origin must come only from its explicit manifest")
    root = Path(root).resolve(strict=True)
    temporary = (root / ".codex-temp").resolve(strict=True)
    runtime = _owned(args.runtime, temporary)
    path = _owned(args.lab_manifest, runtime)
    data, manifest_sha = _json(path, with_sha=True)
    if (not isinstance(data, dict) or data.get("schema_version") != 1 or type(data.get("schema_version")) is not int
            or data.get("kind") != "capacity-attach-client"
            or data.get("synthetic") is not True or data.get("uiV3Synthetic") is not True
            or not isinstance(data.get("lab_nonce"), str) or re.fullmatch(r"[a-f0-9]{32}", data["lab_nonce"]) is None):
        raise LabManifestError("Explicit versioned synthetic lab identity required")
    release = data.get("release_id")
    if not isinstance(release, str) or re.fullmatch(r"[A-Za-z0-9._-]{1,120}", release) is None:
        raise LabManifestError("Invalid lab release identity")
    try:
        url = urlsplit(str(data.get("base_url", "")))
        port = url.port
    except (TypeError, ValueError):
        raise LabManifestError("Invalid lab origin") from None
    if (url.scheme != "https" or url.hostname not in {"127.0.0.1", "::1"} or port is None
            or not 1024 <= port <= 65535 or url.username or url.password or url.path not in {"", "/"} or url.query or url.fragment):
        raise LabManifestError("Lab origin must be an explicit loopback high-port HTTPS origin")
    ca_file = _owned(data.get("ca_file", ""), runtime)
    ca_hash = data.get("ca_sha256")
    if not isinstance(ca_hash, str) or HEX.fullmatch(ca_hash) is None or hashlib.sha256(ca_file.read_bytes()).hexdigest() != ca_hash:
        raise LabManifestError("Lab TLS CA fingerprint mismatch")
    users = data.get("users")
    if (not isinstance(users, list) or len(users) != 200
            or any(not isinstance(row, dict) or set(row) != {"identifier", "student_id"}
                   or row.get("identifier") != f"CAPACITY-SYNTHETIC-{index:04d}"
                   or type(row.get("student_id")) is not int or row["student_id"] <= 0 for index, row in enumerate(users))
            or len({row["student_id"] for row in users}) != 200):
        raise LabManifestError("Exactly 200 distinct ordered synthetic student identities required")
    if type(data.get("offering_id")) is not int or data["offering_id"] <= 0:
        raise LabManifestError("Positive synthetic classroom identity required")
    for name in ("synthetic_password", "synthetic_jwt_secret"):
        value = data.get(name)
        if not isinstance(value, str) or not 16 <= len(value) <= 512 or any(ord(char) < 32 for char in value):
            raise LabManifestError("Explicit synthetic credentials required")
    client_sources, server_sources = _digest_map(data.get("client_source_sha256")), _digest_map(data.get("server_source_sha256"))
    if client_sources != source_hashes(root) or client_sources.keys() != server_sources.keys():
        raise LabManifestError("Lab source export does not match the complete client snapshot")
    config = data.get("server_config")
    if (not isinstance(config, dict) or not config or config.keys() - CONFIG_KEYS
            or config.get("host") != "linux"
            or any((not isinstance(value, str) or re.fullmatch(r"[a-z0-9,_-]{0,500}", value) is None)
                   if key == "lq_families" else (type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1_000_000_000_000)
                   for key, value in config.items() if key != "host")
            or any(data[name] in json.dumps(config, ensure_ascii=False) for name in ("synthetic_password", "synthetic_jwt_secret"))):
        raise LabManifestError("Lab server configuration must contain only reviewed nonsecret fields")
    expected = {"schema_version": 1, "synthetic": True, "lab_nonce": data["lab_nonce"], "release_id": release,
                "source_fingerprint": fingerprint(server_sources), "config_fingerprint": fingerprint(config),
                "users_sha256": fingerprint(users), "offering_id": data["offering_id"]}
    identity = data.get("expected_identity")
    if (not isinstance(identity, dict) or set(identity) != {*expected, "static_graph_sha256"}
            or any(type(identity.get(key)) is not type(value) or identity.get(key) != value for key, value in expected.items())
            or not isinstance(identity.get("static_graph_sha256"), str) or HEX.fullmatch(identity["static_graph_sha256"]) is None):
        raise LabManifestError("Lab expected identity fingerprints are inconsistent")
    artifacts = data.get("artifact_sha256")
    required = {"fixture.json"} | ({"capacity-social.json"} if args.social else set()) | ({"capacity-reading.json"} if args.reading else set())
    if not isinstance(artifacts, dict) or not required.issubset(artifacts) or artifacts.keys() - ARTIFACT_NAMES:
        raise LabManifestError("Explicit synthetic client artifact fingerprints required")
    hashes = {path: manifest_sha, ca_file: ca_hash}
    for name, digest in artifacts.items():
        artifact = _owned(runtime / name, runtime)
        if not isinstance(digest, str) or HEX.fullmatch(digest) is None or hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
            raise LabManifestError("Lab client artifact fingerprint mismatch")
        hashes[artifact] = digest
    fixture = _json(runtime / "fixture.json", expected_sha256=artifacts["fixture.json"])
    if (not isinstance(fixture, dict) or fixture.get("uiV3Synthetic") is not True
            or fixture.get("kind") != "capacity-attach-client" or fixture.get("lab_nonce") != data["lab_nonce"]
            or "databasePath" in fixture
            or type(fixture.get("classOfferingId")) is not int or fixture["classOfferingId"] != data["offering_id"]
            or fixture.get("password") != data["synthetic_password"]):
        raise LabManifestError("Synthetic client fixture does not match lab identity")
    if args.social:
        social = _json(runtime / "capacity-social.json", expected_sha256=artifacts["capacity-social.json"])
        if (not isinstance(social, dict) or social.get("synthetic") is not True
                or social.get("identifiers") != [row["identifier"] for row in users]
                or social.get("student_ids") != [row["student_id"] for row in users]):
            raise LabManifestError("Lab social metadata belongs to a different student cohort")
    return LabManifest(path, runtime, root, data["base_url"].rstrip("/"), ca_file, release, dict(identity),
        client_sources, server_sources, dict(config), {row["identifier"]: row["student_id"] for row in users},
        fixture, data["synthetic_jwt_secret"], hashes)


def validate_socket_lab(runtime, base_url, cafile, manifest_path, manifest_sha256, nonce, root):
    """File-only spawn guard; the parent separately verifies the HTTPS identity.

    No dummy database file/path and no database or HTTP connection is involved.
    This branch is explicit: native workers keep their original fixture guard.
    """
    root = Path(root).resolve(strict=True)
    temporary = (root / ".codex-temp").resolve(strict=True)
    runtime = _owned(runtime, temporary)
    path = _owned(manifest_path, runtime)
    if not isinstance(manifest_sha256, str) or HEX.fullmatch(manifest_sha256) is None:
        raise LabManifestError("Explicit parent-pinned lab manifest fingerprint required")
    data = _json(path, expected_sha256=manifest_sha256)
    if (not isinstance(data, dict) or data.get("kind") != "capacity-attach-client"
            or type(data.get("schema_version")) is not int or data["schema_version"] != 1
            or data.get("synthetic") is not True or data.get("uiV3Synthetic") is not True
            or not isinstance(nonce, str) or re.fullmatch(r"[a-f0-9]{32}", nonce) is None or data.get("lab_nonce") != nonce
            or data.get("base_url", "").rstrip("/") != str(base_url).rstrip("/")):
        raise LabManifestError("Socket lab identity or target does not match the parent")
    certificate = _owned(cafile, runtime)
    if certificate != _owned(data.get("ca_file", ""), runtime) or hashlib.sha256(certificate.read_bytes()).hexdigest() != data.get("ca_sha256"):
        raise LabManifestError("Socket lab TLS CA does not match the parent manifest")
    if type(data.get("offering_id")) is not int or data["offering_id"] <= 0:
        raise LabManifestError("Invalid socket lab classroom identity")
    return data["offering_id"]


def install_client_boundary(lab):
    """Dedicated CLI process: discard credentials and forbid all PostgreSQL use."""
    essential = {name: value for name, value in os.environ.items() if name.upper() in {
        "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PATH", "PATHEXT", "COMSPEC", "TEMP", "TMP", "TMPDIR",
        "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA"}}
    os.environ.clear()
    os.environ.update(essential)
    from tools.isolated_environment import guard_dotenv_loading, guard_postgres_connections, isolate_sqlite_environment
    isolate_sqlite_environment(lab.runtime)
    guard_dotenv_loading()
    guard_postgres_connections()
    os.environ["SECRET_KEY"] = lab.jwt_secret
    connect, connect_ex, dns = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    def allowed(host):
        return host is None or str(host).lower() in {"localhost", "127.0.0.1", "::1"}
    def local_connect(sock, address):
        if not allowed(address[0]):
            raise LabManifestError("Lab client forbids external sockets")
        return connect(sock, address)
    def local_connect_ex(sock, address):
        if not allowed(address[0]):
            raise LabManifestError("Lab client forbids external sockets")
        return connect_ex(sock, address)
    def local_dns(host, *values, **options):
        if not allowed(host):
            raise LabManifestError("Lab client forbids external DNS")
        return dns(host, *values, **options)
    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = local_connect, local_connect_ex, local_dns
