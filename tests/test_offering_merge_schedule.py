"""Durable JWXT identities must never be guessed from local lesson order."""
import json
import unittest

from classroom_app import database
from classroom_app.db.connection import execute_insert_returning_id
from classroom_app.services import offering_merge_service as merge
from tests.test_offering_merge_service import OfferingMergeServiceTests


class OfferingMergeScheduleTests(OfferingMergeServiceTests):
    def seed_schedule(self, conn, *, source, binding=True, link=True):
        semester = execute_insert_returning_id(conn, """INSERT INTO academic_semesters
            (teacher_id,name,start_date,end_date) VALUES(?,'Schedule test','2026-03-01','2026-08-31')""",
            (self.teacher_id,))
        offering = self.source_id if source else self.target_id
        session = self.source_session if source else self.target_session
        if binding:
            conn.execute("""INSERT INTO academic_schedule_session_bindings
                (teacher_id,semester_id,session_id,class_offering_id,event_key,identity_json,
                 original_json,current_json,evidence,updated_at) VALUES(?,?,?,?,?,'{"class":"official-class"}',
                 '{"date":"2026-03-09","sections":[1,2]}','{"date":"2026-03-16","sections":[1,2]}','approved_official_confirmed','now')""",
                (self.teacher_id,semester,session,offering,f'academic:{self.teacher_id}:{semester}:session:{session}'))
        if link:
            conn.execute("""INSERT INTO academic_schedule_change_session_links
                (teacher_id,semester_id,request_id,detail_id,class_offering_id,session_id,status,updated_at)
                VALUES(?,?,'official-request','official-detail',?,?,'approved','now')""",
                (self.teacher_id,semester,offering,session))

    def preview(self, conn):
        return merge.build_merge_preview(conn, teacher_id=self.teacher_id,
            target_offering_id=self.target_id, source_offering_ids=[self.source_id])

    def snapshot(self, conn):
        return merge._snapshot_offerings(conn, [self.target_id, self.source_id])

    def test_empty_schedule_tables_allow_normal_merge_without_ignoring_unknown_tables(self):
        with database.get_db_connection() as conn:
            self.assertEqual([], merge.find_unregistered_offering_tables(conn))
            self.assertTrue(self.preview(conn)['can_execute'])
            conn.execute('CREATE TABLE unknown_schedule_history(id INTEGER, class_offering_id INTEGER)')
            with self.assertRaisesRegex(merge.OfferingMergeError, '尚未登记'):
                self.preview(conn)

    def test_source_official_binding_and_request_link_independently_block_without_any_write(self):
        for binding in (True, False):
            with self.subTest(binding=binding), database.get_db_connection() as conn:
                self.seed_schedule(conn, source=True, binding=binding, link=not binding)
                before = self.snapshot(conn)
                statements = []
                conn.set_trace_callback(statements.append)
                preview = self.preview(conn)
                self.assertFalse(preview['can_execute'])
                self.assertTrue(any('教务课次关联' in text for text in preview['blockers']))
                with self.assertRaisesRegex(merge.OfferingMergeError, '后续调停课改错课次'):
                    self._execute(conn)
                conn.set_trace_callback(None)
                self.assertFalse(any(sql.lstrip().split()[0].upper() in {'INSERT','UPDATE','DELETE','CREATE','ALTER','COMMIT'} for sql in statements))
                self.assertEqual(before, self.snapshot(conn))
                self.assertEqual(0, conn.execute('SELECT COUNT(*) FROM offering_merge_archives').fetchone()[0])
                conn.rollback()

    def test_target_schedule_identity_is_preserved_archived_and_caller_can_rollback_the_whole_merge(self):
        with database.get_db_connection() as conn:
            self.seed_schedule(conn, source=False)
            conn.commit()
            before = self.snapshot(conn)
            conn.execute('BEGIN IMMEDIATE')
            result = self._execute(conn)
            for table in ('academic_schedule_session_bindings','academic_schedule_change_session_links'):
                current = [dict(row) for row in conn.execute(f'SELECT * FROM {table}').fetchall()]
                self.assertEqual(before['tables'][table], current)
            archived = json.loads(conn.execute('SELECT payload_json FROM offering_merge_archives WHERE id=?',
                (result['archive_id'],)).fetchone()[0])
            for table in ('academic_schedule_session_bindings','academic_schedule_change_session_links'):
                self.assertEqual(before['tables'][table], archived['tables'][table])
            self.assertIsNone(conn.execute('SELECT id FROM class_offerings WHERE id=?',(self.source_id,)).fetchone())
            conn.rollback()
            self.assertEqual(before, self.snapshot(conn))
            self.assertEqual(0, conn.execute('SELECT COUNT(*) FROM offering_merge_archives').fetchone()[0])

    def test_new_source_binding_after_preview_is_rejected_before_archive_or_deletion(self):
        with database.get_db_connection() as conn:
            reviewed = self.preview(conn)
            self.seed_schedule(conn, source=True)
            conn.commit()
            before = self.snapshot(conn)
            with self.assertRaises(merge.OfferingMergeError):
                merge.execute_offering_merge(conn, teacher_id=self.teacher_id,
                    target_offering_id=self.target_id, source_offering_ids=[self.source_id],
                    confirm_class_name='软工2401班', expected_review_hash=reviewed['review_hash'])
            self.assertEqual(before, self.snapshot(conn))
            self.assertEqual(0, conn.execute('SELECT COUNT(*) FROM offering_merge_archives').fetchone()[0])

    def test_changed_target_schedule_baseline_invalidates_same_count_confirmation(self):
        with database.get_db_connection() as conn:
            self.seed_schedule(conn, source=False)
            for table, field, value in (
                ('academic_schedule_session_bindings', 'current_json', json.dumps({'date': '2026-03-17'})),
                ('academic_schedule_change_session_links', 'status', 'returned'),
            ):
                with self.subTest(table=table):
                    old = self.preview(conn)
                    conn.execute(f'UPDATE {table} SET {field}=?', (value,))
                    new = self.preview(conn)
                    self.assertEqual(old['total_source_rows'], new['total_source_rows'])
                    self.assertNotEqual(old['review_hash'], new['review_hash'])
                    with self.assertRaisesRegex(merge.OfferingMergeError, '确认已失效'):
                        merge.execute_offering_merge(conn, teacher_id=self.teacher_id,
                            target_offering_id=self.target_id, source_offering_ids=[self.source_id],
                            confirm_class_name='软工2401班', expected_review_hash=old['review_hash'])
                    self.assertEqual(0, conn.execute('SELECT COUNT(*) FROM offering_merge_archives').fetchone()[0])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(OfferingMergeScheduleTests(name)
        for name in OfferingMergeScheduleTests.__dict__ if name.startswith('test_'))
