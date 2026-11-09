"""AI-only accounts never acquire human credentials; owned SQLite fixtures."""
import unittest
from unittest.mock import patch

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from classroom_app import dependencies
from classroom_app.routers.ui_parts import auth
from classroom_app.services.account_control_service import ensure_student_human_auth
from classroom_app.services.student_auth_service import build_password_setup_token
from classroom_app.services import wechat_mp_service
from classroom_app.db.schema_account_control import ensure_account_control_schema
from tests.wanderer_test_support import WandererDbTestCase


class WandererAccountControlTest(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            self.wanderer_row = dict(conn.execute("SELECT * FROM wanderer_agents ORDER BY id LIMIT 1").fetchone())
            self.sid = int(self.wanderer_row["student_id"])
            conn.execute("UPDATE students SET enrollment_status='wanderer',control_mode='ai',hashed_password=NULL,password_reset_required=0 WHERE id=?", (self.sid,))
            self.student = dict(conn.execute("SELECT * FROM students WHERE id=?", (self.sid,)).fetchone())
            conn.commit()
        dependencies._drop_identity_cache_for_user(str(self.sid), "student")
        app = FastAPI()
        app.include_router(auth.router)
        @app.get("/api/synthetic/optional")
        def optional(user=Depends(dependencies.get_current_user_optional)):
            return {"user": user}
        self.client = TestClient(app, follow_redirects=False)
        self.addCleanup(self.client.close)

    def test_identity_and_preexisting_setup_token_cannot_take_over(self):
        response = self.client.post("/api/student/login/identity", data={
            "name": self.student["name"], "student_id_number": self.student["student_id_number"]})
        self.assertIn(response.status_code, (400, 403))
        self.assertNotIn("setup_token", response.json())
        token = build_password_setup_token(self.sid, "/dashboard", "first_login")
        setup = self.client.post("/api/student/password/setup", data={
            "setup_token": token, "password": "SyntheticPassword2026", "confirm_password": "SyntheticPassword2026"})
        self.assertEqual(setup.status_code, 403)
        with self.connect() as conn:
            self.assertIsNone(conn.execute("SELECT hashed_password FROM students WHERE id=?", (self.sid,)).fetchone()[0])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM user_sessions WHERE role='student' AND user_id=?", (str(self.sid),)).fetchone()[0], 0)

    def test_live_binding_blocks_legacy_active_marker_and_signed_session_issuance(self):
        with self.connect() as conn:
            conn.execute("UPDATE students SET enrollment_status='active',control_mode='human' WHERE id=?", (self.sid,))
            conn.commit()
            with self.assertRaises(HTTPException):
                ensure_student_human_auth(conn, self.sid)
        with self.assertRaises(HTTPException):
            dependencies.create_access_token({"id": self.sid, "role": "student", "name": "synthetic"}, "127.0.0.1")
        with self.assertRaises(HTTPException):
            dependencies.validate_authenticated_user_identity({"id": self.sid, "role": "student", "is_wanderer": True,
                                                               "auth_channel": "wanderer_service"})

    def test_stolen_password_and_mp_channels_are_rejected(self):
        password = "SyntheticPassword2026"
        with self.connect() as conn:
            conn.execute("UPDATE students SET hashed_password=? WHERE id=?", (dependencies.get_password_hash(password), self.sid))
            conn.commit()
        response = self.client.post("/api/student/login/password", data={"identifier": self.student["student_id_number"], "password": password})
        self.assertEqual(response.status_code, 403)
        with self.connect() as conn:
            with self.assertRaises(HTTPException):
                wechat_mp_service.issue_mp_session(conn, user_role="student", user_pk=self.sid)
            self.assertIsNone(wechat_mp_service.load_mp_user(conn, {"user_role": "student", "user_pk": self.sid}))
            with self.assertRaises(HTTPException):
                wechat_mp_service.create_binding(conn, user_role="student", user_pk=self.sid, openid="synthetic-openid")
            from classroom_app.services.remember_login_service import issue_remember_token
            with self.assertRaises(HTTPException):
                issue_remember_token(conn, user_id=self.sid, role="student", session_id="synthetic-session")

    def test_legacy_signed_session_is_denied_and_conversion_invalidates_cached_identity(self):
        from classroom_app.services.account_control_service import mark_student_ai_controlled
        # Simulate an issued pre-migration token, without weakening the route.
        with patch("classroom_app.services.account_control_service.ensure_student_human_auth"):
            token = dependencies.create_access_token({"id": self.sid, "role": "student", "name": "synthetic"}, "127.0.0.1")
        self.client.cookies.set("access_token", token)
        self.assertEqual(self.client.get("/api/synthetic/optional").status_code, 403)
        dependencies._cache_valid_identity("student", str(self.sid))
        with self.connect() as conn:
            mark_student_ai_controlled(conn, self.sid)
            conn.commit()
        self.assertIsNone(dependencies.verify_token(token))
        self.assertFalse(dependencies._identity_cache_is_valid("student", str(self.sid)))

    def test_migration_only_revokes_ai_credentials_and_is_repeatable(self):
        with self.connect() as conn:
            class_id = self.student["class_id"]
            conn.execute("INSERT INTO students(name,student_id_number,class_id,hashed_password,enrollment_status) VALUES('Synthetic human','AUTH-MIGRATION-HUMAN',?,'human-hash','active') ON CONFLICT(student_id_number) DO NOTHING", (class_id,))
            human = conn.execute("SELECT id FROM students WHERE student_id_number='AUTH-MIGRATION-HUMAN'").fetchone()[0]
            conn.execute("UPDATE students SET hashed_password='ai-stolen-hash',control_mode='human' WHERE id=?", (self.sid,))
            ensure_account_control_schema(conn)
            ensure_account_control_schema(conn)
            self.assertEqual(conn.execute("SELECT hashed_password FROM students WHERE id=?", (human,)).fetchone()[0], "human-hash")
            row = conn.execute("SELECT hashed_password,control_mode FROM students WHERE id=?", (self.sid,)).fetchone()
            self.assertIsNone(row["hashed_password"])
            self.assertEqual(row["control_mode"], "ai")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM account_control_migrations WHERE student_id=?", (self.sid,)).fetchone()[0], 1)
            conn.commit()

    def test_placeholder_class_never_grants_private_classroom_read(self):
        from classroom_app.services.resource_access_service import _student_in_classroom, _student_in_course
        from classroom_app.services.wanderer_actions_classroom import current_offerings
        with self.connect() as conn:
            self.assertFalse(_student_in_classroom(conn, self.sid, 999))
            self.assertFalse(_student_in_course(conn, self.sid, 999))
            self.assertEqual(current_offerings(conn, self.wanderer_row), [])


if __name__ == "__main__":
    unittest.main()
