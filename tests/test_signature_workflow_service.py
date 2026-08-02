from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.db import schema_signature_workflow
from classroom_app.services import signature_service, signature_workflow_service


class SignatureWorkflowServiceTests(unittest.TestCase):
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
                requester_teacher_id INTEGER NOT NULL,
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
            ) VALUES ('签名图', '签名教师', 'teacher', 3, 'teacher', 2, '归属教师')
            """
        )
        self.conn.commit()
        self.actor_patch = patch.object(
            signature_service,
            "build_signature_actor",
            side_effect=lambda _conn, user: {
                "role": "teacher",
                "id": int(user["id"]),
                "name": {1: "申请教师", 2: "归属教师", 3: "签名教师"}[int(user["id"])],
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

    def test_multi_point_any_reviewer_approval_and_one_time_consumption(self) -> None:
        points = [
            "academic_final_material.exam_analysis.department_review_signature",
            "academic_final_material.exam_analysis.dean_review_signature",
        ]
        created = signature_workflow_service.create_access_request(
            self.conn,
            {"role": "teacher", "id": 1},
            1,
            function_point_keys=points,
            note="同一份试卷分析表的两个签名栏",
        )["request"]
        self.assertEqual("pending", created["status"])
        self.assertEqual({2, 3}, {item["id"] for item in created["reviewers"]})
        self.assertEqual(points, [item["function_point_key"] for item in created["items"]])
        recipients = self.conn.execute(
            "SELECT recipient_user_pk FROM message_center_notifications WHERE ref_type = 'signature_request' ORDER BY recipient_user_pk"
        ).fetchall()
        self.assertEqual([2, 3], [row[0] for row in recipients])

        approved = signature_workflow_service.review_access_request(
            self.conn,
            {"role": "teacher", "id": 3},
            created["id"],
            action="approve",
        )["request"]
        self.assertEqual("approved", approved["status"])
        self.assertEqual({"available"}, {item["status"] for item in approved["items"]})
        self.assertIn("superseded", {item["status"] for item in approved["reviewers"]})

        first = signature_workflow_service.authorize_and_consume_signature_use(
            self.conn,
            {"role": "teacher", "id": 1},
            1,
            function_point_key=points[0],
            context_type="academic_final_material",
            context_id="record-88",
            context_label="服务器配置与管理 · 软工2406班",
        )
        self.assertEqual("approval", first["authorization_mode"])
        self.assertFalse(first["already_consumed"])
        partial = signature_workflow_service.get_request(self.conn, created["id"])
        self.assertEqual("partially_used", partial["status"])

        repeated = signature_workflow_service.authorize_and_consume_signature_use(
            self.conn,
            {"role": "teacher", "id": 1},
            1,
            function_point_key=points[0],
            context_type="academic_final_material",
            context_id="record-88",
        )
        self.assertTrue(repeated["already_consumed"])
        self.assertEqual(first["usage_log_id"], repeated["usage_log_id"])
        usage_count = self.conn.execute("SELECT COUNT(*) FROM signature_usage_logs").fetchone()[0]
        self.assertEqual(1, usage_count)

        signature_workflow_service.authorize_and_consume_signature_use(
            self.conn,
            {"role": "teacher", "id": 1},
            1,
            function_point_key=points[1],
            context_type="academic_final_material",
            context_id="record-88",
        )
        self.assertEqual("consumed", signature_workflow_service.get_request(self.conn, created["id"])["status"])
        used_recipients = self.conn.execute(
            "SELECT recipient_user_pk FROM message_center_notifications WHERE ref_type = 'signature_use' ORDER BY id"
        ).fetchall()
        self.assertEqual([2, 3, 2, 3], [row[0] for row in used_recipients])

        with self.assertRaises(signature_service.SignatureServiceError) as caught:
            signature_workflow_service.authorize_and_consume_signature_use(
                self.conn,
                {"role": "teacher", "id": 1},
                1,
                function_point_key=points[0],
                context_type="academic_final_material",
                context_id="another-record",
            )
        self.assertEqual(403, caught.exception.status_code)

    def test_one_rejection_keeps_request_open_for_other_reviewer(self) -> None:
        point = "assessment_plan.reviewer_signature"
        request = signature_workflow_service.create_access_request(
            self.conn,
            {"role": "teacher", "id": 1},
            1,
            function_point_keys=[point],
        )["request"]
        rejected_once = signature_workflow_service.review_access_request(
            self.conn,
            {"role": "teacher", "id": 2},
            request["id"],
            action="reject",
        )["request"]
        self.assertEqual("pending", rejected_once["status"])
        approved = signature_workflow_service.review_access_request(
            self.conn,
            {"role": "teacher", "id": 3},
            request["id"],
            action="approve",
        )["request"]
        self.assertEqual("approved", approved["status"])

    def test_new_request_is_allowed_after_prior_request_is_approved(self) -> None:
        point = "assessment_plan.reviewer_signature"
        first = signature_workflow_service.create_access_request(
            self.conn,
            {"role": "teacher", "id": 1},
            1,
            function_point_keys=[point],
        )["request"]
        with self.assertRaises(signature_service.SignatureServiceError) as duplicate:
            signature_workflow_service.create_access_request(
                self.conn,
                {"role": "teacher", "id": 1},
                1,
                function_point_keys=[point],
            )
        self.assertEqual(409, duplicate.exception.status_code)

        signature_workflow_service.review_access_request(
            self.conn,
            {"role": "teacher", "id": 2},
            first["id"],
            action="approve",
        )
        second = signature_workflow_service.create_access_request(
            self.conn,
            {"role": "teacher", "id": 1},
            1,
            function_point_keys=[point],
        )["request"]
        self.assertEqual("pending", second["status"])
        self.assertNotEqual(first["id"], second["id"])

    def test_signer_and_owner_have_direct_use_but_unregistered_point_is_rejected(self) -> None:
        point = "assessment_plan.examiner_signature"
        signer_use = signature_workflow_service.authorize_and_consume_signature_use(
            self.conn,
            {"role": "teacher", "id": 3},
            1,
            function_point_key=point,
            context_type="assessment_plan",
            context_id="plan-signer",
        )
        owner_use = signature_workflow_service.authorize_and_consume_signature_use(
            self.conn,
            {"role": "teacher", "id": 2},
            1,
            function_point_key=point,
            context_type="assessment_plan",
            context_id="plan-owner",
        )
        self.assertEqual("self", signer_use["authorization_mode"])
        self.assertEqual("owner", owner_use["authorization_mode"])
        with self.assertRaises(signature_service.SignatureServiceError) as caught:
            signature_workflow_service.authorize_and_consume_signature_use(
                self.conn,
                {"role": "teacher", "id": 2},
                1,
                function_point_key="unregistered.anywhere",
                context_type="assessment_plan",
                context_id="plan-bad",
            )
        self.assertEqual(400, caught.exception.status_code)

    def test_third_party_request_requires_a_bound_signer_account(self) -> None:
        self.conn.execute(
            """
            INSERT INTO electronic_signatures (
                name, subject_name, subject_role, subject_id,
                owner_role, owner_id, owner_name_snapshot
            ) VALUES ('旧签名图', '签名教师', 'teacher', NULL, 'teacher', 2, '归属教师')
            """
        )
        signature_id = int(self.conn.execute("SELECT MAX(id) FROM electronic_signatures").fetchone()[0])

        with self.assertRaises(signature_service.SignatureServiceError) as caught:
            signature_workflow_service.create_access_request(
                self.conn,
                {"role": "teacher", "id": 1},
                signature_id,
                function_point_keys=["assessment_plan.reviewer_signature"],
            )

        self.assertEqual(422, caught.exception.status_code)
        self.assertIn("签名者账号", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
