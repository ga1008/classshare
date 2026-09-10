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
