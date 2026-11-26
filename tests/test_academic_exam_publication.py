import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

from classroom_app.database import get_db_connection
from classroom_app.services import academic_course_exam_sync_service as exams
from classroom_app.services import academic_invigilation_sync_service as invigilation
from classroom_app.services import message_center_service as inbox
from tests.sqlite_database_fixture import isolated_sqlite_database


class AcademicExamPublicationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(isolated_sqlite_database())
        self.term = dict(teacher_id=777, semester={"id": 781},
                         term_params_list=[{"xnm": "2099", "xqm": "3"}], synced_at="2099-09-01T03:00:00")
        with get_db_connection() as conn:
            conn.execute("""INSERT INTO teachers(id,name,email,hashed_password) VALUES
                         (777,'Teacher','teacher@example.test','test')""")
            conn.execute("""INSERT INTO teacher_email_configs(teacher_id,smtp_host,from_email,is_default)
                         VALUES(777,'smtp.example.test','sender@example.test',1)""")
            conn.execute("""INSERT INTO academic_semesters(id,teacher_id,name,start_date,end_date)
                         VALUES(781,777,'2099-2100第一学期','2099-09-01','2100-01-31')""")
            conn.execute("""INSERT INTO classes(id,name,created_by_teacher_id) VALUES
                         (782,'Primary',777),(783,'Linked',777),(784,'Unrelated',777)""")
            conn.execute("""INSERT INTO courses(id,name,academic_course_code,created_by_teacher_id)
                         VALUES(785,'Networks','NET',777)""")
            conn.execute("""INSERT INTO class_offerings(id,class_id,course_id,teacher_id,semester_id,academic_teaching_class_name)
                         VALUES(786,782,785,777,781,'NET-A')""")
            conn.execute("""INSERT INTO class_offering_class_links(offering_id,class_id,teacher_id)
                         VALUES(786,783,777)""")
            conn.executemany("""INSERT INTO students(id,name,student_id_number,class_id,email,enrollment_status)
                             VALUES(?, 'Student',?,?,?,?)""", [
                (790,"S790",782,"a@example.test","active"), (791,"S791",783,"","active"),
                (792,"S792",784,"b@example.test","active"), (793,"S793",783,"c@example.test","inactive")])
            exams.ensure_course_exam_schema(conn)
            conn.commit()
        fields = dict(academic_year="2099", academic_term="3",course_code="NET", course_name="Networks",
                      teaching_class_name="NET-A", starts_at="2099-12-23T09:00:00", ends_at="2099-12-23T11:00:00",
                      exam_time_text="2099-12-23 09:00-11:00", location="301")
        self.exam = exams.AcademicCourseExamItem(exam_key="A", **fields)
        self.duty = invigilation.AcademicInvigilationItem(invigilation_key="B", **fields)

    def sync(self, *items, duty=False):
        with get_db_connection() as conn:
            persist = invigilation._persist_invigilations if duty else exams._persist_course_exams
            result = persist(conn, **self.term, items=list(items))
            conn.commit()
            return result

    def count(self, table):
        with get_db_connection() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_audience_queue_recovery_dedup_and_rollback(self):
        with patch.object(inbox, "queue_notification_email_if_applicable", side_effect=RuntimeError("enqueue failed")):
            with self.assertRaisesRegex(RuntimeError, "enqueue failed"):
                self.sync(self.exam)
        for table in ("teacher_calendar_events", "teacher_academic_course_exam_items", "message_center_notifications"):
            self.assertEqual(self.count(table), 0)
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = list(pool.map(lambda _: self.sync(replace(self.exam)), range(2)))
        self.assertEqual(sum(a["student_notification_count"] for a in attempts), 2)
        self.assertEqual(sum(a["teacher_notification_count"] for a in attempts), 1)
        self.assertEqual(self.sync(self.duty, duty=True)["notification_count"], 1)
        with get_db_connection() as conn:
            notices = list(conn.execute("SELECT recipient_role,recipient_user_pk,email_status,title FROM message_center_notifications"))
            self.assertCountEqual([(r[0],r[1],r[2]) for r in notices], [
                ("student",790,"queued"),("student",791,"skipped"),("teacher",777,"queued"),("teacher",777,"queued")])
            self.assertEqual([r[0] for r in notices if "监考" in r["title"]], ["teacher"])
            conn.execute("UPDATE students SET email='fixed@example.test' WHERE id=791")
            conn.execute("UPDATE students SET enrollment_status='active' WHERE id=793")
            conn.commit()
        self.assertEqual(self.sync(self.exam)["student_notification_count"], 1)
        self.assertEqual(self.count("email_outbox"), 5)
        self.assertEqual(self.count("message_center_notifications"), 5)

    def test_event_changes_withdrawal_restore_and_previous_room_again(self):
        self.sync(self.duty, duty=True)
        self.sync(replace(self.duty, invigilation_role="主监考", location="302"), duty=True)
        self.sync(self.duty, duty=True)
        self.assertEqual(self.sync(duty=True)["stale_count"], 1)
        self.assertEqual(self.sync(duty=True)["notification_count"], 0)
        self.assertEqual(self.sync(self.duty, duty=True)["notification_count"], 1)
        self.assertEqual(self.sync(self.duty, duty=True)["notification_count"], 0)
        self.assertEqual(self.count("teacher_calendar_events"), 1)
        self.assertEqual(self.count("email_outbox"), 5)
        with get_db_connection() as conn:
            titles = [r[0] for r in conn.execute("SELECT title FROM message_center_notifications ORDER BY id")]
            self.assertEqual(titles, [f"教务系统{word}监考：Networks" for word in ["新增","更新","更新","撤回","恢复"]])

    def test_unscheduled_exam_is_published_only_to_owner_until_offering_matches(self):
        item = replace(self.exam, course_code="X", course_name="Other", teaching_class_name="Other",
                       starts_at="", ends_at="", exam_time_text="待排")
        unmatched = self.sync(item)
        self.assertEqual((unmatched["teacher_notification_count"], unmatched["student_notification_count"]), (1,0))
        corrected = self.sync(self.exam)
        self.assertEqual((corrected["teacher_notification_count"], corrected["student_notification_count"]), (1,2))
        withdrawn = self.sync()
        self.assertEqual((withdrawn["teacher_notification_count"], withdrawn["student_notification_count"]), (1,2))
        self.assertEqual(self.sync()["student_notification_count"], 0)

    def test_time_change_and_reversion_reach_students_once_each(self):
        self.sync(self.exam)
        moved = replace(self.exam, starts_at="2099-12-23T14:00:00", ends_at="2099-12-23T16:00:00")
        for item in (moved, self.exam):
            self.assertEqual(self.sync(item)["student_notification_count"], 2)
            self.assertEqual(self.sync(item)["student_notification_count"], 0)
        with get_db_connection() as conn:
            rows = conn.execute("SELECT title FROM message_center_notifications WHERE recipient_user_pk=790 ORDER BY id").fetchall()
            self.assertEqual([r[0] for r in rows], [f"教务系统{k}考试：Networks" for k in ("新增","更新","更新")])

    def test_split_rooms_publish_once_per_class_and_withdraw_the_affected_students(self):
        room_b = replace(self.exam, exam_key="B", location="302")
        self.assertEqual(self.sync(self.exam, room_b)["student_notification_count"], 2)
        self.assertEqual(self.sync(room_b, self.exam)["student_notification_count"], 0)
        with get_db_connection() as conn:
            notices = conn.execute("SELECT body_preview FROM message_center_notifications WHERE recipient_role='student'").fetchall()
            self.assertTrue(all("301、302" in r[0] and "个人座位名单" in r[0] for r in notices))
        # Representative 301 disappears, 302 remains. Neither class gets a cancellation.
        self.assertEqual(self.sync(room_b)["student_notification_count"], 2)
        with get_db_connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM message_center_notifications WHERE recipient_role='student' AND title LIKE '%撤回%'").fetchone()[0], 0)
        # Explicit administrative classes have independent rooms.
        primary = replace(self.exam, class_composition="Primary")
        linked = replace(room_b, class_composition="Linked")
        self.assertEqual(self.sync(primary, linked)["student_notification_count"], 2)
        with get_db_connection() as conn:
            for uid, room, other in ((790, "301", "302"), (791,"302","301")):
                last = conn.execute("SELECT title,body_preview FROM message_center_notifications WHERE recipient_role='student' AND recipient_user_pk=? ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
                self.assertIn(room, last["body_preview"])
                self.assertNotIn(other, last["body_preview"])
                self.assertNotIn("候选", last["title"])
        self.assertEqual(self.sync(linked)["student_notification_count"], 1)
        with get_db_connection() as conn:
            cancelled = conn.execute("SELECT recipient_user_pk FROM message_center_notifications WHERE recipient_role='student' AND title LIKE '%撤回%'").fetchall()
            self.assertEqual([r[0] for r in cancelled], [790])
        self.assertEqual(self.sync(linked)["student_notification_count"], 0)
        self.assertEqual(self.sync(primary, linked)["student_notification_count"], 1)

    def test_legacy_invigilation_todo_keeps_read_state_and_gains_one_email(self):
        self.sync(self.duty, duty=True)
        with get_db_connection() as conn:
            old_event = conn.execute("SELECT * FROM teacher_calendar_events").fetchone()
            metadata = json.loads(old_event["metadata_json"])
            del metadata["notice_revision"]
            conn.execute("UPDATE teacher_calendar_events SET metadata_json=?", (json.dumps(metadata),))
            conn.execute("DELETE FROM email_outbox")
            conn.execute("DELETE FROM message_center_notifications")
            inbox.create_todo_notification(
                conn, recipient_role="teacher", recipient_user_pk=777, title="教务系统新增监考：Networks",
                ref_id=f"academic-invigilation:{old_event['source_key']}:{self.duty.starts_at}")
            old = conn.execute("SELECT id, email_status FROM message_center_notifications").fetchone()
            self.assertEqual(old["email_status"], "not_required")
            conn.execute("UPDATE message_center_notifications SET read_at='2099-09-01'")
            conn.commit()
        for _ in range(2):
            self.assertEqual(self.sync(self.duty, duty=True)["notification_count"], 0)
        with get_db_connection() as conn:
            cards = conn.execute("SELECT id,read_at,category,email_status FROM message_center_notifications").fetchall()
            self.assertEqual([tuple(r) for r in cards], [(old["id"],"2099-09-01","academic_exam","queued")])
        self.assertEqual(self.count("email_outbox"), 1)

    def test_past_events_are_stored_without_initial_mail(self):
        self.sync(replace(self.exam, starts_at="2001-12-23T09:00:00", ends_at="2001-12-23T11:00:00"))
        self.assertEqual(self.count("teacher_calendar_events"), 1)
        self.assertEqual(self.count("message_center_notifications"), 0)
