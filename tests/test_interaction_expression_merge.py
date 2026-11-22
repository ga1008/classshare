"""Real merge of synthetic offerings using the registered, pre-attendance-extension schema."""
import json
import unittest
from unittest.mock import patch

from classroom_app import database
from classroom_app.db import schema_polls, schema_study_group_scheme
from classroom_app.services import interaction_expression_refresh as refresh
from classroom_app.services import offering_merge_service as merge
from classroom_app.services.interaction_expression_facts import load_expression_facts
from classroom_app.services.interaction_expression_opportunities import load_interaction_expression_opportunities
from classroom_app.time_utils import local_now


class InteractionExpressionMergeTests(unittest.TestCase):
    def setUp(self):
        # Share the existing offering-merge fixture's teachers, classes, membership,
        # lesson conflicts, homework and enrollment setup. Keep its owned SQLite DB.
        from test_offering_merge_service import OfferingMergeServiceTests
        # These startup modules cache readiness for the lifetime of one application
        # database. This fixture replaces that database with a new file per case.
        self.enterContext(patch.object(schema_study_group_scheme, "_SCHEMA_READY", False))
        self.enterContext(patch.object(schema_polls, "_SCHEMA_READY", False))
        self.fixture = OfferingMergeServiceTests("test_registry_covers_every_offering_table")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.target, self.source, self.author = self.fixture.target_id, self.fixture.source_id, self.fixture.student_b
        # Model a supported earlier schema, with none of these optional domains.
        # The real registry, unknown-table guard and execute path remain active.
        with database.get_db_connection() as conn:
            for table in ("attendance_appeal_attachments", "attendance_appeal_items", "login_attendance_timeline_audits",
                          "offering_material_sequence_slots", "smart_classroom_offering_snapshots",
                          "cultivation_rank_scopes", "project_topic_bindings", "project_topic_dispatch_history",
                          "project_topic_archived_roster", "project_topic_dispatch_drafts", "project_topic_dispatches"):
                exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                if exists:
                    self.assertIsNone(conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone())
                    conn.execute(f"DROP TABLE {table}")
            self.assertEqual([], merge.find_unregistered_offering_tables(conn))
            session = conn.execute("""INSERT INTO ai_workspace_sessions
                (session_uuid,user_pk,user_role,created_at,updated_at) VALUES ('test',?,'student','2026-03-11','2026-03-11')""",
                (self.author,)).lastrowid
            self.message = conn.execute("""INSERT INTO ai_workspace_messages(session_id,request_id,role,message,created_at)
                VALUES (?,'test-question','user','一个课程问题','2026-03-11')""", (session,)).lastrowid
            conn.execute("INSERT INTO ai_workspace_course_questions VALUES (?,?,?)", (self.message, self.source, self.author))
            for offering in (self.target, self.source):
                student = self.fixture.student_a if offering == self.target else self.author
                self.assertEqual([], load_expression_facts(conn, offering, [student], include_private=True)[student]["unavailable_sources"])
                self.assertEqual([], load_interaction_expression_opportunities(conn, offering, [student])[student]["missing_sources"])
                self.assertEqual("published", refresh.refresh_offering_expression(conn, offering)["status"])

    def preview(self, conn):
        return merge.build_merge_preview(conn, teacher_id=self.fixture.teacher_id,
                 target_offering_id=self.target, source_offering_ids=[self.source])

    def test_moved_question_is_unique_and_both_old_projections_are_fenced(self):
        with database.get_db_connection() as conn:
            self.assertEqual(1, load_expression_facts(conn, self.source, [self.author])[self.author]["workspace_ai_message_count"])
            first = self.preview(conn)
            self.assertTrue(first["can_execute"])
            token = refresh._claim(conn, self.target, local_now(), force=True)
            self.assertTrue(token)
            self.assertEqual(first["review_hash"], self.preview(conn)["review_hash"])
            result = merge.execute_offering_merge(conn, teacher_id=self.fixture.teacher_id,
                target_offering_id=self.target, source_offering_ids=[self.source], confirm_class_name="软工2401班",
                expected_review_hash=first["review_hash"])
            self.assertEqual("success", result["status"])
            self.assertEqual([(self.message, self.target, self.author)], [tuple(r) for r in
                conn.execute("SELECT message_id,class_offering_id,student_id FROM ai_workspace_course_questions")])
            for table in ("interaction_expression_snapshots", "interaction_expression_refresh_state"):
                self.assertEqual(0, conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            now = local_now()
            self.assertEqual("superseded", refresh._publish(conn, self.target, token, {}, calculated_at=now, now=now)["status"])
            self.assertEqual(1, load_expression_facts(conn, self.target, [self.author])[self.author]["workspace_ai_message_count"])
            self.assertEqual(0, load_expression_facts(conn, self.source, [self.author])[self.author]["workspace_ai_message_count"])
            for _ in range(2):
                self.assertEqual(2, refresh.refresh_offering_expression(conn, self.target)["students"])
                row = conn.execute("SELECT self_json,teacher_json FROM interaction_expression_snapshots WHERE student_id=?",
                                   (self.author,)).fetchone()
                for view in row:
                    self.assertEqual(1, json.loads(view)["facts"]["workspace_ai_message_count"])
            with self.assertRaises(merge.OfferingMergeError):
                self.fixture._execute(conn)
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM ai_workspace_course_questions").fetchone()[0])

    def test_failed_merge_restores_original_sources_and_scoped_views(self):
        with database.get_db_connection() as conn:
            before = [tuple(row) for row in conn.execute("SELECT * FROM interaction_expression_snapshots ORDER BY student_id")]
            with patch.object(merge, "replace_offering_class_links", side_effect=RuntimeError("stop before finalization")):
                with self.assertRaisesRegex(RuntimeError, "stop before finalization"):
                    self.fixture._execute(conn)
            conn.rollback()
            self.assertEqual(self.source, conn.execute(
                "SELECT class_offering_id FROM ai_workspace_course_questions WHERE message_id=?", (self.message,)).fetchone()[0])
            self.assertEqual(before, [tuple(row) for row in conn.execute("SELECT * FROM interaction_expression_snapshots ORDER BY student_id")])
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM interaction_expression_refresh_state").fetchone()[0])
