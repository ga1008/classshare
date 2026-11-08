"""Opt-in weekly capture/ordinary material races in a verified offline PG DB."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime
import json
import os
from pathlib import Path
from threading import Event
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from classroom_app import config
from classroom_app.db import schema, schema_gongwen, schema_scheduler
from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.db.postgres_indexes import _build_in_memory_sqlite_schema
from classroom_app.routers import learning
from classroom_app.services import learning_progress_service as progress, scheduled_task_handlers as handlers
from tools.assessment_postgres_rehearsal import connect_offline
from tools import db_postgres_export as exporter


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"),
                     "Requires an explicitly owned offline PostgreSQL cluster")
class WeeklySnapshotPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"])
        cls.port = int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"])
        cls.database = "lanshare_assessment_rehearsal_weekly_" + uuid4().hex[:12]
        cls.admin = connect_offline(cluster_dir=cls.cluster, port=cls.port, database="lanshare_assessment_rehearsal")
        cls.admin.autocommit = True
        cls.admin.execute(f'CREATE DATABASE "{cls.database}" TEMPLATE template0')
        cls.guards = ExitStack()
        cls.guards.enter_context(patch.multiple(config, DB_ENGINE="postgres", POSTGRES_BACKEND_READY=True,
            POSTGRES_POOL_ENABLED=False, DATABASE_URL=f"postgresql://rehearsal_admin@127.0.0.1:{cls.port}/{cls.database}"))
        try:
            # PostgreSQL startup extends the established canonical foundation;
            # build that empty foundation from the same in-memory schema used
            # by the real index harvester, never from an application data file.
            source = _build_in_memory_sqlite_schema()
            try:
                with cls._foundation_connection() as conn:
                    tables = exporter._table_names(source)
                    for table in tables:
                        conn.execute(exporter._create_table_sql(table, exporter._columns(source, table),
                            type_overrides=exporter._foreign_key_type_overrides(source, table)))
                    for table in tables:
                        for statement in exporter._unique_constraints(source, table):
                            conn.execute(statement)
                    for table in tables:
                        for statement in exporter._foreign_key_constraints(source, table):
                            conn.execute(statement)
            finally:
                source.close()
            with LanSharePostgresConnection(cls._foundation_connection()) as conn:
                with patch.object(schema_gongwen, "_SCHEMA_READY", False), \
                        patch.object(schema_scheduler, "_SCHEMA_READY", False):
                    schema_gongwen.ensure_gongwen_schema(conn)
                    schema_scheduler.ensure_scheduler_schema(conn)
            schema.init_database()
        except BaseException:
            cls.guards.close()
            cls.admin.execute(f'DROP DATABASE "{cls.database}"')
            cls.admin.close()
            raise
        cls.fixture_number = 0

    @classmethod
    def _foundation_connection(cls):
        return connect_offline(cluster_dir=cls.cluster, port=cls.port, database=cls.database)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.guards.close()
            cls.admin.execute(f'DROP DATABASE "{cls.database}"')
        finally:
            cls.admin.close()

    def connect(self, connection_class=LanSharePostgresConnection):
        raw = connect_offline(cluster_dir=self.cluster, port=self.port, database=self.database)
        raw.row_factory = sqlite_compatible_dict_row
        raw.execute("SET lock_timeout='2s'")
        raw.execute("SET statement_timeout='10s'")
        raw.commit()
        return connection_class(raw)

    def setUp(self):
        type(self).fixture_number += 1
        base = 8000000 + self.fixture_number * 10000
        self.offering, self.material = base, base
        count = 200 if self._testMethodName.startswith("test_00") else 2
        self.students = [base + index + 1 for index in range(count)]
        with self.connect() as conn:
            conn.execute("INSERT INTO teachers(id,name,email,hashed_password) VALUES(?,'Synthetic weekly teacher',?,'x')",
                         (base, f"weekly-{base}@example.invalid"))
            conn.execute("INSERT INTO classes(id,name,created_by_teacher_id) VALUES(?,?,?)", (base, f"Synthetic weekly class {base}", base))
            for index in range(5 if count == 200 else 1):
                conn.execute("INSERT INTO courses(id,name,created_by_teacher_id) VALUES(?,'Synthetic weekly course',?)", (base + index, base))
                conn.execute("INSERT INTO class_offerings(id,class_id,course_id,teacher_id) VALUES(?,?,?,?)",
                             (base + index, base, base + index, base))
            for student in self.students:
                conn.execute("INSERT INTO students(id,student_id_number,name,class_id) VALUES(?,?,'Synthetic weekly student',?)",
                             (student, "WEEKLY-" + str(student), base))
            conn.execute("""INSERT INTO course_materials(id,teacher_id,root_id,material_path,name,node_type,
                preview_type,mime_type,file_ext,scope_level,owner_role,owner_user_pk)
                VALUES(?,?,?,'synthetic-weekly.md','Synthetic weekly','file','markdown','text/markdown','.md','public','teacher',?)""",
                (base, base, base, base))
            conn.execute("INSERT INTO course_material_assignments(material_id,class_offering_id,assigned_by_teacher_id,publication_mode) VALUES(?,?,?,'explicit')",
                         (base, base, base))
            progress.record_material_learning_progress(conn, class_offering_id=base, student_id=self.students[0],
                material_id=base, duration_seconds=300, active_seconds=120, completed=True, defer_projection=True)
            for index in range(5 if count == 200 else 1):
                for student in self.students:
                    progress.mark_student_learning_progress_dirty(conn, base + index, student, source_ref="weekly-native-fixture")

    def material_state(self):
        with self.connect() as conn:
            return dict(conn.execute("SELECT accumulated_seconds,active_seconds FROM learning_material_progress "
                "WHERE class_offering_id=? AND student_id=? AND material_id=?",
                (self.offering, self.students[0], self.material)).fetchone())

    def race(self, *, short_transactions):
        entered, release = Event(), Event()
        original = progress._build_learning_metrics
        def controlled_metrics(conn, offering, student):
            if offering == self.offering and student == self.students[1]:
                entered.set()
                if not release.wait(5):
                    raise AssertionError("Native test did not release the controlled second actor")
            return original(conn, offering, student)
        def worker():
            if short_transactions:
                return progress.capture_cultivation_weekly_snapshots_job(week_start="2026-11-09")
            with self.connect() as conn:
                return progress.capture_cultivation_weekly_snapshots(conn, week_start="2026-11-09")
        with patch.object(progress, "_build_learning_metrics", side_effect=controlled_metrics), \
                ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(worker)
            try:
                self.assertTrue(entered.wait(30), "Actual first-actor publication must precede this race")
                began = time.monotonic()
                try:
                    result = learning.post_material_progress(self.offering,
                        learning.MaterialProgressPayload(material_id=self.material, duration_seconds=30, active_seconds=20),
                        user={"role": "student", "id": self.students[0], "name": "Synthetic weekly student"})
                    outcome = {"status": result["status"]}
                except Exception as exc:
                    outcome = {"error": type(exc).__name__, "sqlstate": getattr(exc, "sqlstate", None)}
                outcome["elapsed_seconds"] = time.monotonic() - began
            finally:
                release.set()
            result = future.result(timeout=90)
            return outcome, result

    def test_00_worker_releases_first_actor_before_the_remaining_thousand_actor_job(self):
        baseline = self.material_state()
        old, old_result = self.race(short_transactions=False)
        self.assertEqual("55P03", old.get("sqlstate"), old)
        self.assertEqual(baseline, self.material_state(), "A timed-out legacy write must really roll back")
        self.assertGreaterEqual(old_result["checked"], 1000)
        with self.connect() as conn:
            for student in self.students:
                progress.mark_student_learning_progress_dirty(conn, self.offering, student)
        new, new_result = self.race(short_transactions=True)
        print("weekly_material_race=" + json.dumps({"old": old, "new": new,
            "old_checked": old_result["checked"], "new_checked": new_result["checked"]}, sort_keys=True))
        self.assertEqual("success", new.get("status"), new)
        self.assertGreaterEqual(new_result["checked"], 1000)
        self.assertEqual({"accumulated_seconds": baseline["accumulated_seconds"] + 30,
                          "active_seconds": baseline["active_seconds"] + 20}, self.material_state())
        # The successful concurrent write deliberately dirties the first actor
        # after its weekly publication. The ordinary dispatcher must converge it.
        progress.refresh_pending_learning_snapshots(limit=2000)
        with self.connect() as conn:
            rows = conn.execute("SELECT dirty,dirty_version,calculated_version FROM learning_progress_snapshots "
                "WHERE class_offering_id=?", (self.offering,)).fetchall()
            self.assertEqual(200, len(rows))
            self.assertTrue(all(not row["dirty"] and row["dirty_version"] == row["calculated_version"] for row in rows))

    def test_10_actual_unknown_actor_commit_and_prefix_failure_recover_without_extra_score(self):
        original = progress._capture_weekly_snapshot_actor
        def second_actor_failure(conn, offering, student, **kwargs):
            result = original(conn, offering, student, **kwargs)
            if student == self.students[1]:
                raise RuntimeError("Synthetic failure after this actor's real writes")
            return result
        with patch.object(progress, "_capture_weekly_snapshot_actor", side_effect=second_actor_failure):
            with self.assertRaisesRegex(RuntimeError, "Synthetic failure"):
                progress.capture_cultivation_weekly_snapshots_job(class_offering_id=self.offering, week_start="2026-11-09")
        with self.connect() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) AS n FROM cultivation_weekly_snapshots WHERE class_offering_id=?", (self.offering,)).fetchone()["n"])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) AS n FROM learning_stage_status WHERE class_offering_id=? AND student_id=?", (self.offering, self.students[1])).fetchone()["n"])
            events = conn.execute("SELECT COUNT(*) AS n FROM cultivation_score_events WHERE class_offering_id=?", (self.offering,)).fetchone()["n"]
            self.assertGreater(events, 0)
            progress.mark_student_learning_progress_dirty(conn, self.offering, self.students[0])
        lost = {"once": True}
        class CommitOnceLost(LanSharePostgresConnection):
            captured = False
            def execute(inner, sql, params=None):
                if "INSERT INTO cultivation_weekly_snapshots" in sql:
                    inner.captured = True
                return super().execute(sql, params)
            def commit(inner):
                super().commit()
                if inner.captured and lost["once"]:
                    lost["once"] = False
                    raise ConnectionError("Synthetic committed actor acknowledgement lost")
        with patch.object(progress, "get_db_connection", side_effect=lambda: self.connect(CommitOnceLost)):
            with self.assertRaises(ConnectionError):
                progress.capture_cultivation_weekly_snapshots_job(class_offering_id=self.offering, week_start="2026-11-09")
        progress.capture_cultivation_weekly_snapshots_job(class_offering_id=self.offering, week_start="2026-11-09")
        with self.connect() as conn:
            self.assertEqual(2, conn.execute("SELECT COUNT(*) AS n FROM cultivation_weekly_snapshots WHERE class_offering_id=?", (self.offering,)).fetchone()["n"])
            self.assertEqual(events, conn.execute("SELECT COUNT(*) AS n FROM cultivation_score_events WHERE class_offering_id=?", (self.offering,)).fetchone()["n"])
            rows = conn.execute("SELECT dirty,dirty_version,calculated_version FROM learning_progress_snapshots WHERE class_offering_id=?", (self.offering,)).fetchall()
            self.assertTrue(all(not row["dirty"] and row["dirty_version"] == row["calculated_version"] for row in rows))

    def test_20_native_caller_rollback_and_weekly_claim_window_are_real_transactions(self):
        with self.connect() as conn:
            progress.capture_cultivation_weekly_snapshots(conn, class_offering_id=self.offering, week_start="2026-11-09")
            conn.rollback()
        with self.connect() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) AS n FROM cultivation_weekly_snapshots WHERE class_offering_id=?", (self.offering,)).fetchone()["n"])
            task = conn.execute("""INSERT INTO scheduled_tasks(task_kind,status,run_at,locked_by,locked_at,payload_json)
                VALUES(?,'running','2026-11-08T23:59:00','owned-weekly','2026-11-08T23:59:00',?) RETURNING *""",
                (progress.CULTIVATION_WEEKLY_SNAPSHOT_TASK_KIND, json.dumps({"class_offering_id": self.offering,
                    "week_start": "2026-11-08", "refresh_current": True}))).fetchone()
            task = dict(task)
        result = handlers.handle_cultivation_weekly_snapshot(task)
        with self.connect() as conn:
            row = conn.execute("SELECT payload_json,last_result FROM scheduled_tasks WHERE id=?", (task["id"],)).fetchone()
            window = json.loads(row["payload_json"])[handlers._WEEKLY_CAPTURE_EXECUTION]
            self.assertEqual("2026-11-02", window["week_start"])
            self.assertTrue(result.startswith("weekly-capture:" + window["token"] + " "))
            self.assertEqual("", row["last_result"])
            conn.execute("UPDATE scheduled_tasks SET locked_by='replacement' WHERE id=?", (task["id"],))
        with self.assertRaisesRegex(RuntimeError, "claim"):
            handlers.handle_cultivation_weekly_snapshot(task)

        # A replacement must wait only for the actor currently protected by
        # FOR SHARE, then fence the original owner before its next actor.
        with self.connect() as conn:
            current = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task["id"],)).fetchone())
        entered, release, update_started = Event(), Event(), Event()
        original = progress._capture_weekly_snapshot_actor
        captured = []
        def held_first_actor(conn, offering, student, **kwargs):
            result = original(conn, offering, student, **kwargs)
            captured.append(student)
            if student == self.students[0]:
                entered.set()
                if not release.wait(5):
                    raise AssertionError("Native test did not release the actor claim fence")
            return result
        def replace_claim():
            with self.connect() as conn:
                update_started.set()
                conn.execute("UPDATE scheduled_tasks SET locked_by='next-owner' WHERE id=?", (task["id"],))
        with patch.object(progress, "_capture_weekly_snapshot_actor", side_effect=held_first_actor), \
                ThreadPoolExecutor(max_workers=2) as workers:
            worker = workers.submit(handlers.handle_cultivation_weekly_snapshot, current)
            try:
                self.assertTrue(entered.wait(10))
                replacement = workers.submit(replace_claim)
                self.assertTrue(update_started.wait(2))
                time.sleep(0.05)
                self.assertFalse(replacement.done(), "The current actor's task SHARE fence must be real")
            finally:
                release.set()
            replacement.result(timeout=5)
            with self.assertRaisesRegex(RuntimeError, "claim"):
                worker.result(timeout=10)
        self.assertEqual([self.students[0]], captured)


if __name__ == "__main__":
    unittest.main()
