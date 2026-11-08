"""Owned Linux Docker capacity lab; no production discovery or inherited env.

prepare/client are file-only Windows operations. Linux build/start/seed/verify
are explicit controller actions, never implicit effects of importing this file.
The production Dockerfile and Nginx source remain unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
LABEL = "io.lanshare.capacity-lab.nonce"
MARKER = Path("/lab/lab-marker.json")
ARTIFACTS = Path("/lab/artifacts")
KIND = "lanshare-capacity-linux-lab"
MODE2 = "capacity-real-domains-v1"
HEX64 = re.compile(r"[a-f0-9]{64}")
NONCE = re.compile(r"[a-f0-9]{32}")
REQUIRED_EXPORT = {
    "Dockerfile", "DockerfileBase", ".dockerignore", "main.py", "scheduled_task_dispatcher.py",
    "ai_assistant.py", "ai_assistant_doc_extract.py", "agent_task_worker.py", "ai_blog_crawler_worker.py", "email_worker.py",
    "package.json", "package-lock.json", "nginx.conf", "deployment/nginx-main.conf", "requirements.txt",
    "requirements.lock.txt", "requirements-docker.txt", "deployment/docker/entrypoint.sh",
    "postcss.config.js", "tailwind.config.js", "tsconfig.json", "vite.config.ts",
    "tools/build_static_assets.mjs", "tools/ui/native_lq_print.mjs", "tools/publish_static_assets.py",
    "deployment/docker/Dockerfile.frontend-deps", "deployment/docker/verify-dependencies.sh",
    "tools/deploy/dependency_images.py", "tools/platform_capacity_linux_lab.py",
    "tools/platform_capacity_linux_seed.py", "tools/platform_capacity_linux_background.py",
    "tools/platform_capacity_lab_manifest.py", "tools/platform_capacity_background.py",
    "tools/platform_capacity_probe.py", "tools/platform_capacity_ambient.py",
    "tools/platform_capacity_socket_workers.py", "tools/platform_capacity_http_pools.py",
    "tools/platform_capacity_social.py", "tools/platform_capacity_reading.py", "tools/isolated_environment.py",
    "tools/db_postgres_export.py",
}
FORBIDDEN_ROOTS = {".git", ".codex-temp", "data", "storage", "attendance", "chat_logs", "homework_submissions",
                   "logs", "rosters", "shared_files", "node_modules", "venv", ".venv", "python_runtime"}
SERVER_CONFIG = {
    "lq_families": "manage-shell,navbar-shell,dashboard,calendar,profile,messages,centered,growth,manage-pages",
    "threadpool_tokens": 32, "postgres_pool_max": 12, "uvicorn_limit_concurrency": 2048,
    "workers": 1, "host": "linux", "backlog": 2048, "timeout_keep_alive": 30,
    "ws_ping_interval": 20.0, "ws_ping_timeout": 20.0,
}
NOFILE_LIMITS = {"soft": 8192, "hard": 8192}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 8_000_000:
        raise ValueError("Missing or oversized lab JSON")
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("Duplicate lab JSON field")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite lab JSON")))


def write_json(path, value, *, private=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if private:
        temporary.chmod(0o600)
        if os.name == "posix":
            owner, actual = path.parent.stat(), temporary.stat()
            if (actual.st_uid, actual.st_gid) != (owner.st_uid, owner.st_gid):
                os.chown(temporary, owner.st_uid, owner.st_gid)
    temporary.replace(path)


def safe_source_path(name):
    if not isinstance(name, str) or "\\" in name or ":" in name or any(ord(char) < 32 for char in name):
        raise ValueError("Source manifest requires POSIX relative paths")
    path = PurePosixPath(name)
    if (path.is_absolute() or not path.parts or name != path.as_posix() or any(part in {"", ".", ".."} for part in path.parts)
            or path.parts[0].lower() in FORBIDDEN_ROOTS or any(part == "__pycache__" for part in path.parts)
            or path.name.lower() == ".env" or path.name.lower().endswith(".env")
            or path.suffix.lower() in {".key", ".pem", ".crt", ".p12", ".db", ".sqlite", ".sqlite3"}
            or name.startswith("tools/guardianangel.net.cn_nginx")):
        raise ValueError("Secret, runtime data or unsafe path in source allowlist")
    return path


def source_hashes(root):
    root = Path(root)
    paths = [*sorted((root / "classroom_app").rglob("*.py")), root / "scheduled_task_dispatcher.py"]
    return {path.relative_to(root).as_posix(): digest(path) for path in paths}


def validate_export_manifest(root, manifest):
    root = Path(root).resolve(strict=True)
    if not isinstance(manifest, dict) or not REQUIRED_EXPORT.issubset(manifest):
        raise ValueError("Reviewed source allowlist is missing required build/lab inputs")
    for name, expected in manifest.items():
        relative = safe_source_path(name)
        path = root.joinpath(*relative.parts)
        links = any(item.is_symlink() or getattr(item, "is_junction", lambda: False)() for item in (path, *path.parents) if item != root and item.is_relative_to(root))
        if (not isinstance(expected, str) or HEX64.fullmatch(expected) is None or links
                or not path.resolve(strict=True).is_relative_to(root) or not path.is_file() or digest(path) != expected):
            raise ValueError("Source allowlist no longer matches actual bytes")
    sources = source_hashes(root)
    if any(manifest.get(name) != value for name, value in sources.items()):
        raise ValueError("Allowlist must contain every backend source and dispatcher")
    return sources


def owned_client_path(path, root=ROOT, *, exists=False):
    parent = (Path(root) / ".codex-temp").resolve(strict=True)
    path = Path(path).resolve(strict=exists)
    if path == parent or not path.is_relative_to(parent):
        raise ValueError("Client lab files must be owned .codex-temp children")
    return path


def refresh_source_manifest(baseline_path, output, *, root=ROOT):
    """Explicitly refresh a reviewed allowlist, reporting every changed input."""
    root = Path(root).resolve(strict=True)
    baseline = read_json(baseline_path)
    if not isinstance(baseline, dict):
        raise ValueError("Reviewed baseline allowlist required")
    tracked = _run(["git", "ls-files", "-z"], cwd=root, capture=True).split("\0")
    root_inputs = {name for name in tracked if name and len(PurePosixPath(name).parts) == 1
                   and PurePosixPath(name).suffix.lower() in {".py", ".json", ".js", ".ts", ".mjs", ".cjs", ".toml", ".yml", ".yaml"}}
    # The earlier release-comparison list is not a complete Docker context.
    # Track all application/template/vendor/build source, including lazy ESM
    # chunks. Do not discover unrelated untracked files or any runtime data.
    tracked_trees = {name for name in tracked if name.startswith(("static/", "frontend/", "templates/", "classroom_app/", "deployment/docker/"))}
    names = set(baseline) | REQUIRED_EXPORT | set(source_hashes(root)) | root_inputs | tracked_trees
    current = {}
    for name in sorted(names):
        relative = safe_source_path(name)
        current[name] = digest(root.joinpath(*relative.parts))
    validate_export_manifest(root, current)
    output = owned_client_path(output, root)
    write_json(output, current)
    return {"source_manifest": str(output), "source_files": len(current), "fingerprint": fingerprint(current),
            "changed_from_baseline": [name for name in current if name in baseline and baseline[name] != current[name]],
            "added_to_allowlist": [name for name in current if name not in baseline]}


def prepare_export(source_manifest, runtime, *, root=ROOT, nonce=None):
    root = Path(root).resolve(strict=True)
    runtime = owned_client_path(runtime, root)
    if runtime.exists():
        raise ValueError("A new empty client lab runtime is required")
    nonce = nonce or uuid.uuid4().hex
    if NONCE.fullmatch(str(nonce)) is None:
        raise ValueError("Explicit 32-hex lab nonce required")
    manifest = read_json(source_manifest)
    client_sources = validate_export_manifest(root, manifest)
    runtime.mkdir(parents=True)
    archive = runtime / "source.tar"
    with tarfile.open(archive, "w") as tar:
        for name in sorted(manifest):
            path = root.joinpath(*PurePosixPath(name).parts)
            info = tarfile.TarInfo(name)
            info.size = path.stat().st_size
            info.mode, info.mtime = 0o644, 0
            with path.open("rb") as stream:
                tar.addfile(info, stream)
    plan = {"schema_version": 1, "kind": KIND, "synthetic": True, "lab_nonce": nonce,
            "compose_project": f"lanshare-capacity-{nonce}", "release_id": f"capacity-linux-{nonce}",
            "source_manifest": manifest, "client_source_sha256": client_sources, "archive_sha256": digest(archive),
            "synthetic_password": "Capacity-only-" + secrets.token_hex(16), "synthetic_jwt_secret": secrets.token_hex(32),
            "database_password": secrets.token_hex(32), "server_config": dict(SERVER_CONFIG)}
    write_json(runtime / "lab-plan.json", plan, private=True)
    shutil.copyfile(root / "tools/platform_capacity_linux_lab.py", runtime / "controller.py")
    return {"lab_nonce": nonce, "bundle": str(runtime), "source_files": len(manifest), "source_archive_sha256": plan["archive_sha256"]}


def validate_plan(plan):
    if (plan.get("schema_version") != 1 or plan.get("kind") != KIND or plan.get("synthetic") is not True
            or NONCE.fullmatch(str(plan.get("lab_nonce", ""))) is None
            or plan.get("compose_project") != "lanshare-capacity-" + plan["lab_nonce"]
            or plan.get("release_id") != "capacity-linux-" + plan["lab_nonce"]):
        raise ValueError("Explicit owned Linux lab plan required")
    for key in ("synthetic_password", "synthetic_jwt_secret", "database_password"):
        if not isinstance(plan.get(key), str) or not re.fullmatch(r"[A-Za-z0-9-]{24,128}", plan[key]):
            raise ValueError("Only generated lab credentials may enter lab env")


def extract_source(bundle, destination):
    bundle, destination = Path(bundle), Path(destination)
    plan = read_json(bundle / "lab-plan.json")
    validate_plan(plan)
    if digest(bundle / "source.tar") != plan["archive_sha256"] or destination.exists():
        raise ValueError("Export changed or source destination already exists")
    with tarfile.open(bundle / "source.tar") as tar:
        members = tar.getmembers()
        if len({m.name for m in members}) != len(members) or {m.name for m in members} != set(plan["source_manifest"]):
            raise ValueError("Archive members differ from the source allowlist")
        if sum(member.size for member in members) > 512_000_000:
            raise ValueError("Source archive exceeds the bounded lab context")
        for member in members:
            safe_source_path(member.name)
            if not member.isfile() or member.size > 128_000_000:
                raise ValueError("Only bounded regular source files may be extracted")
        destination.mkdir(parents=True)
        for member in members:
            payload = tar.extractfile(member).read()
            if hashlib.sha256(payload).hexdigest() != plan["source_manifest"][member.name]:
                raise ValueError("Exported source digest mismatch")
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
    validate_export_manifest(destination, plan["source_manifest"])
    return plan


def database_url(marker):
    d = marker["database"]
    return f"postgresql://{d['user']}:{d['password']}@postgres:5432/{d['name']}"


def render_nginx(source, *, client_ip, release_id):
    address = ipaddress.ip_address(client_ip)
    if address.version != 4 or not address.is_private or address.is_unspecified or address.is_multicast:
        raise ValueError("An explicit exact Docker-host private IPv4 is required")
    if source.count("location = /api/internal/health {") != 2 or source.count("listen 443 ssl;") != 1:
        raise ValueError("Production Nginx structure changed; review the lab derivation")
    result = source.replace("server_name guardianangel.net.cn;", "server_name localhost;")
    result = result.replace("guardianangel.net.cn_bundle.crt", "capacity-lab.crt").replace("guardianangel.net.cn.key", "capacity-lab.key")
    result = result.replace("location = /api/internal/health {", f"location = /api/internal/health {{\n        allow {address};\n        allow 127.0.0.1;\n        deny all;")
    identity = ("    location = /api/internal/capacity-lab-identity {\n"
                f"        allow {address};\n        allow 127.0.0.1;\n        deny all;\n"
                "        default_type application/json;\n        alias /lab/artifacts/capacity-lab-identity.json;\n"
                f"        add_header X-LanShare-Release {release_id} always;\n    }}\n\n")
    result = result.replace("    location ^~ /api/internal/ {", identity + "    location ^~ /api/internal/ {")
    return result


def _network_ipam_config(marker):
    network = marker["network"]
    config = {"subnet": network["subnet"], "gateway": network["gateway"]}
    if marker.get("capacity_mode") == MODE2:
        subnet = ipaddress.IPv4Network(config["subnet"])
        if subnet.prefixlen != 24:
            raise ValueError("Mode2 requires an owned IPv4 /24 network")
        _, dynamic_range = subnet.subnets(new_prefix=25)
        static_addresses = map(ipaddress.IPv4Address, (config["gateway"], *network["addresses"].values()))
        if any(address not in subnet or address in dynamic_range for address in static_addresses):
            raise ValueError("Mode2 static addresses must remain outside its dynamic IP range")
        # Controllers may overlap while resident roles are stopped. Keep all
        # dynamic allocations away from their fixed slots from the first up.
        config["ip_range"] = str(dynamic_range)
    return config


def compose_definition(marker, image):
    nonce, project = marker["lab_nonce"], marker["compose_project"]
    labels = {LABEL: nonce, "io.lanshare.capacity-lab.kind": KIND}
    shared = {"image": image, "pull_policy": "never", "init": True, "restart": "no", "env_file": ["./app.env"], "labels": labels,
              "ulimits": {"nofile": dict(NOFILE_LIMITS)},
              "volumes": ["data:/app/data", "./lab-marker.json:/lab/lab-marker.json:ro", "./artifacts:/lab/artifacts"],
              "networks": {"lab": {}}}
    app = {**shared, "command": ["main"], "cpu_shares": 2048, "stop_grace_period": "5m",
           "ports": ["127.0.0.1:28767:8000"], "volumes": shared["volumes"] + ["static:/srv/lanshare-static"],
           "environment": {"POSTGRES_APPLICATION_NAME": "lanshare-app", "LANSHARE_PUBLISH_STATIC": "1"},
           "healthcheck": {"test": ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/internal/live',timeout=5)"],
                           "interval": "5s", "timeout": "6s", "retries": 36},
           "networks": {"lab": {"ipv4_address": marker["network"]["addresses"]["app"]}}}
    worker = {**shared, "command": ["python", "tools/platform_capacity_linux_background.py", "worker", "--lab-marker", str(MARKER)],
              "cpu_shares": 512, "stop_grace_period": "5m", "environment": {"POSTGRES_POOL_MAX": "4", "POSTGRES_APPLICATION_NAME": "lanshare-scheduler"},
              "networks": {"lab": {"ipv4_address": marker["network"]["addresses"]["scheduler"]}}}
    pg = {"image": "postgres:16-alpine", "init": True, "restart": "no", "cpu_shares": 2048, "shm_size": "256mb",
          "labels": labels, "env_file": ["./postgres.env"], "volumes": ["postgres:/var/lib/postgresql/data"],
          "environment": {"PGDATA": marker["database"]["data_directory"], "TZ": "Asia/Shanghai"},
          "command": ["postgres", "-c", "shared_buffers=192MB", "-c", "effective_cache_size=1GB", "-c", "work_mem=4MB",
                      "-c", "maintenance_work_mem=64MB", "-c", "random_page_cost=1.1", "-c", "effective_io_concurrency=200",
                      "-c", "wal_compression=on", "-c", "max_connections=50", "-c", "log_min_duration_statement=1000"],
          "healthcheck": {"test": ["CMD-SHELL", 'pg_isready -h 127.0.0.1 -U "$${POSTGRES_USER}" -d "$${POSTGRES_DB}"'], "interval": "5s", "timeout": "3s", "retries": 30},
          "networks": {"lab": {"ipv4_address": marker["database"]["server_address"]}}}
    nginx = {"image": "nginx:1.27-alpine", "restart": "no", "cpu_shares": 1024, "labels": labels,
             "ulimits": {"nofile": dict(NOFILE_LIMITS)},
             "ports": ["127.0.0.1:28769:443"], "volumes": ["./nginx-main.conf:/etc/nginx/nginx.conf:ro", "./nginx.conf:/etc/nginx/conf.d/default.conf:ro",
                 "./tls:/etc/nginx/ssl:ro", "static:/srv/lanshare/static:ro", "./artifacts:/lab/artifacts:ro"],
             "healthcheck": {"test": ["CMD-SHELL", "wget -qO- http://127.0.0.1/api/internal/live >/dev/null 2>&1"], "interval": "5s", "timeout": "5s", "retries": 36},
             "networks": {"lab": {"ipv4_address": marker["network"]["addresses"]["nginx"]}}}
    return {"name": project, "services": {"postgres": pg, "app": app, "scheduler": worker, "nginx": nginx,
             "controller": {**shared, "profiles": ["controller"], "entrypoint": ["python", "tools/platform_capacity_linux_lab.py"]}},
            "networks": {"lab": {"name": project + "-network", "internal": True, "labels": labels,
                "ipam": {"config": [_network_ipam_config(marker)]}}},
            "volumes": {name: {"name": project + "-" + name, "labels": labels} for name in ("postgres", "data", "static")}}


def _run(command, *, cwd=None, capture=False):
    # Never include env/DSN/credentials in argv or error messages.
    env = {key:value for key,value in os.environ.items() if key in {
        "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ", "TMPDIR", "SYSTEMROOT", "WINDIR"}}
    env["DOCKER_HOST"] = "unix:///var/run/docker.sock"
    result = subprocess.run(command, cwd=cwd, env=env, text=True, encoding="utf-8", capture_output=capture, check=False)
    if result.returncode:
        raise RuntimeError(f"Lab command failed: {Path(command[0]).name} (exit {result.returncode})")
    return result.stdout if capture else None


def linux_runtime(path):
    path = Path(path).resolve(strict=True)
    marker = read_json(path / "lab-marker.json")
    validate_plan(marker)
    if os.name != "posix" or path.name != marker["lab_nonce"] or str(path).startswith(("/mnt/", "/media/")):
        raise ValueError("An owned nonce directory on Linux ext4 is required")
    filesystem = _run(["findmnt", "-n", "-o", "FSTYPE", "--target", str(path)], capture=True).strip()
    if filesystem != "ext4":
        raise ValueError("Lab runtime/code/database must reside on ext4")
    return path, marker


def application_environment(marker):
    return {"PYTHON_DOTENV_DISABLED": "1", "DB_ENGINE": "postgres", "POSTGRES_BACKEND_READY": "true", "POSTGRES_POOL_ENABLED": "true",
           "DATABASE_URL": database_url(marker), "LANSHARE_CAPACITY_LAB_NONCE": marker["lab_nonce"], "LANSHARE_DATA_ROOT": "/app/data", "MAIN_DATA_DIR": "/app/data",
           "POSTGRES_POOL_MIN": "1", "POSTGRES_POOL_MAX": "12", "MAIN_THREADPOOL_TOKENS": "32", "MAIN_WORKERS": "1", "MAIN_HOST": "0.0.0.0", "MAIN_PORT": "8000",
           "MAIN_LIMIT_CONCURRENCY": str(SERVER_CONFIG["uvicorn_limit_concurrency"]), "MAIN_BACKLOG": "2048", "MAIN_TIMEOUT_KEEP_ALIVE": "30", "MAIN_WS_PING_INTERVAL": "20", "MAIN_WS_PING_TIMEOUT": "20",
           "SECRET_KEY": marker["synthetic_jwt_secret"], "LANSHARE_RELEASE_ID": marker["release_id"], "LANSHARE_LQ_FAMILIES": SERVER_CONFIG["lq_families"],
           "TRUSTED_PROXY_HOSTS": "nginx", "TRUSTED_PROXY_CIDRS": "127.0.0.1/32,::1/128",
           "AI_ASSISTANT_URL": "http://127.0.0.1:1", "UI_COPY_GENERATION_ENABLED": "false", "TZ": "Asia/Shanghai", "APP_TIMEZONE": "Asia/Shanghai",
           "INITIAL_SUPER_ADMIN_EMAIL": "capacity-admin@example.invalid", "INITIAL_SUPER_ADMIN_NAME": "Synthetic capacity administrator", "INITIAL_SUPER_ADMIN_PASSWORD": marker["synthetic_password"]}


def _env_text(values):
    return "".join(f"{key}={value}\n" for key,value in values.items())


def validate_lab_layout(runtime, marker):
    runtime = Path(runtime)
    image = "lanshare-capacity:" + marker["lab_nonce"]
    if (runtime / "build-state.json").exists():
        build = read_json(runtime / "build-state.json")
        if build["lab_nonce"] != marker["lab_nonce"]:
            raise ValueError("Build nonce changed")
        image = build["image_id"]
    if read_json(runtime / "compose.json") != compose_definition(marker, image):
        raise ValueError("Generated isolated Compose changed after staging")
    if ((runtime / "app.env").read_text(encoding="utf-8") != _env_text(application_environment(marker))
            or (runtime / "postgres.env").read_text(encoding="utf-8") != _env_text({"POSTGRES_USER": marker["database"]["user"],
                "POSTGRES_PASSWORD": marker["database_password"], "POSTGRES_DB": marker["database"]["name"]})):
        raise ValueError("Generated synthetic env changed after staging")
    source = runtime / "source/nginx.conf"
    if (digest(source) != marker["source_manifest"]["nginx.conf"]
            or (runtime / "nginx.conf").read_text(encoding="utf-8") != render_nginx(source.read_text(encoding="utf-8"), client_ip=marker["client_ip"], release_id=marker["release_id"])):
        raise ValueError("Lab Nginx derivation changed after staging")
    main_source = runtime / "source/deployment/nginx-main.conf"
    if (digest(main_source) != marker["source_manifest"]["deployment/nginx-main.conf"]
            or (runtime / "nginx-main.conf").read_bytes() != main_source.read_bytes()):
        raise ValueError("Lab Nginx main configuration changed after staging")
    for name in ("artifacts", "tls", "source"):
        path = runtime / name
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(runtime):
            raise ValueError("Lab mount escaped its owned runtime")


def stage_bundle(bundle, runtime, *, client_ip):
    if os.name != "posix":
        raise ValueError("Stage inside the dedicated Linux VM")
    bundle = Path(bundle).resolve(strict=True)
    plan = read_json(bundle / "lab-plan.json"); validate_plan(plan)
    runtime = Path(runtime).resolve()
    if runtime.exists() or runtime.name != plan["lab_nonce"] or str(runtime).startswith(("/mnt/", "/media/")):
        raise ValueError("Use a fresh nonce directory on Linux ext4")
    render_nginx("location = /api/internal/health {\n}\nlocation = /api/internal/health {\n}\nlisten 443 ssl;", client_ip=client_ip, release_id=plan["release_id"])
    runtime.mkdir(parents=True)
    extract_source(bundle, runtime / "source")
    subnet_octet = 32 + int(plan["lab_nonce"][:2], 16) % 192
    network = {"subnet": f"172.30.{subnet_octet}.0/24", "gateway": f"172.30.{subnet_octet}.1",
               "addresses": {name: f"172.30.{subnet_octet}.{number}" for name, number in [("postgres", 2), ("app", 3), ("nginx", 4), ("scheduler", 5)]}}
    marker = {**plan, "artifact_root": str(ARTIFACTS), "client_ip": str(ipaddress.ip_address(client_ip)), "network": network,
              "database": {"host": "postgres", "port": 5432, "name": "capacity_" + plan["lab_nonce"],
                "user": "capacity_" + plan["lab_nonce"][:16], "password": plan["database_password"],
                "system_identifier": None, "data_directory": "/var/lib/postgresql/data/pgdata", "server_address": network["addresses"]["postgres"]}}
    write_json(runtime / "lab-marker.json", marker, private=True)
    linux_runtime(runtime)
    (runtime / "artifacts").mkdir()
    (runtime / "tls").mkdir()
    _run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3", "-subj", "/CN=capacity-loopback-only",
          "-addext", "subjectAltName=IP:127.0.0.1,IP:::1", "-keyout", str(runtime / "tls/capacity-lab.key"), "-out", str(runtime / "tls/capacity-lab.crt")])
    (runtime / "tls/capacity-lab.key").chmod(0o600)
    for name, values in [("app.env", application_environment(marker)), ("postgres.env", {"POSTGRES_USER": marker["database"]["user"], "POSTGRES_PASSWORD": marker["database_password"], "POSTGRES_DB": marker["database"]["name"]})]:
        target = runtime / name
        target.write_text(_env_text(values), encoding="utf-8"); target.chmod(0o600)
    (runtime / "nginx.conf").write_text(render_nginx((runtime / "source/nginx.conf").read_text(encoding="utf-8"), client_ip=client_ip, release_id=marker["release_id"]), encoding="utf-8")
    shutil.copyfile(runtime / "source/deployment/nginx-main.conf", runtime / "nginx-main.conf")
    write_json(runtime / "compose.json", compose_definition(marker, "lanshare-capacity:" + marker["lab_nonce"]))
    return {"lab_nonce": marker["lab_nonce"], "runtime": str(runtime), "state": "staged_not_started"}


def _container_marker(marker_path):
    marker_path = Path(marker_path)
    if marker_path != MARKER or not Path("/.dockerenv").is_file():
        raise ValueError("Lab command requires its explicit container marker")
    marker = read_json(marker_path); validate_plan(marker)
    d = marker.get("database", {})
    if (marker.get("artifact_root") != str(ARTIFACTS) or d.get("host") != "postgres" or d.get("port") != 5432
            or d.get("name") != "capacity_" + marker["lab_nonce"] or d.get("user") != "capacity_" + marker["lab_nonce"][:16]
            or d.get("password") != marker["database_password"] or d.get("data_directory") != "/var/lib/postgresql/data/pgdata"
            or d.get("server_address") != marker.get("network", {}).get("addresses", {}).get("postgres")
            or os.environ.get("LANSHARE_CAPACITY_LAB_NONCE") != marker["lab_nonce"]
            or os.environ.get("DATABASE_URL") != database_url(marker)
            or os.environ.get("DB_ENGINE") != "postgres" or os.environ.get("PYTHON_DOTENV_DISABLED") != "1"
            or os.environ.get("LANSHARE_DATA_ROOT") != "/app/data"
            or Path("/app/.env").exists() or Path("/app/docker.env").exists()):
        raise ValueError("Container environment does not match the owned synthetic lab")
    from tools.isolated_environment import guard_dotenv_loading
    guard_dotenv_loading()
    return marker


def _install_container_network_guard(marker):
    previous = getattr(socket, "_capacity_linux_lab_nonce", None)
    if previous is not None:
        if previous != marker["lab_nonce"]:
            raise ValueError("Another lab already owns this process network boundary")
        return
    addresses = marker["network"]["addresses"]
    allowed = {"127.0.0.1", "::1", "localhost", *addresses, *addresses.values()}
    connect, connect_ex, dns = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    def permitted(host):
        if isinstance(host, bytes):
            try:
                host = host.decode("ascii")
            except UnicodeDecodeError:
                return False
        return host is None or str(host).lower() in allowed
    def guarded_connect(sock, address):
        if not permitted(address[0]):
            raise RuntimeError("Linux capacity lab forbids external sockets")
        return connect(sock, address)
    def guarded_connect_ex(sock, address):
        if not permitted(address[0]):
            raise RuntimeError("Linux capacity lab forbids external sockets")
        return connect_ex(sock, address)
    def guarded_dns(host, *args, **kwargs):
        if not permitted(host):
            raise RuntimeError("Linux capacity lab forbids external DNS")
        answer = dns(host, *args, **kwargs)
        if any(not permitted(item[4][0]) for item in answer):
            raise RuntimeError("Lab DNS resolved outside its exact declared network")
        return answer
    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = guarded_connect, guarded_connect_ex, guarded_dns
    socket._capacity_linux_lab_nonce = marker["lab_nonce"]


def _checked_pg_connection(marker):
    _install_container_network_guard(marker)
    import psycopg
    conn = psycopg.connect(database_url(marker), connect_timeout=5, autocommit=True)
    try:
        row = conn.execute("SELECT current_database(),current_user,current_setting('data_directory'),"
                           "host(inet_server_addr()),current_setting('port')::int,current_setting('server_version_num')::int").fetchone()
        system_id = str(conn.execute("SELECT system_identifier::text FROM pg_control_system()").fetchone()[0])
        d = marker["database"]
        if (tuple(row[:5]) != (d["name"], d["user"], d["data_directory"], d["server_address"], 5432)
                or int(row[5]) // 10000 != 16
                or d.get("system_identifier") is not None and system_id != d["system_identifier"]):
            raise ValueError("PostgreSQL system/database/role/address identity mismatch")
        return conn, system_id
    except BaseException:
        conn.close()
        raise


def _validate_seeded_fixture(conn, marker, fixture):
    users = fixture.get("users", [])
    if (fixture.get("kind") != "capacity-linux-server-fixture" or fixture.get("synthetic") is not True
            or fixture.get("uiV3Synthetic") is not True or fixture.get("lab_nonce") != marker["lab_nonce"]
            or len(users) != 200 or any(row.get("identifier") != f"CAPACITY-SYNTHETIC-{i:04d}"
                or type(row.get("student_id")) is not int or row["student_id"] <= 0 for i, row in enumerate(users))
            or len({row["student_id"] for row in users}) != 200):
        raise ValueError("Seeded fixture does not contain its exact 200 owned identities")
    row = conn.execute("SELECT o.class_id,o.teacher_id,t.email FROM class_offerings o JOIN teachers t ON t.id=o.teacher_id WHERE o.id=%s",
                       (fixture["classOfferingId"],)).fetchone()
    if not row or tuple(row) != (fixture["classId"], fixture["teacher"]["id"], fixture["teacher"]["email"]) or not str(row[2]).endswith("@example.invalid"):
        raise ValueError("Synthetic teacher/classroom ownership mismatch")
    actual = conn.execute("SELECT id,student_id_number,class_id,description FROM students WHERE id=ANY(%s) ORDER BY student_id_number",
                          ([item["student_id"] for item in users],)).fetchall()
    if (len(actual) != 200 or any(tuple(row) != (item["student_id"], item["identifier"], fixture["classId"], "owned synthetic capacity fixture")
                                  for row, item in zip(actual, users))):
        raise ValueError("Database students differ from the explicit lab fixture")


def validate_container_boundary(marker_path=MARKER, *, require_seeded=True):
    """Validate physical PG identity and nonce before any application import.

    Returned marker contains synthetic credentials; consumers must never print
    it. Native Windows establish_boundary and all its checks are unchanged.
    """
    marker = _container_marker(marker_path)
    if not marker["database"].get("system_identifier"):
        raise ValueError("Bootstrap must record the PostgreSQL system identity first")
    conn, system_id = _checked_pg_connection(marker)
    try:
        rows = conn.execute("SELECT lab_nonce FROM public.capacity_lab_identity WHERE singleton=TRUE").fetchall()
        if rows != [(marker["lab_nonce"],)]:
            raise ValueError("Database lab nonce mismatch")
        fixture_path = ARTIFACTS / "server-fixture.json"
        fixture = read_json(fixture_path) if require_seeded else None
        if require_seeded:
            _validate_seeded_fixture(conn, marker, fixture)
    finally:
        conn.close()
    return {"lab_nonce": marker["lab_nonce"], "compose_project": marker["compose_project"],
            "pg_system_identifier": system_id, "marker": marker, "fixture": fixture, "artifact_root": ARTIFACTS,
            "fixture_sha256": digest(fixture_path) if require_seeded else None}


def container_bootstrap(marker_path=MARKER):
    marker = _container_marker(marker_path)
    if marker["database"].get("system_identifier") is not None:
        raise ValueError("Bootstrap is one-time and requires an unclaimed fresh database")
    conn, system_id = _checked_pg_connection(marker)
    try:
        if conn.execute("SELECT COUNT(*) FROM pg_catalog.pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')").fetchone()[0] != 0:
            raise ValueError("Refuse to claim a database containing any existing tables")
        with conn.transaction():
            conn.execute("CREATE TABLE public.capacity_lab_identity (singleton BOOLEAN PRIMARY KEY CHECK(singleton),lab_nonce TEXT NOT NULL)")
            conn.execute("INSERT INTO public.capacity_lab_identity(singleton,lab_nonce) VALUES(TRUE,%s)", (marker["lab_nonce"],))
        result = {"lab_nonce": marker["lab_nonce"], "compose_project": marker["compose_project"], "system_identifier": system_id,
                  "database": marker["database"]["name"], "server_address": marker["database"]["server_address"]}
        write_json(ARTIFACTS / "database-identity.json", result)
        return result
    finally:
        conn.close()


def vm_budget():
    if os.name != "posix" or not Path("/proc/meminfo").is_file():
        raise ValueError("Measure the dedicated Linux VM, not the Windows load generator")
    memory = {line.split(":", 1)[0]: int(line.split()[1]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line}
    cpu_count = os.cpu_count()
    if cpu_count != 2 or not 3 * 2**30 <= memory["MemTotal"] <= 4 * 2**30 + 64 * 2**20 or memory["SwapTotal"] != 0:
        raise ValueError("Final lab VM must be two CPUs, four GiB RAM and zero swap")
    info = json.loads(_run(["docker", "--host", "unix:///var/run/docker.sock", "info", "--format", "{{json .}}"], capture=True))
    if info.get("OSType") != "linux" or info.get("NCPU") != 2 or not 3 * 2**30 <= info.get("MemTotal", 0) <= 4 * 2**30 + 64 * 2**20:
        raise ValueError("Docker daemon does not share the measured two-CPU/four-GiB Linux VM")
    filesystem = _run(["findmnt", "-n", "-o", "FSTYPE", "--target", info["DockerRootDir"]], capture=True).strip()
    if filesystem != "ext4":
        raise ValueError("Docker images and volume data must reside on ext4")
    return {"cpus": cpu_count, "mem_total_bytes": memory["MemTotal"], "swap_total_bytes": memory["SwapTotal"],
            "docker_cpus": info["NCPU"], "docker_mem_total_bytes": info["MemTotal"], "docker_root": info["DockerRootDir"],
            "docker_version": info.get("ServerVersion"), "filesystem": filesystem,
            "scope": "Whole dedicated VM contains app, PG, Linux Nginx, Docker and background worker"}


def compose_command(runtime, marker, *args, capture=False):
    return _run(["docker", "--host", "unix:///var/run/docker.sock", "compose", "--project-name", marker["compose_project"],
                 "--file", str(Path(runtime) / "compose.json"), *args], cwd=runtime, capture=capture)


def inspect_owned_resources(runtime, marker):
    docker = ["docker", "--host", "unix:///var/run/docker.sock"]
    ids = compose_command(runtime, marker, "ps", "--all", "--quiet", capture=True).split()
    containers = json.loads(_run([*docker, "inspect", *ids], capture=True)) if ids else []
    result = []
    for item in containers:
        labels = item["Config"].get("Labels", {})
        if labels.get(LABEL) != marker["lab_nonce"] or labels.get("com.docker.compose.project") != marker["compose_project"]:
            raise ValueError("Refuse any container outside the explicit nonce/project")
        service = labels["com.docker.compose.service"]
        nofile = [limit for limit in item.get("HostConfig", {}).get("Ulimits", []) or [] if limit.get("Name") == "nofile"]
        if service != "postgres" and nofile != [{"Name": "nofile", "Hard": NOFILE_LIMITS["hard"], "Soft": NOFILE_LIMITS["soft"]}]:
            raise ValueError("Live application/proxy nofile differs from the reviewed connection budget")
        mounts = []
        for mount in item.get("Mounts", []):
            if mount["Type"] == "volume":
                volume = json.loads(_run([*docker, "volume", "inspect", mount["Name"]], capture=True))[0]
                if volume.get("Labels", {}).get(LABEL) != marker["lab_nonce"]:
                    raise ValueError("Volume nonce ownership mismatch")
                mounts.append({"name": mount["Name"], "destination": mount["Destination"]})
        networks = item.get("NetworkSettings", {}).get("Networks", {})
        if set(networks) != {marker["compose_project"] + "-network"}:
            raise ValueError("Container is attached to an undeclared network")
        result.append({"id": item["Id"], "service": service, "image_id": item["Image"], "nofile": nofile,
                       "running": item["State"]["Running"], "exit_code": item["State"]["ExitCode"], "oom_killed": item["State"].get("OOMKilled", False),
                       "volumes": mounts})
    if containers:
        network = json.loads(_run([*docker, "network", "inspect", marker["compose_project"] + "-network"], capture=True))[0]
        if network.get("Labels", {}).get(LABEL) != marker["lab_nonce"] or network.get("Internal") is not True:
            raise ValueError("Lab must retain its nonce-owned isolated network")
        if marker.get("capacity_mode") == MODE2:
            expected = _network_ipam_config(marker)
            config = network.get("IPAM", {}).get("Config")
            if (not isinstance(config, list) or len(config) != 1 or not isinstance(config[0], dict)
                    or any(config[0].get(key) != expected[value]
                           for key, value in (("Subnet", "subnet"), ("Gateway", "gateway"), ("IPRange", "ip_range")))):
                raise ValueError("Mode2 live network must retain its exact dynamic IP range")
    return result


def assert_no_preexisting_resources(marker):
    """Before first up: never start PG against a pre-existing named volume."""
    docker = ["docker", "--host", "unix:///var/run/docker.sock"]
    volumes = set(_run([*docker, "volume", "ls", "--format", "{{.Name}}"], capture=True).splitlines())
    networks = set(_run([*docker, "network", "ls", "--format", "{{.Name}}"], capture=True).splitlines())
    expected = {marker["compose_project"] + "-" + name for name in ("postgres", "data", "static")}
    if expected & volumes or marker["compose_project"] + "-network" in networks:
        raise ValueError("Fresh start refuses any pre-existing project volume or network")


def build_lab(runtime):
    runtime, marker = linux_runtime(runtime)
    validate_lab_layout(runtime, marker)
    if (runtime / "build-state.json").exists():
        raise ValueError("Built lab images are immutable; use a fresh lab nonce")
    source = runtime / "source"
    validate_export_manifest(source, marker["source_manifest"])
    _run([sys.executable, "tools/deploy/dependency_images.py", "ensure"], cwd=source)
    dependencies = json.loads(_run([sys.executable, "tools/deploy/dependency_images.py", "plan"], cwd=source, capture=True))
    image = "lanshare-capacity:" + marker["lab_nonce"]
    _run(["docker", "--host", "unix:///var/run/docker.sock", "build", "--network", "none", "--pull=false",
          "--build-arg", "APP_VERSION=" + marker["release_id"], "--build-arg", "LANSHARE_RUNTIME_BASE=" + dependencies["runtime"]["image"],
          "--build-arg", "LANSHARE_FRONTEND_BASE=" + dependencies["frontend"]["image"],
          "--label", LABEL + "=" + marker["lab_nonce"], "--tag", image, str(source)])
    image_info = json.loads(_run(["docker", "--host", "unix:///var/run/docker.sock", "image", "inspect", image], capture=True))[0]
    if image_info.get("Config", {}).get("Labels", {}).get(LABEL) != marker["lab_nonce"]:
        raise ValueError("Built application image ownership mismatch")
    result = {"lab_nonce": marker["lab_nonce"], "image": image, "image_id": image_info["Id"],
              "release_id": marker["release_id"], "source_manifest_fingerprint": fingerprint(marker["source_manifest"]),
              "dependency_images": {key:spec["image"] for key,spec in dependencies.items()}}
    write_json(runtime / "build-state.json", result)
    write_json(runtime / "compose.json", compose_definition(marker, result["image_id"]))
    return result


def static_asset_hashes(static_root, revision):
    static_root = Path(static_root)
    result = {}
    for root in (static_root / "assets" / revision, static_root / "dist/assets"):
        if not root.is_dir():
            raise ValueError("Formal build is missing its immutable asset directory")
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError("Formal static assets must not contain links")
            if path.is_file():
                result[path.relative_to(static_root).as_posix()] = digest(path)
    if not result:
        raise ValueError("Formal static asset list is empty")
    return result


def verify_published_assets(expected, destination):
    destination = Path(destination)
    actual = {}
    for path in destination.rglob("*"):
        if path.is_symlink():
            raise ValueError("Published static volume contains a link")
        if path.is_file():
            actual[path.relative_to(destination).as_posix()] = digest(path)
    if actual != expected:
        raise ValueError("Published static volume differs from actual image asset bytes")
    return {"status":"passed", "files":len(expected), "fingerprint":fingerprint(expected)}


def verify_frontend_runtime_inputs(root, source_manifest):
    """Exercise the actual SSR loaders before calling a fresh image healthy."""
    from classroom_app import frontend_assets
    root = Path(root)
    if frontend_assets.STATIC_DIR.resolve() != (root / "static").resolve():
        raise ValueError("Frontend runtime resolved outside the actual application image")
    frontend_assets.load_frontend_asset_manifest.cache_clear()
    frontend_assets.load_static_asset_graph.cache_clear()
    vendor = frontend_assets.load_frontend_asset_manifest()
    graph = frontend_assets.load_static_asset_graph()
    if not vendor or not graph:
        raise ValueError("Formal frontend vendor manifest and asset graph are required")
    vendor_files = {name:digest(root / name) for name in source_manifest if name.startswith("static/vendor/")}
    if not vendor_files or any(source_manifest[name] != value for name,value in vendor_files.items()):
        raise ValueError("Actual vendor dependency bytes differ from the complete source export")
    return {"frontend_manifest_entries":len(vendor), "vendor_source_files":len(vendor_files),
            "vendor_source_fingerprint":fingerprint(vendor_files), "runtime_graph_revision":graph["revision"]}


def process_nofile_limits():
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    actual = {"soft": soft, "hard": hard}
    if actual != NOFILE_LIMITS:
        raise ValueError("Effective process nofile differs from the reviewed connection budget")
    return actual


def container_inventory(marker_path=MARKER):
    identity = validate_container_boundary(marker_path)
    root = Path("/app")
    graph_path = root / "static/assets/manifest.json"
    graph = read_json(graph_path)
    if graph.get("schema") != 1 or HEX64.fullmatch(str(graph.get("revision", ""))) is None or not isinstance(graph.get("entries"), dict):
        raise ValueError("A formal static asset graph is required")
    frontend_inputs = verify_frontend_runtime_inputs(root, identity["marker"]["source_manifest"])
    for target in graph["entries"].values():
        relative = PurePosixPath(target)
        if relative.is_absolute() or ".." in relative.parts or not root.joinpath("static", *relative.parts).is_file():
            raise ValueError("Formal static asset graph has a missing or escaping target")
    assets = static_asset_hashes(root / "static", graph["revision"])
    asset_record = ARTIFACTS / "image-static-files.json"
    if asset_record.exists():
        if read_json(asset_record) != assets:
            raise ValueError("Actual image asset bytes changed after the initial inventory")
    else:
        write_json(asset_record, assets)
    if os.environ.get("LANSHARE_PUBLISH_STATIC") == "1":
        write_json(ARTIFACTS / "published-static-verification.json", verify_published_assets(assets, Path("/srv/lanshare-static")))
    sources = source_hashes(root)
    if sources != identity["marker"]["client_source_sha256"]:
        raise ValueError("Container backend bytes differ from the explicit client export")
    runtime_python = {name:digest(root / name) for name in identity["marker"]["source_manifest"] if name.endswith(".py")}
    if any(value != identity["marker"]["source_manifest"][name] for name,value in runtime_python.items()):
        raise ValueError("Container Python entrypoint or tool bytes differ from the frozen export")
    config = dict(SERVER_CONFIG)
    for key, env in [("threadpool_tokens", "MAIN_THREADPOOL_TOKENS"), ("postgres_pool_max", "POSTGRES_POOL_MAX"),
                     ("uvicorn_limit_concurrency", "MAIN_LIMIT_CONCURRENCY"), ("workers", "MAIN_WORKERS"), ("backlog", "MAIN_BACKLOG"),
                     ("timeout_keep_alive", "MAIN_TIMEOUT_KEEP_ALIVE"), ("ws_ping_interval", "MAIN_WS_PING_INTERVAL"), ("ws_ping_timeout", "MAIN_WS_PING_TIMEOUT")]:
        config[key] = float(os.environ[env]) if key.startswith("ws_") else int(os.environ[env])
    config["lq_families"] = os.environ["LANSHARE_LQ_FAMILIES"]
    if config != SERVER_CONFIG:
        raise ValueError("Effective application settings differ from the reviewed comparison settings")
    nofile = process_nofile_limits()
    result = {"lab_nonce": identity["lab_nonce"], "release_id": identity["marker"]["release_id"], "server_source_sha256": sources,
              "server_config": config, "process_nofile": nofile, "static_graph_sha256": digest(graph_path), "static_graph_revision": graph["revision"],
              "runtime_python_sha256": runtime_python, "immutable_asset_files":len(assets), "immutable_assets_fingerprint":fingerprint(assets)}
    result.update(frontend_inputs)
    write_json(ARTIFACTS / "server-inventory.json", result)
    return result


def publish_identity(runtime, marker):
    inventory = read_json(Path(runtime) / "artifacts/server-inventory.json")
    fixture = read_json(Path(runtime) / "artifacts/server-fixture.json")
    if inventory["lab_nonce"] != marker["lab_nonce"] or fixture["lab_nonce"] != marker["lab_nonce"]:
        raise ValueError("Controller inventory/fixture nonce mismatch")
    identity = {"schema_version": 1, "synthetic": True, "lab_nonce": marker["lab_nonce"], "release_id": marker["release_id"],
                "source_fingerprint": fingerprint(inventory["server_source_sha256"]), "config_fingerprint": fingerprint(inventory["server_config"]),
                "static_graph_sha256": inventory["static_graph_sha256"], "users_sha256": fingerprint(fixture["users"]), "offering_id": fixture["classOfferingId"]}
    write_json(Path(runtime) / "artifacts/capacity-lab-identity.json", identity)
    handoff = {"kind": "capacity-linux-controller-handoff", "schema_version": 1, "synthetic": True,
               "lab_nonce": marker["lab_nonce"], "release_id": marker["release_id"], "users": fixture["users"],
               "offering_id": fixture["classOfferingId"], "expected_identity": identity, "server_source_sha256": inventory["server_source_sha256"],
               "client_source_sha256": marker["client_source_sha256"],
               "server_config": inventory["server_config"], "synthetic_password": marker["synthetic_password"], "synthetic_jwt_secret": marker["synthetic_jwt_secret"],
               "base_url": "https://127.0.0.1:28769", "ca_sha256": digest(Path(runtime) / "tls/capacity-lab.crt")}
    handoff_dir = Path(runtime) / "client-handoff"; handoff_dir.mkdir(exist_ok=False)
    write_json(handoff_dir / "handoff.json", handoff, private=True)
    for name in ("capacity-social.json", "capacity-reading.json"):
        shutil.copyfile(Path(runtime) / "artifacts" / name, handoff_dir / name)
    shutil.copyfile(Path(runtime) / "tls/capacity-lab.crt", handoff_dir / "capacity-lab.crt")


def start_lab(runtime):
    runtime, marker = linux_runtime(runtime)
    validate_lab_layout(runtime, marker)
    if (runtime / "start-state.json").exists() or marker["database"]["system_identifier"] is not None:
        raise ValueError("Fresh lab startup is one-time; do not reseed or replace its database")
    build = read_json(runtime / "build-state.json")
    if build["lab_nonce"] != marker["lab_nonce"]:
        raise ValueError("Build belongs to another lab")
    write_json(runtime / "vm-budget-before.json", vm_budget())
    if inspect_owned_resources(runtime, marker):
        raise ValueError("Fresh start requires no existing project containers")
    assert_no_preexisting_resources(marker)
    compose_command(runtime, marker, "up", "-d", "--wait", "postgres")
    inspect_owned_resources(runtime, marker)
    compose_command(runtime, marker, "run", "--rm", "controller", "container-bootstrap")
    pg = read_json(runtime / "artifacts/database-identity.json")
    if pg["lab_nonce"] != marker["lab_nonce"]:
        raise ValueError("PostgreSQL bootstrap ownership mismatch")
    marker["database"]["system_identifier"] = pg["system_identifier"]
    write_json(runtime / "lab-marker.json", marker, private=True)
    compose_command(runtime, marker, "run", "--rm", "controller", "container-seed")
    compose_command(runtime, marker, "run", "--rm", "controller", "container-inventory")
    publish_identity(runtime, marker)
    compose_command(runtime, marker, "up", "-d", "--wait", "--wait-timeout", "180", "app", "nginx")
    containers = inspect_owned_resources(runtime, marker)
    if any(item["service"] == "app" and item["image_id"] != build["image_id"] for item in containers):
        raise ValueError("Running app image differs from the built immutable image")
    result = {"lab_nonce": marker["lab_nonce"], "release_id": marker["release_id"], "state": "started",
              "mode": "windows-comparable-app-pg-nginx-controlled-background", "full_production_roles_started": False,
              "containers": containers}
    write_json(runtime / "start-state.json", result)
    return result


def create_client_runtime(handoff_dir, runtime, *, root=ROOT):
    handoff_dir = owned_client_path(handoff_dir, root, exists=True)
    handoff = read_json(handoff_dir / "handoff.json")
    runtime = owned_client_path(runtime, root)
    if (runtime.exists() or handoff.get("kind") != "capacity-linux-controller-handoff" or handoff.get("synthetic") is not True
            or NONCE.fullmatch(str(handoff.get("lab_nonce", ""))) is None
            or digest(handoff_dir / "capacity-lab.crt") != handoff.get("ca_sha256")
            or source_hashes(root) != handoff.get("client_source_sha256")):
        raise ValueError("Explicit fresh client runtime and intact controller handoff required")
    runtime.mkdir(parents=True)
    fixture = {"kind": "capacity-attach-client", "uiV3Synthetic": True, "lab_nonce": handoff["lab_nonce"],
               "classOfferingId": handoff["offering_id"], "password": handoff["synthetic_password"],
               "visualMaterialIds": [read_json(handoff_dir / "capacity-reading.json")["material_id"]]}
    write_json(runtime / "fixture.json", fixture, private=True)
    for name in ("capacity-social.json", "capacity-reading.json", "capacity-lab.crt"):
        shutil.copyfile(handoff_dir / name, runtime / name)
    manifest = {**handoff, "kind": "capacity-attach-client", "uiV3Synthetic": True,
                "client_source_sha256": handoff["client_source_sha256"], "ca_file": str((runtime / "capacity-lab.crt").resolve()),
                "artifact_sha256": {name:digest(runtime / name) for name in ("fixture.json", "capacity-social.json", "capacity-reading.json")}}
    write_json(runtime / "lab-manifest.json", manifest, private=True)
    return {"lab_nonce": handoff["lab_nonce"], "runtime": str(runtime), "manifest": str(runtime / "lab-manifest.json"),
            "database_created_or_accessed": False, "state": "client_metadata_only"}


def verify_background_closed(containers, state, background):
    worker = [item for item in containers if item["service"] == "scheduler"]
    finite = {"todo_due_reminder", "assignment_due_reminder", "capacity_controlled_provider_wait"}
    unfinished = [key for key,count in background.get("task_states", {}).items()
                  if count and key.rsplit(":", 1)[0] in finite and not key.endswith(":done")]
    if (len(worker) != 1 or worker[0]["running"] or worker[0]["exit_code"] != 0 or worker[0]["oom_killed"]
            or state.get("status") != "stopped" or state.get("projection_measurement_errors") or background.get("projection_measurement_errors")
            or background.get("pending_projection_marks") or background.get("handler_failures") or unfinished
            or background.get("worker_identity_matches_run") is not True
            or background.get("finite_task_verification", {}).get("all_present_and_done") is not True
            or not background.get("projection_durable_facts", {}).get("all_rows_clean_and_current")
            or any(check.get("missing_fixture_recipients") or not check.get("task_completed") for check in background.get("notification_checks", []))):
        raise ValueError("Background container/state/finite-task/drain verification failed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("refresh-manifest", "prepare", "stage", "build", "start", "client", "background-start", "background-stop-intake",
        "background-stop", "verify", "stop", "container-bootstrap", "container-schema-source", "container-seed", "container-inventory", "container-background-seed", "container-verify"))
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--handoff", type=Path)
    parser.add_argument("--client-ip")
    parser.add_argument("--lab-marker", type=Path, default=MARKER)
    parser.add_argument("--duration", type=int, default=3720)
    parser.add_argument("--client-report", type=Path)
    args = parser.parse_args(argv)
    if args.action == "refresh-manifest":
        result = refresh_source_manifest(args.source_manifest, args.output)
    elif args.action == "prepare":
        result = prepare_export(args.source_manifest, args.runtime)
    elif args.action == "stage":
        result = stage_bundle(args.bundle, args.runtime, client_ip=args.client_ip)
    elif args.action == "build":
        result = build_lab(args.runtime)
    elif args.action == "start":
        result = start_lab(args.runtime)
    elif args.action == "client":
        result = create_client_runtime(args.handoff, args.runtime)
    elif args.action == "container-bootstrap":
        result = container_bootstrap(args.lab_marker)
    elif args.action == "container-seed":
        identity = validate_container_boundary(args.lab_marker, require_seeded=False)
        from tools.platform_capacity_linux_seed import seed_lab
        result = seed_lab(identity)
    elif args.action == "container-schema-source":
        identity = validate_container_boundary(args.lab_marker, require_seeded=False)
        from tools.platform_capacity_linux_seed import schema_source_child
        result = schema_source_child(identity)
    elif args.action == "container-inventory":
        result = container_inventory(args.lab_marker)
        result = {key:value for key,value in result.items() if key not in {"server_source_sha256", "runtime_python_sha256"}}
    elif args.action == "container-background-seed":
        identity = validate_container_boundary(args.lab_marker)
        from tools.platform_capacity_linux_background import seed_lab_background
        result = seed_lab_background(identity, duration=args.duration)
    elif args.action == "container-verify":
        identity = validate_container_boundary(args.lab_marker)
        from tools.platform_capacity_linux_seed import final_durable_facts
        from tools.platform_capacity_linux_background import report_lab_background
        result = {"durable": final_durable_facts(identity, args.client_report), "background": report_lab_background(identity)}
        # Complete reports are files, not console dumps of private fixture data.
        result = {"lab_nonce": identity["lab_nonce"], "durable_status": result["durable"]["status"], "background_report_written": True}
    else:
        runtime, marker = linux_runtime(args.runtime)
        validate_lab_layout(runtime, marker)
        containers = inspect_owned_resources(runtime, marker)
        if args.action == "background-start":
            vm_budget()
            compose_command(runtime, marker, "run", "--rm", "controller", "container-background-seed", "--duration", str(args.duration))
            compose_command(runtime, marker, "up", "-d", "scheduler")
            result = {"lab_nonce": marker["lab_nonce"], "state": "background_started"}
        elif args.action == "background-stop-intake":
            (runtime / "artifacts/stop-background-intake").touch(exist_ok=False)
            result = {"lab_nonce": marker["lab_nonce"], "state": "background_intake_stopped"}
        elif args.action == "background-stop":
            compose_command(runtime, marker, "stop", "--timeout", "300", "scheduler")
            result = {"lab_nonce": marker["lab_nonce"], "state": "background_stop_requested", "verification_required": True}
        elif args.action == "stop":
            for service in ("nginx", "scheduler", "app", "postgres"):
                compose_command(runtime, marker, "stop", "--timeout", "300", service)
            result = {"lab_nonce": marker["lab_nonce"], "state": "stopped_volumes_preserved"}
        elif args.action == "verify":
            report = Path(args.client_report).resolve(strict=True)
            if not report.is_relative_to(runtime / "artifacts"):
                raise ValueError("Copy the client report into this lab's artifact directory")
            compose_command(runtime, marker, "run", "--rm", "controller", "container-verify", "--client-report", str(ARTIFACTS / report.name))
            state = read_json(runtime / "artifacts/background-worker-state.json")
            background = read_json(runtime / "artifacts/background-report.json")
            verify_background_closed(containers, state, background)
            compose_command(runtime, marker, "exec", "-T", "app", "python", "tools/platform_capacity_linux_lab.py", "container-inventory")
            inventory = read_json(runtime / "artifacts/server-inventory.json")
            identity = read_json(runtime / "artifacts/capacity-lab-identity.json")
            build = read_json(runtime / "build-state.json")
            if inventory["static_graph_sha256"] != identity["static_graph_sha256"] or any(
                    item["image_id"] != build["image_id"] for item in containers if item["service"] in {"app", "scheduler"}):
                raise ValueError("Final image or static graph differs from the frozen lab identity")
            for host_name, container_name in [("nginx-main.conf", "/etc/nginx/nginx.conf"), ("nginx.conf", "/etc/nginx/conf.d/default.conf"), ("tls/capacity-lab.crt", "/etc/nginx/ssl/capacity-lab.crt")]:
                actual = compose_command(runtime, marker, "exec", "-T", "nginx", "sha256sum", container_name, capture=True).split()[0]
                if actual != digest(runtime / host_name):
                    raise ValueError("Final proxy configuration or TLS certificate mismatch")
            result = {"lab_nonce": marker["lab_nonce"], "durable_status": read_json(runtime / "artifacts/final-durable-facts.json")["status"],
                      "background_stopped_successfully": True, "vm_budget": vm_budget(), "containers": containers,
                      "performance_status_separate": read_json(report)["status"],
                      "artifact_sha256": {path.name:digest(path) for path in sorted((runtime / "artifacts").iterdir()) if path.is_file() and path.name != "controller-final-verification.json"}}
            result["status"] = "passed" if result["durable_status"] == "passed" else "failed"
            write_json(runtime / "artifacts/controller-final-verification.json", result)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("status") == "failed" else 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
