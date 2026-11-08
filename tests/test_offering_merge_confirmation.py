import json
import unittest
from unittest.mock import patch

from classroom_app import database
from classroom_app.db.connection import execute_insert_returning_id
from classroom_app.services import offering_merge_service as merge
from tests.test_offering_merge_service import OfferingMergeServiceTests


class OfferingMergeConfirmationTests(OfferingMergeServiceTests):
    def preview(self, conn):
        return merge.build_merge_preview(conn, teacher_id=self.teacher_id,
            target_offering_id=self.target_id, source_offering_ids=[self.source_id])

    def _observe_collection(self, conn, offering_id, batch_id, *, source="browser", seconds=30):
        from classroom_app.services.behavior_collection_health_service import record_collection_observation
        diagnostic = source == "client_diagnostic"
        return record_collection_observation(conn, class_offering_id=offering_id,
            user_pk=self.student_b, user_role="student", batch_id=batch_id,
            received_at="2026-11-08T08:01:00", source=source,
            valid_sample_count=0 if diagnostic else 1, rejected_sample_count=0,
            learning_delta_seconds=0 if diagnostic else seconds,
            observed_start=None if diagnostic else "2026-11-08T08:00:00",
            observed_end=None if diagnostic else "2026-11-08T08:00:30",
            reason_codes=["auth_required"] if diagnostic else [],
            diagnostics={"error_code": "auth_required"} if diagnostic else {"sample": "original"},
            ip_address="192.0.2.10")

    def test_collection_observations_preserve_original_scope_despite_cross_course_batch_collision(self):
        with database.get_db_connection() as conn:
            # The same actor and client batch ID remain distinct facts in their original courses.
            for offering_id, seconds in ((self.target_id, 20), (self.source_id, 30)):
                self.assertTrue(self._observe_collection(conn, offering_id, "b1_shared", seconds=seconds))
            self._observe_collection(conn, self.source_id, "d1_auth_failure", source="client_diagnostic")
            self._observe_collection(conn, self.source_id, "b1_server", source="server", seconds=10)
            conn.commit()
            before = [dict(row) for row in conn.execute(
                "SELECT * FROM behavior_collection_observations ORDER BY observation_key").fetchall()]
            self.assertEqual([], merge.find_unregistered_offering_tables(conn))
            preview = self.preview(conn)
            self.assertTrue(preview["can_execute"])
            entry = next(item for item in preview["tables"] if item["table"] == "behavior_collection_observations")
            self.assertEqual(3, entry["source_rows"])
            self.assertEqual(merge.STRATEGY_COLLECTION_OBSERVATIONS, entry["strategy"])
            self.assertTrue(any("原课堂编号、账号和批次" in warning for warning in preview["warnings"]))
            result = self._execute(conn)
            self.assertEqual(3, result["summary"]["behavior_collection_observations.observation_scope_preserved"])
            after = [dict(row) for row in conn.execute(
                "SELECT * FROM behavior_collection_observations ORDER BY observation_key").fetchall()]
            self.assertEqual(before, after, "Merging must not re-key, collapse or re-credit immutable observations")
            self.assertIsNone(conn.execute("SELECT id FROM class_offerings WHERE id=?", (self.source_id,)).fetchone())
            archived = json.loads(conn.execute("SELECT payload_json FROM offering_merge_archives ORDER BY id DESC LIMIT 1").fetchone()[0])
            self.assertEqual(before, sorted(archived["tables"]["behavior_collection_observations"], key=lambda row: row["observation_key"]))
            # Original-scope idempotency is preserved even after the source offering is removed.
            self.assertFalse(self._observe_collection(conn, self.source_id, "b1_shared", seconds=90))
            self.assertEqual(60, conn.execute("SELECT SUM(learning_delta_seconds) FROM behavior_collection_observations").fetchone()[0])
            conn.rollback()
            self.assertIsNotNone(conn.execute("SELECT id FROM class_offerings WHERE id=?", (self.source_id,)).fetchone())
            self.assertEqual(before, [dict(row) for row in conn.execute(
                "SELECT * FROM behavior_collection_observations ORDER BY observation_key").fetchall()])

    def test_collection_observation_content_changes_invalidate_review_without_mutating_evidence(self):
        with database.get_db_connection() as conn:
            self._observe_collection(conn, self.source_id, "d1_auth_failure", source="client_diagnostic")
            conn.commit()
            before = self.preview(conn)
            conn.execute("UPDATE behavior_collection_observations SET diagnostics_json=? WHERE class_offering_id=?",
                         ('{"error_code":"client_request_timeout"}', self.source_id))
            after = self.preview(conn)
            self.assertEqual(before["total_source_rows"], after["total_source_rows"])
            self.assertNotEqual(before["review_hash"], after["review_hash"])
            with self.assertRaisesRegex(merge.OfferingMergeError, "确认已失效"):
                merge.execute_offering_merge(conn, teacher_id=self.teacher_id,
                    target_offering_id=self.target_id, source_offering_ids=[self.source_id],
                    confirm_class_name="软工2401班", expected_review_hash=before["review_hash"])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM offering_merge_archives").fetchone()[0])

    def test_pending_behavior_blocks_merge_and_applied_receipt_keeps_immutable_scope(self):
        with database.get_db_connection() as conn:
            conn.execute("""INSERT INTO behavior_write_inbox
                (batch_key,batch_id,payload_hash,class_offering_id,user_pk,user_role,display_name,events_json,received_at)
                VALUES('receipt-key','receipt-id','immutable-hash',?,?,'student','Synthetic','[]','2026-11-05')""",
                (self.source_id, self.student_b))
            conn.commit()
            self.assertEqual([], merge.find_unregistered_offering_tables(conn))
            before = self.preview(conn)
            self.assertFalse(before["can_execute"])
            self.assertTrue(any("等待落库" in item for item in before["blockers"]))
            conn.execute("UPDATE behavior_write_inbox SET state='applied',result_json='{}',applied_at='2026-11-05'")
            conn.execute("""INSERT INTO behavior_write_inbox
                (batch_key,batch_id,payload_hash,class_offering_id,user_pk,user_role,display_name,events_json,received_at)
                VALUES('target-key','target-id','target-hash',?,?,'student','Synthetic','[]','2026-11-05')""",
                (self.target_id, self.student_a))
            self.assertFalse(self.preview(conn)["can_execute"], "target work must also drain before a merge")
            conn.execute("UPDATE behavior_write_inbox SET state='applied',result_json='{}',applied_at='2026-11-05'")
            conn.commit()
            ready = self.preview(conn)
            self.assertTrue(ready["can_execute"])
            self.assertNotEqual(before["review_hash"], ready["review_hash"])
            self._execute(conn)
            row = conn.execute("SELECT batch_key,class_offering_id,payload_hash,state FROM behavior_write_inbox").fetchone()
            self.assertEqual(("receipt-key", self.source_id, "immutable-hash", "applied"), tuple(row))
            archived = conn.execute("SELECT payload_json FROM offering_merge_archives ORDER BY id DESC LIMIT 1").fetchone()[0]
            self.assertIn("receipt-key", archived)

    def test_known_unmapped_scopes_allow_empty_tables_and_block_populated_source(self):
        from classroom_app.db import schema_session_knowledge_trees, schema_wanderers
        with database.get_db_connection() as conn:
            with patch.object(schema_session_knowledge_trees, "_SCHEMA_READY", False), patch.object(schema_wanderers, "_SCHEMA_READY", False):
                schema_session_knowledge_trees.ensure_session_knowledge_tree_schema(conn)
                schema_wanderers.ensure_wanderer_schema(conn, engine="sqlite")
            self.assertTrue(self.preview(conn)["can_execute"])
            inserts = {
                "session_knowledge_trees": ("INSERT INTO session_knowledge_trees(id,class_offering_id,session_id) VALUES('probe',?,?)", (self.source_id, self.source_session)),
                "wanderer_action_log": ("INSERT INTO wanderer_action_log(wanderer_id,action_kind,class_offering_id,created_at) VALUES(1,'probe',?,'2026-11-05')", (self.source_id,)),
                "wanderer_social_inbox": ("INSERT INTO wanderer_social_inbox(wanderer_id,source,conversation_key,peer_identity,class_offering_id,run_at,created_at,updated_at) VALUES(1,'classroom','probe','student:1',?,'2026-11-05','2026-11-05','2026-11-05')", (self.source_id,)),
            }
            for table, (statement, params) in inserts.items():
                with self.subTest(table=table):
                    conn.execute(statement, params)
                    self.assertFalse(self.preview(conn)["can_execute"])
                    with self.assertRaises(merge.OfferingMergeError):
                        self._execute(conn)
                    conn.execute(f"DELETE FROM {table}")

    def test_quarantined_behavior_blocks_each_participant_until_explicit_discard(self):
        with database.get_db_connection() as conn:
            for key, offering_id, student_id in (("source-quarantine", self.source_id, self.student_b),
                                                ("target-quarantine", self.target_id, self.student_a)):
                conn.execute("""INSERT INTO behavior_write_inbox
                    (batch_key,batch_id,payload_hash,class_offering_id,user_pk,user_role,display_name,events_json,received_at,state)
                    VALUES(?,?,?, ?,?,'student','Synthetic','[]','2026-11-05','quarantined')""",
                    (key, key, key+"-hash", offering_id, student_id))
                preview = self.preview(conn)
                self.assertFalse(preview["can_execute"])
                self.assertTrue(any("隔离待修复" in item for item in preview["blockers"]))
                with self.assertRaises(merge.OfferingMergeError):
                    self._execute(conn)
                conn.execute("UPDATE behavior_write_inbox SET state='discarded' WHERE batch_key=?", (key,))
            ready = self.preview(conn)
            self.assertTrue(ready["can_execute"])
            self._execute(conn)
            rows = conn.execute("SELECT batch_key,class_offering_id,payload_hash,state FROM behavior_write_inbox ORDER BY batch_key").fetchall()
            self.assertEqual([("source-quarantine", self.source_id, "source-quarantine-hash", "discarded"),
                              ("target-quarantine", self.target_id, "target-quarantine-hash", "discarded")], [tuple(row) for row in rows])

    def test_review_performs_no_ddl_or_mutation_and_detects_same_count_changes(self):
        with database.get_db_connection() as conn:
            statements = []
            conn.set_trace_callback(statements.append)
            before = self.preview(conn)
            conn.set_trace_callback(None)
            self.assertFalse(any(sql.lstrip().split()[0].upper() in {"CREATE", "ALTER", "INSERT", "UPDATE", "DELETE", "COMMIT", "BEGIN"} for sql in statements))
            conn.execute("UPDATE assignments SET title='Changed with same count' WHERE id=?", (self.source_assignment,))
            after = self.preview(conn)
            self.assertEqual(before['total_source_rows'], after['total_source_rows'])
            self.assertNotEqual(before['review_hash'], after['review_hash'])
            with self.assertRaisesRegex(merge.OfferingMergeError, "确认已失效"):
                merge.execute_offering_merge(conn, teacher_id=self.teacher_id,
                    target_offering_id=self.target_id, source_offering_ids=[self.source_id],
                    confirm_class_name="软工2401班", expected_review_hash=before['review_hash'])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM offering_merge_archives").fetchone()[0], 0)
            conn.rollback()

    def test_checkin_session_relations_survive_and_caller_rollback_is_complete(self):
        with database.get_db_connection() as conn:
            checkin_id = execute_insert_returning_id(conn, """INSERT INTO smart_classroom_checkin_sessions
                (teacher_id,class_offering_id,session_id,platform_code,remote_checkin_id)
                VALUES(?,?,?,'fixture','confirmed-history')""", (self.teacher_id,self.source_id,self.source_session))
            conn.execute("""INSERT INTO smart_classroom_checkin_students
                (checkin_session_id,teacher_id,class_offering_id,session_id,student_number,student_name,status,status_label)
                VALUES(?,?,?,?, 'fixture','fixture','present','present')""",
                (checkin_id,self.teacher_id,self.source_id,self.source_session))
            conn.commit()
            # The fresh-user operation ledger will already own this transaction.
            conn.execute("BEGIN IMMEDIATE")
            before = self.preview(conn)
            outcome = merge.execute_offering_merge(conn, teacher_id=self.teacher_id,
                target_offering_id=self.target_id, source_offering_ids=[self.source_id],
                confirm_class_name="软工2401班", expected_review_hash=before['review_hash'])
            self.assertTrue(conn.in_transaction)
            for table in ('smart_classroom_checkin_sessions','smart_classroom_checkin_students'):
                row = conn.execute(f'SELECT class_offering_id,session_id FROM {table}').fetchone()
                self.assertEqual(tuple(row), (self.target_id,self.target_session))
            self.assertTrue(outcome['archive_id'])
            conn.rollback()
            self.assertIsNotNone(conn.execute('SELECT id FROM class_offerings WHERE id=?',(self.source_id,)).fetchone())
            self.assertEqual(conn.execute('SELECT session_id FROM smart_classroom_checkin_sessions').fetchone()[0], self.source_session)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM offering_merge_archives').fetchone()[0], 0)

    def test_missing_target_sessions_cannot_drop_bound_source_history(self):
        with database.get_db_connection() as conn:
            conn.execute('DELETE FROM class_offering_sessions WHERE id=?',(self.target_session,))
            preview = self.preview(conn)
            self.assertFalse(preview['can_execute'])
            self.assertTrue(any('主课堂没有课次' in item for item in preview['blockers']))
            with self.assertRaises(merge.OfferingMergeError):
                self._execute(conn)
            self.assertIsNotNone(conn.execute('SELECT id FROM class_offering_sessions WHERE id=?',(self.source_session,)).fetchone())

    def test_running_generation_blocks_but_queued_task_is_preserved_and_repointed(self):
        with database.get_db_connection() as conn:
            task_id = execute_insert_returning_id(conn, """INSERT INTO session_material_generation_tasks
                (teacher_id,class_offering_id,session_id,status) VALUES(?,?,?,'running')""",
                (self.teacher_id,self.source_id,self.source_session))
            self.assertFalse(self.preview(conn)['can_execute'])
            conn.execute("UPDATE session_material_generation_tasks SET status='queued' WHERE id=?",(task_id,))
            self.assertTrue(self.preview(conn)['can_execute'])
            self._execute(conn)
            row = conn.execute('SELECT class_offering_id,session_id,status FROM session_material_generation_tasks WHERE id=?',(task_id,)).fetchone()
            self.assertEqual(tuple(row), (self.target_id,self.target_session,'queued'))

    def test_management_poll_assignment_blocks_without_broadening_participants(self):
        with database.get_db_connection() as conn:
            from classroom_app.db import schema_polls
            with patch.object(schema_polls, '_SCHEMA_READY', False):
                schema_polls.ensure_poll_schema(conn)
            conn.execute("INSERT INTO poll_assignments(poll_id,class_offering_id) VALUES(777,?)", (self.source_id,))
            preview = self.preview(conn)
            self.assertFalse(preview['can_execute'])
            self.assertTrue(any('课堂投票分配' in item for item in preview['blockers']))
            with self.assertRaises(merge.OfferingMergeError):
                self._execute(conn)
            self.assertEqual(conn.execute('SELECT class_offering_id FROM poll_assignments').fetchone()[0], self.source_id)

    def test_unmigrated_blog_audience_blocks_without_broadening_or_deleting(self):
        with database.get_db_connection() as conn:
            conn.execute("""INSERT INTO blog_posts(author_identity,author_role,author_user_pk,author_display_name,title,content_md,status,visibility,visible_class_offering_id)
                VALUES('teacher:1','teacher',?,'fixture','private audience','body','published','class',?)""",(self.teacher_id,self.source_id))
            preview = self.preview(conn)
            self.assertFalse(preview['can_execute'])
            self.assertTrue(any('课堂定向博客' in item for item in preview['blockers']))
            with self.assertRaises(merge.OfferingMergeError):
                self._execute(conn)
            self.assertEqual(conn.execute('SELECT visible_class_offering_id FROM blog_posts').fetchone()[0], self.source_id)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(OfferingMergeConfirmationTests(name)
        for name in OfferingMergeConfirmationTests.__dict__ if name.startswith('test_'))
