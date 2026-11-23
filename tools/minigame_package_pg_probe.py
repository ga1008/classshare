"""Rehearse catalog, package lifecycle and grants against an owned, empty native PostgreSQL."""

import io
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import uuid
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.native_postgres_temp import OwnedPostgresRuntime


def seed(conn):
    for table in (
        "teachers(id integer primary key,name text,is_active integer,is_super_admin integer,school_code text,school_name text,college text,department text)",
        "students(id integer primary key,name text,class_id integer,school_code text,school_name text,college text,department text,enrollment_status text,control_mode text)",
        "classes(id integer primary key,name text,created_by_teacher_id integer)",
        "class_offerings(id integer primary key,class_id integer,course_id integer,teacher_id integer)",
        "class_offering_class_links(offering_id integer,class_id integer)",
        "courses(id integer primary key,name text)",
        "learning_certificates(id integer,student_id integer,class_offering_id integer,tier integer)",
        "user_sessions(session_user_key text primary key,session_id text,user_id text,role text,name text,ip text,last_login text,expires_at text,updated_at text)",
        "social_blocks(owner_identity text,blocked_identity text)",
        "private_message_blocks(owner_identity text,blocked_identity text)",
        "social_friendships(id integer,identity_a text,identity_b text,status text)",
    ):
        conn.execute("CREATE TABLE " + table)
    conn.execute("INSERT INTO teachers VALUES(9,'Teacher',1,0,'S','School','C','D')")
    conn.execute("INSERT INTO students VALUES(2,'Student',20,'S','School','C','D','active','human')")
    conn.execute("INSERT INTO students VALUES(3,'Visitor',99,'X','Other','Y','Z','active','human')")
    conn.execute("INSERT INTO classes VALUES(19,'Primary class',9),(20,'Linked class',9)")
    conn.execute("INSERT INTO courses VALUES(33,'Course')")
    conn.execute("INSERT INTO class_offerings VALUES(8,19,33,9)")
    conn.execute("INSERT INTO class_offering_class_links VALUES(8,20)")
    conn.execute("INSERT INTO learning_certificates VALUES(1,2,8,3)")
    users = []
    for role, uid in (("teacher", 9), ("student", 2), ("student", 3)):
        actor = f"{role}:{uid}"
        sid = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO user_sessions VALUES(?,?,?,?,?,'127.0.0.1','','2099-01-01T00:00:00+00:00','')",
            (actor, sid, str(uid), role, actor),
        )
        users.append(
            {"role": role, "id": uid, "actor": actor, "session_id": sid, "name": actor, "display_name": actor}
        )
    conn.commit()
    return users


def native_tool(owned, name, *args):
    """Pin every native DB operation to this probe's loopback, non-default PG instance."""
    executable = owned.pg_ctl.with_name(name + owned.pg_ctl.suffix)
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PG")}
    subprocess.run(
        [str(executable), "-h", "127.0.0.1", "-p", str(owned.port), "-U", "rehearsal_admin", *map(str, args)],
        check=True,
        timeout=45,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )


def package_fingerprints(root):
    """Include original ZIP, thumbnail, validation report, owner marker and all runtime assets."""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def backup_counts(dsn, game_id):
    import psycopg

    with psycopg.connect(dsn, connect_timeout=3) as conn:
        row = conn.execute(
            "SELECT status,current_version_id FROM mg_games WHERE id=%s", (game_id,)
        ).fetchone()
        assert row
        counts = {"status": row[0], "version": row[1]}
        for table, column in (
            ("mg_versions", "game_id"),
            ("mg_launch_leases", "game_id"),
            ("mg_admin_assets", "game_id"),
            ("mg_rt_progress", "game"),
            ("mg_rt_activity", "game"),
        ):
            counts[table] = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column}=%s",
                (game_id,),
            ).fetchone()[0]
        counts["indexedFiles"] = conn.execute(
            "SELECT COUNT(*) FROM mg_version_files f JOIN mg_versions v ON v.id=f.version_id WHERE v.game_id=%s",
            (game_id,),
        ).fetchone()[0]
        return counts


