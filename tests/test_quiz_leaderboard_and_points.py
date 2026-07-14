"""随堂测抢答排行与学分币发奖的单元测试（sqlite，走真实服务流）。"""

import os
import unittest

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.database import get_db_connection, init_database
from classroom_app.services import classroom_interaction_service as svc
from classroom_app.services.student_points_service import ensure_points_schema, get_points_balance

TEACHER_ID = 957
CLASS_ID = 951
COURSE_ID = 951
OFFERING_ID = 951
STUDENTS = [9701, 9702, 9703]

TEACHER_USER = {"id": TEACHER_ID, "role": "teacher", "name": "王老师"}


def _student_user(student_id: int) -> dict:
    return {"id": student_id, "role": "student", "name": f"学生{student_id}"}


def _cleanup(conn):
    for sql, params in (
        ("DELETE FROM classroom_live_responses WHERE activity_id IN (SELECT id FROM classroom_live_activities WHERE class_offering_id = ?)", (OFFERING_ID,)),
        ("DELETE FROM classroom_live_options WHERE activity_id IN (SELECT id FROM classroom_live_activities WHERE class_offering_id = ?)", (OFFERING_ID,)),
        ("DELETE FROM classroom_live_activities WHERE class_offering_id = ?", (OFFERING_ID,)),
        ("DELETE FROM student_point_ledger WHERE student_id IN (9701, 9702, 9703)", ()),
        ("DELETE FROM students WHERE id IN (9701, 9702, 9703)", ()),
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
    conn.execute(
        """
        INSERT INTO teachers (id, name, email, hashed_password, school_code, school_name, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (TEACHER_ID, "王老师", "teacher957@example.test", "hashed", "gxufl", "测试学院", 1),
    )
    conn.execute("INSERT INTO classes (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (CLASS_ID, "抢答班", TEACHER_ID))
    conn.execute("INSERT INTO courses (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (COURSE_ID, "编译原理", TEACHER_ID))
    conn.execute(
        "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (?, ?, ?, ?)",
        (OFFERING_ID, CLASS_ID, COURSE_ID, TEACHER_ID),
    )
    for sid in STUDENTS:
        conn.execute(
            "INSERT INTO students (id, student_id_number, name, class_id) VALUES (?, ?, ?, ?)",
            (sid, f"S{sid}", f"学生{sid}", CLASS_ID),
        )


class QuizLeaderboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_database()

    def setUp(self):
        with get_db_connection() as conn:
            ensure_points_schema(conn)
            _cleanup(conn)
            _seed(conn)
            conn.commit()

    def tearDown(self):
        with get_db_connection() as conn:
            _cleanup(conn)
            conn.commit()

    def _create_quiz(self, conn) -> dict:
        return svc.create_activity(
            conn,
            OFFERING_ID,
            TEACHER_USER,
            {
                "kind": "quiz",
                "title": "抢答",
                "prompt": "LL(1) 分析表冲突说明了什么？",
                "options": [
                    {"label": "文法不是 LL(1)", "is_correct": True},
                    {"label": "词法有错", "is_correct": False},
                ],
            },
        )

    def _option_ids(self, conn, activity_id: int) -> dict:
        rows = conn.execute(
            "SELECT id, is_correct FROM classroom_live_options WHERE activity_id = ? ORDER BY sort_order",
            (activity_id,),
        ).fetchall()
        return {
            "correct": next(int(r["id"]) for r in rows if r["is_correct"]),
            "wrong": next(int(r["id"]) for r in rows if not r["is_correct"]),
        }

    def test_close_awards_points_and_builds_leaderboard(self):
        with get_db_connection() as conn:
            activity = self._create_quiz(conn)
            activity_id = int(activity["id"])
            option_ids = self._option_ids(conn, activity_id)

            # 9701、9702 答对（按先后），9703 答错。
            svc.respond_to_activity(conn, activity_id, _student_user(9701), {"option_id": option_ids["correct"]})
            svc.respond_to_activity(conn, activity_id, _student_user(9702), {"option_id": option_ids["correct"]})
            svc.respond_to_activity(conn, activity_id, _student_user(9703), {"option_id": option_ids["wrong"]})

            detail = svc.close_activity(conn, activity_id, TEACHER_USER)

            # 排行只含答对者，按作答先后。
            leaderboard = detail.get("leaderboard") or []
            self.assertEqual([row["student_name"] for row in leaderboard], ["学生9701", "学生9702"])
            self.assertEqual([row["rank"] for row in leaderboard], [1, 2])

            # 答对 +15、前三手速 +10；答错 0。
            self.assertEqual(get_points_balance(conn, 9701), 25)
            self.assertEqual(get_points_balance(conn, 9702), 25)
            self.assertEqual(get_points_balance(conn, 9703), 0)

            # 重复 close 幂等（不重复发奖）。
            svc.close_activity(conn, activity_id, TEACHER_USER)
            self.assertEqual(get_points_balance(conn, 9701), 25)
            conn.commit()

    def test_anonymous_correct_answer_hidden_on_leaderboard(self):
        with get_db_connection() as conn:
            activity = self._create_quiz(conn)
            activity_id = int(activity["id"])
            option_ids = self._option_ids(conn, activity_id)
            svc.respond_to_activity(
                conn, activity_id, _student_user(9701),
                {"option_id": option_ids["correct"], "is_anonymous": True},
            )
            detail = svc.close_activity(conn, activity_id, TEACHER_USER)
            leaderboard = detail.get("leaderboard") or []
            self.assertEqual(leaderboard[0]["student_name"], "匿名同学")
            # 匿名不影响拿奖励。
            self.assertEqual(get_points_balance(conn, 9701), 25)
            conn.commit()

    def test_active_quiz_has_no_leaderboard(self):
        with get_db_connection() as conn:
            activity = self._create_quiz(conn)
            detail = svc.load_activity_detail(conn, int(activity["id"]), TEACHER_USER)
            self.assertNotIn("leaderboard", detail)
            conn.commit()


if __name__ == "__main__":
    unittest.main()
