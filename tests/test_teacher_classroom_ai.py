"""教师端课堂 AI：课堂全景简报、课堂全知视图、动作提案与本人确认执行（sqlite，不调模型）。"""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from classroom_app.database import get_db_connection
from classroom_app.routers import ai as ai_router
from classroom_app.services import chat_action_service as actions
from classroom_app.services import chat_platform_query_service as views
from classroom_app.services.poll_service import ORIGIN_CLASSROOM, create_poll
from classroom_app.services.teacher_classroom_brief_service import build_teacher_classroom_brief, load_roster
from tests.sqlite_database_fixture import isolated_sqlite_database

TEACHER = 9601
OTHER_TEACHER = 9602
CLASS_ID = 9601
COURSE_ID = 9601
OFFERING = 9601
STUDENTS = [96011, 96012, 96013, 96014, 96015, 96016]
ASSIGNMENT = 96091
TEACHER_USER = {"id": TEACHER, "role": "teacher", "name": "王老师"}
OTHER_USER = {"id": OTHER_TEACHER, "role": "teacher", "name": "李老师"}


def _cleanup(conn):
    for sql, params in (
        ("DELETE FROM ai_chat_action_proposals WHERE class_offering_id = ?", (OFFERING,)),
        ("DELETE FROM poll_votes WHERE poll_id IN (SELECT id FROM polls WHERE origin_class_offering_id = ?)", (OFFERING,)),
        ("DELETE FROM poll_ballots WHERE poll_id IN (SELECT id FROM polls WHERE origin_class_offering_id = ?)", (OFFERING,)),
        ("DELETE FROM poll_options WHERE poll_id IN (SELECT id FROM polls WHERE origin_class_offering_id = ?)", (OFFERING,)),
        ("DELETE FROM poll_assignments WHERE class_offering_id = ?", (OFFERING,)),
        ("DELETE FROM polls WHERE origin_class_offering_id = ?", (OFFERING,)),
        ("DELETE FROM study_group_members WHERE group_id IN (SELECT id FROM study_groups WHERE class_offering_id = ?)", (OFFERING,)),
        ("DELETE FROM study_groups WHERE class_offering_id = ?", (OFFERING,)),
        ("DELETE FROM group_schemes WHERE class_offering_id = ?", (OFFERING,)),
        ("DELETE FROM learning_progress_snapshots WHERE class_offering_id = ?", (OFFERING,)),
        ("DELETE FROM submissions WHERE assignment_id = ?", (ASSIGNMENT,)),
        ("DELETE FROM assignments WHERE id = ?", (ASSIGNMENT,)),
        ("DELETE FROM class_offering_sessions WHERE class_offering_id = ?", (OFFERING,)),
        ("DELETE FROM students WHERE id IN (%s)" % ",".join("?" * len(STUDENTS)), tuple(STUDENTS)),
        ("DELETE FROM class_offerings WHERE id = ?", (OFFERING,)),
        ("DELETE FROM courses WHERE id = ?", (COURSE_ID,)),
        ("DELETE FROM classes WHERE id = ?", (CLASS_ID,)),
        ("DELETE FROM teachers WHERE id IN (?, ?)", (TEACHER, OTHER_TEACHER)),
    ):
        try:
            conn.execute(sql, params)
        except Exception:
            pass


