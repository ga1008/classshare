from contextlib import closing
import sqlite3
import unittest

from classroom_app.services.interaction_expression_opportunities import load_interaction_expression_opportunities
from classroom_app.time_utils import APP_TIMEZONE, to_local_datetime


START = "2026-11-11T09:00:00"
END = "2026-11-11T10:00:00"
LATER = "2026-11-12T12:00:00"
FUTURE = "2026-11-15T12:00:00"


class InteractionOpportunityTests(unittest.TestCase):
    def setUp(self):
        self.db = self.enterContext(closing(sqlite3.connect(":memory:")))
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE class_offerings (id INTEGER PRIMARY KEY,class_id INTEGER);
            CREATE TABLE class_offering_class_links (offering_id INTEGER,class_id INTEGER);
            CREATE TABLE students (id INTEGER PRIMARY KEY,class_id INTEGER,enrollment_status TEXT,control_mode TEXT);
            CREATE TABLE wanderer_agents (student_id INTEGER);
            CREATE TABLE classroom_live_activities (id INTEGER PRIMARY KEY,class_offering_id INTEGER,kind TEXT,
                status TEXT,started_at TEXT,closed_at TEXT);
            CREATE TABLE classroom_live_responses(activity_id INTEGER,student_id INTEGER,created_at TEXT,is_anonymous INTEGER);
            CREATE TABLE classroom_live_questions(activity_id INTEGER,class_offering_id INTEGER,student_id INTEGER,
                created_at TEXT,status TEXT,is_anonymous INTEGER);
            CREATE TABLE polls(id INTEGER PRIMARY KEY,status TEXT,audience_scope TEXT,created_at TEXT,
                updated_at TEXT,closed_at TEXT,deadline_at TEXT);
            CREATE TABLE poll_assignments(poll_id INTEGER,class_offering_id INTEGER,created_at TEXT);
            CREATE TABLE poll_participants(poll_id INTEGER,student_id INTEGER,created_at TEXT);
            CREATE TABLE poll_ballots(poll_id INTEGER,voter_id INTEGER,created_at TEXT);
            CREATE TABLE classroom_retake_students(class_offering_id INTEGER,student_id INTEGER,status TEXT,confirmed_at TEXT);
            CREATE TABLE study_groups(id INTEGER PRIMARY KEY,class_offering_id INTEGER,status TEXT,archived_at TEXT);
            CREATE TABLE study_group_members(group_id INTEGER,student_id INTEGER,status TEXT,joined_at TEXT,left_at TEXT);
            CREATE TABLE login_attendance_rounds(id INTEGER PRIMARY KEY,class_offering_id INTEGER,
                state TEXT,is_current INTEGER,starts_at_ms INTEGER,ends_at_ms INTEGER);
            CREATE TABLE login_attendance_records(round_id INTEGER,student_id INTEGER,expected INTEGER);
            INSERT INTO class_offerings VALUES(1,10),(2,20);
            INSERT INTO class_offering_class_links VALUES(1,11);
            INSERT INTO students VALUES (1,10,'active','human'),(2,11,'active','human'),
                (3,10,'active','human'),(4,20,'active','human'),(5,10,'withdrawn','human'),
                (6,10,'active','ai'),(7,10,'active','human');
            INSERT INTO wanderer_agents VALUES(7);
        """)

    def load(self):
        return load_interaction_expression_opportunities(self.db, 1, range(1, 8), as_of=LATER)

    def activity(self, ident, kind, status, start=START, end=None, offering=1):
        self.db.execute("INSERT INTO classroom_live_activities VALUES(?,?,?,?,?,?)",
                        (ident, offering, kind, status, start, end))

    def roster(self, *students):
        bounds = [int(to_local_datetime(t).replace(tzinfo=APP_TIMEZONE).timestamp() * 1000) for t in (START, END)]
        self.db.execute("INSERT INTO login_attendance_rounds VALUES(1,1,'published',1,?,?)", bounds)
        self.db.executemany("INSERT INTO login_attendance_records VALUES(1,?,1)", [(s,) for s in students])

    def poll(self, ident, status, audience="custom", *, deadline=None, closed=None, assigned=START):
        self.db.execute("INSERT INTO polls VALUES(?,?,?,?,?,?,?)",
                        (ident, status, audience, START, START, closed, deadline))
        self.db.execute("INSERT INTO poll_assignments VALUES(?,1,?)", (ident, assigned))

    def test_no_opportunities_or_unrelated_enrollments(self):
        self.activity(1, "qna", "active", start=FUTURE)
        self.activity(2, "quiz", "active", offering=2)
        self.poll(1, "draft")
        self.db.execute("INSERT INTO poll_participants VALUES(1,1,?)", (START,))
        records = self.load()
        self.assertEqual("complete", records[1]["coverage_status"])
        self.assertEqual(0, records[1]["expression_offered"])
        self.assertIsNone(records[1]["coverage_ratio"])
        for student in (4, 5, 6, 7):
            self.assertEqual("ineligible", records[student]["coverage_status"])
        self.assertEqual({}, load_interaction_expression_opportunities(self.db, 1, []))

    def test_classroom_window_requires_roster_or_own_work_and_counts_each_activity_once(self):
        self.activity(1, "qna", "closed", end=END)
        self.activity(2, "quiz", "closed", end=END)
        self.activity(3, "qna", "active")
        self.roster(1, 2)
        self.db.executemany("INSERT INTO classroom_live_questions VALUES(1,1,?,?,?,0)",
                            [(1, START, "open"), (1, START, "addressed"), (2, START, "hidden")])
        self.db.execute("INSERT INTO classroom_live_responses VALUES(2,3,?,0)", (START,))
        records = self.load()
        self.assertEqual((2, 1, 1, 2, 0.5), tuple(records[1][k] for k in
                         ("offered", "participated", "open", "expression_offered", "coverage_ratio")))
        self.assertEqual(0, records[2]["participated"])
        self.assertEqual(0.0, records[2]["coverage_ratio"])
        self.assertEqual(1, records[3]["participated"])
        self.assertEqual(1, records[3]["uncertain"])
        self.assertEqual("limited", records[3]["coverage_status"])
        self.assertIsNone(records[3]["coverage_ratio"])

    def test_polls_respect_named_audience_windows_and_retake_exception(self):
        self.roster(1, 2, 3)
        self.poll(1, "active", deadline=END)
        self.poll(2, "closed", closed=END)
        self.poll(3, "active", "class", deadline=END)
        self.poll(4, "draft")
        self.poll(5, "active", "class", assigned=FUTURE)
        self.poll(6, "active", "custom", deadline=FUTURE)
        self.db.executemany("INSERT INTO poll_participants VALUES(?,?,?)",
                            [(1, 1, START), (1, 2, START), (1, 3, LATER),
                             (2, 2, START), (4, 2, START), (6, 2, START)])
        self.db.execute("INSERT INTO poll_ballots VALUES(1,2,?)", (START,))
        self.db.execute("INSERT INTO poll_assignments VALUES(3,2,?)", (START,))
        self.db.execute("INSERT INTO classroom_retake_students VALUES(2,2,'confirmed',?)", (START,))
        records = self.load()
        self.assertEqual({"offered": 2, "participated": 0, "open": 0, "open_participated": 0},
                         records[1]["by_kind"]["poll"])
        self.assertEqual({"offered": 1, "participated": 1, "open": 1, "open_participated": 0},
                         records[2]["by_kind"]["poll"])
        self.assertEqual(1, records[2]["uncertain"])  # closed draft OR once published
        self.assertIsNone(records[2]["coverage_ratio"])
        self.assertEqual(1, records[3]["offered"])
        self.assertEqual(0, records[2]["expression_offered"])

    def test_class_exemption_after_poll_end_does_not_revoke_a_verified_historical_opportunity(self):
        self.roster(1, 2, 3)
        self.poll(1, "active", "class", deadline=END)
        self.db.executemany("INSERT INTO classroom_retake_students VALUES(1,?,'confirmed',?)",
                            [(1, START), (2, LATER), (3, None)])
        records = self.load()
        self.assertEqual(0, records[1]["offered"])
        self.assertEqual(1, records[2]["offered"])
        self.assertEqual(0.0, records[2]["coverage_ratio"])
        self.assertEqual(1, records[3]["uncertain"])

    def test_shared_ballot_and_open_answers_are_attributed_only_when_course_is_known(self):
        self.poll(10, "active", "class")
        self.db.execute("INSERT INTO poll_assignments VALUES (10,2,?)", (START,))
        self.db.execute("INSERT INTO class_offering_class_links VALUES (2,11)")
        self.db.executemany("INSERT INTO poll_ballots VALUES(10,?,?)", [(1, START), (2, START)])
        self.activity(1, "quiz", "active")
        self.db.execute("INSERT INTO classroom_live_responses VALUES(1,1,?,0)", (START,))
        result = self.load()
        self.assertEqual((0, 0, 2, 2), tuple(result[1][k] for k in
                         ("offered", "participated", "open", "open_participated")))
        self.assertEqual(0, result[2]["by_kind"]["poll"]["open_participated"])
        self.assertEqual(1, result[2]["uncertain"])
        other = load_interaction_expression_opportunities(self.db, 2, [2], as_of=LATER)[2]
        self.assertEqual((0, 1), (other["open_participated"], other["uncertain"]))

    def test_two_hundred_students_use_same_number_of_queries_as_one(self):
        students = range(100, 300)
        self.db.executemany("INSERT INTO students VALUES(?,10,'active','human')", [(s,) for s in students])
        self.poll(10, "active", "class")
        self.activity(1, "qna", "active")
        counts = []
        for requested in ([100], students):
            statements = []
            self.db.set_trace_callback(statements.append)
            try:
                result = load_interaction_expression_opportunities(self.db, 1, requested, as_of=LATER)
            finally:
                self.db.set_trace_callback(None)
            self.assertEqual(2, result[100]["open"])
            counts.append(len(statements))
        self.assertEqual(counts[0], counts[1])
        self.assertLessEqual(counts[0], 13)

    def test_anonymous_participation_and_proof_of_eligibility_remain_in_self_projection(self):
        self.activity(1, "qna", "closed", end=END)
        self.activity(2, "quiz", "closed", end=END)
        self.db.execute("INSERT INTO classroom_live_questions VALUES(1,1,1,?,'addressed',1)", (START,))
        self.db.execute("INSERT INTO classroom_live_responses VALUES(2,1,?,1)", (START,))
        staff = self.load()[1]
        self.assertEqual(0, staff["offered"])
        self.assertEqual(0, staff["participated"])
        self.assertEqual(2, staff["uncertain"])
        own = load_interaction_expression_opportunities(self.db, 1, [1], as_of=LATER, include_anonymous=True)[1]
        self.assertEqual(2, own["participated"])
        self.assertEqual(1.0, own["coverage_ratio"])

    def test_group_channels_have_membership_windows_and_never_enter_denominator(self):
        self.db.execute("INSERT INTO study_groups VALUES(1,1,'active',NULL)")
        self.db.executemany("INSERT INTO study_group_members VALUES(1,?,?,?,?)",
                            [(1, "active", START, None), (2, "removed", START, END),
                             (3, "active", FUTURE, None)])
        records = self.load()
        self.assertEqual([1, 0, 0], [records[s]["group_channels"] for s in (1, 2, 3)])
        self.assertFalse(records[1]["offered"] or records[1]["expression_offered"])

    def test_missing_optional_source_is_limited_and_never_a_published_zero(self):
        self.db.execute("DROP TABLE classroom_live_questions")
        self.roster(1)
        self.activity(1, "qna", "closed", end=END)
        item = self.load()[1]
        self.assertEqual(["classroom_live_questions"], item["missing_sources"])
        self.assertEqual("limited", item["coverage_status"])
        self.assertIsNone(item["coverage_ratio"])
