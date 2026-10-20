"""Teacher detail and lifecycle transactions on owned synthetic databases."""
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.db.migrations import _ensure_teacher_organization_memberships_schema
from classroom_app.db.schema_academic_teacher_timetables import ensure_academic_teacher_timetable_schema
from classroom_app.dependencies import verify_password
from classroom_app.routers.manage_parts import system_config
from classroom_app.services import teacher_account_service as accounts
from classroom_app.services.agent_delegation_service import create_persistent_authorization
from classroom_app.services.teacher_identity_service import teacher_login_candidates
from tests.test_agent_platform_writes import PlatformWriteFixture


class TeacherAccountLifecycleTests(PlatformWriteFixture):
    def setUp(self):
        super().setUp()
        ensure_academic_teacher_timetable_schema(self.conn)
        self.conn.execute("UPDATE teachers SET is_super_admin=1 WHERE id=7")
        for teacher_id in (7, 8):
            accounts.upsert_teacher_membership(
                self.conn, teacher_id=teacher_id, school_code="gxufl", school_name="广西外国语学院",
                college="合成学院", department="合成系", employee_number=f"000{teacher_id}", is_primary=True,
            )
        self.conn.execute(
            """INSERT INTO user_sessions(session_user_key,session_id,user_id,role,expires_at)
               VALUES('teacher:8','target-session','8','teacher','2099-01-01T00:00:00+00:00')"""
        )
        self.persistent = create_persistent_authorization(
            self.conn, actor_role="teacher", actor_id=8, source_session_id="target-session",
            scopes=["platform:read"], intent_reference="Synthetic lifecycle probe", ttl_seconds=600,
        )
        self.conn.execute(
            """INSERT INTO academic_teacher_directory(school_code,teacher_no,name,college_name,profile_json,synced_at)
               VALUES('gxufl','0008','Synthetic directory teacher','合成学院','{}','2026-10-21T00:00:00')"""
        )
        self.conn.commit()
        app = FastAPI()
        app.include_router(system_config.router, prefix="/manage")
        app.dependency_overrides[system_config.get_current_teacher] = lambda: self.teacher
        guard = patch.object(system_config, "get_db_connection", self.connection)
        guard.start()
        self.addCleanup(guard.stop)
        self.client = TestClient(app, raise_server_exceptions=True)
        self.addCleanup(self.client.close)

    def record(self, teacher_id=8):
        return accounts.get_teacher_account(self.conn, teacher_id)

    def post(self, suffix, *, teacher_id=8, **fields):
        return self.client.post(
            f"/manage/system/teachers/{teacher_id}{suffix}",
            data={"expected_revision": self.record(teacher_id)["revision"], **fields},
        )

    def edit(self, **changes):
        teacher = self.record()
        fields = {key: teacher[key] for key in (
            "name", "email", "employee_number", "school_code", "school_name", "college", "department",
        )}
        return self.post("", **{**fields, **changes})

    def assert_target_revoked(self):
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE session_user_key='teacher:8'").fetchone()[0])
        self.assertEqual("revoked", self.conn.execute(
            "SELECT status FROM agent_persistent_authorizations WHERE id=?", (self.persistent["id"],),
        ).fetchone()[0])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE session_user_key='student:7'").fetchone()[0])

    def test_detail_has_current_counts_revision_and_reverse_link(self):
        self.conn.execute("INSERT INTO classes(id,name,created_by_teacher_id) VALUES(31,'Owned class',8)")
        self.conn.execute("INSERT INTO courses(id,name,created_by_teacher_id) VALUES(21,'Owned course',8)")
        self.conn.execute("INSERT INTO class_offerings(id,course_id,class_id,teacher_id) VALUES(41,21,31,8)")
        self.conn.commit()
        response = self.client.get("/manage/system/teachers/8")
        self.assertEqual(200, response.status_code, response.text)
        body = response.json()
        self.assertEqual((1, 1, 1), tuple(body["teacher"][key] for key in ("class_count", "course_count", "offering_count")))
        self.assertEqual(self.record()["revision"], body["teacher"]["revision"])
        self.assertEqual("linked", body["timetable_links"]["status"])
        self.assertNotIn("hashed_password", response.text)

    def test_stale_or_missing_revision_is_409_without_overwriting_saved_fields(self):
        revision = self.record()["revision"]
        first = self.edit(name="First editor")
        self.assertEqual(200, first.status_code, first.text)
        for stale in (revision, ""):
            response = self.edit(name="Stale editor", expected_revision=stale)
            self.assertEqual(409, response.status_code, response.text)
        self.assertEqual("First editor", self.record()["name"])

    def test_every_lifecycle_and_membership_endpoint_rejects_a_stale_revision(self):
        revision = self.record()["revision"]
        membership_id = self.record()["memberships"][0]["id"]
        self.assertEqual(200, self.edit(name="Current editor").status_code)
        for method, suffix in (
            ("POST", "/memberships"), ("POST", f"/memberships/{membership_id}/primary"),
            ("DELETE", f"/memberships/{membership_id}"), ("POST", "/reset-password"),
            ("POST", "/super-admin/grant"), ("POST", "/super-admin/revoke"),
            ("POST", "/activate"), ("POST", "/deactivate"), ("POST", "/cancel"), ("DELETE", ""),
        ):
            with self.subTest(suffix=suffix, method=method):
                response = self.client.request(
                    method, "/manage/system/teachers/8" + suffix,
                    data={"expected_revision": revision, "password": "valid-password123"},
                )
                self.assertEqual(409, response.status_code, response.text)
        self.assertEqual("active", self.record()["account_status"])
        self.assertEqual("Current editor", self.record()["name"])

    def test_number_change_refreshes_association_and_revokes_old_credentials(self):
        response = self.edit(employee_number="0018")
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("unlinked", response.json()["timetable_links"]["status"])
        self.assertFalse(teacher_login_candidates(self.conn, "0008"))
        self.assertEqual(8, teacher_login_candidates(self.conn, "0018")[0]["id"])
        self.assert_target_revoked()

    def test_failed_association_response_rolls_back_identity_and_revocations(self):
        with patch.object(system_config, "_teacher_detail_payload", side_effect=RuntimeError("synthetic read failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic read failure"):
                self.edit(employee_number="0018")
        self.assertEqual("0008", self.record()["employee_number"])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE session_user_key='teacher:8'").fetchone()[0])
        self.assertEqual("active", self.conn.execute(
            "SELECT status FROM agent_persistent_authorizations WHERE id=?", (self.persistent["id"],),
        ).fetchone()[0])

    def test_plain_contact_edit_does_not_revoke_the_target_session(self):
        response = self.edit(phone="13000000000")
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE session_user_key='teacher:8'").fetchone()[0])

    def test_deactivate_reactivate_preserves_school_flags_and_history(self):
        secondary = accounts.upsert_teacher_membership(
            self.conn, teacher_id=8, school_name="Other school", department="Other department", employee_number="B008",
        )
        accounts.deactivate_teacher_membership(
            self.conn, teacher_id=8, membership_id=secondary["id"], actor_teacher_id=7,
        )
        self.conn.execute("UPDATE classes SET created_by_teacher_id=8 WHERE id=30")
        self.conn.commit()
        before_flags = [(m["id"], m["is_active"], m["is_primary"]) for m in self.record()["memberships"]]
        stopped = self.post("/deactivate")
        self.assertEqual(200, stopped.status_code, stopped.text)
        self.assertEqual("inactive", stopped.json()["timetable_links"]["status"])
        self.assertEqual(before_flags, [(m["id"], m["is_active"], m["is_primary"]) for m in self.record()["memberships"]])
        self.assert_target_revoked()
        resumed = self.post("/activate")
        self.assertEqual(200, resumed.status_code, resumed.text)
        self.assertEqual("active", resumed.json()["teacher"]["account_status"])
        self.assertEqual("linked", resumed.json()["timetable_links"]["status"])
        self.assertEqual(before_flags, [(m["id"], m["is_active"], m["is_primary"]) for m in self.record()["memberships"]])
        self.assertEqual(8, self.conn.execute("SELECT created_by_teacher_id FROM classes WHERE id=30").fetchone()[0])
        self.assertFalse(self.record()["is_super_admin"])
        self.assert_target_revoked()

    def test_old_deactivation_recovers_only_memberships_from_that_event(self):
        second = accounts.upsert_teacher_membership(
            self.conn, teacher_id=8, school_name="Other school", department="Other department", employee_number="B008",
        )
        self.conn.execute("UPDATE teacher_organization_memberships SET is_active=0,is_primary=0,deactivated_at='old' WHERE id=?", (second["id"],))
        self.conn.execute("UPDATE teacher_organization_memberships SET is_active=0,is_primary=0,deactivated_at='account-event' WHERE teacher_id=8 AND school_code='gxufl'")
        self.conn.execute("UPDATE teachers SET is_active=0,deactivated_at='account-event' WHERE id=8")
        self.conn.commit()
        response = self.post("/activate")
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(["gxufl"], [m["school_code"] for m in self.record()["active_memberships"]])

    def test_create_cannot_implicitly_restore_or_overwrite_a_disabled_account(self):
        self.assertEqual(200, self.post("/deactivate").status_code)
        original = self.record()
        response = self.client.post("/manage/system/teachers", data={
            "name": "Different person", "email": "8@example.test", "password": "different-password123",
            "school_code": "gxufl", "school_name": "广西外国语学院", "department": "合成系",
            "employee_number": "0099", "is_super_admin": "1",
        })
        self.assertEqual(400, response.status_code, response.text)
        self.assertIn("原教师卡片", response.json()["detail"])
        self.assertEqual(original, self.record())
        self.assertEqual("test", self.conn.execute("SELECT hashed_password FROM teachers WHERE id=8").fetchone()[0])

    def test_cancel_is_terminal_preserves_history_and_is_not_implicit_restore(self):
        self.conn.execute("UPDATE classes SET created_by_teacher_id=8 WHERE id=30")
        self.conn.commit()
        cancelled = self.post("/cancel")
        self.assertEqual(200, cancelled.status_code, cancelled.text)
        self.assertEqual("cancelled", self.record()["account_status"])
        self.assertEqual("cancelled", cancelled.json()["timetable_links"]["status"])
        self.assertEqual([], self.record()["active_memberships"])
        self.assert_target_revoked()
        for suffix in ("/activate", "/deactivate", "/reset-password", "/super-admin/grant"):
            with self.subTest(suffix=suffix):
                response = self.post(suffix, password="valid-password123")
                self.assertEqual(400, response.status_code, response.text)
        self.assertEqual(400, self.edit(name="Revived").status_code)
        response = self.client.post("/manage/system/teachers", data={
            "name": "Revive", "email": "8@example.test", "password": "valid-password123",
            "school_code": "gxufl", "school_name": "广西外国语学院", "department": "合成系", "employee_number": "0008",
        })
        self.assertEqual(400, response.status_code, response.text)
        self.assertEqual(8, self.conn.execute("SELECT created_by_teacher_id FROM classes WHERE id=30").fetchone()[0])
        _ensure_teacher_organization_memberships_schema(self.conn)
        self.assertEqual([], self.record()["active_memberships"])

    def test_self_and_last_admin_protections_and_live_permission_check(self):
        for suffix in ("/deactivate", "/cancel", "/super-admin/revoke"):
            response = self.post(suffix, teacher_id=7)
            self.assertEqual(400, response.status_code, response.text)
        self.conn.execute("UPDATE teachers SET is_super_admin=0 WHERE id=7")
        self.conn.commit()
        response = self.edit(name="Not authorized")
        self.assertEqual(403, response.status_code, response.text)
        self.assertEqual("Teacher 8", self.record()["name"])

    def test_reset_password_revokes_sessions_and_persistent_authority(self):
        response = self.post("/reset-password", password="replacement-password123")
        self.assertEqual(200, response.status_code, response.text)
        self.assert_target_revoked()
        self.assertTrue(verify_password(
            "replacement-password123", self.conn.execute("SELECT hashed_password FROM teachers WHERE id=8").fetchone()[0],
        ))

    def test_concurrent_revisions_allow_only_one_writer(self):
        revision = self.record()["revision"]
        self.conn.commit()
        with tempfile.TemporaryDirectory(prefix="teacher-lifecycle-") as folder:
            path = Path(folder) / "synthetic.sqlite"
            destination = sqlite3.connect(path)
            self.conn.backup(destination)
            destination.close()

            def rename(name):
                conn = sqlite3.connect(path, timeout=10)
                conn.row_factory = sqlite3.Row
                try:
                    before = accounts.require_teacher_account_revision(conn, 8, revision)
                    accounts.update_teacher_account(
                        conn, teacher_id=8, name=name, email=before["email"], employee_number=before["employee_number"],
                    )
                    conn.commit()
                    return "saved"
                except accounts.TeacherAccountConflict:
                    conn.rollback()
                    return "conflict"
                finally:
                    conn.close()

            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(["conflict", "saved"], sorted(pool.map(rename, ("First", "Second"))))
