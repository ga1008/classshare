"""Schedule reconciliation against real isolated SQLite, never the configured DB."""
from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from classroom_app.db.schema_academic_schedule_predictions import ensure_academic_schedule_prediction_schema
from classroom_app.services.academic_schedule_prediction_service import (
    ScheduleSnapshotError, ScheduleSyncLeaseError, claim_schedule_sync, fail_schedule_sync,
    load_authorized_prediction_lessons, load_teacher_prediction_snapshot, load_teacher_prediction_terms,
    reconcile_and_publish_snapshot, release_schedule_sync,
)


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE teachers(id INTEGER PRIMARY KEY,school_code TEXT,name TEXT);
INSERT INTO teachers VALUES(1,'school','老师一'),(2,'other','老师二');
CREATE TABLE academic_semesters(id INTEGER PRIMARY KEY,teacher_id INTEGER,school_code TEXT,name TEXT,start_date TEXT,end_date TEXT,week_count INTEGER);
INSERT INTO academic_semesters VALUES(1,1,'school','2026-2027第一学期','2026-08-31','2027-01-10',19),
 (2,2,'other','2026-2027第一学期','2026-08-31','2027-01-10',19),
 (3,1,'school','2027-2028第一学期','2027-08-30','2028-01-09',19);
CREATE TABLE courses(id INTEGER PRIMARY KEY,name TEXT,academic_course_code TEXT);
INSERT INTO courses VALUES(1,'网络','NET'),(2,'其他','OTHER');
CREATE TABLE class_offerings(id INTEGER PRIMARY KEY,teacher_id INTEGER,semester_id INTEGER,course_id INTEGER,
 academic_teaching_class_id TEXT,academic_teaching_class_name TEXT);
INSERT INTO class_offerings VALUES(10,1,1,1,'NET-A','网络-0001'),(20,2,2,2,'OTHER-A','其他-0001');
CREATE TABLE class_offering_sessions(id INTEGER PRIMARY KEY,class_offering_id INTEGER,order_index INTEGER,title TEXT,content TEXT,
 course_lesson_id INTEGER,learning_material_id INTEGER,section_count INTEGER,slot_section_count INTEGER,session_date TEXT,
 weekday INTEGER,week_index INTEGER,academic_section_text TEXT,academic_location TEXT,schedule_status TEXT,updated_at TEXT);
INSERT INTO class_offering_sessions VALUES
 (101,10,1,'原第一课','不可覆盖第一课内容',11,51,2,2,'2026-09-20',6,3,'2-3','B416','scheduled','old'),
 (102,10,2,'原第二课','不可覆盖第二课内容',12,52,2,2,'2026-09-26',5,4,'8-9','B416','scheduled','old'),
 (103,10,3,'原第三课','不可覆盖第三课内容',13,53,2,2,'2026-09-27',6,4,'2-3','B416','scheduled','old'),
 (201,20,1,'其他老师课','私有',21,61,2,2,'2026-09-20',6,3,'2-3','秘密','scheduled','old');
