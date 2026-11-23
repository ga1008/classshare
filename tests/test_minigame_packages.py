import contextlib
import asyncio
import io
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, Mock, patch
import zipfile

from fastapi import HTTPException
from PIL import Image
from classroom_app.services import minigame_packages as packages
from classroom_app.services import minigame_package_validation as validation
from classroom_app.services import minigame_platform as platform
from fixtures.minigame_probe_media import media_files


def sample_files():
    sample = validation.BASE / "docs/minigames/example"

    def read(name):
        return json.loads((sample / name).read_text(encoding="utf-8"))

    config = read("config.json")
    media = media_files()
    config["assets"] = sorted([*media, config["icon"]])
    definition = {
        "config": config,
        "writeback": read("contracts/writeback.json"),
        "rules": read("contracts/rules.json"),
    }
    icon = io.BytesIO()
    Image.new("RGB", (128, 128), "navy").save(icon, "PNG")
    return {
        "config.json": validation.canonical(config).encode(),
        "contracts/writeback.json": validation.canonical(definition["writeback"]).encode(),
        "contracts/rules.json": validation.canonical(definition["rules"]).encode(),
        "js/lan-game-sdk.v1.js": validation.SDK.read_bytes(),
        "js/offline-contracts.js": (
            "window.LanGameOfflineContracts = " + validation.canonical(definition) + ";"
        ).encode(),
        "js/game.js": b"window.LanGame.ready().then(c=>document.getElementById('name').textContent=c.me.displayName);",
        "main.html": b'<!doctype html><html lang="zh"><title>Probe</title><body><main id="name"></main>'
        b'<script defer src="js/lan-game-sdk.v1.js"></script><script defer src="js/offline-contracts.js"></script>'
        b'<script defer src="js/game.js"></script></body></html>',
        config["icon"]: icon.getvalue(),
        **media,
    }


