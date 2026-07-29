"""结课（closeout）服务的单元测试。

关注三件最容易出错、且出错代价最高的事：

1. 扫描出来的"未结束任务"计数必须准确（教师据此做不可逆决策）。
2. 未提交者记默认分是安全的；**已提交未批改的绝不能被静默打分**。
3. 批量结课时单条失败不能拖垮整场。
"""

import sqlite3
import unittest

from classroom_app.services import classroom_closeout_service as closeout


SCHEMA = """
CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE class_offerings (
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL,
    class_id INTEGER NOT NULL,
    teacher_id INTEGER NOT NULL
);
CREATE TABLE students (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL,
    student_id_number TEXT,
    name TEXT,
    enrollment_status TEXT DEFAULT 'active'
);
CREATE TABLE assignments (
    id TEXT PRIMARY KEY,
    course_id INTEGER NOT NULL,
    class_offering_id INTEGER,
    title TEXT,
    status TEXT DEFAULT 'published',
    exam_paper_id TEXT,
    availability_mode TEXT DEFAULT 'permanent',
    starts_at TEXT,
    due_at TEXT,
    auto_close INTEGER DEFAULT 1,
    closed_at TEXT,
    late_submission_enabled INTEGER DEFAULT 0,
    late_submission_until TEXT,
    created_at TEXT DEFAULT '2026-01-01T00:00:00'
);
CREATE TABLE submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id TEXT NOT NULL,
    student_pk_id INTEGER NOT NULL,
    student_name TEXT,
    status TEXT DEFAULT 'submitted',
    score REAL,
    feedback_md TEXT,
    answers_json TEXT,
    grading_started_at TEXT,
    grading_attempt_fingerprint TEXT,
    submitted_by_role TEXT DEFAULT 'student',
    submitted_by_teacher_id INTEGER,
    submission_channel TEXT DEFAULT 'online',
    resubmission_allowed INTEGER DEFAULT 0,
    resubmission_due_at TEXT,
    returned_at TEXT,
    returned_by_teacher_id INTEGER,
    returned_reason TEXT,
    is_absence_score INTEGER DEFAULT 0,
    absence_scored_at TEXT,
    absence_scored_by_teacher_id INTEGER,
    submitted_at TEXT
);
CREATE TABLE polls (
    id INTEGER PRIMARY KEY,
    title TEXT,
    status TEXT DEFAULT 'active',
    deadline_at TEXT
);
CREATE TABLE poll_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_id INTEGER NOT NULL,
    class_offering_id INTEGER NOT NULL
);
CREATE TABLE poll_ballots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_id INTEGER NOT NULL,
    voter_id INTEGER NOT NULL
);
CREATE TABLE group_schemes (
    id INTEGER PRIMARY KEY,
    class_offering_id INTEGER NOT NULL,
    name TEXT,
    status TEXT DEFAULT 'active',
    group_count INTEGER DEFAULT 0,
    expires_at TEXT,
    archived_at TEXT,
    updated_at TEXT
);
CREATE TABLE classroom_live_activities (
    id INTEGER PRIMARY KEY,
    class_offering_id INTEGER NOT NULL,
    kind TEXT,
    title TEXT,
    status TEXT DEFAULT 'active'
);
CREATE TABLE classroom_live_questions (
    id INTEGER PRIMARY KEY,
    activity_id INTEGER NOT NULL,
    status TEXT DEFAULT 'open',
    updated_at TEXT
);
CREATE TABLE classroom_live_help_signals (
    id INTEGER PRIMARY KEY,
    class_offering_id INTEGER NOT NULL,
    status TEXT DEFAULT 'active',
    updated_at TEXT
);
"""

TEACHER = {"id": 1, "role": "teacher", "name": "老师"}
OFFERING_ID = 1001


class ClassroomCloseoutServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

        self.conn.execute("INSERT INTO courses (id, name) VALUES (10, '课程')")
        self.conn.execute("INSERT INTO classes (id, name) VALUES (101, '班级')")
        self.conn.execute(
            "INSERT INTO class_offerings (id, course_id, class_id, teacher_id) VALUES (?, 10, 101, 1)",
            (OFFERING_ID,),
        )
        for pk, number, name in ((201, "S01", "学生甲"), (202, "S02", "学生乙"), (203, "S03", "学生丙")):
            self.conn.execute(
                "INSERT INTO students (id, class_id, student_id_number, name) VALUES (?, 101, ?, ?)",
                (pk, number, name),
            )
        self.conn.execute(
            "INSERT INTO assignments (id, course_id, class_offering_id, title, status, auto_close) "
            "VALUES ('a-1', 10, ?, '第一次作业', 'published', 0)",
            (OFFERING_ID,),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    # -- helpers ---------------------------------------------------------

    def _assignment(self, assignment_id="a-1"):
        row = self.conn.execute(
            """
            SELECT a.*, o.class_id AS offering_class_id
            FROM assignments a
            LEFT JOIN class_offerings o ON o.id = a.class_offering_id
            WHERE a.id = ?
            """,
            (assignment_id,),
        ).fetchone()
        return dict(row)

    def _submission(self, student_pk_id, assignment_id="a-1"):
        row = self.conn.execute(
            "SELECT * FROM submissions WHERE assignment_id = ? AND student_pk_id = ?",
            (assignment_id, student_pk_id),
        ).fetchone()
        return dict(row) if row else None

    def _summary(self):
        return closeout.build_closeout_summary(self.conn, OFFERING_ID, TEACHER["id"])

    def _card(self, summary, kind):
        return next((c for c in summary["cards"] if c["kind"] == kind), None)

    # -- normalize_absence_score ----------------------------------------

    def test_normalize_absence_score_clamps_and_defaults(self):
        self.assertEqual(0.0, closeout.normalize_absence_score(None))
        self.assertEqual(0.0, closeout.normalize_absence_score(""))
        self.assertEqual(0.0, closeout.normalize_absence_score("垃圾输入"))
        self.assertEqual(0.0, closeout.normalize_absence_score(-30))
        self.assertEqual(100.0, closeout.normalize_absence_score(9999))
        self.assertEqual(60.0, closeout.normalize_absence_score("60"))
        self.assertEqual(59.5, closeout.normalize_absence_score(59.5))
        # 非法输入落到显式 default 而不是 0，供逐卡覆盖使用
        self.assertEqual(45.0, closeout.normalize_absence_score("x", default=45))

    # -- summary ---------------------------------------------------------

    def test_summary_counts_unsubmitted_ungraded_and_graded(self):
        self.conn.execute(
            "INSERT INTO submissions (assignment_id, student_pk_id, status, score) VALUES ('a-1', 201, 'graded', 88)"
        )
        self.conn.execute(
            "INSERT INTO submissions (assignment_id, student_pk_id, status) VALUES ('a-1', 202, 'submitted')"
        )
        # 203 没有任何提交记录 -> 未提交
        self.conn.commit()

        card = self._card(self._summary(), closeout.KIND_ASSIGNMENT)
        self.assertIsNotNone(card)
        self.assertEqual(3, card["total_students"])
        self.assertEqual(1, card["graded_count"])
        self.assertEqual(1, card["ungraded_count"])
        self.assertEqual(1, card["unsubmitted_count"])
        self.assertTrue(card["scorable"])

    def test_summary_classifies_exam_separately_from_assignment(self):
        self.conn.execute(
            "INSERT INTO assignments (id, course_id, class_offering_id, title, status, exam_paper_id, auto_close) "
            "VALUES ('e-1', 10, ?, '期中测验', 'published', 'paper-1', 0)",
            (OFFERING_ID,),
        )
        self.conn.commit()

        summary = self._summary()
        exam = self._card(summary, closeout.KIND_EXAM)
        self.assertIsNotNone(exam)
        self.assertEqual("测验", exam["kind_label"])
        self.assertEqual("期中测验", exam["title"])
        self.assertIsNotNone(self._card(summary, closeout.KIND_ASSIGNMENT))

    def test_summary_skips_already_closed_assignments(self):
        self.conn.execute("UPDATE assignments SET status = 'closed' WHERE id = 'a-1'")
        self.conn.commit()

        summary = self._summary()
        self.assertEqual(0, summary["total"])
        self.assertEqual([], summary["cards"])

    def test_summary_includes_polls_schemes_activities_and_pending_signals(self):
        self.conn.execute("INSERT INTO polls (id, title, status) VALUES (5, '课程满意度', 'active')")
        self.conn.execute("INSERT INTO poll_assignments (poll_id, class_offering_id) VALUES (5, ?)", (OFFERING_ID,))
        self.conn.execute("INSERT INTO poll_ballots (poll_id, voter_id) VALUES (5, 201)")
        self.conn.execute(
            "INSERT INTO group_schemes (id, class_offering_id, name, status, group_count) "
            "VALUES (7, ?, '随机分组', 'active', 4)",
            (OFFERING_ID,),
        )
        self.conn.execute(
            "INSERT INTO classroom_live_activities (id, class_offering_id, kind, title, status) "
            "VALUES (9, ?, 'quiz', '随堂测', 'active')",
            (OFFERING_ID,),
        )
        self.conn.execute("INSERT INTO classroom_live_questions (id, activity_id, status) VALUES (11, 9, 'open')")
        self.conn.execute(
            "INSERT INTO classroom_live_help_signals (id, class_offering_id, status) VALUES (13, ?, 'active')",
            (OFFERING_ID,),
        )
        self.conn.commit()

        summary = self._summary()
        kinds = {c["kind"] for c in summary["cards"]}
        self.assertEqual(
            {
                closeout.KIND_ASSIGNMENT,
                closeout.KIND_POLL,
                closeout.KIND_GROUP_SCHEME,
                closeout.KIND_LIVE_ACTIVITY,
                closeout.KIND_QUESTION,
                closeout.KIND_HELP_SIGNAL,
            },
            kinds,
        )
        self.assertEqual(1, self._card(summary, closeout.KIND_POLL)["voted_count"])
        self.assertEqual(4, self._card(summary, closeout.KIND_GROUP_SCHEME)["group_count"])
        self.assertEqual(1, self._card(summary, closeout.KIND_QUESTION)["pending_count"])

    def test_summary_returns_not_exists_for_unknown_offering(self):
        summary = closeout.build_closeout_summary(self.conn, 999999, TEACHER["id"])
        self.assertFalse(summary["exists"])
        self.assertEqual([], summary["cards"])

    # -- apply_absence_scores -------------------------------------------

    def test_absence_scores_create_placeholder_rows_defaulting_to_zero(self):
        result = closeout.apply_absence_scores(self.conn, self._assignment(), teacher_id=1)

        self.assertEqual(3, result["created_count"])
        self.assertEqual(0, result["updated_count"])
        row = self._submission(201)
        self.assertEqual("unsubmitted", row["status"])
        self.assertEqual(0, row["score"])
        self.assertEqual(1, row["is_absence_score"])
        self.assertEqual("absence_zero", row["submission_channel"])
        self.assertEqual("学生甲", row["student_name"])

    def test_absence_scores_honour_custom_default_score(self):
        closeout.apply_absence_scores(self.conn, self._assignment(), teacher_id=1, score=60)

        row = self._submission(202)
        self.assertEqual(60, row["score"])
        self.assertIn("60", row["feedback_md"])

    def test_absence_scores_never_touch_submitted_work_by_default(self):
        self.conn.execute(
            "INSERT INTO submissions (assignment_id, student_pk_id, status, score) VALUES ('a-1', 201, 'graded', 92)"
        )
        self.conn.execute(
            "INSERT INTO submissions (assignment_id, student_pk_id, status) VALUES ('a-1', 202, 'submitted')"
        )
        self.conn.commit()

        result = closeout.apply_absence_scores(self.conn, self._assignment(), teacher_id=1)

        self.assertEqual(1, result["created_count"])  # 只有 203
        self.assertEqual(2, result["skipped_count"])
        self.assertEqual(92, self._submission(201)["score"])
        self.assertEqual("submitted", self._submission(202)["status"])
        self.assertIsNone(self._submission(202)["score"])

    def test_absence_scores_grade_ungraded_only_when_explicitly_opted_in(self):
        self.conn.execute(
            "INSERT INTO submissions (assignment_id, student_pk_id, status) VALUES ('a-1', 202, 'submitted')"
        )
        self.conn.commit()

        result = closeout.apply_absence_scores(
            self.conn, self._assignment(), teacher_id=1, score=30, include_ungraded=True
        )

        self.assertEqual(1, result["graded_count"])
        row = self._submission(202)
        self.assertEqual("graded", row["status"])
        self.assertEqual(30, row["score"])
        # include_ungraded 写的是真实成绩，不该被打上缺交标记
        self.assertEqual(0, row["is_absence_score"])

    def test_absence_scores_rewrite_existing_placeholder_when_score_changes(self):
        closeout.apply_absence_scores(self.conn, self._assignment(), teacher_id=1, score=0)
        result = closeout.apply_absence_scores(self.conn, self._assignment(), teacher_id=1, score=40)

        self.assertEqual(0, result["created_count"])
        self.assertEqual(3, result["updated_count"])
        self.assertEqual(40, self._submission(203)["score"])

    def test_absence_scores_ignore_inactive_students(self):
        self.conn.execute("UPDATE students SET enrollment_status = 'withdrawn' WHERE id = 203")
        self.conn.commit()

        result = closeout.apply_absence_scores(self.conn, self._assignment(), teacher_id=1)

        self.assertEqual(2, result["created_count"])
        self.assertIsNone(self._submission(203))

    def test_absence_scores_report_message_when_assignment_has_no_class(self):
        self.conn.execute("UPDATE assignments SET class_offering_id = NULL WHERE id = 'a-1'")
        self.conn.commit()

        result = closeout.apply_absence_scores(self.conn, self._assignment(), teacher_id=1)

        self.assertIn("未绑定班级", result["message"])
        self.assertEqual(0, result["created_count"])

    # -- close_assignment ------------------------------------------------

    def test_close_assignment_sets_closed_status_and_scores_unsubmitted(self):
        result = closeout.close_assignment(self.conn, self._assignment(), teacher_id=1, score=0)

        self.assertTrue(result["closed"])
        self.assertEqual("closed", self._assignment()["status"])
        self.assertIsNotNone(self._assignment()["closed_at"])
        self.assertEqual(3, result["created_count"])

    def test_close_assignment_is_idempotent_and_preserves_original_closed_at(self):
        closeout.close_assignment(self.conn, self._assignment(), teacher_id=1)
        first_closed_at = self._assignment()["closed_at"]

        second = closeout.close_assignment(self.conn, self._assignment(), teacher_id=1, score=70)

        self.assertFalse(second["closed"])  # 状态没再变
        self.assertEqual(first_closed_at, self._assignment()["closed_at"])
        # 但补分照跑，方便教师改了默认分之后重来
        self.assertEqual(70, self._submission(201)["score"])

    def test_close_assignment_can_skip_absence_scoring(self):
        result = closeout.close_assignment(
            self.conn, self._assignment(), teacher_id=1, apply_absence=False
        )

        self.assertTrue(result["closed"])
        self.assertEqual(0, result["created_count"])
        self.assertIsNone(self._submission(201))

    def test_close_assignment_with_full_submission_just_closes(self):
        for pk in (201, 202, 203):
            self.conn.execute(
                "INSERT INTO submissions (assignment_id, student_pk_id, status, score) VALUES ('a-1', ?, 'graded', 80)",
                (pk,),
            )
        self.conn.commit()

        result = closeout.close_assignment(self.conn, self._assignment(), teacher_id=1)

        self.assertTrue(result["closed"])
        self.assertEqual(0, result["created_count"])
        self.assertEqual(0, result["updated_count"])
        self.assertEqual(3, result["skipped_count"])
        self.assertEqual(80, self._submission(201)["score"])

    # -- execute_closeout ------------------------------------------------

    def test_execute_closeout_closes_assignments_and_help_signals_by_default(self):
        self.conn.execute(
            "INSERT INTO classroom_live_help_signals (id, class_offering_id, status) VALUES (13, ?, 'active')",
            (OFFERING_ID,),
        )
        self.conn.commit()

        result = closeout.execute_closeout(self.conn, OFFERING_ID, TEACHER)

        self.assertEqual([], result["failures"])
        self.assertEqual("closed", self._assignment()["status"])
        self.assertEqual(0, self._submission(201)["score"])
        self.assertEqual(
            "resolved",
            self.conn.execute("SELECT status FROM classroom_live_help_signals WHERE id = 13").fetchone()["status"],
        )

    def test_execute_closeout_marks_open_questions_addressed(self):
        self.conn.execute(
            "INSERT INTO classroom_live_activities (id, class_offering_id, kind, title, status) "
            "VALUES (9, ?, 'qa', '提问墙', 'closed')",
            (OFFERING_ID,),
        )
        self.conn.execute("INSERT INTO classroom_live_questions (id, activity_id, status) VALUES (11, 9, 'open')")
        self.conn.commit()

        closeout.execute_closeout(self.conn, OFFERING_ID, TEACHER)

        self.assertEqual(
            "addressed",
            self.conn.execute("SELECT status FROM classroom_live_questions WHERE id = 11").fetchone()["status"],
        )

    def test_execute_closeout_applies_fallback_default_score(self):
        closeout.execute_closeout(self.conn, OFFERING_ID, TEACHER, {"default_score": 55})

        self.assertEqual(55, self._submission(201)["score"])
        self.assertEqual(55, self._submission(203)["score"])

    def test_execute_closeout_per_card_score_overrides_fallback(self):
        self.conn.execute(
            "INSERT INTO assignments (id, course_id, class_offering_id, title, status, auto_close) "
            "VALUES ('a-2', 10, ?, '第二次作业', 'published', 0)",
            (OFFERING_ID,),
        )
        self.conn.commit()

        closeout.execute_closeout(
            self.conn,
            OFFERING_ID,
            TEACHER,
            {"default_score": 20, "assignment": {"a-2": {"default_score": 75}}},
        )

        self.assertEqual(20, self._submission(201, "a-1")["score"])
        self.assertEqual(75, self._submission(201, "a-2")["score"])

    def test_execute_closeout_respects_per_card_skip(self):
        result = closeout.execute_closeout(
            self.conn, OFFERING_ID, TEACHER, {"assignment": {"a-1": {"action": "skip"}}}
        )

        self.assertEqual(1, result["skipped_total"])
        self.assertEqual(0, result["processed_total"])
        self.assertEqual("published", self._assignment()["status"])
        self.assertIsNone(self._submission(201))

    def test_execute_closeout_does_not_grade_ungraded_unless_opted_in(self):
        self.conn.execute(
            "INSERT INTO submissions (assignment_id, student_pk_id, status) VALUES ('a-1', 202, 'submitted')"
        )
        self.conn.commit()

        closeout.execute_closeout(self.conn, OFFERING_ID, TEACHER, {"default_score": 50})

        self.assertEqual("submitted", self._submission(202)["status"])
        self.assertIsNone(self._submission(202)["score"])

    def test_execute_closeout_isolates_failures_and_keeps_going(self):
        # close_group_scheme 依赖 study_groups 等真实表，这里缺失 -> 该卡片必然失败，
        # 但作业必须照常截止，失败明细进 failures 返回给前端。
        self.conn.execute(
            "INSERT INTO group_schemes (id, class_offering_id, name, status) VALUES (7, ?, '随机分组', 'active')",
            (OFFERING_ID,),
        )
        self.conn.commit()

        result = closeout.execute_closeout(self.conn, OFFERING_ID, TEACHER)

        failed_kinds = {f["kind"] for f in result["failures"]}
        self.assertNotIn(closeout.KIND_ASSIGNMENT, failed_kinds)
        self.assertEqual("closed", self._assignment()["status"])
        self.assertEqual(1, result["processed"].get(closeout.KIND_ASSIGNMENT))

    def test_execute_closeout_raises_for_unknown_offering(self):
        with self.assertRaises(ValueError):
            closeout.execute_closeout(self.conn, 999999, TEACHER)


if __name__ == "__main__":
    unittest.main()