CREATE TABLE session_materials(session_id INTEGER,material_id INTEGER);
INSERT INTO session_materials VALUES(101,901),(102,902),(103,903);
"""


def slot(day, sections=(2, 3), room="B416"):
    from datetime import date
    parsed = date.fromisoformat(day)
    return {"date": day, "week": (parsed - date(2026, 8, 31)).days // 7 + 1,
            "weekday": parsed.isoweekday(), "sections": list(sections), "room": room}


IDENTITY = {"teaching_class_id": "NET-A", "teaching_class_name": "网络-0001", "course_code": "NET", "course_name": "网络", "class_label": "甲班、乙班"}


def official(day, sections=(2, 3), room="B416"):
    return {**IDENTITY, **slot(day, sections, room)}


def request(request_id="R1", *, status="pending", original=None, proposed=None, kind="move"):
    return {**IDENTITY, "request_id": request_id, "serial": "20260001", "status": status,
            "raw_status": "1" if status == "pending" else "3", "kind": kind, "reason": "假期调课",
            "applied_at": "2026-09-18 20:00:00", "details": [{"detail_id": request_id + "-1",
            "original": original or slot("2026-09-20"), "proposed": proposed if proposed is not None else (None if kind == "cancel" else slot("2026-10-11"))}]}


def base_snapshot(requests=None):
    return {"official": [official("2026-09-20"), official("2026-09-26", (8, 9)), official("2026-09-27")],
            "requests": requests or [], "source_summary": []}


class AcademicSchedulePredictionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        ensure_academic_schedule_prediction_schema(self.conn)
        self.conn.commit()
        self.now = datetime(2026, 9, 19, 2, tzinfo=timezone.utc)
        self.addCleanup(self.conn.close)

    def publish(self, snapshot, *, teacher_id=1, semester=1):
        lease = claim_schedule_sync(self.conn, teacher_id, now=self.now)
        self.conn.commit()
        result = reconcile_and_publish_snapshot(self.conn, teacher_id, semester, snapshot, lease["token"], now=self.now)
        release_schedule_sync(self.conn, teacher_id, lease["token"])
        self.conn.commit()
        return result

    def read(self):
        return load_teacher_prediction_snapshot(self.conn, 1, 1)

    def test_pending_two_ends_same_stable_session_without_mutating_content(self):
        before = dict(self.conn.execute("SELECT * FROM class_offering_sessions WHERE id=101").fetchone())
        result = self.publish(base_snapshot([request()]))
        snapshot = self.read()
        pair = [item for item in snapshot["lessons"] if item.get("adjustment")]
        self.assertEqual((3, 1), (result["official_count"], result["predicted_count"]))
        self.assertEqual({101}, {item["session_id"] for item in pair})
        self.assertEqual({(1, 3, "bound")}, {(item["session_no"], item["session_total"], item["binding_status"]) for item in pair})
        self.assertEqual({"/classroom/10?session_id=101"}, {item["classroom_url"] for item in pair})
        self.assertEqual({3, 6}, {item["week_index"] for item in pair})
        self.assertEqual(1, sum(item["counts_towards_total"] for item in pair))
        self.assertEqual(pair[1]["event_key"], pair[0]["adjustment"]["counterpart_event_key"])
        self.assertEqual(before, dict(self.conn.execute("SELECT * FROM class_offering_sessions WHERE id=101").fetchone()))

    def test_approved_move_preserves_identity_order_material_after_crossing_two_classes(self):
        self.publish(base_snapshot([request()]))
        snapshot = base_snapshot([request(status="approved")])
        snapshot["official"][0] = official("2026-10-11")
        result = self.publish(snapshot)
        session = dict(self.conn.execute("SELECT * FROM class_offering_sessions WHERE id=101").fetchone())
        self.assertEqual([101], result["updated_session_ids"])
        self.assertEqual(("2026-10-11", 1, 11, 51, "原第一课", "不可覆盖第一课内容"),
                         tuple(session[key] for key in ("session_date", "order_index", "course_lesson_id", "learning_material_id", "title", "content")))
        self.assertEqual([(101, 901), (102, 902), (103, 903)], [tuple(row) for row in self.conn.execute("SELECT * FROM session_materials")])
        self.assertFalse(any(item.get("adjustment") for item in self.read()["lessons"]))
        self.assertEqual(101, self.read()["lessons"][-1]["session_id"])
        self.assertEqual(1, self.read()["lessons"][-1]["session_no"])
        self.publish(snapshot)
        self.assertEqual(101, self.read()["lessons"][-1]["session_id"])

    def test_approved_not_reflected_is_plain_fact_with_warning_and_no_schedule_mutation(self):
        self.publish(base_snapshot([request()]))
        self.publish(base_snapshot([request(status="approved")]))
        state = self.read()
        self.assertEqual(3, len(state["lessons"]))
        self.assertFalse(any(item.get("adjustment") for item in state["lessons"]))
        self.assertIn("approved_not_reflected", {item["code"] for item in state["warnings"]})
        self.assertEqual("2026-09-20", self.conn.execute("SELECT session_date FROM class_offering_sessions WHERE id=101").fetchone()[0])

    def test_cancel_middle_session_does_not_shift_following_lessons(self):
        change = request(original=slot("2026-09-26", (8, 9)), kind="cancel")
        self.publish(base_snapshot([change]))
        self.assertEqual(3, len(self.read()["lessons"]))
        change["status"] = "approved"
        snapshot = base_snapshot([change])
        snapshot["official"].pop(1)
        self.publish(snapshot)
        rows = [tuple(row) for row in self.conn.execute("SELECT id,order_index,schedule_status FROM class_offering_sessions WHERE class_offering_id=10 ORDER BY id")]
        self.assertEqual([(101, 1, "scheduled"), (102, 2, "cancelled"), (103, 3, "scheduled")], rows)
        self.assertEqual([101, 103], [row["session_id"] for row in self.read()["lessons"]])

    def test_room_change_has_one_card_then_updates_room_only_when_approved_and_reflected(self):
        change = request(proposed=slot("2026-09-20", room="B210"))
        self.publish(base_snapshot([change]))
        lesson = self.read()["lessons"][0]
        self.assertEqual(("room", "B416", None), (lesson["adjustment"]["kind"], lesson["classroom"], lesson["adjustment"]["counterpart_event_key"]))
        self.assertEqual([], self.read()["predicted_lessons"])
        snapshot = base_snapshot([change])
        snapshot["requests"][0]["status"] = "approved"
        snapshot["official"][0]["room"] = "B210"
        self.publish(snapshot)
        self.assertEqual("B210", self.conn.execute("SELECT academic_location FROM class_offering_sessions WHERE id=101").fetchone()[0])

    def test_merged_four_periods_split_by_known_session_and_approved_endpoint(self):
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-09-13',academic_section_text='4-5',weekday=6,week_index=2 WHERE id=102")
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-09-12',academic_section_text='6-7',weekday=5,week_index=2 WHERE id=101")
        change = request(status="approved", original=slot("2026-09-12", (6, 7)), proposed=slot("2026-09-13", (6, 7)))
        self.publish({"official": [official("2026-09-13", (4, 5, 6, 7))], "requests": [change]})
        lessons = self.read()["lessons"]
        self.assertEqual([[4, 5], [6, 7]], [row["sections"] for row in lessons])
        self.assertEqual([102, 101], [row["session_id"] for row in lessons])

    def test_unexplained_date_change_is_unbound_and_does_not_renumber(self):
        snapshot = base_snapshot()
        snapshot["official"][0] = official("2026-10-11")
        self.publish(snapshot)
        last = self.read()["lessons"][-1]
        self.assertIsNone(last["session_id"])
        self.assertEqual("unresolved", last["binding_status"])
        self.assertEqual("", last["classroom_url"])
        self.assertEqual("2026-09-20", self.conn.execute("SELECT session_date FROM class_offering_sessions WHERE id=101").fetchone()[0])

    def test_pending_conflict_does_not_invent_unique_target(self):
        self.publish(base_snapshot([request(), request("R2", proposed=slot("2026-10-10"))]))
        self.assertEqual([], self.read()["predicted_lessons"])
        self.assertIn("pending_conflict", {item["code"] for item in self.read()["warnings"]})

    def test_request_missing_removes_active_projection_but_retains_unknown_history(self):
        self.publish(base_snapshot([request()]))
        self.publish(base_snapshot())
        self.assertEqual([], self.read()["predicted_lessons"])
        self.assertEqual("missing_unconfirmed", self.read()["request_history"][0]["presence"])
        self.assertEqual("missing_unconfirmed", self.conn.execute("SELECT status FROM academic_schedule_change_session_links").fetchone()[0])

    def test_rejected_request_does_not_predict_or_move(self):
        self.publish(base_snapshot([request(status="rejected")]))
        self.assertFalse(any(item.get("adjustment") for item in self.read()["lessons"]))
        self.assertEqual("2026-09-20", self.conn.execute("SELECT session_date FROM class_offering_sessions WHERE id=101").fetchone()[0])

    def test_wrong_teacher_scope_never_binds_other_teachers_sessions(self):
        snapshot = base_snapshot([request()])
        self.publish(snapshot, teacher_id=2, semester=2)
        result = load_teacher_prediction_snapshot(self.conn, 2, 2)
        self.assertTrue(all(row["session_id"] is None for row in result["lessons"]))
        self.assertEqual([], load_authorized_prediction_lessons(self.conn, [10])["lessons"])

    def test_scope_and_date_validation_preserves_last_success(self):
        self.publish(base_snapshot())
        before = self.read()
        for invalid in ({**base_snapshot(), "teacher_id": 2}, {**base_snapshot(), "semester_id": 3},
                        {**base_snapshot(), "complete": False}, {"official": base_snapshot()["official"]}):
            lease = claim_schedule_sync(self.conn, 1, now=self.now)
            with self.assertRaises(ScheduleSnapshotError):
                reconcile_and_publish_snapshot(self.conn, 1, 1, invalid, lease["token"], now=self.now)
            fail_schedule_sync(self.conn, 1, lease["token"], "失败")
            self.conn.commit()
            self.assertEqual(before["lessons"], self.read()["lessons"])
            self.assertEqual(before["sync_state"]["revision"], self.read()["sync_state"]["revision"])
        invalid = base_snapshot()
        invalid["official"][0]["weekday"] = 1
        lease = claim_schedule_sync(self.conn, 1, now=self.now)
        with self.assertRaises(ScheduleSnapshotError):
            reconcile_and_publish_snapshot(self.conn, 1, 1, invalid, lease["token"], now=self.now)

    def test_teacher_wide_lease_fences_other_terms_and_stale_worker(self):
        first = claim_schedule_sync(self.conn, 1, now=self.now)
        self.conn.commit()
        self.assertEqual("busy", claim_schedule_sync(self.conn, 1, now=self.now)["status"])
        second = claim_schedule_sync(self.conn, 1, now=self.now + timedelta(seconds=601))
        self.conn.commit()
        self.assertEqual("claimed", second["status"])
        self.assertFalse(release_schedule_sync(self.conn, 1, first["token"]))
        with self.assertRaises(ScheduleSyncLeaseError):
            reconcile_and_publish_snapshot(self.conn, 1, 1, base_snapshot(), first["token"], now=self.now + timedelta(seconds=602))
        self.assertIsNone(self.read())
        reconcile_and_publish_snapshot(self.conn, 1, 1, base_snapshot(), second["token"], now=self.now + timedelta(seconds=602))

    def test_expired_lease_cannot_publish_without_replacement_worker(self):
        lease = claim_schedule_sync(self.conn, 1, now=self.now)
        with self.assertRaises(ScheduleSyncLeaseError):
            reconcile_and_publish_snapshot(self.conn, 1, 1, base_snapshot(), lease["token"], now=self.now + timedelta(seconds=601))
        self.assertIsNone(self.read())

    def test_published_token_cannot_publish_a_second_snapshot(self):
        lease = claim_schedule_sync(self.conn, 1, now=self.now)
        reconcile_and_publish_snapshot(self.conn, 1, 1, base_snapshot(), lease["token"], now=self.now)
        with self.assertRaises(ScheduleSyncLeaseError):
            reconcile_and_publish_snapshot(self.conn, 1, 1, base_snapshot(), lease["token"], now=self.now)
        self.assertEqual(1, self.read()["sync_state"]["revision"])

    def test_atomic_publication_and_caller_controls_commit(self):
        self.publish(base_snapshot())
        lease = claim_schedule_sync(self.conn, 1, now=self.now)
        self.conn.commit()
        snapshot = base_snapshot([request(status="approved")])
        snapshot["official"][0] = official("2026-10-11")
        reconcile_and_publish_snapshot(self.conn, 1, 1, snapshot, lease["token"], now=self.now)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(1, self.read()["sync_state"]["revision"])
        self.assertEqual("2026-09-20", self.conn.execute("SELECT session_date FROM class_offering_sessions WHERE id=101").fetchone()[0])

    def test_student_scope_term_and_fully_cancelled_coverage(self):
        self.publish(base_snapshot([request()]))
        result = load_authorized_prediction_lessons(self.conn, [10, 20], semester_id=1)
        self.assertEqual([10], result["covered_offering_ids"])
        self.assertTrue(all(row["class_offering_id"] == 10 for row in result["lessons"]))
        self.assertEqual([], load_authorized_prediction_lessons(self.conn, [20])["lessons"])
        self.assertEqual([], load_authorized_prediction_lessons(self.conn, [10], academic_year="2027-2028")["lessons"])
        self.assertEqual([], load_authorized_prediction_lessons(self.conn, [10], term="2")["lessons"])
        self.publish({"official": [], "requests": []})
        empty = load_authorized_prediction_lessons(self.conn, [10])
        self.assertEqual([], empty["lessons"])
        self.assertEqual([10], empty["covered_offering_ids"])
        self.assertEqual([1], [row["semester_id"] for row in load_teacher_prediction_terms(self.conn, 1)])

    def test_read_helpers_old_schema_no_ddl(self):
        conn = sqlite3.connect(":memory:")
        try:
            self.assertIsNone(load_teacher_prediction_snapshot(conn, 1, 1))
            self.assertEqual([], load_teacher_prediction_terms(conn, 1))
            self.assertEqual([], load_authorized_prediction_lessons(conn, [10])["lessons"])
            self.assertEqual([], conn.execute("SELECT name FROM sqlite_master").fetchall())
        finally:
            conn.close()

    def test_student_semester_aliases_and_summer_are_exact(self):
        self.publish(base_snapshot())
        for alias in ("2026-2027学年第1学期", "2026-2027-1", "2026-2027第一学期"):
            self.conn.execute("UPDATE academic_semesters SET name=? WHERE id=1", (alias,))
            self.assertEqual([10], load_authorized_prediction_lessons(self.conn, [10], academic_year="2026-2027", term="1")["covered_offering_ids"])
            self.assertEqual([], load_authorized_prediction_lessons(self.conn, [10], academic_year="2026-2027", term="3")["lessons"])
        self.conn.execute("UPDATE academic_semesters SET name='2026-2027第三学期' WHERE id=1")
        self.assertEqual([10], load_authorized_prediction_lessons(self.conn, [10], academic_year="2026-2027", term="3")["covered_offering_ids"])
        self.assertEqual([], load_authorized_prediction_lessons(self.conn, [10], term="1")["lessons"])

    def test_duplicate_identical_rows_do_not_count_twice(self):
        snapshot = base_snapshot([request(), request()])
        snapshot["official"].append(copy.deepcopy(snapshot["official"][0]))
        result = self.publish(snapshot)
        self.assertEqual((3, 1, 1), (result["official_count"], result["predicted_count"], result["request_count"]))

    def test_two_independent_workers_cannot_claim_same_teacher(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "isolated-schedule.db"
            target = sqlite3.connect(path)
            self.conn.backup(target)
            target.close()
            barrier = threading.Barrier(2)

            def claim():
                conn = sqlite3.connect(path, timeout=5)
                conn.row_factory = sqlite3.Row
                try:
                    barrier.wait(timeout=5)
                    result = claim_schedule_sync(conn, 1, now=self.now)
                    conn.commit()
                    return result["status"]
                finally:
                    conn.close()

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(claim) for _ in range(2)]
                self.assertEqual(["busy", "claimed"], sorted(future.result(timeout=10) for future in futures))

    def test_failure_after_session_update_rolls_back_entire_publication(self):
        self.publish(base_snapshot())
        self.conn.execute("CREATE TRIGGER reject_snapshot BEFORE UPDATE ON teacher_academic_schedule_snapshots BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
        lease = claim_schedule_sync(self.conn, 1, now=self.now)
        self.conn.commit()
        snapshot = base_snapshot([request(status="approved")])
        snapshot["official"][0] = official("2026-10-11")
        with self.assertRaises(sqlite3.IntegrityError):
            reconcile_and_publish_snapshot(self.conn, 1, 1, snapshot, lease["token"], now=self.now)
        self.conn.commit()
        self.assertEqual("2026-09-20", self.conn.execute("SELECT session_date FROM class_offering_sessions WHERE id=101").fetchone()[0])
        self.assertEqual(1, self.read()["sync_state"]["revision"])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM academic_schedule_change_session_links").fetchone()[0])

    def test_manual_schedule_edit_after_pending_is_not_overwritten_by_approval(self):
        self.publish(base_snapshot([request()]))
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-10-12' WHERE id=101")
        self.conn.commit()
        snapshot = base_snapshot([request(status="approved")])
        snapshot["official"][0] = official("2026-10-11")
        self.publish(snapshot)
        self.assertEqual("2026-10-12", self.conn.execute("SELECT session_date FROM class_offering_sessions WHERE id=101").fetchone()[0])
        self.assertIn("local_schedule_conflict", {item["code"] for item in self.read()["warnings"]})
        self.assertIsNone(self.read()["lessons"][-1]["session_id"])

    def test_conflicting_approved_targets_never_choose_by_list_order(self):
        changes = [request(status="approved"), request("R2", status="approved", proposed=slot("2026-10-10"))]
        snapshot = {"official": [official("2026-10-11"), official("2026-10-10")], "requests": changes}
        for order in (changes, list(reversed(changes))):
            snapshot["requests"] = order
            result = self.publish(snapshot)
            self.assertEqual([], result["updated_session_ids"])
            self.assertEqual("2026-09-20", self.conn.execute("SELECT session_date FROM class_offering_sessions WHERE id=101").fetchone()[0])
            self.assertEqual({"R1", "R2"}, {item["request_id"] for item in self.read()["warnings"] if item["code"] == "approved_conflict"})
            self.assertTrue(all(item["session_id"] is None for item in self.read()["lessons"]))

    def test_identical_approved_targets_deduplicate_schedule_update(self):
        snapshot = {"official": [official("2026-10-11")], "requests": [request(status="approved"), request("R2", status="approved")]}
        self.assertEqual([101], self.publish(snapshot)["updated_session_ids"])
        self.assertEqual(101, self.read()["lessons"][0]["session_id"])
        self.assertNotIn("approved_conflict", {item["code"] for item in self.read()["warnings"]})

    def test_manual_room_conflict_keeps_binding_baseline_across_repeated_syncs(self):
        change = request(proposed=slot("2026-09-20", room="C108"))
        self.publish(base_snapshot([change]))
        before = dict(self.conn.execute("SELECT * FROM academic_schedule_session_bindings WHERE session_id=101").fetchone())
        self.conn.execute("UPDATE class_offering_sessions SET academic_location='MANUAL_ROOM' WHERE id=101")
        self.conn.commit()
        change["status"] = "approved"
        snapshot = base_snapshot([change])
        snapshot["official"][0]["room"] = "C108"
        # An intervening complete snapshot without the request must not wash
        # the baseline either, even though the date/period still match.
        for requests in ([change], [], [change], [change]):
            snapshot["requests"] = requests
            self.publish(snapshot)
            self.assertEqual("MANUAL_ROOM", self.conn.execute("SELECT academic_location FROM class_offering_sessions WHERE id=101").fetchone()[0])
            self.assertIn("local_schedule_conflict", {item["code"] for item in self.read()["warnings"]})
            self.assertEqual(before, dict(self.conn.execute("SELECT * FROM academic_schedule_session_bindings WHERE session_id=101").fetchone()))

    def test_unknown_application_state_is_visible_without_prediction(self):
        change = request(status="unknown")
        change["raw_status"] = "new_remote_code"
        self.publish(base_snapshot([change]))
        self.assertEqual([], self.read()["predicted_lessons"])
        self.assertTrue(any(row["code"] == "unknown_request_status" and row["request_id"] == "R1" for row in self.read()["warnings"]))

    def test_pending_target_overlap_warns_but_keeps_uncounted_prediction(self):
        self.publish(base_snapshot([request(proposed=slot("2026-09-27"))]))
        result = self.read()
        self.assertEqual(1, len(result["predicted_lessons"]))
        self.assertFalse(result["predicted_lessons"][0]["counts_towards_total"])
        self.assertEqual(101, result["predicted_lessons"][0]["session_id"])
        self.assertEqual(3, sum(row["counts_towards_total"] for row in result["lessons"]))
        self.assertIn("pending_target_conflict", {row["code"] for row in result["warnings"]})

    def test_pending_target_already_same_session_official_is_not_drawn_twice(self):
        self.publish(base_snapshot([request()]))
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-10-11' WHERE id=101")
        self.conn.commit()
        snapshot = base_snapshot([request()])
        snapshot["official"].append(official("2026-10-11"))
        self.publish(snapshot)
        result = self.read()
        self.assertEqual([], result["predicted_lessons"])
        target = [row for row in result["lessons"] if row["actual_date"] == "2026-10-11"]
        self.assertEqual(1, len(target))
        self.assertEqual(101, target[0]["session_id"])
        self.assertIn("pending_target_already_official", {row["code"] for row in result["warnings"]})


if __name__ == "__main__":
    unittest.main()