def _seed(conn):
    now = datetime.now()
    iso = now.isoformat(timespec="seconds")
    for tid, name, email in ((TEACHER, "王老师", "t9601@example.test"), (OTHER_TEACHER, "李老师", "t9602@example.test")):
        conn.execute(
            "INSERT INTO teachers (id, name, email, hashed_password, school_code, school_name, is_active) VALUES (?, ?, ?, 'x', 'gxufl', '测试学院', 1)",
            (tid, name, email),
        )
    conn.execute("INSERT INTO classes (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (CLASS_ID, "软件2301", TEACHER))
    conn.execute("INSERT INTO courses (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (COURSE_ID, "数据库原理", TEACHER))
    conn.execute(
        "INSERT INTO class_offerings (id, class_id, course_id, teacher_id, semester) VALUES (?, ?, ?, ?, ?)",
        (OFFERING, CLASS_ID, COURSE_ID, TEACHER, "2026-2027第一学期"),
    )
    names = ["张三", "李四", "王五", "赵六", "钱七", "孙八"]
    for sid, name in zip(STUDENTS, names):
        conn.execute(
            "INSERT INTO students (id, student_id_number, name, class_id) VALUES (?, ?, ?, ?)",
            (sid, f"S{sid}", name, CLASS_ID),
        )
    conn.execute(
        "INSERT INTO assignments (id, course_id, class_offering_id, title, status, due_at, created_at) VALUES (?, ?, ?, ?, 'published', ?, ?)",
        (ASSIGNMENT, COURSE_ID, OFFERING, "第一次作业：ER 图设计", (now + timedelta(days=2)).isoformat(timespec="seconds"), iso),
    )
    for sid, score in ((STUDENTS[0], 92), (STUDENTS[1], 58), (STUDENTS[2], None)):
        conn.execute(
            "INSERT INTO submissions (assignment_id, student_pk_id, student_name, status, score, feedback_md, submitted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ASSIGNMENT, sid, names[STUDENTS.index(sid)], "graded" if score is not None else "submitted", score, "结构清晰" if score else "", iso),
        )
    for index in (1, 2):
        conn.execute(
            """
            INSERT INTO class_offering_sessions (class_offering_id, order_index, title, session_date, weekday, section_count, slot_section_count)
            VALUES (?, ?, ?, ?, ?, 2, 2)
            """,
            (OFFERING, index, f"第{index}讲 关系模型", (now + timedelta(days=index * 7 - 7)).date().isoformat(), 1),
        )
    try:
        conn.execute(
            "INSERT INTO learning_progress_snapshots (class_offering_id, student_id, score, progress_percent, level_key, calculated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (OFFERING, STUDENTS[0], 88.5, 70, "foundation", iso),
        )
    except Exception:
        pass


class _ClassroomFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(isolated_sqlite_database())

    def setUp(self):
        with get_db_connection() as conn:
            _cleanup(conn)
            _seed(conn)
            conn.commit()

    def tearDown(self):
        with get_db_connection() as conn:
            _cleanup(conn)
            conn.commit()

    def _create_poll(self, status="active"):
        with get_db_connection() as conn:
            poll = create_poll(
                conn,
                TEACHER_USER,
                {"title": "下次课讲什么", "options": ["索引", "事务", "范式"], "status": status},
                origin=ORIGIN_CLASSROOM,
                class_offering_id=OFFERING,
            )
            conn.commit()
        return poll


class ClassroomBriefTests(_ClassroomFixture):
    def test_brief_covers_roster_assessments_sessions_and_polls(self):
        self._create_poll()
        with get_db_connection() as conn:
            brief = build_teacher_classroom_brief(conn, TEACHER_USER, OFFERING)
            roster = load_roster(conn, OFFERING)
        self.assertIn("数据库原理 · 软件2301", brief)
        self.assertIn("学生 6 人", brief)
        self.assertIn("张三 · S96011", brief)
        self.assertIn("第一次作业：ER 图设计", brief)
        self.assertIn("3/6", brief)  # submitted / roster
        self.assertIn("下次课讲什么", brief)
        self.assertIn("第 1 次", brief)
        self.assertIn("不要声称已经执行", brief)
        self.assertEqual(len(roster), 6)
        self.assertEqual(sum(item["submitted"] for item in roster), 3)

    def test_brief_header_only_mode_and_failed_query_marker(self):
        with get_db_connection() as conn:
            lean = build_teacher_classroom_brief(conn, TEACHER_USER, OFFERING, detail=False)
            self.assertIn("学生 6 人", lean)
            self.assertNotIn("【学生名册】", lean)
            self.assertIn("使用规则", lean)
            # A broken query must surface as "数据不可用", never as a silent zero.
            with patch("classroom_app.services.teacher_classroom_brief_service.load_assessments", side_effect=lambda *a, **k: (k["errors"].append("作业与考试"), [])[1]):
                broken = build_teacher_classroom_brief(conn, TEACHER_USER, OFFERING)
        self.assertRegex(broken, r"本次不可用：[^\n]*作业与考试")
        self.assertIn("【作业与考试】数据不可用", broken)
        self.assertTrue(broken.rstrip().endswith("给出文档位置链接。"))

    def test_brief_is_empty_for_other_teacher_and_students(self):
        with get_db_connection() as conn:
            self.assertEqual(build_teacher_classroom_brief(conn, OTHER_USER, OFFERING), "")
            self.assertEqual(build_teacher_classroom_brief(conn, {"id": STUDENTS[0], "role": "student"}, OFFERING), "")


class ClassroomViewTests(_ClassroomFixture):
    def test_student_profile_and_submissions_scoped_to_context_offering(self):
        with get_db_connection() as conn:
            profile = views.run_platform_view(conn, teacher_id=TEACHER, view="student_profile", params={"student_keyword": "李四"}, context_class_offering_id=OFFERING)
            submissions = views.run_platform_view(conn, teacher_id=TEACHER, view="student_submissions", params={"student_keyword": f"S{STUDENTS[1]}"}, context_class_offering_id=OFFERING)
            foreign = views.run_platform_view(conn, teacher_id=OTHER_TEACHER, view="student_profile", params={"student_keyword": "李四"}, context_class_offering_id=OFFERING)
        items = {row["项目"]: row["值"] for row in profile["rows"]}
        self.assertEqual(items["姓名"], "李四")
        self.assertEqual(items["已提交 / 已批改"], "1 / 1")
        self.assertEqual(items["平均分"], "58.0")
        self.assertEqual(submissions["rows"][0]["分数"], 58)
        self.assertTrue(submissions["rows"][0]["链接"].startswith("/submission/"))
        self.assertEqual(foreign["rows"], [])  # the other teacher owns no classroom here

    def test_poll_results_group_detail_session_progress_and_overview(self):
        self._create_poll()
        with get_db_connection() as conn:
            poll = views.run_platform_view(conn, teacher_id=TEACHER, view="poll_results", params={}, context_class_offering_id=OFFERING)
            overview = views.run_platform_view(conn, teacher_id=TEACHER, view="classroom_overview", params={}, context_class_offering_id=OFFERING)
            progress = views.run_platform_view(conn, teacher_id=TEACHER, view="session_progress", params={}, context_class_offering_id=OFFERING)
            scheme = views.run_platform_view(conn, teacher_id=TEACHER, view="group_scheme_detail", params={}, context_class_offering_id=OFFERING)
        self.assertEqual([row["选项"] for row in poll["rows"]], ["索引", "事务", "范式"])
        self.assertIn("已投 0 / 应投 6", poll["note"])
        self.assertIn("学生人数", [row["项目"] for row in overview["rows"]])
        self.assertEqual(len(progress["rows"]), 2)
        self.assertIn("没有匹配的分组方案", scheme["note"])

    def test_unmatched_class_keyword_and_ambiguous_student_are_reported(self):
        with get_db_connection() as conn:
            conn.execute("INSERT INTO students (id, student_id_number, name, class_id) VALUES (?, ?, ?, ?)", (STUDENTS[0] + 1000, "S-dup", "张三", CLASS_ID))
            try:
                missing = views.run_platform_view(conn, teacher_id=TEACHER, view="classroom_overview", params={"class_keyword": "不存在的班"}, context_class_offering_id=OFFERING)
                ambiguous = views.run_platform_view(conn, teacher_id=TEACHER, view="student_profile", params={"student_keyword": "张三"}, context_class_offering_id=OFFERING)
            finally:
                conn.execute("DELETE FROM students WHERE id = ?", (STUDENTS[0] + 1000,))
                conn.commit()
        self.assertEqual(missing["rows"], [])
        self.assertIn("没有找到", missing["note"])
        self.assertIn("匹配到多名学生", ambiguous["note"])

    def test_local_fallback_plans_classroom_views(self):
        plan = views.infer_platform_query_tool_calls("李四同学的作业情况怎么样")
        self.assertEqual([call["view"] for call in plan["tool_calls"]], ["student_profile", "student_submissions"])
        self.assertEqual(plan["tool_calls"][0]["params"]["student_keyword"], "李四")
        self.assertEqual(views.infer_platform_query_tool_calls("看看投票结果")["tool_calls"][0]["view"], "poll_results")
        self.assertEqual(views.infer_platform_query_tool_calls("分组情况如何")["tool_calls"][0]["view"], "group_scheme_detail")
        self.assertTrue(views.message_may_need_platform_data("张三的修为怎么样"))


class ClassroomActionTests(_ClassroomFixture):
    def test_local_parser_extracts_poll_options_and_group_layout(self):
        poll_calls = actions.infer_actions_locally("帮我发起一个投票：下次课讲什么，选项 索引、事务、范式，10分钟后截止")
        self.assertEqual(poll_calls[0]["action"], "create_poll")
        self.assertEqual(poll_calls[0]["args"]["options"], ["索引", "事务", "范式"])
        self.assertEqual(poll_calls[0]["args"]["deadline_minutes"], 10)
        group_calls = actions.infer_actions_locally("按名单随机分组，分成 3 组，不要配组长")
        self.assertEqual(group_calls[0]["action"], "create_random_groups")
        self.assertEqual(group_calls[0]["args"]["group_count"], 3)
        self.assertFalse(group_calls[0]["args"]["auto_leaders"])
        self.assertEqual(actions.infer_actions_locally("把投票结束掉")[0]["action"], "close_poll")
        self.assertTrue(actions.message_may_request_action("来个投票"))
        self.assertTrue(actions.message_may_request_action("帮我发起一个投票"))
        self.assertFalse(actions.message_may_request_action("今天讲了什么"))
        # Read-only questions about polls / groups go to the data views, not to proposals.
        self.assertFalse(actions.message_may_request_action("帮我看看投票结果"))
        self.assertFalse(actions.message_may_request_action("给我看看分组方案"))
        self.assertFalse(actions.message_may_request_action("帮我分析一下小组作业"))

    def test_proposal_then_confirm_creates_poll_groups_and_closes_poll(self):
        with get_db_connection() as conn:
            proposals, problems = actions.create_proposals(
                conn,
                TEACHER_USER,
                OFFERING,
                [
                    {"action": "create_poll", "args": {"title": "复习重点", "options": ["索引", "事务"], "vote_type": "multiple"}},
                    {"action": "create_random_groups", "args": {"group_count": 2}},
                ],
                source_message="发起投票并分组",
            )
            conn.commit()
        self.assertEqual(problems, [])
        self.assertEqual([p["action"] for p in proposals], ["create_poll", "create_random_groups"])
        self.assertIn("分成 2 组，每组 3-3 人", proposals[1]["lines"][1])
        with get_db_connection() as conn:
            poll_result = actions.execute_proposal(conn, TEACHER_USER, proposals[0]["token"])
            group_result = actions.execute_proposal(conn, TEACHER_USER, proposals[1]["token"])
            conn.commit()
            option_count = conn.execute("SELECT COUNT(*) AS n FROM poll_options WHERE poll_id = ?", (poll_result["poll_id"],)).fetchone()["n"]
            member_count = conn.execute(
                "SELECT COUNT(*) AS n FROM study_group_members m JOIN study_groups g ON g.id = m.group_id WHERE g.scheme_id = ? AND m.status = 'active'",
                (group_result["scheme_id"],),
            ).fetchone()["n"]
            detail = views.run_platform_view(conn, teacher_id=TEACHER, view="group_scheme_detail", params={}, context_class_offering_id=OFFERING)
        self.assertEqual(poll_result["status"], "active")
        self.assertEqual(option_count, 2)
        self.assertTrue(poll_result["notify_poll_active"])
        self.assertEqual(group_result["group_count"], 2)
        self.assertEqual(member_count, 6)
        self.assertEqual(sum(row["人数"] for row in detail["rows"]), 6)
        self.assertIn("未分组：无", detail["note"])
        # close it through a second proposal
        with get_db_connection() as conn:
            close_proposals, close_problems = actions.create_proposals(conn, TEACHER_USER, OFFERING, [{"action": "close_poll", "args": {}}])
            conn.commit()
            closed = actions.execute_proposal(conn, TEACHER_USER, close_proposals[0]["token"])
            conn.commit()
            status = conn.execute("SELECT status FROM polls WHERE id = ?", (poll_result["poll_id"],)).fetchone()["status"]
        self.assertEqual(close_problems, [])
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(status, "closed")

    def test_invalid_plans_report_problems_instead_of_proposals(self):
        with get_db_connection() as conn:
            proposals, problems = actions.create_proposals(conn, TEACHER_USER, OFFERING, [{"action": "create_poll", "args": {"title": "只有一个选项", "options": ["A"]}}, {"action": "close_poll", "args": {}}])
            conn.commit()
        self.assertEqual(proposals, [])
        self.assertEqual(len(problems), 2)
        self.assertIn("至少需要 2 个选项", problems[0])

    def test_only_owner_can_execute_and_only_once(self):
        with get_db_connection() as conn:
            proposals, _ = actions.create_proposals(conn, TEACHER_USER, OFFERING, [{"action": "create_random_groups", "args": {"group_size": 2}}])
            conn.commit()
        token = proposals[0]["token"]
        from fastapi import HTTPException

        with get_db_connection() as conn:
            with self.assertRaises(HTTPException) as denied:
                actions.execute_proposal(conn, OTHER_USER, token)
            self.assertEqual(denied.exception.status_code, 404)
            actions.execute_proposal(conn, TEACHER_USER, token)
            conn.commit()
            with self.assertRaises(HTTPException) as repeated:
                actions.execute_proposal(conn, TEACHER_USER, token)
            self.assertEqual(repeated.exception.status_code, 409)
            # A row already claimed by a concurrent request is refused as well.
            claimed, _ = actions.create_proposals(conn, TEACHER_USER, OFFERING, [{"action": "create_random_groups", "args": {}}])
            conn.execute("UPDATE ai_chat_action_proposals SET status = 'executing' WHERE token = ?", (claimed[0]["token"],))
            with self.assertRaises(HTTPException) as busy:
                actions.execute_proposal(conn, TEACHER_USER, claimed[0]["token"])
            self.assertEqual(busy.exception.status_code, 409)
            with self.assertRaises(HTTPException):
                actions.create_proposals(conn, OTHER_USER, OFFERING, [{"action": "close_poll", "args": {}}])
        with get_db_connection() as conn:
            expired, _ = actions.create_proposals(conn, TEACHER_USER, OFFERING, [{"action": "create_random_groups", "args": {}}])
            conn.execute("UPDATE ai_chat_action_proposals SET expires_at = '2000-01-01T00:00:00' WHERE token = ?", (expired[0]["token"],))
            conn.commit()
            with self.assertRaises(HTTPException) as stale:
                actions.execute_proposal(conn, TEACHER_USER, expired[0]["token"])
            self.assertEqual(stale.exception.status_code, 409)

    def test_chat_pre_step_streams_action_proposal_event(self):
        async def fake_plan(message):
            return [{"action": "create_poll", "args": {"title": "课堂投票", "options": ["A", "B"]}}]

        async def collect(offering):
            payload = {"system_prompt": "base"}
            events = []
            with patch.object(actions, "plan_actions", AsyncMock(side_effect=fake_plan)):
                async for line in ai_router._classroom_action_proposal_events(TEACHER_USER, "发起一个投票", payload, offering):
                    events.append(json.loads(line))
            return events, payload

        events, payload = asyncio.run(collect(OFFERING))
        kinds = [event["event"] for event in events]
        self.assertIn("action_proposal", kinds)
        proposal_event = next(event for event in events if event["event"] == "action_proposal")
        self.assertEqual(proposal_event["proposals"][0]["action"], "create_poll")
        self.assertIn("课堂动作提案", payload["system_prompt"])
        self.assertIn("不要说“已经发起", payload["system_prompt"])
        events, payload = asyncio.run(collect(None))
        self.assertEqual(events, [])
        self.assertIn("不在某个课堂内", payload["system_prompt"])

    def test_classroom_context_blocks_only_for_owner(self):
        self.assertIn("当前课堂全景", ai_router._classroom_context_blocks(TEACHER_USER, OFFERING))
        self.assertEqual(ai_router._classroom_context_blocks(OTHER_USER, OFFERING), "")


class ChatActionApiTests(_ClassroomFixture):
    def tearDown(self):
        from classroom_app.app import app
        from classroom_app.dependencies import get_current_teacher, get_current_user

        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_teacher, None)
        super().tearDown()

    def _client(self, user):
        from fastapi.testclient import TestClient
        from classroom_app.app import app
        from classroom_app.dependencies import get_current_teacher, get_current_user

        app.dependency_overrides[get_current_user] = lambda: dict(user)
        app.dependency_overrides[get_current_teacher] = lambda: dict(user)
        return TestClient(app)

    def test_execute_and_cancel_routes(self):
        with get_db_connection() as conn:
            proposals, _ = actions.create_proposals(
                conn,
                TEACHER_USER,
                OFFERING,
                [{"action": "create_poll", "args": {"title": "API 投票", "options": ["是", "否"]}}, {"action": "create_random_groups", "args": {"group_count": 3}}],
            )
            conn.commit()
        client = self._client(TEACHER_USER)
        executed = client.post(f"/api/ai/chat-actions/{proposals[0]['token']}/execute")
        self.assertEqual(executed.status_code, 200, executed.text)
        result = executed.json()["result"]
        self.assertEqual(result["kind"], "poll")
        self.assertNotIn("notify_poll_active", result)
        self.assertTrue(result["link_url"].startswith(f"/classroom/{OFFERING}"))
        cancelled = client.post(f"/api/ai/chat-actions/{proposals[1]['token']}/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["result"]["status"], "cancelled")
        self.assertEqual(client.post(f"/api/ai/chat-actions/{proposals[1]['token']}/execute").status_code, 409)
        self.assertIn(client.post("/api/ai/chat-actions/not-a-token/execute").status_code, {401, 404})


if __name__ == "__main__":
    unittest.main()
