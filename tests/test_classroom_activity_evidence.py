"""Evidence semantics against isolated synthetic SQLite, never application data."""
from datetime import datetime, timedelta
import json
import sqlite3
import unittest

from classroom_app.db.schema_behavior_collection_health import ensure_behavior_collection_health_schema
from classroom_app.services.behavior_collection_health_service import record_collection_observation
from classroom_app.services.classroom_activity_evidence_service import (
    CLASSROOM_TIMEZONE, build_classroom_activity_evidence,
)


class ClassroomActivityEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            CREATE TABLE class_offerings(id INTEGER PRIMARY KEY, class_id INTEGER);
            CREATE TABLE class_offering_class_links(id INTEGER PRIMARY KEY, offering_id INTEGER, class_id INTEGER, is_primary INTEGER);
            CREATE TABLE classes(id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE students(id INTEGER PRIMARY KEY, name TEXT, student_id_number TEXT, class_id INTEGER, enrollment_status TEXT);
            CREATE INDEX idx_test_students_class ON students(class_id);
            CREATE TABLE classroom_retake_students(class_offering_id INTEGER,student_id INTEGER,status TEXT,
                UNIQUE(class_offering_id,student_id));
            CREATE TABLE student_login_audit_logs(student_id INTEGER, logged_at TEXT, ip_address TEXT);
            CREATE INDEX idx_test_login_actor ON student_login_audit_logs(student_id,logged_at);
            CREATE TABLE classroom_behavior_states(class_offering_id INTEGER,user_pk INTEGER,user_role TEXT,presence_coverage_json TEXT);
            CREATE TABLE classroom_behavior_events(class_offering_id INTEGER,user_pk INTEGER,user_role TEXT,action_type TEXT,created_at TEXT);
            CREATE INDEX idx_test_events_actor ON classroom_behavior_events(class_offering_id,user_pk,user_role,created_at);
            CREATE TABLE behavior_write_inbox(class_offering_id INTEGER,user_pk INTEGER,user_role TEXT,state TEXT,received_at TEXT);
            CREATE INDEX idx_test_inbox_actor ON behavior_write_inbox(user_role,user_pk,state,received_at);
            INSERT INTO class_offerings VALUES(10,1),(11,1);
            INSERT INTO classes VALUES(1,'Primary'),(2,'Linked'),(3,'Retake only'),(4,'Not linked');
            -- The link intentionally omits the primary: both must remain visible.
            INSERT INTO class_offering_class_links VALUES(1,10,2,1);
            INSERT INTO students VALUES
                (1,'Measured','S1',1,'active'),(2,'Observed zero','S2',2,'active'),
                (3,'Only login','S3',1,'active'),(4,'Only server actions','S4',2,'active'),
                (5,'Invalid collection','S5',1,'active'),(6,'Confirmed retake','S6',3,'active'),
                (7,'Suggested retake','S7',3,'active'),(8,'Inactive','S8',1,'inactive'),
                (9,'Other classroom','S9',4,'active');
            INSERT INTO classroom_retake_students VALUES(10,6,'confirmed'),(10,7,'suggested');
        """)
        ensure_behavior_collection_health_schema(self.conn, engine="sqlite")
        self.start = datetime(2026, 11, 8, 8, tzinfo=CLASSROOM_TIMEZONE)
        self.end = self.start + timedelta(hours=4)

    def stamp(self, seconds):
        return (self.start + timedelta(seconds=seconds)).replace(tzinfo=None).isoformat()

    def ms(self, seconds):
        return int((self.start + timedelta(seconds=seconds)).timestamp() * 1000)

    def coverage(self, student, ranges=None, reference=15000, raw=None):
        data = raw if raw is not None else json.dumps({
            "reference": self.ms(reference), "ranges": {"learning": ranges or []},
        })
        self.conn.execute("INSERT INTO classroom_behavior_states VALUES(10,?,'student',?)", (student, data))

    def observe(self, student, *, source="browser", valid=1, rejected=0, start=0, end=30, received=30,
                reasons=None, batch=None, course=10, diagnostics=None):
        record_collection_observation(
            self.conn, class_offering_id=course, user_pk=student, user_role="student",
            batch_id=batch or f"observation_{student}_{source}_{received}", source=source,
            received_at=self.stamp(received), valid_sample_count=valid, rejected_sample_count=rejected,
            learning_delta_seconds=0, observed_start=self.stamp(start) if start is not None else None,
            observed_end=self.stamp(end) if end is not None else None, reason_codes=reasons, diagnostics=diagnostics,
        )

    def report(self, **kwargs):
        return build_classroom_activity_evidence(self.conn, 10, self.start, self.end, **kwargs)

    def by_id(self, report):
        return {item["id"]: item for item in report["students"]}

    def test_login_access_valid_zero_and_unknown_are_separate_channels(self):
        self.conn.execute("INSERT INTO student_login_audit_logs VALUES(3,?,?)", (self.stamp(60), "192.0.2.3"))
        self.conn.execute("INSERT INTO behavior_write_inbox VALUES(10,4,'student','applied',?)", (self.stamp(90),))
        self.conn.execute("INSERT INTO classroom_behavior_events VALUES(10,4,'student','page_view',?)", (self.stamp(90),))
        self.conn.execute("INSERT INTO classroom_behavior_events VALUES(10,4,'student','assignment_submit',?)", (self.stamp(120),))
        self.coverage(1, [[self.ms(-30), self.ms(30)], [self.ms(600), self.ms(720)], [self.ms(14370), self.ms(14430)]])
        self.coverage(2)
        self.observe(1)
        self.observe(2)
        self.observe(5, valid=0, rejected=1, start=None, end=None, reasons=["presence_interval_invalid"])
        self.observe(4, source="server", valid=0, start=None, end=None)
        result = self.report()
        students = self.by_id(result)
        self.assertEqual({1, 2, 3, 4, 5, 6}, set(students))
        self.assertTrue(students[6]["confirmed_retake"])
        self.assertEqual(180, students[1]["learning"]["recorded_seconds"])
        self.assertEqual("measured", students[1]["collection"]["status"])
        self.assertEqual("observed_zero", students[2]["collection"]["status"])
        self.assertEqual(0, students[2]["learning"]["measured_seconds"])
        self.assertEqual(1, students[3]["login"]["successful_count"])
        self.assertIsNone(students[3]["classroom_access"]["first_at"])
        self.assertIsNone(students[3]["learning"]["measured_seconds"])
        self.assertEqual("no_observation", students[4]["collection"]["status"])
        self.assertEqual(1, students[4]["classroom_access"]["operation_record_count"])
        self.assertEqual("error", students[5]["collection"]["status"])
        self.assertEqual(1, result["summary"]["observed_zero_students"])
        self.assertEqual(1, result["summary"]["successful_login_students"])
        self.assertEqual(1, result["summary"]["classroom_access_students"])
        self.assertEqual(30, result["summary"]["average_recorded_seconds_all"])
        self.assertEqual(180, result["summary"]["average_recorded_seconds_positive"])
        self.assertNotIn("192.0.2.3", json.dumps(result))
        self.assertEqual(["192.0.2.3"], self.by_id(self.report(include_login_ips=True))[3]["login"]["ip_addresses"])

    def test_diagnostics_never_create_access_or_measured_zero(self):
        self.observe(1, source="client_diagnostic", valid=0, start=None, end=None, reasons=["http_401"])
        student = self.by_id(self.report())[1]
        self.assertEqual("gap", student["collection"]["status"])
        self.assertEqual(1, student["collection"]["diagnostic_count"])
        self.assertIsNone(student["classroom_access"]["first_at"])
        self.assertIsNone(student["learning"]["measured_seconds"])
        self.assertFalse(student["collection"]["is_observed_zero"])

    def test_automatic_focus_visibility_events_are_not_learning_operations(self):
        for action in ("page_focus", "page_visibility"):
            self.conn.execute("INSERT INTO classroom_behavior_events VALUES(10,1,'student',?,?)", (action, self.stamp(30)))
        student = self.by_id(self.report())[1]
        self.assertEqual(0, student["classroom_access"]["operation_record_count"])
        self.assertEqual({"page_focus": 1, "page_visibility": 1}, student["classroom_access"]["action_counts"])
        self.assertIsNone(student["learning"]["measured_seconds"])

    def test_pending_quarantined_and_corrupt_coverage_cannot_be_true_zero(self):
        for student, state in ((1, "pending"), (2, "quarantined"), (3, "discarded")):
            self.conn.execute("INSERT INTO behavior_write_inbox VALUES(10,?,'student',?,?)", (student, state, self.stamp(30)))
            self.coverage(student)
            self.observe(student)
        self.coverage(4, raw="{broken")
        self.observe(4)
        self.observe(5)  # Missing state cannot turn a valid observation into zero.
        students = self.by_id(self.report())
        self.assertEqual("pending", students[1]["collection"]["status"])
        for student in (2, 3, 4, 5):
            self.assertEqual("error", students[student]["collection"]["status"])
            self.assertFalse(students[student]["collection"]["is_observed_zero"])
        self.assertIsNone(students[4]["learning"]["measured_seconds"])
        self.assertIsNone(students[5]["learning"]["measured_seconds"])

    def test_error_and_gap_keep_real_positive_learning_and_do_not_sum_batch_deltas(self):
        self.coverage(1, [[self.ms(0), self.ms(90)]])
        self.observe(1, reasons=["presence_session_clipped"])
        self.observe(1, batch="second_copy", received=40)  # Ranges remain the union, not sum of batches.
        self.coverage(2, [[self.ms(0), self.ms(30)]])
        self.observe(2, reasons=["presence_coverage_capacity"])
        students = self.by_id(self.report())
        self.assertEqual(90, students[1]["learning"]["recorded_seconds"])
        self.assertEqual("gap", students[1]["collection"]["status"])
        self.assertEqual("error", students[2]["collection"]["status"])
        self.assertEqual(30, students[2]["learning"]["recorded_seconds"])

    def test_delayed_replay_is_scoped_by_observation_and_not_double_counted(self):
        self.coverage(1, [[self.ms(0), self.ms(30)]])
        self.observe(1, received=86430)
        self.coverage(2)
        self.observe(2, start=-86400, end=-86370, received=60)
        students = self.by_id(self.report())
        self.assertEqual("measured", students[1]["collection"]["status"])
        self.assertEqual(1, students[1]["collection"]["valid_sample_count_in_intersecting_batches"])
        self.assertEqual("no_observation", students[2]["collection"]["status"])
        # A row selected by both union branches is still counted once.
        self.observe(1, batch="received_here", received=40)
        self.assertEqual(2, self.by_id(self.report())[1]["collection"]["valid_sample_count_in_intersecting_batches"])

    def test_late_diagnostic_uses_bounded_client_claim_and_never_becomes_presence(self):
        self.observe(1, source="client_diagnostic", valid=0, start=None, end=None, received=86430,
                     reasons=["http_401"], diagnostics={"observed_ms": self.ms(30)})
        # The retry failed the next day, but refers to an affected interval in
        # this class window. That too marks uncertainty, never credited time.
        self.observe(2, source="client_diagnostic", valid=0, start=None, end=None, received=86430,
                     reasons=["network_error"], diagnostics={"observed_ms": self.ms(86430),
                     "interval_start_ms": self.ms(0), "interval_end_ms": self.ms(30)})
        students = self.by_id(self.report())
        for student in (1, 2):
            self.assertEqual("gap", students[student]["collection"]["status"])
            self.assertEqual(1, students[student]["collection"]["diagnostic_client_claim_time_count"])
            self.assertEqual(0, students[student]["collection"]["valid_sample_count_in_intersecting_batches"])
            self.assertIsNone(students[student]["classroom_access"]["first_at"])
            self.assertIsNone(students[student]["learning"]["measured_seconds"])
        next_day = build_classroom_activity_evidence(self.conn, 10, self.start + timedelta(days=1), self.end + timedelta(days=1))
        self.assertEqual("no_observation", self.by_id(next_day)[1]["collection"]["status"])

    def test_future_or_expired_diagnostic_clock_falls_back_to_received_window(self):
        for student, claimed in ((1, 10 * 86400), (2, -10 * 86400)):
            self.observe(student, source="client_diagnostic", valid=0, start=None, end=None, received=86430,
                         reasons=["http_409"], diagnostics={"observed_ms": self.ms(claimed),
                         "interval_start_ms": self.ms(0), "interval_end_ms": self.ms(30)})
        students = self.by_id(self.report())
        self.assertEqual("no_observation", students[1]["collection"]["status"])
        self.assertEqual("no_observation", students[2]["collection"]["status"])
        received = self.by_id(build_classroom_activity_evidence(
            self.conn, 10, self.start + timedelta(days=1), self.end + timedelta(days=1),
        ))
        for student in (1, 2):
            self.assertEqual("gap", received[student]["collection"]["status"])
            self.assertEqual(1, received[student]["collection"]["diagnostic_received_time_count"])
            self.assertIn("client_clock_out_of_range", received[student]["collection"]["reason_codes"])
            self.assertFalse(received[student]["collection"]["is_observed_zero"])

    def test_half_open_window_and_mixed_timestamp_separators_across_midnight(self):
        start = self.end.replace(hour=23)
        end = start + timedelta(hours=2)
        stamps = ["2026-11-08 22:59:59", "2026-11-08T22:59:59", "2026-11-08 23:00:00",
                  "2026-11-08T23:30:00", "2026-11-09 00:30:00", "2026-11-09T00:59:59",
                  "2026-11-09 01:00:00", "2026-11-09T01:00:00"]
        self.conn.executemany("INSERT INTO student_login_audit_logs VALUES(1,?,NULL)", [(stamp,) for stamp in stamps])
        report = build_classroom_activity_evidence(self.conn, 10, start, end)
        login = self.by_id(report)[1]["login"]
        self.assertEqual(4, login["successful_count"])
        self.assertEqual("2026-11-08T23:00:00+08:00", login["first_at"])
        self.assertEqual("2026-11-09T00:59:59+08:00", login["last_at"])

    def test_wide_batch_envelope_with_no_sample_in_middle_window_is_not_observed_zero(self):
        self.coverage(1)
        self.observe(1, valid=2, start=3600, end=10800, received=10800)
        report = build_classroom_activity_evidence(
            self.conn, 10, self.start + timedelta(hours=2), self.start + timedelta(hours=2, minutes=30),
        )
        student = self.by_id(report)[1]
        self.assertEqual(2, student["collection"]["valid_sample_count_in_intersecting_batches"])
        self.assertEqual(0, student["collection"]["contained_valid_sample_count"])
        self.assertEqual("gap", student["collection"]["status"])
        self.assertIsNone(student["learning"]["measured_seconds"])
        self.assertFalse(student["collection"]["is_observed_zero"])

    def test_cross_boundary_batch_and_touching_intervals_cannot_create_observed_zero(self):
        self.coverage(1)
        self.observe(1, start=-30, end=30)
        self.coverage(2)
        self.observe(2, start=-90, end=0, received=0)
        self.coverage(3)
        self.observe(3, start=14400, end=14430, received=14430)
        students = self.by_id(self.report())
        self.assertEqual("gap", students[1]["collection"]["status"])
        self.assertIn("observation_window_partial", students[1]["collection"]["reason_codes"])
        for student in (2, 3):
            self.assertEqual("no_observation", students[student]["collection"]["status"])
            self.assertEqual(0, students[student]["collection"]["contained_valid_sample_count"])
        self.assertEqual(0, self.report()["summary"]["observed_zero_students"])

    def test_old_ranges_and_subsecond_intervals_are_not_misclassified_as_real_zero(self):
        self.coverage(1, [[self.ms(10), self.ms(10) + 250]])
        self.coverage(2, reference=9 * 86400)
        students = self.by_id(self.report())
        self.assertEqual(0, students[1]["learning"]["recorded_seconds"])
        self.assertEqual(250, students[1]["learning"]["recorded_milliseconds"])
        self.assertEqual("measured", students[1]["collection"]["status"])
        self.assertEqual("gap", students[2]["collection"]["status"])
        self.assertIn("coverage_retention_gap", students[2]["collection"]["reason_codes"])

    def test_read_only_batched_query_count_and_classroom_actor_isolation(self):
        self.conn.executemany("INSERT INTO students VALUES(?,?,?,1,'active')",
                              [(i, f"Synthetic {i}", f"X{i}") for i in range(100, 700)])
        self.conn.execute("INSERT INTO behavior_write_inbox VALUES(11,1,'student','applied',?)", (self.stamp(30),))
        self.conn.execute("INSERT INTO classroom_behavior_events VALUES(10,1,'teacher','assignment_submit',?)", (self.stamp(30),))
        self.observe(1, course=11)
        self.conn.commit()
        self.conn.execute("PRAGMA query_only=ON")
        queries = []
        self.conn.set_trace_callback(queries.append)
        result = self.report()
        self.assertEqual(606, result["summary"]["student_count"])
        self.assertLessEqual(len(queries), 19)  # Chunked reads, never one query per pupil.
        self.assertEqual(0, result["summary"]["classroom_access_students"])
        self.assertEqual(606, result["summary"]["collection_status_counts"]["no_observation"])

    def test_window_validation_and_empty_roster_return_unknown_aggregates(self):
        for end in (self.start, self.start - timedelta(seconds=1), self.start + timedelta(hours=25)):
            with self.assertRaises(ValueError):
                build_classroom_activity_evidence(self.conn, 10, self.start, end)
        self.conn.execute("UPDATE students SET enrollment_status='inactive'")
        report = self.report()
        self.assertEqual([], report["students"])
        self.assertIsNone(report["summary"]["average_recorded_seconds_all"])
        self.assertIsNone(report["summary"]["longest_recorded_seconds"])


if __name__ == "__main__":
    unittest.main()
