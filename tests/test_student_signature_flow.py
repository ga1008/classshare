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
    ("teacher", 9): "平台管理员",
    ("student", 1): "学生甲",
    ("student", 5): "学生乙",
}

ADMIN_IDENTITIES = {("teacher", 9)}


class StudentSignatureFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(
            """
            CREATE TABLE teachers (
                id INTEGER PRIMARY KEY, name TEXT, email TEXT,
                is_super_admin INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO teachers VALUES (1, '申请教师', 'one@example.test', 0, 1);
            INSERT INTO teachers VALUES (2, '归属教师', 'two@example.test', 0, 1);
            INSERT INTO teachers VALUES (3, '签名教师', 'three@example.test', 0, 1);
            INSERT INTO teachers VALUES (9, '平台管理员', 'admin@example.test', 1, 1);
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
                updated_at TEXT,
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
                "is_super_admin": (str(user["role"]), int(user["id"])) in ADMIN_IDENTITIES,
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

    def _insert_signature(self, *, name, subject_name, subject_role, subject_id, owner_role, owner_id, owner_name="") -> int:
        self.conn.execute(
            """
            INSERT INTO electronic_signatures (
                name, subject_name, subject_role, subject_id,
                owner_role, owner_id, owner_name_snapshot
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, subject_name, subject_role, subject_id, owner_role, owner_id, owner_name),
        )
        return int(self.conn.execute("SELECT MAX(id) FROM electronic_signatures").fetchone()[0])

    def test_claim_binds_unbound_signature_and_blocks_name_mismatch(self) -> None:
        signature_id = self._insert_signature(
            name="学生乙签名", subject_name="学生乙", subject_role="student", subject_id=None,
            owner_role="teacher", owner_id=2, owner_name="归属教师",
        )
        with self.assertRaises(signature_service.SignatureServiceError) as denied:
            signature_workflow_service.claim_signature(self.conn, {"role": "student", "id": 1}, signature_id)
        self.assertEqual(403, denied.exception.status_code)

        claimed = signature_workflow_service.claim_signature(self.conn, {"role": "student", "id": 5}, signature_id)
        self.assertEqual("success", claimed["status"])
        row = self.conn.execute(
            "SELECT subject_role, subject_id FROM electronic_signatures WHERE id = ?", (signature_id,)
        ).fetchone()
        self.assertEqual(("student", 5), (row["subject_role"], int(row["subject_id"])))
        owner_note = self.conn.execute(
            "SELECT recipient_role, recipient_user_pk FROM message_center_notifications WHERE ref_type = 'signature_claim'"
        ).fetchone()
        self.assertEqual(("teacher", 2), (owner_note["recipient_role"], int(owner_note["recipient_user_pk"])))

        with self.assertRaises(signature_service.SignatureServiceError) as repeated:
            signature_workflow_service.claim_signature(self.conn, {"role": "student", "id": 5}, signature_id)
        self.assertEqual(403, repeated.exception.status_code)

    def test_fully_unbound_signature_falls_back_to_admin_review(self) -> None:
        signature_id = self._insert_signature(
            name="外聘签名", subject_name="外聘专家", subject_role="teacher", subject_id=None,
            owner_role="teacher", owner_id=None,
        )
        created = signature_workflow_service.create_access_request(
            self.conn,
            {"role": "teacher", "id": 1},
            signature_id,
            function_point_keys=["assessment_plan.reviewer_signature"],
        )["request"]
        self.assertEqual([("teacher", 9, "admin")], [
            (item["role"], item["id"], item["kind"]) for item in created["reviewers"]
        ])

        admin_inbox = signature_workflow_service.list_access_requests(
            self.conn, {"role": "teacher", "id": 9}, direction="incoming"
        )
        self.assertTrue(admin_inbox["admin_view"])
        self.assertIn(created["id"], [item["id"] for item in admin_inbox["items"]])

        approved = signature_workflow_service.review_access_request(
            self.conn, {"role": "teacher", "id": 9}, created["id"], action="approve"
        )["request"]
        self.assertEqual("approved", approved["status"])

    def test_admin_can_step_into_any_pending_request(self) -> None:
        created = signature_workflow_service.create_access_request(
            self.conn,
            {"role": "teacher", "id": 1},
            1,
            function_point_keys=["academic_final_material.grade_register.teacher_signature"],
        )["request"]
        self.assertNotIn(
            ("teacher", 9), {(item["role"], item["id"]) for item in created["reviewers"]}
        )
        approved = signature_workflow_service.review_access_request(
            self.conn, {"role": "teacher", "id": 9}, created["id"], action="approve"
        )["request"]
        self.assertEqual("approved", approved["status"])
        admin_review = next(item for item in approved["reviewers"] if item["id"] == 9)
        self.assertEqual(("admin", "approved"), (admin_review["kind"], admin_review["status"]))

        ordinary_outsider = signature_workflow_service.create_access_request(
            self.conn,
            {"role": "teacher", "id": 1},
            2,
            function_point_keys=["assessment_plan.examiner_signature"],
        )["request"]
        with self.assertRaises(signature_service.SignatureServiceError) as denied:
            signature_workflow_service.review_access_request(
                self.conn, {"role": "student", "id": 5}, ordinary_outsider["id"], action="approve"
            )
        self.assertEqual(403, denied.exception.status_code)

    def test_platform_stamp_is_directly_usable_by_teachers(self) -> None:
        signature_id = self._insert_signature(
            name="审核意见·同意", subject_name="同意（楷行）", subject_role="other", subject_id=None,
            owner_role="system", owner_id=None,
        )
        used = signature_workflow_service.authorize_and_consume_signature_use(
            self.conn,
            {"role": "teacher", "id": 1},
            signature_id,
            function_point_key="academic_final_material.exam_analysis.dean_review_signature",
            context_type="academic_final_material",
            context_id="record-20",
            context_label="试卷分析表",
        )
        self.assertEqual("platform", used["authorization_mode"])
        with self.assertRaises(signature_service.SignatureServiceError) as blocked:
            signature_workflow_service.create_access_request(
                self.conn,
                {"role": "teacher", "id": 1},
                signature_id,
                function_point_keys=["academic_final_material.exam_analysis.dean_review_signature"],
            )
        self.assertEqual(400, blocked.exception.status_code)


if __name__ == "__main__":
    unittest.main()
