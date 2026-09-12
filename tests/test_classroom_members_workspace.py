from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader

from classroom_app.dependencies import get_current_user
from classroom_app.db.schema_offering_class_links import ensure_offering_class_links_schema, _READY_KEYS
from classroom_app.routers import learning
from classroom_app.services.classroom_member_service import list_classroom_members
from classroom_app.services.cultivation_weight_service import (
    CultivationWeightConflictError, load_cultivation_weight_config,
)
from classroom_app.services import learning_progress_service as progress
from tests.test_cultivation_weights import _build_conn


class ClassroomMemberWorkspaceTests(unittest.TestCase):
    def setUp(self):
        _READY_KEYS.clear()
        self.addCleanup(_READY_KEYS.clear)
        self.conn = _build_conn(check_same_thread=False)
        self.addCleanup(self.conn.close)
        ensure_offering_class_links_schema(self.conn, engine="sqlite", force=True)
        self.conn.execute("INSERT INTO classes(id,name) VALUES(11,'Second class')")
        self.conn.execute("INSERT INTO class_offering_class_links(offering_id,class_id,teacher_id,is_primary) VALUES(1,11,30,0)")
        # Primary fallback is preserved alongside explicit links by production helpers.
        self.conn.execute("INSERT INTO students(id,class_id,name,student_id_number) VALUES(10,11,'Percent% Name','000000000001')")
        self.conn.execute("INSERT INTO students(id,class_id,name,student_id_number) VALUES(11,11,'PercentX Name','0004')")
        self.conn.execute("INSERT INTO cultivation_alerts(class_offering_id,student_id,rule_key,severity,status) VALUES(1,10,'test','L1','active')")
        self.conn.commit()

    def test_roster_is_complete_paginated_and_does_not_calculate_learning(self):
        with patch.object(progress, "_build_learning_metrics", side_effect=AssertionError("no calculations")):
            first = list_classroom_members(self.conn, 1, page_size=2)
            second = list_classroom_members(self.conn, 1, page=2, page_size=2)
        self.assertEqual(first["total"], 4)
        self.assertEqual(first["student_count"], 4)
        self.assertEqual(first["attention_count"], 1)
        self.assertEqual({row["id"] for row in first["items"] + second["items"]}, {7,8,10,11})
        self.assertTrue(all(row["score"] is None for row in first["items"]))
        self.assertEqual(second["items"][0]["student_id_number"], "000000000001")

    def test_filter_counts_use_all_matching_rows_and_escape_wildcards(self):
        search = list_classroom_members(self.conn, 1, q="%", page=999)
        self.assertEqual(search["total"], 1)
        self.assertEqual(search["page"], 1)
        self.assertEqual(search["items"][0]["id"], 10)
        alert = list_classroom_members(self.conn, 1, state="attention", class_id=11)
        self.assertEqual(alert["total"], 1)
        self.assertEqual(list_classroom_members(self.conn, 1, class_id=999)["total"], 0)

    def test_teacher_shell_survives_missing_overview_and_has_one_dialog(self):
        env = Environment(loader=FileSystemLoader("templates"))
        html = env.get_template("partials/classroom_members/workspace.html").render(
            classroom={"id": 1, "course_name": "Course", "class_name": "Class"}, classroom_page={"learning_overview": None})
        self.assertEqual(html.count('role="tab"'), 6)
        self.assertEqual(html.count('role="dialog"'), 1)
        self.assertIn('data-attendance-classroom-panel', html)
        self.assertIn('data-learning-roster-search', html)
        self.assertNotIn('id="student-insight-modal"', html)

    def test_workspace_switch_hides_teacher_entry_and_shell_but_keeps_student_entry(self):
        env = Environment(loader=FileSystemLoader("templates"))
        env.globals["app_topbar_icon"] = lambda _name: ""
        # Render the real classroom topbar without unrelated page data fixtures.
        source = Path("templates/classroom_main_v4.html").read_text(encoding="utf-8")
        topbar = env.from_string(source.split('{% block body %}', 1)[1].split('</header>', 1)[0] + '</header>')
        context = {"classroom": {"id": 1, "course_name": "Course", "class_name": "Class"},
                   "classroom_page": {"learning_progress": {"score": 12}}, "classroom_members_workspace_enabled": False}
        disabled = topbar.render(**context, user_info={"role": "teacher"})
        self.assertNotIn('data-learning-modal-open', disabled)
        self.assertIn('data-classroom-closeout-open', disabled)
        self.assertIn('data-cw-open="materials"', disabled)
        self.assertNotIn('data-member-workspace', env.get_template('partials/classroom_members/workspace.html').render(**context))
        student = topbar.render(**context, user_info={"role": "student"})
        self.assertIn('data-learning-modal-open', student)
        self.assertIn('本课修为', student)
        context["classroom_members_workspace_enabled"] = True
        self.assertIn('data-learning-modal-open', topbar.render(**context, user_info={"role": "teacher"}))

    def test_disabled_workspace_fragment_is_controlled_and_does_not_read_database(self):
        from classroom_app.routers.ui_parts import classroom
        app = FastAPI(); app.include_router(classroom.router)
        app.dependency_overrides[get_current_user] = lambda: {"role": "teacher", "id": 30}
        with patch.object(classroom.classroom_feature_flags, 'CLASSROOM_MEMBERS_WORKSPACE_ENABLED', False), \
             patch.object(classroom, 'get_db_connection', side_effect=AssertionError('disabled fragment must not load')), TestClient(app) as client:
            response = client.get('/api/classrooms/1/member-panels/settings')
            self.assertEqual(response.status_code, 503)
            self.assertIn('暂时停用', response.json()['detail'])
            app.dependency_overrides[get_current_user] = lambda: {"role": "student", "id": 30}
            self.assertEqual(client.get('/api/classrooms/1/member-panels/settings').status_code, 403)

    def test_weight_cas_blocks_late_writer_without_duplicate_score_events(self):
        stale = load_cultivation_weight_config(self.conn, 1)
        weights = {"material": 30, "task": 55, "interaction": 10, "consistency": 5}
        result = progress.update_class_cultivation_weights(self.conn, 1, teacher_id=30, weights_payload=weights, expected_revision=0)
        self.assertEqual(result["weight_settings"]["revision"], 1)
        self.conn.commit()
        with patch.object(progress, "load_cultivation_weight_config", return_value=stale):
            with self.assertRaises(CultivationWeightConflictError):
                progress.update_class_cultivation_weights(self.conn, 1, teacher_id=30,
                    weights_payload={"material": 30, "task": 25, "interaction": 40, "consistency": 5}, expected_revision=0)
        self.assertEqual(self.conn.execute("SELECT cultivation_weights_revision FROM class_offerings WHERE id=1").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM cultivation_score_events").fetchone()[0], 4)

    def test_members_route_rejects_student_even_with_matching_numeric_identity(self):
        @contextmanager
        def database():
            try: yield self.conn
            except Exception:
                self.conn.rollback()
                raise
        app = FastAPI(); app.include_router(learning.router)
        app.dependency_overrides[get_current_user] = lambda: {"role": "student", "id": 30}
        with patch.object(learning, "get_db_connection", database), TestClient(app) as client:
            self.assertEqual(client.get('/api/classrooms/1/members').status_code, 403)
            app.dependency_overrides[get_current_user] = lambda: {"role": "teacher", "id": 30}
            self.assertEqual(client.get('/api/classrooms/1/members').json()["total"], 4)
            self.assertEqual(client.get('/api/classrooms/1/members?state=bad').status_code, 400)
            self.assertEqual(client.post('/api/classrooms/1/learning/weights', json={"weights": {}}).status_code, 428)

    def test_lazy_panels_authorize_classroom_and_do_not_calculate_unrelated_learning(self):
        from classroom_app.routers.ui_parts import classroom

        self.conn.execute('ALTER TABLE teachers ADD COLUMN is_super_admin INTEGER DEFAULT 0')
        self.conn.execute('ALTER TABLE teachers ADD COLUMN is_active INTEGER DEFAULT 1')

        @contextmanager
        def database():
            yield self.conn

        app = FastAPI(); app.include_router(classroom.router)
        app.dependency_overrides[get_current_user] = lambda: {"role": "teacher", "id": 30}
        with patch.object(classroom, "get_db_connection", database), \
             patch.object(classroom, "build_class_learning_overview", side_effect=AssertionError("unrelated learning calculation")), TestClient(app) as client:
            for key, selector in (("settings", "data-weight-revision"), ("exams", "data-exam-roster-panel")):
                response = client.get(f'/api/classrooms/1/member-panels/{key}')
                self.assertEqual(response.status_code, 200)
                self.assertIn(selector, response.text)
                self.assertEqual(response.headers['cache-control'], 'private, no-store')
            self.assertEqual(client.get('/api/classrooms/1/member-panels/not-a-template').status_code, 404)
            app.dependency_overrides[get_current_user] = lambda: {"role": "student", "id": 30}
            self.assertEqual(client.get('/api/classrooms/1/member-panels/exams').status_code, 403)
            app.dependency_overrides[get_current_user] = lambda: {"role": "teacher", "id": 999}
            self.assertEqual(client.get('/api/classrooms/1/member-panels/exams').status_code, 403)

    def test_repeated_alert_side_effect_returns_receipt_without_resending(self):
        @contextmanager
        def database():
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        app = FastAPI(); app.include_router(learning.router)
        app.dependency_overrides[get_current_user] = lambda: {"role": "teacher", "id": 30}
        alert_id = self.conn.execute("SELECT id FROM cultivation_alerts LIMIT 1").fetchone()[0]
        message = {"conversation_key": "synthetic", "message_serialized": {"id": 1}}
        with patch.object(learning, "get_db_connection", database), \
             patch.object(learning, "build_class_cultivation_alert_context", return_value={"total_count": 1}), \
             patch.object(learning, "create_private_message", return_value=message) as send, TestClient(app) as client:
            url = f'/api/classrooms/1/learning/alerts/{alert_id}/actions'
            first = client.post(url, json={"action": "private_message"})
            second = client.post(url, json={"action": "private_message"})
            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 200)
            self.assertTrue(second.json()["side_effect"]["already_applied"])
            self.assertEqual(send.call_count, 1)

    def test_failed_alert_side_effect_rolls_back_receipt_for_explicit_retry(self):
        @contextmanager
        def database():
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        app = FastAPI(); app.include_router(learning.router)
        app.dependency_overrides[get_current_user] = lambda: {"role": "teacher", "id": 30}
        alert_id = self.conn.execute("SELECT id FROM cultivation_alerts LIMIT 1").fetchone()[0]
        with patch.object(learning, "get_db_connection", database), \
             patch.object(learning, "create_private_message", side_effect=ValueError("synthetic rejected")), TestClient(app) as client:
            self.assertEqual(client.post(f'/api/classrooms/1/learning/alerts/{alert_id}/actions', json={"action":"private_message"}).status_code, 400)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM cultivation_alert_action_receipts").fetchone()[0], 0)


if __name__ == '__main__': unittest.main()