def archive(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as out:
        for name, payload in files.items():
            out.writestr(
                zipfile.ZipInfo(name, (2025, 1, 1, 0, 0, 0)), payload, compress_type=zipfile.ZIP_DEFLATED
            )
    stream.seek(0)
    return stream


class GamePackageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.object(platform, "game_root", return_value=self.root / "game-root"))
        self.enterContext(patch.object(platform, "game_db", self.db))
        self.enterContext(
            patch.object(
                platform, "load_actor", return_value={"id": 9, "role": "teacher", "actor": "teacher:9"}
            )
        )
        self.enterContext(patch.object(platform, "assert_session"))
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "MINIGAMES_DELETION_REGISTER": str(self.root / "independent-ledger"),
                    "MINIGAMES_APP_ORIGIN": "http://localhost:8000",
                    "MINIGAMES_CONTENT_ORIGIN": "http://127.0.0.1:8010",
                    "MINIGAMES_PROBE_COMMAND": "",
                },
            )
        )
        self.user = {"id": 9, "role": "teacher", "actor": "teacher:9", "session_id": "fixture-1"}
        with self.db() as conn:
            conn.execute("CREATE TABLE teachers(id INTEGER PRIMARY KEY,is_super_admin INTEGER DEFAULT 0)")
            conn.execute("INSERT INTO teachers(id) VALUES (9)")
            platform.ensure_all_schema(conn)
            conn.commit()
        self.game = packages.mutate(self.user, "", "create", {"title": "合成游戏"}, "new")["game"]
        self.files = sample_files()

    @contextlib.contextmanager
    def db(self):
        conn = sqlite3.connect(self.root / "test.sqlite")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def ingest(self):
        return packages.receive_upload(
            self.user, self.game["id"], self.game["revision"], "bytes", archive(self.files)
        )

    def game_detail(self):
        with self.db() as conn:
            return packages.detail(conn, self.user, self.game["id"])

    def fake_probe(self):
        return patch.object(
            packages,
            "probe_package",
            side_effect=lambda runtime, digest: {"digest": digest, "offline": True, "platform": True},
        )

    def test_teacher_publish_unlist_delete_and_stale_revision(self):
        res = self.ingest()
        self.assertEqual(res, self.ingest())
        with self.fake_probe():
            self.assertTrue(packages.worker_once())
        info = self.game_detail()
        vid = res["versionId"]
        self.assertEqual(info["versions"][0]["status"], "ready")
        game = self.game["id"]
        rev = info["game"]["revision"]
        with self.assertRaises(HTTPException) as blocked:
            packages.mutate(
                self.user, game, "publish", {"expectedRevision": rev, "versionId": vid}, "publish"
            )
        self.assertEqual(blocked.exception.detail["code"], "PLAYTEST_REQUIRED")
        with self.db() as conn:
            conn.execute(
                """INSERT INTO mg_launch_leases(handle_hash,lease_id,actor,sid_hash,game_id,version_id,
                purpose,policy_revision,host_instance,expires_at,signature,command_key,command_digest)
                VALUES(?,?,?,?,?,?,'preview',1,?,?,'fixture','fixture-key','fixture-digest')""",
                ("h", "l", self.user["actor"], "s", game, vid, "browser", platform.now_ms() + 90000),
            )
            conn.commit()
        with self.assertRaises(HTTPException) as unopened:
            packages.mutate(
                self.user, game, "playtest", {"expectedRevision": rev, "passed": True}, "tested", vid
            )
        self.assertEqual(unopened.exception.detail["code"], "PREVIEW_REQUIRED")
        with self.db() as conn:
            packages.mark_preview_loaded(conn, game, vid, self.user["actor"], "l")
            conn.execute("DELETE FROM mg_launch_leases")
            conn.commit()
        packages.mutate(self.user, game, "playtest", {"expectedRevision": rev, "passed": True}, "tested", vid)
        published = packages.mutate(
            self.user, game, "publish", {"expectedRevision": rev, "versionId": vid}, "publish"
        )
        self.assertEqual(published["game"]["currentVersionId"], vid)
        with self.assertRaises(HTTPException):
            packages.mutate(self.user, game, "patch", {"expectedRevision": rev, "minTier": 1}, "stale")
        row = packages.mutate(
            self.user, game, "unlist", {"expectedRevision": published["game"]["revision"]}, "stop"
        )["game"]
        tree = packages.asset_path("version", game, vid)
        self.assertTrue((tree / "runtime/main.html").is_file())
        with self.db() as conn:
            rows = conn.execute(
                "SELECT path,mime FROM mg_version_files WHERE version_id=?", (vid,)
            ).fetchall()
            self.assertEqual(len(rows), len(self.files))
            self.assertEqual(dict(rows)["static/beacon.png"], "image/png")
        plan = packages.mutate(self.user, game, "plan", {"expectedRevision": row["revision"]}, "plan")
        body = {
            "expectedRevision": row["revision"],
            "planToken": plan["planToken"],
            "confirmationText": row["gameKey"],
        }
        deleted = packages.mutate(self.user, game, "delete", body, "delete")
        self.assertEqual(deleted, packages.mutate(self.user, game, "delete", body, "delete"))
        packages.worker_once()
        self.assertFalse(tree.exists())
        self.assertEqual(self.game_detail()["game"]["status"], "deleted")
        self.assertTrue((self.root / "independent-ledger" / (deleted["jobId"] + ".json")).is_file())
        with self.db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM mg_version_files WHERE version_id=?", (vid,)).fetchone()[
                    0
                ],
                0,
            )
            for table in (
                "mg_versions",
                "mg_admin_assets",
                "mg_admin_playtests",
                "mg_admin_plans",
                "mg_launch_leases",
            ):
                self.assertEqual(
                    conn.execute(f"SELECT COUNT(*) FROM {table} WHERE game_id=?", (game,)).fetchone()[0], 0
                )
        self.assertEqual(deleted, packages.mutate(self.user, game, "delete", body, "delete"))

    def test_disabled_executor_parks_once_and_configured_worker_resumes(self):
        upload = self.ingest()
        game, vid = self.game["id"], upload["versionId"]
        stage = packages.asset_path("stage", game, upload["jobId"])
        version = packages.asset_path("version", game, vid)
        with patch.object(validation, "validate_package", wraps=validation.validate_package) as checked:
            with patch.object(packages.subprocess, "run") as executor:
                self.assertTrue(packages.worker_once())
                with patch.object(platform, "now_ms", return_value=platform.now_ms() + 86_400_000):
                    self.assertFalse(packages.worker_once())
                executor.assert_not_called()
            data = self.game_detail()
            self.assertEqual(data["versions"][0]["status"], "validating")
            self.assertEqual(data["jobs"][0]["status"], "waiting_probe")
            self.assertIn("静态检查已通过", data["jobs"][0]["stage"])
            self.assertTrue((stage / "upload.zip").is_file())
            self.assertTrue((stage / "prepared/runtime/main.html").is_file())
            self.assertFalse(version.exists())
            with self.db() as conn:
                usage = conn.execute("SELECT bytes_used FROM mg_admin_assets WHERE asset_id=?",
                                     (upload["jobId"],)).fetchone()[0]
                self.assertLess(usage, 1024 * 1024)
            with self.assertRaises(HTTPException) as denied:
                packages.mutate(self.user, game, "publish", {"expectedRevision": data["game"]["revision"],
                                "versionId": vid}, "early-publish")
            self.assertEqual(denied.exception.detail["code"], "VERSION_NOT_READY")
            with patch.dict(os.environ, {"MINIGAMES_PROBE_COMMAND": '["executor"]'}), self.fake_probe() as probe:
                self.assertTrue(packages.worker_once())
                probe.assert_called_once()
                self.assertEqual(checked.call_count, 1)
                data = self.game_detail()
                job = data["jobs"][0]
                if job["status"] == "retry":
                    self.assertEqual(job["error"].get("code"), "ASSET_BUSY", job)
                    with self.db() as conn:
                        due = conn.execute("SELECT due FROM mg_admin_jobs WHERE id=?", (job["id"],)).fetchone()[0]
                    # Windows can briefly hold the prepared directory during its atomic promotion.
                    with patch.object(platform, "now_ms", return_value=due + 1):
                        self.assertTrue(packages.worker_once())
                    data = self.game_detail()
                    self.assertEqual(data["jobs"][0]["id"], job["id"])
        self.assertEqual((data["versions"][0]["status"], data["game"]["status"]), ("ready", "draft"), data["jobs"])
        self.assertFalse(stage.exists())
        self.assertFalse((version / "static-check.json").exists())
        with self.assertRaises(HTTPException) as denied:
            packages.mutate(self.user, game, "publish", {"expectedRevision": data["game"]["revision"],
                            "versionId": vid}, "after-probe")
        self.assertEqual(denied.exception.detail["code"], "PLAYTEST_REQUIRED")

    def test_broker_outage_shares_backoff_across_uploads_then_resumes_both(self):
        self.ingest()
        packages.receive_upload(self.user, self.game["id"], self.game["revision"], "second", archive(self.files))
        now = platform.now_ms()
        unavailable = HTTPException(503, {"code": "PROBE_UNAVAILABLE", "message": "等待执行器"})
        with (
            patch.dict(os.environ, {"MINIGAMES_PROBE_COMMAND": '["executor"]'}),
            patch.object(validation, "validate_package", wraps=validation.validate_package) as checked,
        ):
            with patch.object(platform, "now_ms", return_value=now), patch.object(
                packages, "probe_package", side_effect=unavailable
            ) as probe:
                self.assertTrue(packages.worker_once())
                self.assertTrue(packages.worker_once())
                self.assertFalse(packages.worker_once())
                probe.assert_called_once()
                self.assertEqual([j["status"] for j in self.game_detail()["jobs"]], ["waiting_probe"] * 2)
            with patch.object(platform, "now_ms", return_value=now + packages.PROBE_BACKOFF_MS + 1), self.fake_probe():
                self.assertTrue(packages.worker_once())
                self.assertTrue(packages.worker_once())
                self.assertFalse(packages.worker_once())
            self.assertEqual(checked.call_count, 2)
        self.assertEqual([j["status"] for j in self.game_detail()["jobs"]], ["succeeded"] * 2)

    def test_restart_after_static_checkpoint_restores_quota_without_unpacking_again(self):
        upload = self.ingest()
        now = platform.now_ms()
        with patch.object(validation, "validate_package", wraps=validation.validate_package) as checked:
            with patch.object(packages, "account_prepared_stage", side_effect=sqlite3.OperationalError("restart")):
                self.assertTrue(packages.worker_once())
            self.assertEqual(self.game_detail()["jobs"][0]["status"], "retry")
            with patch.object(platform, "now_ms", return_value=now + 60_000):
                self.assertTrue(packages.worker_once())
            self.assertEqual(checked.call_count, 1)
        self.assertEqual(self.game_detail()["jobs"][0]["status"], "waiting_probe")
        with self.db() as conn:
            usage = conn.execute("SELECT bytes_used FROM mg_admin_assets WHERE asset_id=?",
                                 (upload["jobId"],)).fetchone()[0]
            self.assertLess(usage, 1024 * 1024)

    def test_site_quota_covers_two_teachers_and_releases_parked_stage(self):
        other = {"id": 10, "role": "teacher", "actor": "teacher:10", "session_id": "fixture-2"}
        with self.db() as conn:
            conn.execute("INSERT INTO teachers(id) VALUES (10)")
            conn.commit()
        second = packages.mutate(other, "", "create", {"title": "第二位教师"}, "new")["game"]
        with patch.dict(os.environ, {"MINIGAMES_STORAGE_BUDGET_MIB": "337"}):
            upload = self.ingest()
            with self.assertRaises(HTTPException) as full:
                packages.receive_upload(other, second["id"], second["revision"], "upload", archive(self.files))
            self.assertEqual(full.exception.detail["code"], "PLATFORM_STORAGE_QUOTA")
            self.assertEqual(upload, self.ingest())
            self.assertTrue(packages.worker_once())
            accepted = packages.receive_upload(other, second["id"], second["revision"], "upload", archive(self.files))
            self.assertNotEqual(upload["jobId"], accepted["jobId"])
            self.assertTrue(packages.worker_once())
            stage = packages.asset_path("stage", self.game["id"], upload["jobId"])
            plan = packages.mutate(self.user, self.game["id"], "plan", {"expectedRevision": self.game["revision"]}, "p")
            packages.mutate(self.user, self.game["id"], "delete", {"expectedRevision": self.game["revision"],
                            "planToken": plan["planToken"], "confirmationText": plan["confirmationText"]}, "d")
            self.assertTrue(packages.worker_once())
            self.assertFalse(stage.exists())
            with self.db() as conn:
                assets = conn.execute("SELECT game_id FROM mg_admin_assets").fetchall()
                self.assertEqual([r["game_id"] for r in assets], [second["id"]])

    def test_asset_probe_time_budget_keeps_candidate_for_retry(self):
        import subprocess
        import sys

        def timed_out(command, **kwargs):
            report = {
                "status": "unavailable",
                "digest": command[-1],
                "resourceError": {
                    "code": "ASSET_PROBE_BUDGET",
                    "file": "static/tone.wav",
                    "field": "offline",
                },
            }
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(report).encode(), stderr=b"")

        self.ingest()
        with (
            patch.dict(os.environ, {"MINIGAMES_PROBE_COMMAND": json.dumps([sys.executable])}),
            patch.object(packages.subprocess, "run", side_effect=timed_out),
        ):
            packages.worker_once()
        data = self.game_detail()
        self.assertEqual(data["versions"][0]["status"], "validating")
        job = data["jobs"][0]
        self.assertEqual(
            (job["status"], job["error"]["code"], job["error"]["file"]),
            ("retry", "ASSET_PROBE_BUDGET", "static/tone.wav"),
        )

    def test_teacher_ai_explicit_fallback_and_bounded_model_fields(self):
        from classroom_app.services import minigame_package_suggestions as suggestions

        self.files["js/lan-game-sdk.v1.js"] = b"do not send package source"
        upload = self.ingest()
        packages.worker_once()
        game = self.game["id"]
        repair = {"expectedRevision": self.game["revision"], "purpose": "repair", "jobId": upload["jobId"]}
        packages.mutate(self.user, game, "suggest", repair, "repair")
        self.assertFalse(packages.worker_once())
        with (
            patch.dict(os.environ, {"MINIGAMES_AI_SUGGESTIONS": "0"}),
            patch.object(suggestions, "model_suggestion") as disabled,
        ):
            asyncio.run(suggestions.process_one())
            disabled.assert_not_called()
        data = self.game_detail()
        repair_job = next(j for j in data["jobs"] if j["kind"] == "suggest")
        self.assertEqual(repair_job["result"]["source"], "rules")
        self.assertIn("SDK", repair_job["result"]["suggestion"])
        meta = {"expectedRevision": self.game["revision"], "purpose": "metadata"}
        res = packages.mutate(self.user, game, "suggest", meta, "metadata")
        response = Mock()
        response.json.return_value = {
            "status": "success",
            "response_json": {"suggestion": "核对人数", "summary": "合成草稿", "tags": ["单人", "单人"]},
        }
        gateway = AsyncMock(return_value=response)
        with (
            patch.dict(os.environ, {"MINIGAMES_AI_SUGGESTIONS": "1"}),
            patch("classroom_app.services.ai_gateway_service.ai_gateway_post", gateway),
        ):
            asyncio.run(suggestions.process_one())
        payload = gateway.await_args.kwargs["json_payload"]
        facts = json.loads(payload["new_message"])
        self.assertEqual(set(facts), {"purpose", "title", "summary", "modeTags", "minPlayers", "maxPlayers"})
        self.assertEqual(payload["file_texts"], [])
        data = self.game_detail()
        out = next(j for j in data["jobs"] if j["id"] == res["jobId"])["result"]
        self.assertEqual((out["source"], out["summary"], out["tags"]), ("model", "合成草稿", ["单人"]))
        self.assertEqual(data["game"]["summary"], "")
        self.assertEqual(data["game"]["status"], "draft")

    def test_idle_seat_and_static_json_floats(self):
        f = dict(self.files)
        f["static/animation.json"] = b'{"duration":0.5}'
        original = json.loads(f["contracts/rules.json"])
        cfg = json.loads(f["config.json"])
        wb = json.loads(f["contracts/writeback.json"])
        for edge in original["transitions"]:
            edge["turn"] = "keep"
        with self.assertRaises(validation.InvalidPackage) as e:
            validation.check_contracts(cfg, wb, original)
        self.assertEqual(e.exception.issue["code"], "E_RULES_IDLE_SEAT")
        runtime = self.root / "with-float"
        runtime.mkdir()
        source = runtime / "upload.zip"
        source.write_bytes(archive(f).getvalue())
        self.assertIn("static/animation.json", validation.validate_package(source, runtime)["files"])

    def test_crash_after_terminal_job_reclaims_stage(self):
        class SimulatedProcessExit(BaseException):
            pass

        upload = self.ingest()
        game, job = self.game["id"], upload["jobId"]
        # Interrupt the real worker after its success transaction and before
        # its file cleanup, preserving the actual ledger and uploaded bytes.
        with self.fake_probe(), patch.object(packages, "remove_asset", side_effect=SimulatedProcessExit):
            with self.assertRaises(SimulatedProcessExit):
                packages.worker_once()
                pending = self.game_detail()["jobs"][0]
                self.assertEqual((pending["status"], pending["error"]["code"]), ("retry", "ASSET_BUSY"))
                with patch.object(packages.platform, "now_ms", return_value=packages.platform.now_ms() + 31_000):
                    packages.worker_once()
        stage = packages.asset_path("stage", game, job)
        self.assertTrue((stage / "upload.zip").is_file())
        self.assertEqual(self.game_detail()["jobs"][0]["status"], "succeeded")
        self.assertTrue(packages.worker_once())
        self.assertFalse(stage.exists())
        self.assertFalse(packages.worker_once())

    def test_save_lineage_survives_switch_and_rollback_checks_real_rank(self):
        game = self.game["id"]
        w = json.loads(self.files["contracts/writeback.json"])
        old = {**w, "milestones": w["milestones"][:1]}
        unrelated = {**old, "series": "other"}
        divergent = {**old, "milestones": [{**old["milestones"][0], "id": "different"}]}
        with self.db() as conn:
            for vid, definition in (
                ("old", old),
                ("extended", w),
                ("other", unrelated),
                ("divergent", divergent),
            ):
                conn.execute(
                    "INSERT INTO mg_versions(id,game_id,status,created_at,writeback_json) VALUES(?,?,'ready',1,?)",
                    (vid, game, json.dumps(definition)),
                )
                if vid != "divergent":
                    conn.execute(
                        "INSERT INTO mg_admin_releases VALUES(?,?,?,?)",
                        (game, vid, definition["series"], definition["saveVersion"]),
                    )
            old_row = packages.one(conn, "mg_versions", "old")
            packages.verify_compatibility(conn, game, "extended", old_row)
            with self.assertRaises(HTTPException) as unsafe_return:
                packages.verify_compatibility(
                    conn, game, "other", packages.one(conn, "mg_versions", "divergent")
                )
            self.assertEqual(unsafe_return.exception.detail["code"], "SAVE_INCOMPATIBLE")
            conn.execute(
                "INSERT INTO mg_rt_progress(game,series,save_ver,actor,rank) VALUES(?,?,?,?,1)",
                (game, w["series"], w["saveVersion"], "student:5"),
            )
            with self.assertRaises(HTTPException) as rollback:
                packages.verify_compatibility(conn, game, "extended", old_row)
            self.assertEqual(rollback.exception.detail["code"], "SAVE_INCOMPATIBLE")

    def test_restore_replays_independent_deletion_and_rejects_partial_register(self):
        from classroom_app.services import minigame_package_recovery as recovery

        upload = self.ingest()
        with self.fake_probe():
            packages.worker_once()
        game, version = self.game["id"], upload["versionId"]
        ledger = self.root / "independent-ledger"
        job = {"id": packages.uid(), "game_id": game, "actor": 9, "created": platform.now_ms()}
        packages.deletion_register(job)
        digest, _ = recovery.register_snapshot(ledger)
        with self.assertRaises(ValueError):
            recovery.reconcile(ledger, "0" * 64, apply=True)
        self.assertEqual(self.game_detail()["game"]["status"], "draft")
        self.assertEqual(recovery.reconcile(ledger, digest)["gamesToErase"], 1)
        self.assertTrue(packages.asset_path("version", game, version).exists())
        recovery.reconcile(ledger, digest, apply=True)
        self.assertFalse(packages.asset_path("version", game, version).exists())
        self.assertEqual(recovery.reconcile(ledger, digest, apply=True)["gamesToErase"], 0)
        self.assertEqual(self.game_detail()["game"]["status"], "deleted")

    def test_rejected_then_correct_same_version(self):
        original = dict(self.files)
        self.files["js/lan-game-sdk.v1.js"] = b"broken"
        self.ingest()
        packages.worker_once()
        data = self.game_detail()
        self.assertEqual(data["jobs"][0]["error"]["code"], "SDK_DIGEST")
        self.assertEqual(data["versions"][0]["status"], "rejected")
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mg_admin_assets").fetchone()[0], 0)
        packages.receive_upload(self.user, self.game["id"], self.game["revision"], "fixed", archive(original))
        with self.fake_probe():
            packages.worker_once()
        self.assertEqual([v["status"] for v in self.game_detail()["versions"]].count("ready"), 1)

    def test_duplicate_path_and_invalid_rules(self):
        bad = dict(self.files)
        bad["JS/game.js"] = b"1"
        for n, case in enumerate(
            (bad, {**self.files, "contracts/rules.json": b'{"engine":"grid-line-v1","size":3,"line":4}'})
        ):
            work = self.root / f"case-{n}"
            work.mkdir()
            path = work / "test.zip"
            path.write_bytes(archive(case).getvalue())
            with self.assertRaises(validation.InvalidPackage):
                validation.validate_package(path, work)

    def pinned_browser_command(self, files, name):
        # The demo is built in-memory above. No externally supplied ZIP receives this local exception.
        import sys
        import shutil

        bins = [
            os.environ.get("MINIGAMES_TEST_BROWSER", ""),
            *map(
                str,
                sorted(
                    (Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright").glob(
                        "chromium-*/chrome-win64/chrome.exe"
                    ),
                    reverse=True,
                ),
            ),
            str(Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe"),
            shutil.which("chromium") or "",
            shutil.which("google-chrome") or "",
        ]
        binary = next((p for p in bins if p and Path(p).is_file()), None)
        if not binary:
            self.skipTest("No locally installed sandboxed Chromium for this fixture")
        run = self.root / name
        run.mkdir()
        z = run / "fixture.zip"
        z.write_bytes(archive(files).getvalue())
        meta = validation.validate_package(z, run)
        argv = [
            sys.executable,
            "-I",
            str(validation.BASE / "tools/minigame_browser_probe.py"),
            "--browser",
            binary,
            "--synthetic-digest",
            meta["digest"],
        ]
        if os.name == "nt":
            argv.append("--trusted-browser-harness")
        return patch.dict(os.environ, {"MINIGAMES_PROBE_COMMAND": json.dumps(argv)})

    def test_actual_browser_smoke_for_exact_reviewed_fixture(self):
        with self.pinned_browser_command(self.files, "trusted"):
            self.ingest()
            packages.worker_once()
            data = self.game_detail()
            job = data["jobs"][0]
            if job["status"] == "retry" and job["error"].get("code") == "ASSET_BUSY":
                # Real Windows browser handles can briefly delay the atomic directory rename.
                # Advance the scheduler to its recorded retry deadline and exercise recovery.
                with self.db() as conn:
                    due = conn.execute("SELECT due FROM mg_admin_jobs WHERE id=?", (job["id"],)).fetchone()[0]
                with patch.object(platform, "now_ms", return_value=due + 1):
                    packages.worker_once()
                data = self.game_detail()
                self.assertEqual(data["jobs"][0]["id"], job["id"])
        self.assertEqual(data["versions"][0]["status"], "ready", str(data["jobs"][0]))
        self.assertTrue(data["jobs"][0]["result"]["probe"]["offline"])
        self.assertEqual(
            data["jobs"][0]["result"]["probe"]["resourceCounts"], {"total": 5, "platform": 5, "offline": 5}
        )

    def test_browser_reports_named_undecodable_assets(self):
        cases = (
            ("static/square.woff2", b"wOF2x", "FONT_DECODE"),
            ("static/tone.wav", b"RIFF" + bytes(4) + b"WAVEbad", "AUDIO_DECODE"),
            ("static/frames.webm", bytes.fromhex("1A45DFA3") + "bad".encode(), "VIDEO_DECODE"),
        )
        for n, (name, broken, code) in enumerate(cases):
            with self.subTest(name=name):
                files = {**self.files, name: broken}
                with self.pinned_browser_command(files, "negative-" + str(n)):
                    result = packages.receive_upload(
                        self.user, self.game["id"], self.game["revision"], "bad-" + str(n), archive(files)
                    )
                    packages.worker_once()
                data = self.game_detail()
                job = next(j for j in data["jobs"] if j["id"] == result["jobId"])
                self.assertEqual(job["status"], "rejected", job)
                self.assertEqual(
                    (job["error"]["file"], job["error"]["field"], job["error"]["code"]),
                    (name, "platform", code),
                )