def make_backup(owned, game_id, survivor_id):
    from classroom_app.services import minigame_platform as platform

    snapshot = owned.runtime / "frozen-pre-deletion-backup"
    snapshot.mkdir()
    source = os.environ["DATABASE_URL"]
    target_counts = backup_counts(source, game_id)
    keep_counts = backup_counts(source, survivor_id)
    assert target_counts["status"] == keep_counts["status"] == "listed"
    assert target_counts["mg_versions"] == 2 and keep_counts["mg_versions"] == 1
    assert target_counts["mg_rt_progress"] and target_counts["mg_rt_activity"]
    assert target_counts["mg_launch_leases"] and keep_counts["mg_launch_leases"]
    native_tool(owned, "pg_dump", "--format=custom", "--file", snapshot / "games.dump", "postgres")
    shutil.copytree(platform.game_root(), snapshot / "games")
    target_files = package_fingerprints(snapshot / "games/versions" / game_id)
    keep_files = package_fingerprints(snapshot / "games/versions" / survivor_id)
    assert target_files and keep_files
    assert any(k.endswith("/upload.zip") for k in keep_files)
    assert any(k.endswith("/runtime/main.html") for k in keep_files)
    common_assets = set(v for k, v in target_files.items() if "/runtime/" in k) & set(
        v for k, v in keep_files.items() if "/runtime/" in k
    )
    assert common_assets, "Two independently stored games must share at least one fixture asset"
    return {
        "root": snapshot,
        "targetCounts": target_counts,
        "keepCounts": keep_counts,
        "targetFiles": target_files,
        "keepFiles": keep_files,
        "sharedAssets": len(common_assets),
    }


