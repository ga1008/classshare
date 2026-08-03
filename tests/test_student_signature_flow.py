"""Student-side closure of the signature workflow.

Covers: student as reviewer (approve a teacher's request on the student's own
signature), student as requester (legacy teacher column stays NULL), role-aware
notification deep links, and the usage trail that keeps every use visible to
the signer.
"""

from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.db import schema_signature_workflow
from classroom_app.services import signature_service, signature_workflow_service


ACTORS = {
    ("teacher", 1): "申请教师",
    ("teacher", 2): "归属教师",
    ("teacher", 3): "签名教师",
    ("student", 1): "学生甲",
    ("student", 5): "学生乙",
}


class StudentSignatureFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(
            """
            CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT, email TEXT);
            INSERT INTO teachers VALUES (1, '申请教师', 'one@example.test');
            INSERT INTO teachers VALUES (2, '归属教师', 'two@example.test');
            INSERT INTO teachers VALUES (3, '签名教师', 'three@example.test');
            CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO students VALUES (1, '学生甲');
            INSERT INTO students VALUES (5, '学生乙');

            CREATE TABLE electronic_signatures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                subject_name TEXT NOT NULL DEFAULT '',
                subject_role TEXT NOT NULL DEFAULT 'teacher',
                owner_role TEXT NOT NULL,
                owner_id INTEGER,
                owner_name_snapshot TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                deleted_at TEXT
            );
            CREATE TABLE signature_usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signature_id INTEGER,
                signature_name_snapshot TEXT NOT NULL DEFAULT '',
                actor_role TEXT NOT NULL,
                actor_id INTEGER NOT NULL,
                actor_name_snapshot TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL DEFAULT 'use',
                context_type TEXT NOT NULL DEFAULT '',
                context_id TEXT NOT NULL DEFAULT '',
                context_label TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                ip TEXT NOT NULL DEFAULT '',
                user_agent TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE signature_access_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signature_id INTEGER NOT NULL,
                requester_teacher_id INTEGER,
                owner_role TEXT NOT NULL DEFAULT '',
                owner_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                request_note TEXT NOT NULL DEFAULT '',
                review_note TEXT NOT NULL DEFAULT '',
                context_type TEXT NOT NULL DEFAULT '',
                context_id TEXT NOT NULL DEFAULT '',
                context_label TEXT NOT NULL DEFAULT '',
                requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TEXT,
                reviewed_by_teacher_id INTEGER
            );
            CREATE TABLE message_center_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_identity TEXT,
                recipient_role TEXT,
                recipient_user_pk INTEGER,
                category TEXT,
                severity TEXT,
                actor_identity TEXT,
                actor_role TEXT,
                actor_user_pk INTEGER,
                actor_display_name TEXT,
                title TEXT,
                body_preview TEXT,
                link_url TEXT,
                class_offering_id INTEGER,
                ref_type TEXT,
                ref_id TEXT,
                metadata_json TEXT,
                created_at TEXT
            );
            """
        )
        schema_signature_workflow._SCHEMA_READY = False
        with patch.object(schema_signature_workflow, "get_configured_db_engine", return_value="sqlite"):
            schema_signature_workflow.ensure_signature_workflow_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO electronic_signatures (
                name, subject_name, subject_role, subject_id,
                owner_role, owner_id, owner_name_snapshot
            ) VALUES ('学生甲签名', '学生甲', 'student', 1, 'teacher', 2, '归属教师')
            """
        )
        self.conn.execute(
            """
            INSERT INTO electronic_signatures (
                name, subject_name, subject_role, subject_id,
                owner_role, owner_id, owner_name_snapshot
            ) VALUES ('签名教师签名', '签名教师', 'teacher', 3, 'teacher', 2, '归属教师')
            """
        )
        self.conn.commit()
        self.actor_patch = patch.object(
            signature_service,
            "build_signature_actor",
            side_effect=lambda _conn, user: {
                "role": str(user["role"]),
                "id": int(user["id"]),
                "name": ACTORS[(str(user["role"]), int(user["id"]))],
                "is_super_admin": False,
                "scope": {},
                "memberships": [],
            },
        )
        self.view_patch = patch.object(signature_service, "can_view_signature", return_value=True)
        self.engine_patches = [
            patch.object(signature_workflow_service, "get_configured_db_engine", return_value="sqlite"),
            patch("classroom_app.services.message_center_service.get_configured_db_engine", return_value="sqlite"),
            patch("classroom_app.services.message_center_service.queue_notification_email_if_applicable"),
        ]
        self.actor_patch.start()
        self.view_patch.start()
        for item in self.engine_patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.engine_patches):
            item.stop()
        self.view_patch.stop()
        self.actor_patch.stop()
        self.conn.close()
        schema_signature_workflow._SCHEMA_READY = False

    def _notification_links(self, ref_type: str) -> dict[tuple[str, int], str]:
        rows = self.conn.execute(
            "SELECT recipient_role, recipient_user_pk, link_url FROM message_center_notifications WHERE ref_type = ?",
            (ref_type,),
        ).fetchall()
        return {(row["recipient_role"], int(row["recipient_user_pk"])): row["link_url"] for row in rows}

    def test_student_reviewer_approves_teacher_request_with_role_aware_links(self) -> None:
        point = "academic_final_material.grade_register.teacher_signature"
        created = signature_workflow_service.create_access_request(
            self.conn,
            {"role": "teacher", "id": 1},
            1,
            function_point_keys=[point],
            note="成绩登记表需要学生确认签名",
        )["request"]
        self.assertEqual("pending", created["status"])
        self.assertEqual(
            {("teacher", 2), ("student", 1)},
            {(item["role"], item["id"]) for item in created["reviewers"]},
        )
        links = self._notification_links("signature_request")
        self.assertEqual("/profile?section=signatures", links[("student", 1)])
        self.assertEqual("/manage/me/signatures#signature-requests", links[("teacher", 2)])

        incoming = signature_workflow_service.list_access_requests(
            self.conn, {"role": "student", "id": 1}, direction="incoming"
        )
        self.assertEqual([created["id"]], [item["id"] for item in incoming["items"]])

        approved = signature_workflow_service.review_access_request(
            self.conn,
            {"role": "student", "id": 1},
            created["id"],
            action="approve",
        )["request"]
        self.assertEqual("approved", approved["status"])
        student_review = next(item for item in approved["reviewers"] if item["role"] == "student")
        self.assertEqual("approved", student_review["status"])

        used = signature_workflow_service.authorize_and_consume_signature_use(
            self.conn,
            {"role": "teacher", "id": 1},
            1,
            function_point_key=point,
            context_type="academic_final_material",
            context_id="record-9",
            context_label="成绩登记表",
        )
        self.assertEqual("approval", used["authorization_mode"])
        usage_links = self._notification_links("signature_use")
        self.assertEqual("/profile?section=signatures", usage_links[("student", 1)])

    def test_owner_self_use_notifies_signer_but_never_the_actor(self) -> None:
        signature_workflow_service.authorize_and_consume_signature_use(
            self.conn,
            {"role": "teacher", "id": 2},
            1,
            function_point_key="academic_final_material.grade_register.teacher_signature",
            context_type="academic_final_material",
            context_id="record-10",
            context_label="成绩登记表",
        )
        recipients = self._notification_links("signature_use")
        self.assertIn(("student", 1), recipients)
        self.assertNotIn(("teacher", 2), recipients)

    def test_student_requester_keeps_legacy_teacher_column_null(self) -> None:
        created = signature_workflow_service.create_access_request(
            self.conn,
            {"role": "student", "id": 5},
            2,
            function_point_keys=["assessment_plan.examiner_signature"],
            note="小组材料需要教师签名",
        )["request"]
        self.assertEqual("pending", created["status"])
        self.assertEqual("student", created["requester_role"])
        self.assertEqual(5, created["requester_id"])
        self.assertEqual("学生乙", created["requester_name"])
        row = self.conn.execute(
            "SELECT requester_teacher_id FROM signature_access_requests WHERE id = ?",
            (created["id"],),
        ).fetchone()
        self.assertIsNone(row["requester_teacher_id"])

        cancelled = signature_workflow_service.cancel_access_request(
            self.conn, {"role": "student", "id": 5}, created["id"]
        )["request"]
        self.assertEqual("cancelled", cancelled["status"])

    def test_usage_trail_covers_subject_and_owner_perspectives(self) -> None:
        signature_workflow_service.authorize_and_consume_signature_use(
            self.conn,
            {"role": "teacher", "id": 2},
            1,
            function_point_key="academic_final_material.grade_register.teacher_signature",
            context_type="academic_final_material",
            context_id="record-11",
            context_label="成绩登记表",
        )
        student_view = signature_workflow_service.list_signature_usage_about_actor(
            self.conn, {"role": "student", "id": 1}
        )
        self.assertEqual(1, len(student_view["items"]))
        entry = student_view["items"][0]
        self.assertEqual("学生甲", entry["signature_name"])
        self.assertEqual("归属教师", entry["used_by_name"])
        self.assertFalse(entry["is_self_use"])

        owner_view = signature_workflow_service.list_signature_usage_about_actor(
            self.conn, {"role": "teacher", "id": 2}
        )
        self.assertEqual(1, len(owner_view["items"]))
        self.assertTrue(owner_view["items"][0]["is_self_use"])

        outsider_view = signature_workflow_service.list_signature_usage_about_actor(
            self.conn, {"role": "student", "id": 5}
        )
        self.assertEqual([], outsider_view["items"])


if __name__ == "__main__":
    unittest.main()
