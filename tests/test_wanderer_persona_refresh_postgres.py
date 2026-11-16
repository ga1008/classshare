"""Real row-lock races on an explicitly owned, disposable PostgreSQL cluster."""
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import unittest

from classroom_app.services import wanderer_persona_seeds as seeds
from tests import test_wanderer_inbox_postgres as fixture
from tools.wanderer_persona_refresh import _database_identity


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"), "Requires owned offline PostgreSQL")
class PersonaRefreshPostgresTest(unittest.TestCase):
    setUpClass = classmethod(fixture.WandererInboxPostgresTest.setUpClass.__func__)
    tearDownClass = classmethod(fixture.WandererInboxPostgresTest.tearDownClass.__func__)
    connect = fixture.WandererInboxPostgresTest.connect

    def setUp(self):
        fixture.WandererInboxPostgresTest.setUp(self)
        with self.connect() as conn:
            conn.execute("INSERT INTO wanderer_agents(id,student_id,slug,display_name,archetype,persona_prompt,created_at,updated_at) "
                         "VALUES(1,101,'lele','合成人设','synthetic','legacy-persona','created','before')")
            conn.commit()

    def plan(self):
        with self.connect() as conn:
            return seeds.plan_seed_persona_refresh(conn)

    def race_admin(self, plan, *, restoring=False):
        started = threading.Event()
        with self.connect() as admin:
            admin.execute("UPDATE wanderer_agents SET persona_prompt='admin-current',persona_updated_at='edited' WHERE id=1")
            def worker():
                with self.connect() as conn:
                    conn.execute("SET LOCAL statement_timeout='5s'")
                    original = conn.execute
                    def observed(sql, params=()):
                        if sql.startswith("UPDATE wanderer_agents SET persona_prompt"):
                            started.set()
                        return original(sql, params)
                    conn.execute = observed
                    result = (seeds.restore_seed_persona_refresh(conn, plan, apply=True) if restoring
                              else seeds.apply_seed_persona_refresh(conn, plan))
                    conn.commit()
                    return result
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(worker)
                try:
                    self.assertTrue(started.wait(3), "worker did not reach the competing UPDATE")
                finally:
                    admin.commit()  # Release the row lock even if the assertion fails.
                result = pending.result(timeout=7)
        with self.connect() as conn:
            row = conn.execute("SELECT persona_prompt,persona_updated_at FROM wanderer_agents WHERE id=1").fetchone()
            self.assertEqual(row["persona_prompt"], "admin-current")
            self.assertEqual(row["persona_updated_at"], "edited")
        return result

    def test_refresh_rechecks_snapshot_after_waiting_for_an_admin_row_lock(self):
        self.assertEqual(self.race_admin(self.plan()), [])

    def test_restore_rechecks_snapshot_after_waiting_for_an_admin_row_lock(self):
        plan = self.plan()
        with self.connect() as conn:
            self.assertEqual(seeds.apply_seed_persona_refresh(conn, plan), ["lele"])
            conn.commit()
        self.assertEqual(self.race_admin(plan, restoring=True)["conflicts"], ["lele"])

    def test_full_metadata_roundtrip_and_connected_target_fingerprint(self):
        plan = self.plan()
        with self.connect() as conn:
            target = _database_identity(conn, "postgres")
            self.assertEqual(target["engine"], "postgres")
            self.assertEqual(len(target["fingerprint"]), 64)
            self.assertEqual(seeds.apply_seed_persona_refresh(conn, plan), ["lele"])
            self.assertEqual(seeds.restore_seed_persona_refresh(conn, plan, apply=True)["restored"], ["lele"])
            row = conn.execute("SELECT " + ",".join(seeds._PERSONA_REFRESH_FIELDS) + " FROM wanderer_agents WHERE id=1").fetchone()
            self.assertEqual(dict(row), plan[0]["old_state"])
            conn.commit()
