"""学生成绩单服务的单元测试（sqlite）。"""

import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.database import get_db_connection, init_database
from classroom_app.services.student_report_card_service import build_student_report_card

TEACHER_ID = 927
CLASS_ID = 921
COURSE_ID = 921
OFFERING_ID = 921
ME = 9211
PEERS = [9212, 9213, 9214, 9215, 9216]
ASSIGNMENT_BIG = 9021   # 6 人已批改 → 出分位
ASSIGNMENT_SMALL = 9022  # 只有我 → 不出分位


def _cleanup(conn):
    for sql, params in (
        ("DELETE FROM submissions WHERE assignment_id IN (?, ?)", (ASSIGNMENT_BIG, ASSIGNMENT_SMALL)),
        ("DELETE FROM assignments WHERE id IN (?, ?)", (ASSIGNMENT_BIG, ASSIGNMENT_SMALL)),
        ("DELETE FROM students WHERE id BETWEEN 9211 AND 9216", ()),
        ("DELETE FROM class_offerings WHERE id = ?", (OFFERING_ID,)),
        ("DELETE FROM courses WHERE id = ?", (COURSE_ID,)),
        ("DELETE FROM classes WHERE id = ?", (CLASS_ID,)),
        ("DELETE FROM teachers WHERE id = ?", (TEACHER_ID,)),
    ):
        try:
            conn.execute(sql, params)
        except Exception:
            pass


def _seed(conn):
    day1 = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
    day2 = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO teachers (id, name, email, hashed_password, school_code, school_name, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (TEACHER_ID, "Teacher", "teacher927@example.test", "hashed", "gxufl", "测试学院", 1),
    )
    conn.execute("INSERT INTO classes (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (CLASS_ID, "结构班", TEACHER_ID))
    conn.execute("INSERT INTO courses (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (COURSE_ID, "数据结构", TEACHER_ID))
    conn.execute(
        "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (?, ?, ?, ?)",
        (OFFERING_ID, CLASS_ID, COURSE_ID, TEACHER_ID),
    )
    for sid in [ME, *PEERS]:
        conn.execute(
            "INSERT INTO students (id, student_id_number, name, class_id) VALUES (?, ?, ?, ?)",
            (sid, f"S{sid}", f"学生{sid}", CLASS_ID),
        )
    for aid, title, created in ((ASSIGNMENT_BIG, "第1次作业", day1), (ASSIGNMENT_SMALL, "第2次作业", day2)):
        conn.execute(
            """
            INSERT INTO assignments (id, course_id, class_offering_id, title, status, created_at)
            VALUES (?, ?, ?, ?, 'published', ?)
            """,
            (aid, COURSE_ID, OFFERING_ID, title, created),
        )
    # 大作业：我 90，同伴 50/60/70/80/85 → 我第 1 名 / 6 人 → 前 25%（1/6≈16.7%>10%）。
    scores = {ME: 90, PEERS[0]: 50, PEERS[1]: 60, PEERS[2]: 70, PEERS[3]: 80, PEERS[4]: 85}
    for sid, score in scores.items():
        conn.execute(
            """
            INSERT INTO submissions (assignment_id, student_pk_id, student_name, status, score, submitted_at)
            VALUES (?, ?, ?, 'graded', ?, ?)
            """,
            (ASSIGNMENT_BIG, sid, f"学生{sid}", score, day1),
        )
    # 小作业：只有我，且迟交。
    conn.execute(
        """
        INSERT INTO submissions (assignment_id, student_pk_id, student_name, status, score, submitted_at, is_late_submission)
        VALUES (?, ?, ?, 'graded', 75, ?, 1)
        """,
        (ASSIGNMENT_SMALL, ME, f"学生{ME}", day2),
    )


class StudentReportCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_database()

    def setUp(self):
        with get_db_connection() as conn:
            _cleanup(conn)
            _seed(conn)
            conn.commit()

    def tearDown(self):
        with get_db_connection() as conn:
            _cleanup(conn)
            conn.commit()

    def test_timeline_scores_class_avg_and_band(self):
        with get_db_connection() as conn:
            card = build_student_report_card(conn, student_id=ME)

        self.assertEqual(len(card["courses"]), 1)
        course = card["courses"][0]
        self.assertEqual(course["course_name"], "数据结构")
        self.assertEqual(course["record_count"], 2)
        # 时间线按提交时间升序。
        self.assertEqual(
            [str(r["assignment_id"]) for r in course["records"]],
            [str(ASSIGNMENT_BIG), str(ASSIGNMENT_SMALL)],
        )

        big = course["records"][0]
        self.assertEqual(big["my_score"], 90.0)
        self.assertEqual(big["class_avg"], round((90 + 50 + 60 + 70 + 80 + 85) / 6, 1))
        self.assertEqual(big["class_count"], 6)
        self.assertEqual(big["band_label"], "前 25%")

        small = course["records"][1]
        self.assertEqual(small["my_score"], 75.0)
        self.assertEqual(small["class_count"], 1)
        self.assertEqual(small["band_label"], "")  # 样本不足不出分位
        self.assertTrue(small["is_late"])

        self.assertEqual(course["avg_score"], round((90 + 75) / 2, 1))
        self.assertEqual(card["summary"]["record_total"], 2)
        self.assertEqual(card["summary"]["overall_avg"], 82.5)
        self.assertEqual(card["summary"]["top_band_count"], 1)
        self.assertEqual(card["summary"]["best_course"], "数据结构")

    def test_chart_series_aligned_with_records(self):
        with get_db_connection() as conn:
            card = build_student_report_card(conn, student_id=ME)
        chart = card["courses"][0]["chart"]
        self.assertEqual(len(chart["labels"]), 2)
        self.assertEqual(chart["mine"], [90.0, 75.0])
        self.assertEqual(len(chart["class_avg"]), 2)

    def test_never_exposes_peer_identities(self):
        with get_db_connection() as conn:
            card = build_student_report_card(conn, student_id=ME)
        import json

        payload = json.dumps(card, ensure_ascii=False)
        for peer in PEERS:
            self.assertNotIn(str(peer), payload)
            self.assertNotIn(f"学生{peer}", payload)

    def test_empty_for_student_without_grades(self):
        with get_db_connection() as conn:
            card = build_student_report_card(conn, student_id=999999)
        self.assertEqual(card["courses"], [])
        self.assertIsNone(card["summary"]["overall_avg"])


if __name__ == "__main__":
    unittest.main()
