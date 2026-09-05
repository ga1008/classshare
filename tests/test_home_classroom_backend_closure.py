"""Full-route acceptance checks against an explicitly selected disposable fixture.

Run alone with HOME_CLASSROOM_CLOSURE_RUNTIME pointing at the task-owned
backend-closure-runtime. Its baseline database is restored before each test.
No workers/lifespan, real network, AI generation or production data are used.
"""
from __future__ import annotations

import json
import io
import hashlib
import os
import re
import sqlite3
import unittest
from contextlib import ExitStack, closing, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


RUNTIME = Path(os.environ["HOME_CLASSROOM_CLOSURE_RUNTIME"]).resolve() if os.environ.get("HOME_CLASSROOM_CLOSURE_RUNTIME") else None
if RUNTIME:
    if RUNTIME.name != "backend-closure-runtime" or not (RUNTIME.parent / "baseline-runtime/db/classroom.db").is_file():
        raise RuntimeError("Explicit disposable backend-closure-runtime required")
    os.environ.update({"PYTHON_DOTENV_DISABLED": "1", "DB_ENGINE": "sqlite", "POSTGRES_BACKEND_READY": "false",
                       "LANSHARE_DATA_ROOT": str(RUNTIME), "MAIN_DATA_DIR": str(RUNTIME),
                       "MAIN_DB_PATH": str(RUNTIME / "db/classroom.db"),
                       "AI_HOST": "127.0.0.1", "AI_PORT": "8134", "AI_ASSISTANT_URL": "http://127.0.0.1:8134"})