def restore_and_replay(owned, backup, game_id, survivor_id):
    """Run the shipped offline CLI against an actual pg_restore and separately restored volume."""
    import psycopg

    register = Path(os.environ["MINIGAMES_DELETION_REGISTER"])
    restore = owned.runtime / "offline-restore"
    source = backup["root"]
    name = "mg_restore_copy"
    dsn = f"postgresql://rehearsal_admin@127.0.0.1:{owned.port}/{name}"
    env = {**os.environ, "MINIGAMES_RESTORE_DATABASE_URL": dsn, "PYTHONIOENCODING": "utf-8"}

    def cli(*args, expect_success=True):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/replay_minigame_deletions.py"), *map(str, args)],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            timeout=25,
        )
        if expect_success:
            if result.returncode:
                raise RuntimeError(result.stderr)
            return json.loads(result.stdout.strip().splitlines()[-1])
        assert result.returncode != 0, "An incorrect register seal must stop replay"
        return result.stderr

    sealed = cli("seal", "--register", register)
    assert sealed["count"] == 1 and len(sealed["digest"]) == 64
    # Keep the trusted seal outside both the database dump and restored volume.
    seal = register.parent / "external-seal.json"
    with seal.open("x", encoding="utf-8") as out:
        json.dump(sealed, out)
    digest = json.loads(seal.read_text(encoding="utf-8"))["digest"]
    native_tool(owned, "createdb", name)
    native_tool(
        owned,
        "pg_restore",
        "--single-transaction",
        "--no-owner",
        "--no-privileges",
        "--dbname",
        name,
        source / "games.dump",
    )
    restore.mkdir()
    shutil.copytree(source / "games", restore / "games")
    before = backup_counts(dsn, game_id)
    assert before == backup["targetCounts"] and backup_counts(dsn, survivor_id) == backup["keepCounts"]
    target_path = restore / "games/versions" / game_id
    keep_path = restore / "games/versions" / survivor_id
    assert package_fingerprints(target_path) == backup["targetFiles"]
    assert package_fingerprints(keep_path) == backup["keepFiles"]
    options = ["reconcile", "--register", register, "--restore-data-root", restore, "--postgres-copy", name]
    rejected = cli(
        *options, "--expected-digest", "0" * 64, "--apply", "--confirm-offline", expect_success=False
    )
    assert "摘要" in rejected
    assert (
        backup_counts(dsn, game_id) == before and package_fingerprints(target_path) == backup["targetFiles"]
    )
    first = cli(*options, "--expected-digest", digest)
    assert first["gamesToErase"] == 1 and first["verifiedSurvivingVersions"] == 1
    assert first["previousLeases"] == before["mg_launch_leases"] + backup["keepCounts"]["mg_launch_leases"]
    assert (
        backup_counts(dsn, game_id) == before and package_fingerprints(target_path) == backup["targetFiles"]
    )
    applied = cli(*options, "--expected-digest", digest, "--apply", "--confirm-offline")
    assert applied["mode"] == "applied" and applied["gamesToErase"] == 1
    after = backup_counts(dsn, game_id)
    assert after["status"] == "deleted" and after["version"] is None
    assert all(value == 0 for key, value in after.items() if key not in {"status", "version"})
    assert not target_path.exists()
    assert backup_counts(dsn, survivor_id) == {**backup["keepCounts"], "mg_launch_leases": 0}
    assert package_fingerprints(keep_path) == backup["keepFiles"]
    with psycopg.connect(dsn, connect_timeout=3) as conn:
        assert conn.execute("SELECT COUNT(*) FROM mg_launch_leases").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM mg_admin_preview_receipts").fetchone()[0] == 0
        assert (
            conn.execute("SELECT COUNT(*) FROM mg_version_files").fetchone()[0]
            == backup["keepCounts"]["indexedFiles"]
        )
    final = cli(*options, "--expected-digest", digest)
    assert (
        final["gamesToErase"] == 0
        and final["verifiedSurvivingVersions"] == 1
        and final["previousLeases"] == 0
    )
    return {
        "backupFormat": "pg_dump custom",
        "restore": "pg_restore --single-transaction",
        "dumpBytes": (source / "games.dump").stat().st_size,
        "retainedVersionsVerified": 1,
        "retainedFilesCompared": len(backup["keepFiles"]),
        "sharedContentDigests": backup["sharedAssets"],
        "wrongExternalSealRejected": True,
        "beforeReplay": before,
        "afterReplay": after,
        "targetFilesRemoved": len(backup["targetFiles"]),
        "globalRestoredLeases": 0,
        "globalRestoredPreviewReceipts": 0,
        "dryRunBefore": first,
        "apply": applied,
        "dryRunAfter": final,
    }


