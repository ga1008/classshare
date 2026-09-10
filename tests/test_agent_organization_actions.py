from __future__ import annotations

import unittest

from fastapi import HTTPException

from classroom_app.services import agent_platform_write_service as writes
from classroom_app.services.agent_action_registry import validate_action_params
from tests.test_agent_platform_writes import PlatformWriteFixture


class OrganizationActionTests(PlatformWriteFixture):
    def setUp(self):
        super().setUp()
        self.conn.execute("UPDATE teachers SET is_super_admin=1 WHERE id=7")
        self.conn.commit()
        self.grant = self.token()

    def execute(self, action, params, operation_id=None):
        return writes.dispatch_write(self.conn, self.grant, operation_id or action, action, params)["result"]["item"]

    def test_directory_create_rename_soft_disable_and_replay_use_normal_service(self):
        school = self.execute("create_organization_school", {"school_code": "TEST", "school_name": "School", "display_order": 0})
        college = self.execute("create_organization_college", {"school_code": "TEST", "college_name": "College", "display_order": -1})
        department = self.execute("create_organization_department", {"school_code": "TEST", "college_name": "College", "department_name": "Department"})
        self.conn.commit()
        for kind, item, name in (("school", school, "School"), ("college", college, "College"), ("department", department, "Department")):
            with self.subTest(kind=kind):
                table = {"school": "organization_schools", "college": "organization_colleges", "department": "organization_departments"}[kind]
                item["updated_at"] = self.conn.execute(f"SELECT updated_at FROM {table} WHERE id=?", (item["id"],)).fetchone()[0]
                updated = self.execute(f"update_organization_{kind}", {f"{kind}_id": item["id"], f"{kind}_name": name + " revised", "expected_updated_at": item["updated_at"], "is_active": True})
                self.conn.commit()
                params = {f"{kind}_id": item["id"], "expected_updated_at": updated["updated_at"]}
                disabled = self.execute(f"delete_organization_{kind}", params)
                self.assertFalse(disabled["is_active"])
                self.assertEqual(item["id"], disabled["id"])
                self.conn.commit()
                replay = writes.dispatch_write(self.conn, self.grant, f"delete_organization_{kind}", f"delete_organization_{kind}", params)
                self.assertTrue(replay["replayed"])
        self.assertIsNotNone(self.conn.execute("SELECT id FROM organization_schools WHERE id=?", (school["id"],)).fetchone())

    def test_revision_conflict_no_write_and_transaction_rollback_preserve_receipts(self):
        created = self.execute("create_organization_school", {"school_code": "TXN", "school_name": "Original"})
        self.conn.commit()
        params = {"school_id": created["id"], "school_name": "Changed", "expected_updated_at": "stale"}
        with self.assertRaises(HTTPException) as failure:
            self.execute("update_organization_school", params)
        self.assertEqual(409, failure.exception.status_code)
        self.conn.rollback()
        self.assertEqual("Original", self.conn.execute("SELECT school_name FROM organization_schools WHERE id=?", (created["id"],)).fetchone()[0])
        params["expected_updated_at"] = created["updated_at"]
        self.execute("update_organization_school", params)
        self.conn.rollback()
        self.assertEqual(1, self.count("agent_action_executions"))
        self.assertEqual("Original", self.conn.execute("SELECT school_name FROM organization_schools WHERE id=?", (created["id"],)).fetchone()[0])

    def test_zero_sort_false_boolean_and_invalid_coercion(self):
        clean, errors = validate_action_params("update_organization_school", {"school_id": 3, "school_name": "School", "expected_updated_at": "v1", "display_order": 0, "is_active": False}, reject_unknown=True)
        self.assertFalse(errors)
        self.assertEqual(0, clean["display_order"])
        self.assertIs(False, clean["is_active"])
        for value in ("false", 0, 1):
            with self.subTest(value=value):
                self.assertTrue(validate_action_params("update_organization_school", {"school_id": 3, "school_name": "School", "expected_updated_at": "v1", "is_active": value})[1])

    def test_self_college_rename_updates_real_membership_and_commits_receipt_before_revocation(self):
        from classroom_app.services.teacher_account_service import upsert_teacher_membership
        upsert_teacher_membership(self.conn, teacher_id=7, school_code="SELF", school_name="Own school", college="Own college", department="Own department", is_primary=True, actor_teacher_id=7)
        self.conn.commit()
        self.grant = self.token()
        college = self.conn.execute("SELECT * FROM organization_colleges WHERE college_name='Own college'").fetchone()
        result = writes.dispatch_write(self.conn, self.grant, "self-college-rename", "update_organization_college", {
            "college_id": college["id"], "college_name": "Renamed college", "expected_updated_at": college["updated_at"]})
        self.assertTrue(result["result"]["agent_stop_required"])
        self.assertEqual("Renamed college", self.conn.execute("SELECT college FROM teacher_organization_memberships WHERE teacher_id=7 AND school_code=?", (college["school_code"],)).fetchone()[0])
        self.assertEqual("Renamed college", self.conn.execute("SELECT college FROM teachers WHERE id=7").fetchone()[0])
        self.conn.commit()
        self.assertEqual("completed", self.conn.execute("SELECT status FROM agent_action_executions WHERE operation_id='self-college-rename'").fetchone()[0])
        with self.assertRaises(HTTPException):
            self.execute("create_organization_school", {"school_code": "STOP", "school_name": "Must not continue"}, "after-authority-changed")
        self.conn.rollback()

    def test_revoked_admin_and_same_numbered_student_cannot_mutate_directory(self):
        self.conn.execute("UPDATE teachers SET is_super_admin=0 WHERE id=7")
        self.conn.commit()
        with self.assertRaises(HTTPException):
            self.execute("create_organization_school", {"school_code": "DENY", "school_name": "Denied"})
        self.conn.rollback()
        student = self.token(self.student)
        with self.assertRaises(HTTPException):
            writes.dispatch_write(self.conn, student, "student-school", "create_organization_school", {"school_code": "DENY", "school_name": "Denied"})
        self.conn.rollback()
        self.assertIsNone(self.conn.execute("SELECT id FROM organization_schools WHERE school_code='DENY'").fetchone())


if __name__ == "__main__":
    unittest.main()
