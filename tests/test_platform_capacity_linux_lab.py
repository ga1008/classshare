from __future__ import annotations

from contextlib import nullcontext
import copy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools import platform_capacity_linux_lab as lab


class LinuxLabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="capacity-linux-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".codex-temp").mkdir()
        self.nonce = "a" * 32
        self.marker = {"schema_version": 1, "kind": lab.KIND, "synthetic": True, "lab_nonce": self.nonce,
            "compose_project": "lanshare-capacity-" + self.nonce, "release_id": "capacity-linux-" + self.nonce,
            "synthetic_password": "Capacity-only-" + "a" * 32, "synthetic_jwt_secret": "b" * 64,
            "database_password": "c" * 64, "artifact_root": str(lab.ARTIFACTS), "client_ip": "172.30.32.1",
            "network": {"subnet": "172.30.32.0/24", "gateway": "172.30.32.1", "addresses": {
                "postgres": "172.30.32.2", "app": "172.30.32.3", "nginx": "172.30.32.4", "scheduler": "172.30.32.5"}},
            "database": {"host": "postgres", "port": 5432, "name": "capacity_" + self.nonce,
                "user": "capacity_" + self.nonce[:16], "password": "c" * 64, "system_identifier": "123456789",
                "data_directory": "/var/lib/postgresql/data/pgdata", "server_address": "172.30.32.2"}}

    def repository(self):
        for name in lab.REQUIRED_EXPORT | {"classroom_app/example.py"}:
            target = self.root / name; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# synthetic reviewed source\n", encoding="utf-8")
        manifest = {path.relative_to(self.root).as_posix():lab.digest(path) for path in self.root.rglob("*") if path.is_file()}
        path = self.root / ".codex-temp/source-manifest.json"; lab.write_json(path, manifest)
        return path, manifest

    def layout(self):
        runtime = self.root / "linux" / self.nonce
        for name in ("artifacts", "tls", "source"):
            (runtime / name).mkdir(parents=True, exist_ok=True)
        nginx = (lab.ROOT / "nginx.conf").read_text(encoding="utf-8")
        (runtime / "source/nginx.conf").write_text(nginx, encoding="utf-8")
        main = runtime / "source/deployment/nginx-main.conf"
        main.parent.mkdir(parents=True)
        main.write_bytes((lab.ROOT / "deployment/nginx-main.conf").read_bytes())
        (runtime / "nginx-main.conf").write_bytes(main.read_bytes())
        marker = copy.deepcopy(self.marker)
        marker["source_manifest"] = {"nginx.conf":lab.digest(runtime / "source/nginx.conf"),
                                     "deployment/nginx-main.conf":lab.digest(main)}
        lab.write_json(runtime / "compose.json", lab.compose_definition(marker, "lanshare-capacity:" + self.nonce))
        (runtime / "app.env").write_text(lab._env_text(lab.application_environment(marker)), encoding="utf-8")
        (runtime / "postgres.env").write_text(lab._env_text({"POSTGRES_USER": marker["database"]["user"],
            "POSTGRES_PASSWORD": marker["database_password"], "POSTGRES_DB": marker["database"]["name"]}), encoding="utf-8")
        (runtime / "nginx.conf").write_text(lab.render_nginx(nginx, client_ip=marker["client_ip"], release_id=marker["release_id"]), encoding="utf-8")
        return runtime, marker

    def test_export_round_trip_is_exact_allowlist_without_environment_or_runtime_data(self):
        path, expected = self.repository()
        (self.root / ".env").write_text("PRODUCTION_SECRET=must-not-export")
        (self.root / "data").mkdir(); (self.root / "data/private.txt").write_text("not source")
        bundle = self.root / ".codex-temp/bundle"
        result = lab.prepare_export(path, bundle, root=self.root, nonce=self.nonce)
        with tarfile.open(bundle / "source.tar") as tar:
            self.assertEqual(set(expected), set(tar.getnames()))
        destination = self.root / "linux-source"
        plan = lab.extract_source(bundle, destination)
        self.assertEqual(expected, {name:lab.digest(destination / name) for name in expected})
        self.assertEqual(result["lab_nonce"], plan["lab_nonce"])
        self.assertFalse((destination / ".env").exists())
        self.assertFalse((destination / "data").exists())
        self.assertNotIn(plan["synthetic_password"].encode(), (bundle / "source.tar").read_bytes())

    def test_actual_isolated_sqlite_initializer_exports_the_required_pg_foundation(self):
        from classroom_app import config
        from classroom_app.database import init_database
        from tools.db_postgres_export import build_postgres_export_package
        self.assertEqual("sqlite",config.DB_ENGINE)
        self.assertFalse(config.DATABASE_URL)
        config.ensure_runtime_directories()
        init_database()
        with tempfile.TemporaryDirectory(prefix="capacity-fresh-schema-",dir=lab.ROOT / ".codex-temp") as folder:
            export=Path(folder) / "export"
            self.assertFalse(export.exists())
            report=build_postgres_export_package(export,source_db=config.DB_PATH)
            self.assertEqual("ok",report["status"]); self.assertEqual(0,report["foreign_key_violations"])
            required={"teachers","students","classes","courses","class_offerings","behavior_write_inbox"}
            self.assertTrue(required.issubset({table["table"] for table in report["tables"]}))
            sql=(export / "package/01-schema.sql").read_text(encoding="utf-8")
            self.assertTrue(all(f'CREATE TABLE IF NOT EXISTS "{name}"' in sql for name in required))
            from tools.platform_capacity_linux_seed import order_fresh_constraints
            target=export / "package/03-constraints-indexes.sql"
            constraints=order_fresh_constraints(config.DB_PATH,target)
            self.assertGreater(constraints["foreign_keys"],0)
            statements=target.read_text(encoding="utf-8").splitlines()
            first_fk=next(i for i,line in enumerate(statements) if " FOREIGN KEY " in line)
            last_key=max(i for i,line in enumerate(statements) if " UNIQUE " in line or line.startswith("CREATE "))
            self.assertLess(last_key,first_fk)
            self.assertTrue(any('REFERENCES "attendance_report_students"' in line for line in statements[first_fk:]))
            with sqlite3.connect(config.DB_PATH) as conn:
                self.assertEqual(0,conn.execute("SELECT COUNT(*) FROM students").fetchone()[0])

    def test_native_social_guard_stays_exact_before_shared_validated_seed_body(self):
        from tools import platform_capacity_social as social
        args=SimpleNamespace(pg_port=18766,users=200)
        fixture={"uiV3Synthetic":True}
        expected="postgresql://rehearsal_admin@127.0.0.1:18766/lanshare_assessment_rehearsal_capacity"
        with patch.dict(social.os.environ,{"DATABASE_URL":expected,"PYTHON_DOTENV_DISABLED":"1"}), \
             patch.object(social,"_seed_validated_social",return_value={"synthetic":True}) as seed:
            self.assertEqual({"synthetic":True},social.seed_social(args,self.root,fixture))
            seed.assert_called_once_with(self.root,fixture,users=200)
        with patch.dict(social.os.environ,{"DATABASE_URL":"postgresql://elsewhere/real","PYTHON_DOTENV_DISABLED":"1"}), \
             patch.object(social,"_seed_validated_social") as seed:
            with self.assertRaisesRegex(RuntimeError,"native synthetic boundary"):
                social.seed_social(args,self.root,fixture)
            seed.assert_not_called()

    def test_start_refuses_preexisting_named_data_before_first_compose_up(self):
        name=self.marker["compose_project"]+"-postgres"
        with patch.object(lab,"_run",side_effect=[name+"\n","bridge\nhost\nnone\n"]):
            with self.assertRaisesRegex(ValueError,"pre-existing project volume"):
                lab.assert_no_preexisting_resources(self.marker)
        with patch.object(lab,"_run",side_effect=["","bridge\nhost\nnone\n"]):
            lab.assert_no_preexisting_resources(self.marker)

    def test_manifest_rejects_changed_source_before_creating_runtime(self):
        path, _ = self.repository()
        (self.root / "main.py").write_text("changed")
        runtime = self.root / ".codex-temp/not-created"
        with self.assertRaisesRegex(ValueError, "actual bytes"):
            lab.prepare_export(path, runtime, root=self.root)
        self.assertFalse(runtime.exists())

    def test_refresh_includes_all_tracked_runtime_vendor_and_build_trees(self):
        baseline,_=self.repository()
        tracked=["static/vendor/runtime/es2022-polyfills.js","static/vendor/mermaid/chunk.mjs",
                 "frontend/src/required.ts","templates/required.html","classroom_app/resources/catalog.json",
                 "deployment/docker/required.sh","root-helper.py","components.json"]
        for name in tracked+["static/vendor/untracked-private.js"]:
            path=self.root / name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text("owned source")
        output=self.root / ".codex-temp/complete-source.json"
        with patch.object(lab,"_run",return_value="\0".join(tracked)+"\0"):
            lab.refresh_source_manifest(baseline,output,root=self.root)
        manifest=lab.read_json(output)
        self.assertTrue(set(tracked).issubset(manifest))
        self.assertNotIn("static/vendor/untracked-private.js",manifest)

    def test_export_rejects_unreviewed_backend_and_sensitive_path_aliases(self):
        _, manifest = self.repository()
        (self.root / "classroom_app/new.py").write_text("# new backend")
        with self.assertRaisesRegex(ValueError, "every backend"):
            lab.validate_export_manifest(self.root, manifest)
        for name in (".env", "docker.env", "data/x", "DATA/x", "../x", "/x", "C:/x", "a/./b", "a//b", "a\\b", "tls/server.key"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                lab.safe_source_path(name)

    def test_archive_link_is_rejected_before_any_extraction(self):
        path, _ = self.repository(); bundle = self.root / ".codex-temp/bundle"
        lab.prepare_export(path, bundle, root=self.root, nonce=self.nonce)
        payloads = {}
        with tarfile.open(bundle / "source.tar") as tar:
            for member in tar:
                payloads[member.name] = tar.extractfile(member).read()
        with tarfile.open(bundle / "source.tar", "w") as tar:
            for name, payload in payloads.items():
                info = tarfile.TarInfo(name)
                if name == "main.py":
                    info.type, info.linkname = tarfile.SYMTYPE, "../../private"
                    tar.addfile(info)
                else:
                    info.size = len(payload); tar.addfile(info, io.BytesIO(payload))
        plan = lab.read_json(bundle / "lab-plan.json"); plan["archive_sha256"] = lab.digest(bundle / "source.tar")
        lab.write_json(bundle / "lab-plan.json", plan)
        destination = self.root / "never-extracted"
        with self.assertRaisesRegex(ValueError, "regular source"):
            lab.extract_source(bundle, destination)
        self.assertFalse(destination.exists())

    def test_nginx_derivation_adds_only_exact_lab_health_and_identity_access(self):
        original = (lab.ROOT / "nginx.conf").read_text(encoding="utf-8")
        result = lab.render_nginx(original, client_ip="172.30.32.1", release_id=self.marker["release_id"])
        self.assertEqual(3, result.count("allow 172.30.32.1;"))
        self.assertNotIn("allow 172.30.32.0/24", result)
        self.assertEqual(1, result.count("location = /api/internal/capacity-lab-identity"))
        self.assertIn("location ^~ /api/internal/ {\n        return 404;", result)
        self.assertIn("keepalive_timeout 20s;", result)
        self.assertIn("proxy_set_header Upgrade $http_upgrade;", result)
        for address in ("0.0.0.0", "8.8.8.8", "172.30.32.0/24", "::1"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                lab.render_nginx(original, client_ip=address, release_id=self.marker["release_id"])
        self.assertEqual(original, (lab.ROOT / "nginx.conf").read_text(encoding="utf-8"))

    def test_compose_keeps_all_data_in_nonce_volumes_and_network_internal(self):
        compose = lab.compose_definition(self.marker, "sha256:" + "d" * 64)
        services = compose["services"]
        self.assertEqual(["main"], services["app"]["command"])
        self.assertEqual(["127.0.0.1:28767:8000"], services["app"]["ports"])
        self.assertEqual(["127.0.0.1:28769:443"], services["nginx"]["ports"])
        self.assertNotIn("ports", services["postgres"])
        self.assertTrue(compose["networks"]["lab"]["internal"])
        self.assertEqual([{"subnet": "172.30.32.0/24", "gateway": "172.30.32.1"}],
                         compose["networks"]["lab"]["ipam"]["config"])
        self.assertTrue(all(spec["labels"][lab.LABEL] == self.nonce for spec in compose["volumes"].values()))
        self.assertIn("$${POSTGRES_USER}", services["postgres"]["healthcheck"]["test"][1])
        self.assertNotIn("docker.sock", json.dumps(compose))
        self.assertNotIn("./docker.env", json.dumps(compose))
        self.assertEqual("never", services["app"]["pull_policy"])

    def test_connection_limits_are_identical_in_generated_env_compose_and_source_export(self):
        services = lab.compose_definition(self.marker, "owned-image")["services"]
        self.assertEqual(2048, lab.SERVER_CONFIG["uvicorn_limit_concurrency"])
        self.assertEqual("2048", lab.application_environment(self.marker)["MAIN_LIMIT_CONCURRENCY"])
        self.assertEqual(1, lab.SERVER_CONFIG["workers"])
        self.assertEqual(12, lab.SERVER_CONFIG["postgres_pool_max"])
        for name in ("app", "scheduler", "controller", "nginx"):
            self.assertEqual({"nofile": {"soft": 8192, "hard": 8192}}, services[name]["ulimits"])
        self.assertIn("./nginx-main.conf:/etc/nginx/nginx.conf:ro", services["nginx"]["volumes"])
        self.assertIn("deployment/nginx-main.conf", lab.REQUIRED_EXPORT)

    def test_layout_rejects_changed_proxy_main_config_and_missing_export(self):
        runtime, marker = self.layout()
        lab.validate_lab_layout(runtime, marker)
        (runtime / "nginx-main.conf").write_text("events { worker_connections 1024; }\n")
        with self.assertRaisesRegex(ValueError, "Nginx main configuration"):
            lab.validate_lab_layout(runtime, marker)
        path, manifest = self.repository()
        del manifest["deployment/nginx-main.conf"]
        with self.assertRaisesRegex(ValueError, "required build/lab inputs"):
            lab.validate_export_manifest(self.root, manifest)

    def test_live_inspection_rejects_missing_or_unbounded_nofile(self):
        network_name = self.marker["compose_project"] + "-network"
        app = {"Id": "owned-app", "Image": "owned-image", "Config": {"Labels": {
                lab.LABEL: self.nonce, "com.docker.compose.project": self.marker["compose_project"],
                "com.docker.compose.service": "app"}},
               "HostConfig": {"Ulimits": [{"Name": "nofile", "Soft": 8192, "Hard": 8192}]},
               "NetworkSettings": {"Networks": {network_name: {}}}, "State": {"Running": True, "ExitCode": 0}}
        network = {"Internal": True, "Labels": {lab.LABEL: self.nonce}}
        with patch.object(lab, "compose_command", return_value="owned-app\n"), \
             patch.object(lab, "_run", side_effect=[json.dumps([app]), json.dumps([network])]):
            self.assertEqual([{"Name": "nofile", "Soft": 8192, "Hard": 8192}],
                             lab.inspect_owned_resources(self.root, self.marker)[0]["nofile"])
        for limits in (None, [], [{"Name": "nofile", "Soft": 1024, "Hard": 8192}],
                       [{"Name": "nofile", "Soft": -1, "Hard": -1}]):
            app["HostConfig"]["Ulimits"] = limits
            with self.subTest(limits=limits), patch.object(lab, "compose_command", return_value="owned-app\n"), \
                 patch.object(lab, "_run", return_value=json.dumps([app])), self.assertRaisesRegex(ValueError, "nofile"):
                lab.inspect_owned_resources(self.root, self.marker)

    def test_effective_process_nofile_is_measured_instead_of_reported_from_source(self):
        resource = SimpleNamespace(RLIMIT_NOFILE=7, getrlimit=Mock(return_value=(8192, 8192)))
        with patch.dict(sys.modules, {"resource": resource}):
            self.assertEqual({"soft": 8192, "hard": 8192}, lab.process_nofile_limits())
            resource.getrlimit.assert_called_once_with(resource.RLIMIT_NOFILE)
            for limits in ((1024, 8192), (8192, -1)):
                resource.getrlimit.return_value = limits
                with self.subTest(limits=limits), self.assertRaisesRegex(ValueError, "Effective process nofile"):
                    lab.process_nofile_limits()

    def test_mode2_fresh_stage_reserves_static_slots_before_first_network_creation(self):
        manifest_path, manifest = self.repository()
        source = self.root / "nginx.conf"
        source.write_bytes((lab.ROOT / "nginx.conf").read_bytes())
        manifest["nginx.conf"] = lab.digest(source)
        lab.write_json(manifest_path, manifest)
        bundle = self.root / ".codex-temp/bundle"
        lab.prepare_export(manifest_path, bundle, root=self.root, nonce=self.nonce)
        plan_path = bundle / "lab-plan.json"
        plan = lab.read_json(plan_path)
        plan["capacity_mode"] = lab.MODE2
        lab.write_json(plan_path, plan)
        runtime = self.root / "linux" / self.nonce

        def create_test_tls(command):
            self.assertEqual(["openssl", "req"], command[:2])
            for option in ("-keyout", "-out"):
                Path(command[command.index(option) + 1]).write_text("owned fixture")

        with patch.object(lab, "os", SimpleNamespace(name="posix", chown=Mock())), \
             patch.object(lab, "linux_runtime"), patch.object(lab, "_run", side_effect=create_test_tls):
            lab.stage_bundle(bundle, runtime, client_ip="172.30.202.1")
        marker = lab.read_json(runtime / "lab-marker.json")
        compose_path = runtime / "compose.json"
        compose = lab.read_json(compose_path)
        self.assertEqual([{"subnet": "172.30.202.0/24", "gateway": "172.30.202.1", "ip_range": "172.30.202.128/25"}],
                         compose["networks"]["lab"]["ipam"]["config"])
        self.assertEqual({}, compose["services"]["controller"]["networks"]["lab"])
        self.assertEqual("172.30.202.2", compose["services"]["postgres"]["networks"]["lab"]["ipv4_address"])
        lab.validate_lab_layout(runtime, marker)
        del compose["networks"]["lab"]["ipam"]["config"][0]["ip_range"]
        lab.write_json(compose_path, compose)
        with self.assertRaisesRegex(ValueError, "Compose changed"):
            lab.validate_lab_layout(runtime, marker)

    def test_mode2_rejects_static_address_overlap_and_wrong_subnet_size(self):
        marker = {**copy.deepcopy(self.marker), "capacity_mode": lab.MODE2}
        marker["network"]["addresses"]["agent"] = "172.30.32.130"
        with self.assertRaisesRegex(ValueError, "outside its dynamic IP range"):
            lab.compose_definition(marker, "owned-image")
        marker["network"]["addresses"]["agent"] = "172.30.32.7"
        marker["network"]["subnet"] = "172.30.32.0/23"
        with self.assertRaisesRegex(ValueError, "owned IPv4 /24"):
            lab.compose_definition(marker, "owned-image")

    def test_live_mode2_ipam_inspection_rejects_missing_or_changed_allocation_range(self):
        marker = {**self.marker, "capacity_mode": lab.MODE2}
        network_name = marker["compose_project"] + "-network"
        postgres = {"Id": "owned-pg", "Image": "owned-image",
                    "Config": {"Labels": {lab.LABEL: self.nonce, "com.docker.compose.project": marker["compose_project"],
                                          "com.docker.compose.service": "postgres"}},
                    "NetworkSettings": {"Networks": {network_name: {}}}, "State": {"Running": True, "ExitCode": 0}}
        correct = {"Subnet": "172.30.32.0/24", "Gateway": "172.30.32.1", "IPRange": "172.30.32.128/25"}

        def inspect(config, selected_marker=marker):
            network = {"Internal": True, "Labels": {lab.LABEL: self.nonce}, "IPAM": {"Config": config}}
            with patch.object(lab, "compose_command", return_value="owned-pg\n"), \
                 patch.object(lab, "_run", side_effect=[json.dumps([postgres]), json.dumps([network])]):
                return lab.inspect_owned_resources(self.root, selected_marker)

        self.assertEqual("postgres", inspect([correct])[0]["service"])
        self.assertEqual("postgres", inspect([{**correct, "IPRange": ""}], self.marker)[0]["service"])
        for config in (None, [], [{**correct, "IPRange": ""}], [{**correct, "IPRange": "172.30.32.0/25"}],
                       [{**correct, "Subnet": "172.30.33.0/24"}], [{**correct, "Gateway": "172.30.32.9"}],
                       [correct, correct]):
            with self.subTest(config=config), self.assertRaisesRegex(ValueError, "exact dynamic IP range"):
                inspect(config)

    def test_layout_refuses_changed_dsn_and_public_port_before_any_command(self):
        runtime, marker = self.layout()
        lab.validate_lab_layout(runtime, marker)
        (runtime / "app.env").write_text("DATABASE_URL=postgresql://production/real\n")
        with self.assertRaisesRegex(ValueError, "synthetic env"):
            lab.validate_lab_layout(runtime, marker)
        (runtime / "app.env").write_text(lab._env_text(lab.application_environment(marker)), encoding="utf-8")
        compose = lab.read_json(runtime / "compose.json"); compose["services"]["app"]["ports"] = ["0.0.0.0:8000:8000"]
        lab.write_json(runtime / "compose.json", compose)
        with self.assertRaisesRegex(ValueError, "Compose changed"):
            lab.validate_lab_layout(runtime, marker)

    def test_generated_env_contains_only_synthetic_admin_and_disabled_external_provider(self):
        env = lab.application_environment(self.marker)
        self.assertEqual("capacity-admin@example.invalid", env["INITIAL_SUPER_ADMIN_EMAIL"])
        self.assertEqual("http://127.0.0.1:1", env["AI_ASSISTANT_URL"])
        self.assertEqual("1", env["PYTHON_DOTENV_DISABLED"])
        self.assertEqual(lab.database_url(self.marker), env["DATABASE_URL"])
        self.assertEqual("/app/data", env["LANSHARE_DATA_ROOT"])

    def test_published_volume_is_verified_against_actual_image_asset_bytes(self):
        source=self.root / "static"; revision="e"*64
        for name,payload in [(f"assets/{revision}/a.js",b"js"),("dist/assets/page-abcdefgh.js",b"vite"),("dist/assets/page-abcdefgh.js.gz",b"gzip")]:
            target=source / name; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(payload)
        expected=lab.static_asset_hashes(source,revision)
        published=self.root / "published"; published.mkdir()
        for name in expected:
            target=published / name; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes((source/name).read_bytes())
        self.assertEqual(3,lab.verify_published_assets(expected,published)["files"])
        (published / "dist/assets/page-abcdefgh.js").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError,"actual image asset bytes"):
            lab.verify_published_assets(expected,published)

    def test_inventory_uses_actual_frontend_loaders_and_rejects_missing_vendor_or_graph(self):
        from classroom_app import frontend_assets
        static=self.root / "static"; (static / "vendor/runtime").mkdir(parents=True)
        manifest_path=static / "vendor/manifest.json"
        lab.write_json(manifest_path,{"polyfill":{"path":"vendor/runtime/es2022-polyfills.js","version":"1"}})
        revision="e"*64; graph_path=static / "assets/manifest.json"
        lab.write_json(graph_path,{"schema":1,"revision":revision,"entries":{"js/demo.js":f"assets/{revision}/js/demo.js"}})
        target=static / f"assets/{revision}/js/demo.js"; target.parent.mkdir(parents=True); target.write_text("graph source")
        source={"static/vendor/manifest.json":lab.digest(manifest_path)}
        try:
            with patch.object(frontend_assets,"STATIC_DIR",static), patch.object(frontend_assets,"ASSET_MANIFEST_PATH",manifest_path), \
                 patch.object(frontend_assets,"get_deployment_release_id",return_value="capacity-inventory-test"):
                with self.assertRaises(FileNotFoundError):
                    lab.verify_frontend_runtime_inputs(self.root,source)
                vendor=static / "vendor/runtime/es2022-polyfills.js"; vendor.write_text("polyfill")
                source["static/vendor/runtime/es2022-polyfills.js"]=lab.digest(vendor)
                result=lab.verify_frontend_runtime_inputs(self.root,source)
                self.assertEqual(1,result["frontend_manifest_entries"]); self.assertEqual(2,result["vendor_source_files"])
                target.unlink()
                with self.assertRaises(FileNotFoundError):
                    lab.verify_frontend_runtime_inputs(self.root,source)
        finally:
            frontend_assets.load_frontend_asset_manifest.cache_clear(); frontend_assets.load_static_asset_graph.cache_clear()

    def test_pg_identity_checks_close_wrong_database_or_system_before_app_import(self):
        def connection(system_id="123456789", address="172.30.32.2", version=160000):
            conn = Mock()
            conn.execute.side_effect = [SimpleNamespace(fetchone=lambda: (self.marker["database"]["name"], self.marker["database"]["user"],
                "/var/lib/postgresql/data/pgdata", address, 5432, version)), SimpleNamespace(fetchone=lambda: (system_id,))]
            return conn
        for kwargs in ({"system_id":"wrong"}, {"address":"127.0.0.1"}, {"version":150000}):
            conn = connection(**kwargs)
            with patch.object(lab, "_install_container_network_guard"), patch.dict(sys.modules, {"psycopg":SimpleNamespace(connect=Mock(return_value=conn))}):
                with self.assertRaisesRegex(ValueError, "identity mismatch"):
                    lab._checked_pg_connection(self.marker)
            conn.close.assert_called_once()
        conn = connection()
        with patch.object(lab, "_install_container_network_guard"), patch.dict(sys.modules, {"psycopg":SimpleNamespace(connect=Mock(return_value=conn))}):
            actual, identity = lab._checked_pg_connection(self.marker)
        self.assertIs(conn, actual); self.assertEqual("123456789", identity); conn.close.assert_not_called()
        # Real PG16 exposes inet::text as address/32; host() keeps the exact IP
        # identity comparison without accepting arbitrary CIDR ranges.
        self.assertIn("host(inet_server_addr())",conn.execute.call_args_list[0].args[0])

    def test_wrong_container_marker_or_environment_fails_before_pg_connection(self):
        with patch.object(lab,"read_json") as reader, patch.object(lab,"_checked_pg_connection") as connect:
            with self.assertRaisesRegex(ValueError,"explicit container marker"):
                lab.validate_container_boundary(self.root / "not-a-container-marker.json")
        reader.assert_not_called(); connect.assert_not_called()
        environment = lab.application_environment(self.marker)
        with patch.object(lab,"read_json",return_value=self.marker), patch.object(Path,"is_file",return_value=True), patch.object(Path,"exists",return_value=False), \
             patch("tools.isolated_environment.guard_dotenv_loading") as dotenv, patch.dict(lab.os.environ,environment,clear=True):
            self.assertEqual(self.marker,lab._container_marker(lab.MARKER))
            dotenv.assert_called_once()
            with patch.dict(lab.os.environ,{"DATABASE_URL":"postgresql://other/real"}), patch.object(lab,"_checked_pg_connection") as connect:
                with self.assertRaisesRegex(ValueError,"owned synthetic lab"):
                    lab.validate_container_boundary(lab.MARKER)
                connect.assert_not_called()

    def test_wrong_database_nonce_is_closed_before_loading_fixture(self):
        conn=Mock(); conn.execute.return_value.fetchall.return_value=[("other-lab",)]
        with patch.object(lab,"_container_marker",return_value=self.marker), patch.object(lab,"_checked_pg_connection",return_value=(conn,"123456789")), \
             patch.object(lab,"read_json") as fixture:
            with self.assertRaisesRegex(ValueError,"lab nonce mismatch"):
                lab.validate_container_boundary()
        fixture.assert_not_called(); conn.close.assert_called_once()

    def test_bootstrap_cannot_claim_existing_tables_or_an_already_claimed_cluster(self):
        marker=copy.deepcopy(self.marker); marker["database"]["system_identifier"]=None
        conn=Mock(); conn.execute.return_value.fetchone.return_value=(1,)
        with patch.object(lab,"_container_marker",return_value=marker), patch.object(lab,"_checked_pg_connection",return_value=(conn,"123456789")), \
             patch.object(lab,"write_json") as write:
            with self.assertRaisesRegex(ValueError,"existing tables"):
                lab.container_bootstrap()
        self.assertEqual(1,conn.execute.call_count); conn.transaction.assert_not_called(); conn.close.assert_called_once(); write.assert_not_called()
        with patch.object(lab,"_container_marker",return_value=self.marker), patch.object(lab,"_checked_pg_connection") as connect:
            with self.assertRaisesRegex(ValueError,"one-time"):
                lab.container_bootstrap()
        connect.assert_not_called()

    def test_vm_budget_requires_whole_linux_two_cpu_four_gib_and_no_swap(self):
        info={"OSType":"linux","NCPU":2,"MemTotal":4*2**30,"DockerRootDir":"/var/lib/docker","ServerVersion":"synthetic"}
        with patch.object(lab.os,"name","posix"), patch.object(lab.os,"cpu_count",return_value=2), patch.object(Path,"is_file",return_value=True), \
             patch.object(Path,"read_text",return_value="MemTotal: 4000000 kB\nSwapTotal: 0 kB\n"), patch.object(lab,"_run",side_effect=[json.dumps(info),"ext4\n"]):
            budget=lab.vm_budget()
        self.assertEqual(2,budget["cpus"]); self.assertEqual(0,budget["swap_total_bytes"])
        for cpus,memory,swap in [(4,4000000,0),(2,8000000,0),(2,4000000,1024)]:
            with self.subTest(cpus=cpus,memory=memory,swap=swap), patch.object(lab.os,"name","posix"), \
                 patch.object(lab.os,"cpu_count",return_value=cpus), patch.object(Path,"is_file",return_value=True), \
                 patch.object(Path,"read_text",return_value=f"MemTotal: {memory} kB\nSwapTotal: {swap} kB\n"), patch.object(lab,"_run") as docker:
                with self.assertRaisesRegex(ValueError,"four GiB"):
                    lab.vm_budget()
                docker.assert_not_called()

    def test_network_guard_rejects_external_dns_and_literal_socket_without_connecting(self):
        old_nonce = getattr(socket, "_capacity_linux_lab_nonce", None)
        def restore():
            if old_nonce is None:
                if hasattr(socket, "_capacity_linux_lab_nonce"): del socket._capacity_linux_lab_nonce
            else: socket._capacity_linux_lab_nonce = old_nonce
        self.addCleanup(restore)
        if old_nonce is not None: del socket._capacity_linux_lab_nonce
        connect, connect_ex = Mock(return_value=None), Mock(return_value=0)
        dns = Mock(return_value=[(2,1,6,"",("172.30.32.2",5432))])
        with patch.object(socket.socket, "connect", connect), patch.object(socket.socket, "connect_ex", connect_ex), patch.object(socket, "getaddrinfo", dns):
            lab._install_container_network_guard(self.marker)
            with self.assertRaisesRegex(RuntimeError, "external DNS"): socket.getaddrinfo("example.com",443)
            with self.assertRaisesRegex(RuntimeError, "external sockets"): socket.socket.connect(None,("8.8.8.8",443))
            socket.socket.connect(None,("172.30.32.2",5432)); self.assertEqual(1, connect.call_count)
            self.assertEqual(1, len(socket.getaddrinfo("postgres",5432)))
            dns.return_value = [(2,1,6,"",("8.8.8.8",5432))]
            with self.assertRaisesRegex(RuntimeError, "resolved outside"): socket.getaddrinfo("postgres",5432)

    def test_socket_bytes_and_strings_share_the_exact_dns_and_connect_allowlist(self):
        old_nonce = getattr(socket, "_capacity_linux_lab_nonce", None)
        def restore_nonce():
            if old_nonce is None:
                if hasattr(socket, "_capacity_linux_lab_nonce"): del socket._capacity_linux_lab_nonce
            else: socket._capacity_linux_lab_nonce = old_nonce
        self.addCleanup(restore_nonce)
        if old_nonce is not None: del socket._capacity_linux_lab_nonce
        connect, connect_ex = Mock(return_value=None), Mock(return_value=0)
        dns = Mock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.30.32.3", 8000))])
        with patch.object(socket.socket, "connect", connect), patch.object(socket.socket, "connect_ex", connect_ex), \
             patch.object(socket, "getaddrinfo", dns):
            lab._install_container_network_guard(self.marker)
            for host in ("app", b"app", "APP", b"APP", "172.30.32.3", b"172.30.32.3", None):
                with self.subTest(host=host):
                    self.assertEqual(len(socket.getaddrinfo(host, 8000)), 1)
                    self.assertIsNone(socket.socket.connect(None, (host, 8000)))
                    self.assertEqual(socket.socket.connect_ex(None, (host, 8000)), 0)
            permitted_calls = len(connect.call_args_list)
            for host in ("example.invalid", b"example.invalid", "8.8.8.8", b"8.8.8.8",
                         b"app\xff", b"app\x00.invalid", "172.30.32.99", b"172.30.32.99"):
                with self.subTest(rejected_host=host):
                    with self.assertRaisesRegex(RuntimeError, "external DNS"): socket.getaddrinfo(host, 8000)
                    with self.assertRaisesRegex(RuntimeError, "external sockets"): socket.socket.connect(None, (host, 8000))
                    with self.assertRaisesRegex(RuntimeError, "external sockets"): socket.socket.connect_ex(None, (host, 8000))
            self.assertEqual(connect.call_count, permitted_calls)
            self.assertEqual(connect_ex.call_count, permitted_calls)
            dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 8000))]
            for host in ("app", b"app"):
                with self.assertRaisesRegex(RuntimeError, "resolved outside"): socket.getaddrinfo(host, 8000)

    def test_background_final_gate_rejects_error_even_with_stopped_heartbeat(self):
        worker = [{"service":"scheduler", "running":False, "exit_code":0, "oom_killed":False}]
        state = {"status":"stopped", "projection_measurement_errors":[]}
        report = {"task_states":{"capacity_controlled_provider_wait:done":10,"wanderer_tick:pending":1},
            "worker_identity_matches_run":True,"finite_task_verification":{"all_present_and_done":True},
            "projection_durable_facts":{"all_rows_clean_and_current":True}, "notification_checks":[]}
        lab.verify_background_closed(worker,state,report)
        cases = [("exit", lambda w,s,r:w[0].update(exit_code=1)), ("state",lambda w,s,r:s.update(status="error")),
                 ("measurement",lambda w,s,r:s.update(projection_measurement_errors=[{"phase":"commit"}])),
                 ("finite",lambda w,s,r:r["task_states"].update({"todo_due_reminder:pending":1})),
                 ("dirty",lambda w,s,r:r["projection_durable_facts"].update(all_rows_clean_and_current=False)),
                 ("recipient",lambda w,s,r:r.update(notification_checks=[{"missing_fixture_recipients":1,"task_completed":True}]))]
        for name, mutate in cases:
            w,s,r = copy.deepcopy((worker,state,report)); mutate(w,s,r)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "verification failed"):
                lab.verify_background_closed(w,s,r)

    def handoff(self):
        (self.root / "classroom_app").mkdir(); (self.root / "classroom_app/example.py").write_text("# source")
        (self.root / "scheduled_task_dispatcher.py").write_text("# dispatcher")
        folder = self.root / ".codex-temp/handoff"; folder.mkdir()
        users = [{"identifier":f"CAPACITY-SYNTHETIC-{i:04d}", "student_id":i+1} for i in range(200)]
        sources = lab.source_hashes(self.root)
        identity = {"schema_version":1,"synthetic":True,"lab_nonce":self.nonce,"release_id":self.marker["release_id"],
            "source_fingerprint":lab.fingerprint(sources),"config_fingerprint":lab.fingerprint(lab.SERVER_CONFIG),
            "static_graph_sha256":"d"*64,"users_sha256":lab.fingerprint(users),"offering_id":7}
        (folder / "capacity-lab.crt").write_bytes(b"synthetic-test-certificate")
        handoff = {"kind":"capacity-linux-controller-handoff","schema_version":1,"synthetic":True,"lab_nonce":self.nonce,
            "release_id":self.marker["release_id"],"users":users,"offering_id":7,"expected_identity":identity,
            "server_source_sha256":sources,"client_source_sha256":sources,"server_config":dict(lab.SERVER_CONFIG),
            "synthetic_password":self.marker["synthetic_password"],"synthetic_jwt_secret":self.marker["synthetic_jwt_secret"],
            "base_url":"https://127.0.0.1:28769","ca_sha256":lab.digest(folder / "capacity-lab.crt")}
        lab.write_json(folder / "handoff.json", handoff)
        lab.write_json(folder / "capacity-social.json", {"synthetic":True,"identifiers":[u["identifier"] for u in users],"student_ids":[u["student_id"] for u in users]})
        lab.write_json(folder / "capacity-reading.json", {"synthetic":True,"class_offering_id":7,"material_id":9})
        return folder

    def test_client_handoff_works_with_shared_loader_and_never_creates_a_fake_db(self):
        folder = self.handoff(); runtime = self.root / ".codex-temp/client"
        lab.create_client_runtime(folder,runtime,root=self.root)
        from tools.platform_capacity_lab_manifest import load_lab_manifest
        args = SimpleNamespace(action="run",users=200,cluster_dir=None,pg_port=None,proxy_port=0,http_port=18767,
            runtime=runtime,lab_manifest=runtime / "lab-manifest.json",social=True,reading=True)
        loaded = load_lab_manifest(args,self.root)
        self.assertEqual(200,len(loaded.identifiers))
        self.assertFalse((runtime / "db").exists())
        self.assertNotIn("databasePath", lab.read_json(runtime / "fixture.json"))
        evidence = loaded.evidence()
        self.assertNotIn(self.marker["synthetic_password"], json.dumps(evidence))
        self.assertNotIn(self.marker["synthetic_jwt_secret"], json.dumps(evidence))

    def test_client_handoff_refuses_source_drift_before_creating_output(self):
        folder = self.handoff(); (self.root / "classroom_app/example.py").write_text("# changed")
        runtime = self.root / ".codex-temp/rejected-client"
        with self.assertRaisesRegex(ValueError,"intact controller handoff"):
            lab.create_client_runtime(folder,runtime,root=self.root)
        self.assertFalse(runtime.exists())

    def test_duplicate_and_nonfinite_artifact_json_are_rejected(self):
        path = self.root / "invalid.json"
        for value in ('{"synthetic":true,"synthetic":false}', '{"elapsed":NaN}'):
            path.write_text(value)
            with self.subTest(value=value), self.assertRaises(ValueError): lab.read_json(path)

    def test_private_json_keeps_0600_and_inherits_the_host_artifact_owner(self):
        target=self.root / "server-fixture.json"; temporary=target.with_suffix(".json.new")
        original=Path.stat
        def stat(path,*args,**kwargs):
            result=original(path,*args,**kwargs)
            values=list(result)
            values[4],values[5]=(1000,1000) if path==target.parent else (0,0)
            return os.stat_result(values)
        chown=Mock()
        with patch.object(lab,"os",SimpleNamespace(name="posix",chown=chown)), patch.object(Path,"stat",stat), patch.object(Path,"chmod",autospec=True) as chmod:
            lab.write_json(target,{"synthetic":True},private=True)
        chmod.assert_called_once_with(temporary,0o600)
        chown.assert_called_once_with(temporary,1000,1000)
        self.assertTrue(lab.read_json(target)["synthetic"])

    def test_fresh_migration_boolean_conversion_preserves_every_row_and_primary_key(self):
        from tools.platform_capacity_linux_seed import adapt_fresh_migration_metadata
        row=("migration-v1","name","checksum","2026-11-05T12:00:00",0,"sqlite",1,None)
        keys=[("schema_migrations_pkey","PRIMARY KEY (version, db_engine)")]
        class Connection:
            def __init__(self,success=1): self.converted=False; self.success=success; self.calls=[]
            def transaction(self): return nullcontext()
            def execute(self,sql):
                self.calls.append(sql)
                if "information_schema.columns" in sql: return SimpleNamespace(fetchone=lambda:("bigint",))
                if sql.startswith("SELECT version"):
                    value=bool(self.success) if self.converted else self.success
                    return SimpleNamespace(fetchall=lambda:[(*row[:6],value,row[7])])
                if sql.startswith("SELECT conname"): return SimpleNamespace(fetchall=lambda:keys)
                if sql.startswith("ALTER TABLE"): self.converted=True
                return SimpleNamespace()
        conn=Connection()
        result=adapt_fresh_migration_metadata(conn)
        self.assertEqual({"success_type":"boolean","changed":True,"rows_preserved":1,"constraints_preserved":1,"other_columns":"unchanged"},result)
        self.assertEqual(1,sum(sql.startswith("ALTER TABLE") for sql in conn.calls))
        self.assertIn("success TYPE boolean USING (success <> 0)",next(sql for sql in conn.calls if sql.startswith("ALTER TABLE")))
        invalid=Connection(success=2)
        with self.assertRaisesRegex(ValueError,"nonboolean integer"):
            adapt_fresh_migration_metadata(invalid)
        self.assertFalse(invalid.converted)

    def test_fresh_native_timestamp_requires_empty_table_and_verifies_exact_pg_type(self):
        from tools.platform_capacity_linux_seed import adapt_fresh_native_signature_timestamp
        class Connection:
            def __init__(self,count=0): self.count=count; self.converted=False; self.calls=[]
            def transaction(self): return nullcontext()
            def execute(self,sql):
                self.calls.append(sql)
                if "information_schema.columns" in sql:
                    return SimpleNamespace(fetchone=lambda:("timestamp without time zone" if self.converted else "text",))
                if sql.startswith("SELECT COUNT"):
                    return SimpleNamespace(fetchone=lambda:(self.count,))
                if sql.startswith("SELECT conname"):
                    return SimpleNamespace(fetchall=lambda:[("pk","PRIMARY KEY (id)")])
                if sql.startswith("ALTER TABLE"): self.converted=True
                return SimpleNamespace()
        conn=Connection()
        result=adapt_fresh_native_signature_timestamp(conn)
        self.assertEqual("timestamp without time zone",result["type"]); self.assertEqual(0,result["rows"])
        self.assertEqual(1,result["constraints_preserved"])
        self.assertEqual(1,sum(sql.startswith("ALTER TABLE") for sql in conn.calls))
        occupied=Connection(count=1)
        with self.assertRaisesRegex(ValueError,"exact empty fresh"):
            adapt_fresh_native_signature_timestamp(occupied)
        self.assertFalse(occupied.converted)

    def test_durable_verifier_uses_readonly_queries_and_keeps_performance_failure(self):
        from tools.platform_capacity_linux_seed import final_durable_facts
        runtime = self.root / "artifacts"; runtime.mkdir()
        expected_identity = {"lab_nonce":self.nonce}; lab.write_json(runtime / "capacity-lab-identity.json",expected_identity)
        report = {"status":"failed","users":200,"run_id":"b"*32,"run_started_epoch":datetime(2026,11,5,tzinfo=timezone.utc).timestamp(),
            "lab":{"mode":"linux_lab_attach_only","lab_nonce":self.nonce,"release_id":self.marker["release_id"],"expected_identity":expected_identity},
            "statuses":[{"flow":"behavior","status":202,"count":1},{"flow":"behavior","status":200,"count":1}],
            "ambient":{"statuses":[{"flow":"ambient-heartbeat","status":202,"count":1}]},
            "health":{"server_local_time":"2026-11-05T09:00:00"}}
        report_path=runtime / "report.json"; lab.write_json(report_path,report)
        calls=[]
        class Connection:
            def transaction(self): return nullcontext()
            def close(self): pass
            def execute(self,sql,params=None):
                calls.append((sql,params))
                if "GROUP BY page_key" in sql: return SimpleNamespace(fetchall=lambda:[("capacity","applied",2),("capacity_ambient","applied",1)])
                if "SELECT student_id,dirty" in sql: return SimpleNamespace(fetchall=lambda:[(i,0,7,7) for i in range(1,201)])
                return SimpleNamespace(fetchone=lambda:(0,))
        identity={"lab_nonce":self.nonce,"marker":self.marker,"artifact_root":runtime,
            "fixture":{"classOfferingId":7,"users":[{"student_id":i} for i in range(1,201)]}}
        with patch.object(lab,"_checked_pg_connection",return_value=(Connection(),"123456789")):
            result=final_durable_facts(identity,report_path)
        self.assertEqual("SET TRANSACTION READ ONLY",calls[0][0])
        receipt_query = next((sql,params) for sql,params in calls if "GROUP BY page_key" in sql)
        self.assertIn("received_at<%s",receipt_query[0]); self.assertEqual("2026-11-05T09:00:01.000000",receipt_query[1][1])
        self.assertEqual("passed",result["status"]); self.assertEqual("failed",result["performance_run_status_separate"])
        self.assertEqual({"capacity":2,"capacity_ambient":1},result["expected_receipts"])
        self.assertTrue(all(not sql.lstrip().upper().startswith(("INSERT","UPDATE","DELETE")) for sql,_ in calls))


if __name__ == "__main__":
    unittest.main()