def exercise(owned):
    from fastapi.testclient import TestClient
    from classroom_app.services import (
        minigame_packages as pk,
        minigame_catalog as catalog,
        minigame_launch as launch,
    )
    from classroom_app.services import minigame_platform as p, minigame_runtime as runtime
    from classroom_app.minigame_content import app as content
    from tools.db import migrate_minigames
    from classroom_app.services.minigame_package_validation import canonical

    assert not p.schema_installed()
    with patch.object(sys, "argv", ["migrate_minigames", "--apply"]):
        assert migrate_minigames.main() == 0 and migrate_minigames.main() == 0
    with p.game_db() as conn:
        version = conn.execute("SHOW server_version").fetchone()[0]
        assert version.startswith("16.")
        teacher, player, outsider = seed(conn)
    new_key = lambda: uuid.uuid4().hex

    def action(op, game, body, vid=""):
        return pk.mutate(teacher, game, op, body, new_key(), vid)

    game = action("create", "", {"scope": "public", "tags": ["海图"]})["game"]
    gid = game["id"]
    source = ROOT / "docs/minigames/downloads/beacon-relay.zip"
    with TestClient(content, base_url="http://localhost") as assets:

        def publish(game_id, stream, revision):
            upload = pk.receive_upload(teacher, game_id, revision, new_key(), stream)
            # This probe verifies native database integration. Browser processes have separate pinned-fixture tests.
            with patch.object(
                pk,
                "probe_package",
                side_effect=lambda _, digest: {"digest": digest, "platform": True, "offline": True},
            ):
                assert pk.worker_once()
            with p.game_db() as conn:
                detail = pk.detail(conn, teacher, game_id)
            assert detail["versions"][0]["status"] == "ready", detail["jobs"][0]
            rev = detail["game"]["revision"]
            vid = upload["versionId"]
            preview = launch.launch(
                teacher,
                game_id,
                {"purpose": "preview", "versionId": vid, "hostInstance": "probe-preview"},
                new_key(),
            )
            assert assets.get(preview["contentEntryUrl"]).status_code == 200
            action("playtest", game_id, {"expectedRevision": rev, "passed": True}, vid)
            return action("publish", game_id, {"versionId": vid, "expectedRevision": rev})["game"]

        with source.open("rb") as source_file:
            game = publish(gid, source_file, game["revision"])
        for scope in ("class", "classroom", "school", "college", "department", "private", "public"):
            body = {
                "scope": scope,
                "expectedRevision": game["revision"],
                "targets": [20] if scope == "class" else [8] if scope == "classroom" else [],
            }
            game = action("patch", gid, body)["game"]
            with p.game_db() as conn:
                assert len(catalog.page(conn, player, q="海图", mode="coop", tag="海图")["items"]) == int(
                    scope != "private"
                ), scope
                assert len(catalog.page(conn, outsider, q="航标", mode="solo")["items"]) == int(
                    scope == "public"
                ), scope
                assert len(catalog.page(conn, teacher, q="航标")["items"]) == 1, scope
        with p.game_db() as conn:
            assert catalog.page(conn, player, q="%", mode="solo", tag="海图")["items"] == []
            assert catalog.page(conn, player, mode="versus")["items"] == []
            assert catalog.page(conn, player, tag="海")["items"] == []
        first = game["currentVersionId"]
        run = runtime.write(player, "solo", "game", gid, {"hostInstance": "kept-solo"}, new_key())
        grant = launch.launch(
            player, gid, {"purpose": "play", "versionId": first, "hostInstance": "kept-solo"}, new_key()
        )
        assert assets.get(grant["contentEntryUrl"]).status_code == 200
        with ZipFile(source) as z:
            files = {n: z.read(n) for n in z.namelist() if not n.endswith("/")}
        config = json.loads(files["config.json"])
        config["version"] = "1.0.1"
        files["config.json"] = canonical(config).encode()
        d = {
            "config": config,
            "writeback": json.loads(files["contracts/writeback.json"]),
            "rules": json.loads(files["contracts/rules.json"]),
        }
        files["js/offline-contracts.js"] = ("window.LanGameOfflineContracts = " + canonical(d) + ";").encode()
        with io.BytesIO() as data:
            with ZipFile(data, "w", ZIP_DEFLATED) as bundle:
                for n, b in files.items():
                    bundle.writestr(n, b)
            data.seek(0)
            updated = publish(gid, data, game["revision"])
        assert updated["currentVersionId"] != first and updated["policyRevision"] == game["policyRevision"]
        assert launch.change_grant(player, grant["leaseId"], "kept-solo", False)["versionId"] == first
        assert assets.get(grant["contentEntryUrl"]).status_code == 200
        moved = runtime.write(
            player,
            "intent",
            "run",
            run["runId"],
            {
                "hostInstance": "kept-solo",
                "controlEpoch": run["controlEpoch"],
                "expectedRevision": run["revision"],
                "intent": {"type": "light", "payload": {"bearing": "east"}},
            },
            new_key(),
        )
        assert moved["versionId"] == first and moved["state"]["rank"] == 1
        with p.game_db() as conn:
            for i in range(3):
                sibling = pk.uid()
                vid = pk.uid()
                conn.execute(
                    """INSERT INTO mg_games(id,owner_teacher_id,title,scope,status,current_version_id,created_at,updated_at)
                    VALUES(?,9,'Sibling','public','listed',?,1,1)""",
                    (sibling, vid),
                )
                conn.execute(
                    "INSERT INTO mg_versions(id,game_id,status,created_at,config_json) VALUES(?,?,'ready',1,?)",
                    (vid, sibling, canonical(config)),
                )
            a = catalog.page(conn, player, limit=2)
            b = catalog.page(conn, player, cursor=a["nextCursor"], limit=2)
            assert len({g["id"] for g in a["items"] + b["items"]}) == 4 and not b["nextCursor"]
            assert catalog.page(conn, player, tag="海图", cursor=a["nextCursor"])["cursorReset"]
            # These three stand-in versions only exercise the cursor inside this transaction.
            conn.rollback()
        survivor = action("create", "", {"scope": "public"})["game"]
        survivor_id = survivor["id"]
        with (source.parent / "three-in-line.zip").open("rb") as stream:
            survivor = publish(survivor_id, stream, survivor["revision"])
        # Snapshot the live, listed target, including its active old-version lease and saved rank.
        # No worker or other game writer runs while the two backup components are captured.
        backup = make_backup(owned, gid, survivor_id)
        unlisted = action("unlist", gid, {"expectedRevision": updated["revision"]})["game"]
        assert assets.get(grant["contentEntryUrl"]).status_code == 404
        plan = action("plan", gid, {"expectedRevision": unlisted["revision"]})
        assert plan["impact"]["saves"] == 1
        ticket = action(
            "delete",
            gid,
            {
                "expectedRevision": unlisted["revision"],
                "planToken": plan["planToken"],
                "confirmationText": unlisted["slug"],
            },
        )
        assert pk.worker_once()
    with p.game_db() as conn:
        assert pk.one(conn, "mg_admin_jobs", ticket["jobId"])["status"] == "succeeded"
        for table in (
            "mg_versions",
            "mg_launch_leases",
            "mg_admin_assets",
            "mg_admin_preview_receipts",
            "mg_admin_playtests",
        ):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE game_id=?", (gid,)).fetchone()[0] == 0, (
                table
            )
        for table in ("mg_rt_activity", "mg_rt_progress"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE game=?", (gid,)).fetchone()[0] == 0, (
                table
            )
    assert not (p.game_root() / "versions" / gid).exists()
    recovery = restore_and_replay(owned, backup, gid, survivor_id)
    return {
        "postgres": version,
        "schema": "explicit migration twice",
        "scopeChecks": 21,
        "filterChecks": 3,
        "cursorChecks": 2,
        "versionsPublished": 2,
        "htmlPreviewReceipt": True,
        "oldGrantRenewal": True,
        "oldRunPersistedRank": 1,
        "unlistedOldContentStatus": 404,
        "deletedFilesAndLeases": True,
        "backupRestore": recovery,
        "browser": "stub only for this PG probe",
    }


def main():
    artifact_root = ROOT / ".codex-temp/evidence/minigame-package-pg16"
    with OwnedPostgresRuntime(artifact_root, prefix="catalog-") as owned:
        print("Evidence", owned.target, flush=True)
        owned.start()
        env = {
            "DB_ENGINE": "postgres",
            "DATABASE_URL": f"postgresql://rehearsal_admin@127.0.0.1:{owned.port}/postgres",
            "POSTGRES_BACKEND_READY": "true",
            "POSTGRES_POOL_ENABLED": "false",
            "LANSHARE_DATA_ROOT": str(owned.runtime / "source-data"),
            "MINIGAMES_DELETION_REGISTER": str(owned.runtime / "independent-deletion-ledger"),
            "MINIGAMES_APP_ORIGIN": "http://127.0.0.1",
            "MINIGAMES_CONTENT_ORIGIN": "http://localhost",
            "SECRET_KEY": secrets.token_hex(32),
        }
        with patch.dict(os.environ, env), patch("dotenv.load_dotenv", return_value=False):
            result = exercise(owned)
            owned.write_evidence_json("result.json", result)
            print(json.dumps(result))


if __name__ == "__main__":
    main()
