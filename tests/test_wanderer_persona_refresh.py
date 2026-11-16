"""Persona migration conflicts and recovery using owned SQLite files only."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from classroom_app.services import wanderer_persona_seeds as seeds
from tools import wanderer_persona_refresh as cli


class PersonaRefreshTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(TemporaryDirectory(prefix="persona-refresh-test-")))
        self.db_path = self.folder / "personas.db"
        with self.connect() as conn:
            conn.execute("CREATE TABLE wanderer_agents (id INTEGER PRIMARY KEY, slug TEXT, student_id INTEGER, "
                         "created_at TEXT, origin TEXT, persona_prompt TEXT, persona_version INTEGER, "
                         "persona_source_hash TEXT, persona_source_json TEXT, persona_updated_at TEXT, updated_at TEXT)")
            conn.execute("INSERT INTO wanderer_agents VALUES "
                         "(1,'lele',101,'created','seed','legacy-persona',1,'legacy-hash','{\"legacy\":true}',NULL,'before')")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def snapshot(self):
        with self.connect() as conn:
            return dict(conn.execute("SELECT * FROM wanderer_agents WHERE id=1").fetchone())

    def plan(self):
        with self.connect() as conn:
            return seeds.plan_seed_persona_refresh(conn)

    def cli_context(self):
        self.enterContext(patch("classroom_app.database.get_db_connection", self.connect))
        self.enterContext(patch("classroom_app.db.connection.get_configured_db_engine", return_value="sqlite"))
        self.printed = self.enterContext(patch.object(cli, "_print"))

    def test_refresh_and_restore_preserve_complete_metadata_and_are_idempotent(self):
        old = self.snapshot()
        plan = self.plan()
        with patch("classroom_app.services.wanderer_identity_service.invalidate_wanderer_cache") as invalidate:
            with self.connect() as conn:
                self.assertEqual(seeds.apply_seed_persona_refresh(conn, plan), ["lele"])
            with self.connect() as conn:
                preview = seeds.restore_seed_persona_refresh(conn, plan)
                self.assertEqual(preview["restored"], ["lele"])
            self.assertNotEqual(self.snapshot(), old)
            with self.connect() as conn:
                self.assertEqual(seeds.restore_seed_persona_refresh(conn, plan, apply=True)["restored"], ["lele"])
            self.assertEqual(invalidate.call_count, 2)
        self.assertEqual(self.snapshot(), old)
        with self.connect() as conn:
            self.assertEqual(seeds.restore_seed_persona_refresh(conn, plan, apply=True)["already_restored"], ["lele"])

    def test_refresh_atomic_predicate_keeps_edit_arriving_at_the_update(self):
        plan = self.plan()
        with self.connect() as conn:
            class Interleaved:
                def execute(inner, sql, params=()):
                    if sql.startswith("UPDATE wanderer_agents SET persona_prompt"):
                        conn.execute("UPDATE wanderer_agents SET persona_prompt='admin-now',persona_updated_at='edited'")
                    return conn.execute(sql, params)
            self.assertEqual(seeds.apply_seed_persona_refresh(Interleaved(), plan), [])
        self.assertEqual(self.snapshot()["persona_prompt"], "admin-now")

    def test_refresh_detects_unmarked_prompt_or_metadata_change(self):
        for assignment in ("persona_prompt='other-refresh'", "persona_source_json='{}'", "origin='dao'", "student_id=999"):
            with self.subTest(assignment=assignment):
                plan = self.plan()
                with self.connect() as conn:
                    old = dict(conn.execute("SELECT * FROM wanderer_agents").fetchone())
                    conn.execute("UPDATE wanderer_agents SET " + assignment)
                    self.assertEqual(seeds.apply_seed_persona_refresh(conn, plan), [])
                    columns = list(old)
                    conn.execute("UPDATE wanderer_agents SET " + ",".join(f"{key}=?" for key in columns), tuple(old.values()))

    def test_restore_never_overwrites_a_later_admin_edit(self):
        plan = self.plan()
        with self.connect() as conn:
            seeds.apply_seed_persona_refresh(conn, plan)
            conn.execute("UPDATE wanderer_agents SET persona_prompt='admin-now',persona_updated_at='edited'")
            report = seeds.restore_seed_persona_refresh(conn, plan, apply=True)
        self.assertEqual(report["conflicts"], ["lele"])
        self.assertEqual(self.snapshot()["persona_prompt"], "admin-now")

    def test_restore_atomic_predicate_keeps_edit_after_read(self):
        plan = self.plan()
        with self.connect() as conn:
            seeds.apply_seed_persona_refresh(conn, plan)
            class Interleaved:
                def execute(inner, sql, params=()):
                    if sql.startswith("UPDATE wanderer_agents SET persona_prompt"):
                        conn.execute("UPDATE wanderer_agents SET persona_prompt='admin-now',persona_updated_at='edited'")
                    return conn.execute(sql, params)
            self.assertEqual(seeds.restore_seed_persona_refresh(Interleaved(), plan, apply=True)["conflicts"], ["lele"])
        self.assertEqual(self.snapshot()["persona_prompt"], "admin-now")

    def test_auto_upgrade_keeps_hash_whitelist_and_atomic_edit_protection(self):
        with self.connect() as conn:
            self.assertEqual(seeds.upgrade_seed_personas(conn), [])
            conn.execute("UPDATE wanderer_agents SET persona_prompt=?", (seeds.render_persona_prompt_v1(seeds.SEED_BY_SLUG["lele"]),))
            class Interleaved:
                def execute(inner, sql, params=()):
                    if sql.startswith("UPDATE wanderer_agents SET persona_prompt"):
                        conn.execute("UPDATE wanderer_agents SET persona_prompt='admin-now',persona_updated_at='edited'")
                    return conn.execute(sql, params)
            self.assertEqual(seeds.upgrade_seed_personas(Interleaved()), [])
        self.assertEqual(self.snapshot()["persona_prompt"], "admin-now")

    def test_cli_dry_run_writes_neither_rows_nor_backup(self):
        self.cli_context()
        old = self.snapshot()
        backup_dir = self.folder / "backups"
        self.assertEqual(cli._run_refresh(False, backup_dir), 0)
        self.assertEqual(self.snapshot(), old)
        self.assertFalse(backup_dir.exists())

    def test_cli_backup_roundtrip_and_target_rejection(self):
        self.cli_context()
        old = self.snapshot()
        self.assertEqual(cli._run_refresh(True, self.folder / "backups"), 0)
        backup = next((self.folder / "backups").glob("*.json"))
        document = json.loads(backup.read_text(encoding="utf-8"))
        self.assertEqual(document["items"][0]["old_state"], old)
        target = document["target"]
        document["target"] = {**target, "fingerprint": "another-target"}
        backup.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "different database target"):
            cli._run_restore(backup, True)
        self.assertNotEqual(self.snapshot(), old)
        document["target"] = target
        backup.write_text(json.dumps(document), encoding="utf-8")
        self.assertEqual(cli._run_restore(backup, False), 0)
        self.assertNotEqual(self.snapshot(), old)
        self.assertEqual(cli._run_restore(backup, True), 0)
        self.assertEqual(self.snapshot(), old)

    def test_cli_returns_conflict_without_overwriting_later_edits(self):
        self.cli_context()
        self.assertEqual(cli._run_refresh(True, self.folder / "backups"), 0)
        backup = next((self.folder / "backups").glob("*.json"))
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_agents SET persona_prompt='manual',persona_updated_at='edited'")
        self.assertEqual(cli._run_restore(backup, True), 2)
        self.assertEqual(self.printed.call_args.args[0]["conflicts"], ["lele"])
        self.assertEqual(self.snapshot()["persona_prompt"], "manual")

    def test_unsafe_legacy_backup_is_rejected_before_connecting(self):
        backup = self.folder / "legacy.json"
        backup.write_text(json.dumps({"contract": "wanderer-persona-refresh-backup-v1", "items": []}))
        with patch("classroom_app.database.get_db_connection") as connect:
            with self.assertRaisesRegex(ValueError, "legacy text-only"):
                cli._run_restore(backup, True)
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