@unittest.skipUnless(RUNTIME, "Requires explicit disposable HOME_CLASSROOM_CLOSURE_RUNTIME")
class HomeClassroomBackendClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from classroom_app.app import app
        from classroom_app.config import DB_PATH
        from fastapi.testclient import TestClient
        assert Path(DB_PATH).resolve() == RUNTIME / "db/classroom.db", "Refusing non-fixture database"
        cls.app = app
        cls.client_type = TestClient
        cls.fixture = json.loads((RUNTIME / "fixture.json").read_text(encoding="utf-8-sig"))
        cls.db_path = RUNTIME / "db/classroom.db"
        cls.offering = int(cls.fixture["classOfferingId"])
        cls.course = int(cls.fixture["courseId"])
        cls.student = int(cls.fixture["student"]["id"])
        cls.peer = int(cls.fixture["otherStudent"]["id"])
        cls.teacher = int(cls.fixture["teacher"]["id"])

    def setUp(self):
        with closing(sqlite3.connect(RUNTIME.parent / "baseline-runtime/db/classroom.db")) as source, closing(sqlite3.connect(self.db_path)) as target:
            source.backup(target)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.http_evidence = []
        self.verified_values = {}
        self.clock = None
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        from classroom_app.routers.ui_parts.common import templates
        original_render = templates.TemplateResponse
        self.render_context = None
        def capture_render(*args, **kwargs):
            self.render_context = kwargs.get("context") or (args[2] if len(args) > 2 else None)
            return original_render(*args, **kwargs)
        self.stack.enter_context(patch.object(templates, "TemplateResponse", side_effect=capture_render))
        # A same-snapshot read comparison must not turn its own page probes into
        # new learning events or asynchronous discussion warmup jobs.
        for target in ("classroom_app.routers.ui_parts.classroom.record_behavior_event",
                       "classroom_app.routers.ui_parts.assignment_pages.record_behavior_event",
                       "classroom_app.routers.ui_parts.exam_pages.record_behavior_event",
                       "classroom_app.routers.ui_parts.classroom.schedule_discussion_mood_refresh_soon"):
            self.stack.enter_context(patch(target, return_value=None))
        # Guard against accidentally invoking remote clients during read checks.
        self.outbound = self.stack.enter_context(patch("httpx.AsyncClient.request", side_effect=AssertionError("No outbound requests in closure tests")))
        self.clients = []
        self.addCleanup(lambda: [client.close() for client in self.clients])
        with self.db() as conn:
            conn.execute("UPDATE students SET class_id=?, enrollment_status='active' WHERE id=?", (self.fixture["classId"], self.peer))
        self.client = self.login("student")

    def tearDown(self):
        self.outbound.assert_not_called()
        evidence = os.environ.get("HOME_CLASSROOM_CLOSURE_EVIDENCE")
        if evidence:
            destination = Path(evidence).resolve()
            assert destination.is_relative_to(RUNTIME.parent)
            destination.mkdir(parents=True, exist_ok=True)
            record = {"test": self._testMethodName, "sqlite_version": sqlite3.sqlite_version,
                      "source_fixture_sha256": hashlib.sha256((RUNTIME.parent / "baseline-runtime/db/classroom.db").read_bytes()).hexdigest(),
                      "real_login": True, "outbound_requests": self.outbound.call_count,
                      "http_reads": self.http_evidence, "verified_values": self.verified_values}
            (destination / f"{self._testMethodName}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    def db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def login(self, key):
        client = self.client_type(self.app, raise_server_exceptions=False)
        self.clients.append(client)
        student = "student" in key.lower()
        fields = {"identifier" if student else "email": self.fixture[key]["studentNumber" if student else "email"], "password": self.fixture["password"]}
        response = client.post("/student/login" if student else "/teacher/login", data=fields, follow_redirects=False)
        self.assertEqual(303, response.status_code)
        self.assertTrue(client.cookies)
        return client

    def get(self, path, client=None):
        response = (client or self.client).get(path)
        self.assertEqual(200, response.status_code, f"{path}: HTTP {response.status_code}")
        self.http_evidence.append({"path": path, "status": response.status_code, "bytes": len(response.content), "injected_clock": self.clock})
        if self.clock and path.startswith("/api/dashboard/workspace"):
            generated_at = response.json()["workspace"]["generated_at"]
            self.assertEqual(self.clock + "+08:00", generated_at, "Injected datetime must retain its time-of-day")
            self.http_evidence[-1]["actual_generated_at"] = generated_at
        response.context = self.render_context
        return response

    def seed_snapshot(self, student, score):
        from tests.test_learning_progress_snapshots import _metrics
        metrics = _metrics(score, material=score)
        with self.db() as conn:
            conn.execute("DELETE FROM learning_stage_exam_attempts WHERE class_offering_id=? AND student_id=?", (self.offering, student))
            conn.execute("DELETE FROM learning_certificates WHERE class_offering_id=? AND student_id=?", (self.offering, student))
            conn.execute("DELETE FROM learning_progress_snapshots WHERE class_offering_id=? AND student_id=?", (self.offering, student))
            conn.execute("""INSERT INTO learning_progress_snapshots
                (class_offering_id,student_id,score,progress_percent,components_json,metrics_json,level_key,calculated_at,dirty)
                VALUES(?,?,?,29,?,?,'mortal','2026-09-05T10:00:00',0)""",
                (self.offering, student, score, json.dumps(metrics["components"]), json.dumps(metrics)))

    def seed_assignment(self, title, **overrides):
        row = {"course_id": self.course, "class_offering_id": self.offering, "title": title, "status": "published",
               "availability_mode": "deadline", "auto_close": 1, "due_at": "2026-09-05T10:05:00",
               "requirements_md": "保留的作业要求", "rubric_md": "保留的评分标准"}
        row.update(overrides)
        with self.db() as conn:
            cursor = conn.execute(f"INSERT INTO assignments ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
            return cursor.lastrowid

    def seed_submission(self, assignment, student, *, status="graded", score=97.431, feedback="", **extra):
        row = {"assignment_id": assignment, "student_pk_id": student, "student_name": "隔离测试", "status": status,
               "score": score, "feedback_md": feedback, "submitted_at": "2026-09-05T09:00:00", **extra}
        with self.db() as conn:
            return conn.execute(f"INSERT INTO submissions ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values())).lastrowid

    def frozen(self, moment):
        self.clock = moment.isoformat()
        stack = ExitStack()
        stack.enter_context(patch("classroom_app.services.dashboard_workspace_service.china_now", return_value=moment))
        stack.enter_context(patch("classroom_app.services.assignment_lifecycle_service._utc_like_now", return_value=moment))
        stack.enter_context(patch("classroom_app.services.late_submission_policy.utc_like_now", return_value=moment))
        stack.enter_context(patch("classroom_app.routers.homework_parts.assignments.utc_like_now", return_value=moment))
        return stack

    def test_d12_same_snapshot_course_profile_stage_rank_and_ssr_rounding(self):
        self.seed_snapshot(self.student, 2.3)
        self.seed_snapshot(self.peer, 1.7)
        progress = self.get(f"/api/classrooms/{self.offering}/learning/progress").json()["progress"]
        profile = self.get("/api/learning/cultivation-profile").json()["profile"]
        classroom = self.get(f"/classroom/{self.offering}")
        dashboard = self.get("/dashboard")
        self.assertEqual(2.3, progress["score"])
        self.assertEqual(progress["score"], progress["class_position"]["current"]["score"])
        self.assertEqual(1, progress["class_position"]["current"]["rank"])
        self.assertEqual(progress["score"], profile["score"])
        self.assertEqual(progress["score"], profile["best_course"]["score"])
        self.assertEqual(self.offering, profile["best_course"]["class_offering_id"])
        self.assertEqual(progress["progress_percent"], progress["next_stage"]["progress_percent"])
        self.assertNotEqual(progress["score"], progress["progress_percent"])
        self.assertEqual(progress["progress_percent"], profile["progress_percent"])
        self.assertEqual(progress["score"], classroom.context["classroom_page"]["learning_progress"]["score"])
        self.assertEqual(progress["score"], dashboard.context["cultivation_profile"]["score"])
        self.assertTrue(re.search(r"修为\s*<strong>2\.3</strong>", classroom.text), "SSR summary must retain the same one-decimal score")
        self.verified_values = {"score": progress["score"], "profile_score": profile["score"],
                                "rank_score": progress["class_position"]["current"]["score"],
                                "rank": progress["class_position"]["current"]["rank"],
                                "stage_percent": progress["next_stage"]["progress_percent"],
                                "progress_percent": progress["progress_percent"]}

    def test_d18_missing_and_dirty_snapshots_recalculate_real_sources_not_zero(self):
        from classroom_app.services import learning_progress_service as learning
        with self.db() as conn:
            expected = learning._build_learning_metrics(conn, self.offering, self.student)["score"]
        self.assertGreater(expected, 0, "Fixture must contain genuine reading/task activity")
        for mode in ("missing", "dirty"):
            self.seed_snapshot(self.student, 99.9)
            with self.db() as conn:
                if mode == "missing":
                    conn.execute("DELETE FROM learning_progress_snapshots WHERE class_offering_id=? AND student_id=?", (self.offering, self.student))
                else:
                    conn.execute("UPDATE learning_progress_snapshots SET dirty=1 WHERE class_offering_id=? AND student_id=?", (self.offering, self.student))
            result = self.get(f"/api/classrooms/{self.offering}/learning/progress").json()["progress"]
            self.assertEqual(expected, result["score"], mode)
            self.assertNotEqual(99.9, result["score"])
            with self.db() as conn:
                snapshot = conn.execute("SELECT score,dirty FROM learning_progress_snapshots WHERE class_offering_id=? AND student_id=?", (self.offering, self.student)).fetchone()
                self.assertEqual((expected, 0), tuple(snapshot))
        self.verified_values = {"recalculated_score": expected, "modes": ["missing", "dirty"], "dirty_after": 0}

    def test_c18_mixed_generated_and_teacher_content_keeps_summary_and_full_http_details(self):
        intro = "第 1 次课，按教务实际排课自动生成，请补充本次课要讲的知识点、实验内容或案例任务。"
        authored = "教师补充：比较交换机与路由器，提交课堂抓包实验。"
        content = intro + "\n上课时间：2026-09-06\n上课地点：B310\n" + authored
        with self.db() as conn:
            session_id = conn.execute("SELECT id FROM class_offering_sessions WHERE class_offering_id=? ORDER BY order_index LIMIT 1", (self.offering,)).fetchone()[0]
            conn.execute("UPDATE class_offering_sessions SET order_index=1,session_date='2026-09-06',academic_campus='',academic_location='B310',schedule_source='academic_sync',content=? WHERE id=?", (content, session_id))
        response = self.get(f"/classroom/{self.offering}")
        session = next(row for row in response.context["classroom_page"]["teaching_plan"]["sessions"] if row["id"] == session_id)
        self.assertEqual(authored, session["workspace_summary"])
        self.assertEqual(content, session["detail_content"])
        self.assertTrue(authored in response.text or json.dumps(authored)[1:-1] in response.text, "Authored text must remain in the rendered/preloaded detail")
        self.verified_values = {"summary": session["workspace_summary"], "full_content_preserved": session["detail_content"] == content}

    def test_d17_r08_unrevealed_group_feedback_absent_from_two_students_html_and_json(self):
        from classroom_app.services import group_assignment_service as groups
        assignment = self.seed_assignment("隔离小组未公布作业", availability_mode="permanent", due_at=None, auto_close=0)
        with self.db() as conn:
            scheme = conn.execute("INSERT INTO group_schemes(class_offering_id,name,status,created_by_teacher_id) VALUES(?,'验收分组','active',?)", (self.offering, self.teacher)).lastrowid
            group = conn.execute("INSERT INTO study_groups(class_offering_id,name,status,join_policy,max_members,created_by_role,created_by_user_pk,scheme_id,group_index) VALUES(?,'验收同组','active','scheme_random',6,'teacher',?,?,1)", (self.offering, self.teacher, scheme)).lastrowid
            for student in (self.student, self.peer):
                conn.execute("INSERT INTO study_group_members(group_id,student_id,member_role,status) VALUES(?,?,'member','active')", (group, student))
            groups.bind_assignment_to_scheme(conn, assignment_id=assignment, class_offering_id=self.offering, scheme_id=scheme, teacher_id=self.teacher)
        secrets = ["UNREVEALED_OWNER_FEEDBACK_97_431", "UNREVEALED_PEER_FEEDBACK_91_827"]
        for student, score, secret in zip((self.student, self.peer), (97.431, 91.827), secrets):
            self.seed_submission(assignment, student, score=score, feedback=secret)
        peer_client = self.login("otherStudent")
        for client in (self.client, peer_client):
            classroom = self.get(f"/classroom/{self.offering}", client)
            detail = self.get(f"/assignment/{assignment}", client)
            workspace = self.get("/api/dashboard/workspace?limit=100", client)
            calendar = self.get("/api/dashboard/calendar", client)
            for response in (classroom, detail, workspace, calendar):
                for secret in secrets:
                    self.assertFalse(secret in response.text, "Unrevealed feedback leaked in an HTTP response")
            task = next(item for item in classroom.context["assignments"] if item["id"] == assignment)
            self.assertTrue(task["group_pending"])
            self.assertIsNone(task["submission_score"])
            self.assertIsNone(task["submission_feedback_md"])
            self.assertTrue(detail.context["group_assignment_state"]["pending"])
            self.assertIsNone(detail.context["submission"]["score"])
            self.assertFalse(detail.context["submission"]["feedback_md"])
            self.assertIn("小组", detail.text)
        with self.db() as conn:
            paper = conn.execute("SELECT id FROM exam_papers WHERE teacher_id=? LIMIT 1", (self.teacher,)).fetchone()
            self.assertIsNotNone(paper)
            conn.execute("UPDATE assignments SET exam_paper_id=? WHERE id=?", (paper[0], assignment))
        for client in (self.client, peer_client):
            exam = self.get(f"/exam/take/{assignment}", client)
            self.assertTrue(exam.context["group_assignment_state"]["pending"])
            self.assertIsNone(exam.context["submission"]["score"])
            for secret in secrets:
                self.assertFalse(secret in exam.text, "Unreleased exam result entered preloaded JavaScript")
        # Release only the first student's result: their content returns, while
        # the other student's feedback must still remain private/unreleased.
        with self.db() as conn:
            submission_id = conn.execute("SELECT id FROM submissions WHERE assignment_id=? AND student_pk_id=?", (assignment, self.student)).fetchone()[0]
            groups._upsert_member_result(conn, assignment_id=str(assignment), group_id=group,
                                         class_offering_id=self.offering, student_pk_id=self.student,
                                         submission_id=submission_id, work_score=97.431, final_score=93.945, revealed=1)
        released = self.get(f"/exam/take/{assignment}")
        self.assertTrue(secrets[0] in released.text)
        self.assertFalse(secrets[1] in released.text)
        self.verified_values = {"students": 2, "surfaces": ["classroom", "assignment", "exam", "workspace", "calendar"],
                                "unreleased_markers_absent": True, "released_own_feedback_restored": True}

    def test_d15_d22_home_classroom_detail_and_time_api_share_exact_effective_windows(self):
        assignment = self.seed_assignment("隔离补交边界", late_submission_enabled=1, late_submission_until="2026-09-05T10:10:00", late_penalty_strategy="fixed", late_penalty_points=5, late_score_cap=80)
        totals = []
        for moment, phase, accepting in ((datetime(2026, 9, 5, 10, 4, 59), "regular", True),
                                         (datetime(2026, 9, 5, 10, 5), "late", True),
                                         (datetime(2026, 9, 5, 10, 10), "closed", False)):
            with self.frozen(moment):
                ws = self.get("/api/dashboard/workspace?limit=100").json()["workspace"]
                item = next(item for item in ws["all_items"] if item["source_id"] == assignment and item["kind"] == "assignment")
                classroom = self.get(f"/classroom/{self.offering}")
                projected = next(item for item in classroom.context["classroom_page"]["assignment_workspace_items"] if item["id"] == assignment)
                detail = self.get(f"/assignment/{assignment}")
                time_state = self.get(f"/api/assignments/time-state?ids={assignment}").json()["assignments"][0]
                self.assertEqual(accepting, item["is_actionable"], phase)
                self.assertEqual(accepting, projected["accepting"], phase)
                self.assertEqual(accepting, detail.context["assignment"]["is_accepting_submissions"], phase)
                self.assertEqual(phase, projected["deadlinePhase"])
                self.assertEqual(phase, time_state["deadline_phase"])
                totals.append({"phase": phase, "home_total": ws["total"], "home_pending": ws["pending_total"],
                               "classroom_total": len(classroom.context["classroom_page"]["assignment_workspace_items"]), "accepting": accepting})
                if phase == "late":
                    self.assertIn("补交", detail.text)
                    self.assertIn("80", projected["latePolicyLabel"])
        self.assertEqual(1, len({row["home_total"] for row in totals}))
        self.assertEqual(1, len({row["classroom_total"] for row in totals}))
        self.assertEqual(totals[0]["home_pending"], totals[1]["home_pending"])
        self.assertEqual(totals[1]["home_pending"] - 1, totals[2]["home_pending"])
        returned = self.seed_assignment("隔离个人重交", status="closed")
        self.seed_submission(returned, self.student, status="submitted", resubmission_allowed=1, resubmission_due_at="2026-09-05T10:20:00")
        for moment, expected in ((datetime(2026, 9, 5, 10, 19, 59), True), (datetime(2026, 9, 5, 10, 20), False)):
            with self.frozen(moment):
                item = next(item for item in self.get("/api/dashboard/workspace?limit=100").json()["workspace"]["all_items"] if item["source_id"] == returned and item["kind"] == "assignment")
                classroom = self.get(f"/classroom/{self.offering}")
                projected = next(item for item in classroom.context["classroom_page"]["assignment_workspace_items"] if item["id"] == returned)
                detail = self.get(f"/assignment/{returned}")
                self.assertEqual(expected, item["is_actionable"])
                self.assertEqual(expected, projected["canResubmit"])
                self.assertEqual(expected, detail.context["can_resubmit_submission"])
        self.verified_values = {"windows": totals, "individual_resubmission": {"before": True, "at_end": False}}

    def test_d22_future_start_preserves_existing_published_acceptance_rule(self):
        assignment = self.seed_assignment("未来开始但已发布", starts_at="2026-09-05T11:00:00", due_at="2026-09-05T12:00:00")
        with self.frozen(datetime(2026, 9, 5, 10)):
            item = next(item for item in self.get("/api/dashboard/workspace?limit=100").json()["workspace"]["all_items"] if item["source_id"] == assignment and item["kind"] == "assignment")
            detail = self.get(f"/assignment/{assignment}")
            self.assertTrue(item["is_actionable"])
            self.assertTrue(detail.context["assignment"]["is_accepting_submissions"])
        self.verified_values = {"published_before_starts_at_accepts": True, "preserved_existing_rule": True}

    def test_d18_a12_failed_snapshot_returns_unavailable_not_zero_and_retry_recovers(self):
        with self.db() as conn:
            conn.execute("DELETE FROM learning_progress_snapshots WHERE class_offering_id=? AND student_id=?", (self.offering, self.student))
        with patch("classroom_app.services.learning_progress_service._build_learning_metrics", side_effect=RuntimeError("synthetic unavailable source")):
            api = self.client.get(f"/api/classrooms/{self.offering}/learning/progress")
            self.assertEqual(503, api.status_code)
            self.assertEqual("修为暂时无法读取，请稍后重试", api.json()["detail"])
            self.assertNotIn("progress", api.json())
            page = self.get(f"/classroom/{self.offering}")
            self.assertIsNone(page.context["classroom_page"]["learning_progress"])
            self.assertEqual("修为暂时无法读取，请稍后重试", page.context["classroom_page"]["learning_progress_error"])
            self.assertFalse(re.search(r"修为\s*<strong>0(?:\.0)?</strong>", page.text))
        recovered = self.get(f"/api/classrooms/{self.offering}/learning/progress").json()["progress"]
        self.assertGreater(recovered["score"], 0)
        self.verified_values = {"failure_status": api.status_code, "failure_has_progress": False,
                                "page_progress": None, "recovered_score": recovered["score"]}

    def test_d17_r08_group_visibility_failure_fails_closed_without_feedback(self):
        assignment = self.seed_assignment("公开任务的状态故障", availability_mode="permanent", due_at=None, auto_close=0)
        self.seed_submission(assignment, self.student, feedback="RESULT_MUST_NOT_ESCAPE_ON_FAILURE")
        with patch("classroom_app.services.group_assignment_service.get_student_display_state", side_effect=RuntimeError("synthetic visibility failure")):
            for path in (f"/classroom/{self.offering}", f"/assignment/{assignment}"):
                response = self.client.get(path)
                self.assertEqual(503, response.status_code)
                self.assertEqual({"detail": "暂时无法确认小组成绩公布状态，请稍后重试"}, response.json())
                self.assertFalse("RESULT_MUST_NOT_ESCAPE_ON_FAILURE" in response.text)
        self.verified_values = {"visibility_failure_status": 503, "result_marker_absent": True}

    def test_d17_r08_personal_trials_and_todos_stay_private_in_both_students_ssr_and_json(self):
        markers = ("CLOSURE_OWNER_PRIVATE", "CLOSURE_PEER_PRIVATE")
        trial_ids = []
        with self.db() as conn:
            paper = conn.execute("SELECT id FROM exam_papers WHERE teacher_id=? LIMIT 1", (self.teacher,)).fetchone()[0]
        for student, marker in zip((self.student, self.peer), markers):
            assignment = self.seed_assignment(marker + "_TRIAL", availability_mode="permanent", due_at=None, auto_close=0, exam_paper_id=paper)
            trial_ids.append(assignment)
            with self.db() as conn:
                conn.execute("INSERT INTO learning_stage_exam_attempts(class_offering_id,student_id,stage_key,assignment_id,exam_paper_id,status) VALUES(?,?,'enlightenment',?,?,'generated')", (self.offering, student, assignment, paper))
                conn.execute("INSERT INTO classroom_todos(class_offering_id,owner_role,owner_user_pk,title,metadata_json) VALUES(?,'student',?,?,'{}')", (self.offering, student, marker + "_TODO"))
        for index, client in enumerate((self.client, self.login("otherStudent"))):
            own, other = markers[index], markers[1 - index]
            for path in ("/dashboard", f"/classroom/{self.offering}", "/api/dashboard/workspace?limit=100", "/api/dashboard/calendar"):
                response = self.get(path, client)
                self.assertFalse(other in response.text, "Peer private marker leaked in SSR/preloaded data")
                if path == "/dashboard":
                    self.assertTrue(own + "_TODO" in response.text)
                if path.startswith("/classroom/"):
                    ids = {item["id"] for item in response.context["classroom_page"]["assignment_workspace_items"]}
                    self.assertIn(trial_ids[index], ids)
                    self.assertNotIn(trial_ids[1 - index], ids)
            denied = client.get(f"/exam/take/{trial_ids[1-index]}", follow_redirects=False)
            # HTML permission failures retain the application's existing 303
            # warning-page flow; API failures use JSON 403.
            self.assertEqual(303, denied.status_code)
            self.assertTrue(denied.headers["location"].startswith("/auth/forbidden?"))
        self.verified_values = {"students": 2, "private_trial_and_todo_absent_from_peer": True, "foreign_html_denial": "303 /auth/forbidden"}

    def test_d22_unpublished_to_published_is_shared_by_home_classroom_and_detail(self):
        assignment = self.seed_assignment("CLOSURE_PUBLICATION_BOUNDARY", status="new", due_at="2026-09-05T12:00:00")
        with self.frozen(datetime(2026, 9, 5, 10)):
            for published in (False, True):
                if published:
                    with self.db() as conn:
                        conn.execute("UPDATE assignments SET status='published' WHERE id=?", (assignment,))
                ws = self.get("/api/dashboard/workspace?limit=100").json()["workspace"]
                ids = {item["source_id"] for item in ws["all_items"] if item["kind"] == "assignment"}
                self.assertEqual(published, assignment in ids)
                classroom = self.get(f"/classroom/{self.offering}")
                ids = {item["id"] for item in classroom.context["classroom_page"]["assignment_workspace_items"]}
                self.assertEqual(published, assignment in ids)
                detail = self.get(f"/assignment/{assignment}")
                if published:
                    self.assertTrue(detail.context["assignment"]["is_accepting_submissions"])
                else:
                    self.assertEqual("/auth/forbidden", detail.url.path)
                    self.assertFalse("CLOSURE_PUBLICATION_BOUNDARY" in detail.text)
        self.verified_values = {"draft_hidden": True, "published_visible_and_accepting": True}

    def test_a12_trial_generation_and_failure_are_visible_without_starting_ai(self):
        from classroom_app.services.learning_progress_service import LEARNING_LEVELS
        self.seed_snapshot(self.student, 20)
        with self.db() as conn:
            attempt = conn.execute("INSERT INTO learning_stage_exam_attempts(class_offering_id,student_id,stage_key,status) VALUES(?,?,?,'generating')", (self.offering, self.student, LEARNING_LEVELS[0]["key"])).lastrowid
        generating = self.get(f"/classroom/{self.offering}")
        self.assertEqual("generating", generating.context["classroom_page"]["learning_progress"]["next_stage"]["status"])
        self.assertTrue("试炼生成中" in generating.text)
        with self.db() as conn:
            conn.execute("UPDATE learning_stage_exam_attempts SET status='failed',ai_error='合成生成失败' WHERE id=?", (attempt,))
        failed = self.get(f"/classroom/{self.offering}")
        stage = failed.context["classroom_page"]["learning_progress"]["eligible_stage"]
        self.assertEqual("failed", stage["latest_attempt"]["status"])
        self.assertTrue("试炼未完成 · 可重试" in failed.text)
        self.verified_values = {"generating_status_visible": True, "failure_retry_status_visible": True, "ai_requests": 0}


if __name__ == "__main__":
    unittest.main()
