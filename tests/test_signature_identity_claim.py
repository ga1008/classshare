"""Identity categories (职务身份) and the claim-with-approval workflow.

Covers: bidirectional identity sync between signature and bound account,
identity propagation when the account side changes, filter-group expansion
(系主任 also matches 副系主任), the claim request flow (create -> admin
approve -> ownership transfer + auto bind + identity sync), rejection, and
the direct same-name claim shortcut.
"""

from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.db import schema_signature_workflow
from classroom_app.services import (
    signature_identity_service,
    signature_service,
    signature_workflow_service,
)


ACTORS = {
    ("teacher", 1): "陈忠伟",
    ("teacher", 2): "归属教师",
    ("teacher", 9): "平台管理员",
    ("student", 1): "学生甲",
}

ADMIN_IDENTITIES = {("teacher", 9)}


class SignatureIdentityClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE teachers (
                id INTEGER PRIMARY KEY, name TEXT, email TEXT,
                is_super_admin INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO teachers VALUES (1, '陈忠伟', 'one@example.test', 0, 1);
            INSERT INTO teachers VALUES (2, '归属教师', 'two@example.test', 0, 1);
            INSERT INTO teachers VALUES (9, '平台管理员', 'admin@example.test', 1, 1);
            CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO students VALUES (1, '学生甲');

            CREATE TABLE electronic_signatures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                subject_name TEXT NOT NULL DEFAULT '',
                subject_role TEXT NOT NULL DEFAULT 'teacher',
                owner_role TEXT NOT NULL,
                owner_id INTEGER,
                owner_name_snapshot TEXT NOT NULL DEFAULT '',
                uploaded_by_role TEXT NOT NULL DEFAULT '',
                uploaded_by_id INTEGER,
                uploaded_by_name_snapshot TEXT NOT NULL DEFAULT '',
                scope_level TEXT NOT NULL DEFAULT 'department',
                school_code TEXT NOT NULL DEFAULT '',
                school_name TEXT NOT NULL DEFAULT '',
                college TEXT NOT NULL DEFAULT '',
                department TEXT NOT NULL DEFAULT '',
                file_hash TEXT NOT NULL DEFAULT '',
                file_ext TEXT NOT NULL DEFAULT '.png',
                mime_type TEXT NOT NULL DEFAULT 'image/png',
                stored_path TEXT NOT NULL DEFAULT '',
                file_size INTEGER NOT NULL DEFAULT 0,
                description TEXT NOT NULL DEFAULT '',
                legacy_source TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                ownership_updated_at TEXT,
                ownership_updated_by_teacher_id INTEGER,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
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
        # Signature of 陈忠伟 (teacher 1) but currently unbound & owned by teacher 2.
        self.conn.execute(
            """
            INSERT INTO electronic_signatures (
                name, subject_name, subject_role, subject_id,
                owner_role, owner_id, owner_name_snapshot, identity_category
            ) VALUES ('陈忠伟', '陈忠伟', 'teacher', NULL, 'teacher', 2, '归属教师', 'dean')
            """
        )
        # Signature bound to teacher 2 already.
        self.conn.execute(
            """
            INSERT INTO electronic_signatures (
                name, subject_name, subject_role, subject_id,
                owner_role, owner_id, owner_name_snapshot, identity_category
            ) VALUES ('归属教师', '归属教师', 'teacher', 2, 'teacher', 2, '归属教师', '')
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
        self.engine_patches = [
            patch.object(signature_workflow_service, "get_configured_db_engine", return_value="sqlite"),
            patch("classroom_app.services.message_center_service.get_configured_db_engine", return_value="sqlite"),
            patch("classroom_app.services.message_center_service.queue_notification_email_if_applicable"),
        ]
        self.actor_patch.start()
        for item in self.engine_patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.engine_patches):
            item.stop()
        self.actor_patch.stop()
        self.conn.close()
        schema_signature_workflow._SCHEMA_READY = False

    def _signature(self, signature_id: int) -> sqlite3.Row:
        return self.conn.execute(
            "SELECT * FROM electronic_signatures WHERE id = ?", (signature_id,)
        ).fetchone()

    def test_identity_filter_group_expansion(self) -> None:
        self.assertEqual(
            ["department_head", "vice_department_head"],
            signature_identity_service.expand_identity_filter("department_head"),
        )
        self.assertEqual(["dean", "vice_dean"], signature_identity_service.expand_identity_filter("dean"))
        self.assertEqual(["teacher"], signature_identity_service.expand_identity_filter("teacher"))
        self.assertEqual([], signature_identity_service.expand_identity_filter("bogus"))

    def test_required_identities_seeded_on_function_points(self) -> None:
        row = self.conn.execute(
            "SELECT required_identities FROM signature_function_points WHERE point_key = ?",
            ("academic_final_material.exam_analysis.department_review_signature",),
        ).fetchone()
        self.assertEqual("department_head", row["required_identities"])

    def test_direct_claim_syncs_identity_to_account(self) -> None:
        # 陈忠伟 claims the unbound same-name signature carrying identity 'dean'.
        result = signature_workflow_service.create_claim_request(
            self.conn, {"role": "teacher", "id": 1}, 1
        )
        self.assertEqual("direct", result["mode"])
        row = self._signature(1)
        self.assertEqual(1, int(row["subject_id"]))
        account = self.conn.execute(
            "SELECT identity_category FROM teachers WHERE id = 1"
        ).fetchone()
        self.assertEqual("dean", account["identity_category"])

    def test_claim_request_approval_transfers_ownership_and_identity(self) -> None:
        # 学生甲 has a different name, so the claim goes through review.
        created = signature_workflow_service.create_claim_request(
            self.conn, {"role": "student", "id": 1}, 1, note="这是我的签名"
        )
        self.assertEqual("request", created["mode"])
        request = created["request"]
        self.assertEqual("claim", request["request_kind"])
        reviewer_ids = {(item["role"], item["id"]) for item in request["reviewers"]}
        self.assertIn(("teacher", 2), reviewer_ids)  # bound owner
        self.assertIn(("teacher", 9), reviewer_ids)  # platform admin

        # Duplicate pending claim is rejected.
        with self.assertRaises(signature_service.SignatureServiceError):
            signature_workflow_service.create_claim_request(
                self.conn, {"role": "student", "id": 1}, 1
            )

        approved = signature_workflow_service.review_access_request(
            self.conn, {"role": "teacher", "id": 9}, request["id"], action="approve"
        )["request"]
        self.assertEqual("approved", approved["status"])
        row = self._signature(1)
        self.assertEqual("student", row["owner_role"])
        self.assertEqual(1, int(row["owner_id"]))
        self.assertEqual("student", row["subject_role"])
        self.assertEqual(1, int(row["subject_id"]))
        self.assertEqual("学生甲", row["subject_name"])
        account = self.conn.execute("SELECT identity_category FROM students WHERE id = 1").fetchone()
        self.assertEqual("dean", account["identity_category"])

    def test_claim_request_rejected_by_all_reviewers(self) -> None:
        created = signature_workflow_service.create_claim_request(
            self.conn, {"role": "student", "id": 1}, 1
        )
        request = created["request"]
        for reviewer in request["reviewers"]:
            result = signature_workflow_service.review_access_request(
                self.conn,
                {"role": reviewer["role"], "id": reviewer["id"]},
                request["id"],
                action="reject",
            )["request"]
        self.assertEqual("rejected", result["status"])
        row = self._signature(1)
        self.assertIsNone(row["subject_id"])
        self.assertEqual("teacher", row["owner_role"])

    def test_claim_already_bound_signature_rejected(self) -> None:
        with self.assertRaises(signature_service.SignatureServiceError) as ctx:
            signature_workflow_service.create_claim_request(
                self.conn, {"role": "teacher", "id": 2}, 2
            )
        self.assertEqual(400, ctx.exception.status_code)

    def test_account_identity_propagates_to_bound_signatures(self) -> None:
        changed = signature_identity_service.propagate_account_identity(
            self.conn, "teacher", 2, "department_head"
        )
        self.assertEqual(1, changed)
        row = self._signature(2)
        self.assertEqual("department_head", row["identity_category"])

    def test_unbind_by_signer_and_permission_guards(self) -> None:
        # Signature 2 bound to teacher 2 (also owner) → owner-signer cannot
        # unbind through this exit (they keep control anyway)…
        row = self._signature(2)
        self.assertFalse(
            signature_service.can_unbind_signature(
                {"role": "teacher", "id": 2, "is_super_admin": False}, row
            )
        )
        # …but a super admin can detach a wrong binding.
        self.assertTrue(
            signature_service.can_unbind_signature(
                {"role": "teacher", "id": 9, "is_super_admin": True}, row
            )
        )
        result = signature_service.unbind_signature(self.conn, {"role": "teacher", "id": 9}, 2)
        self.assertFalse(result["subject_bound"])
        self.assertIsNone(self._signature(2)["subject_id"])
        # Unbound rows expose nothing to unbind anymore.
        self.assertFalse(
            signature_service.can_unbind_signature(
                {"role": "teacher", "id": 9, "is_super_admin": True}, self._signature(2)
            )
        )

    def test_batch_review_isolates_failures(self) -> None:
        # One claimable unbound signature (id 1) and one bound signature (id 2):
        # the bound one's claim can only be approved by its signer, so the
        # admin batch approves the first and reports the veto on the second.
        first = signature_workflow_service.create_claim_request(
            self.conn, {"role": "student", "id": 1}, 1
        )["request"]
        second = signature_workflow_service.create_claim_request(
            self.conn, {"role": "student", "id": 1}, 2
        )["request"]
        result = signature_workflow_service.batch_review_access_requests(
            self.conn,
            {"role": "teacher", "id": 9},
            [first["id"], second["id"]],
            action="approve",
        )
        self.assertEqual(1, result["processed"])
        self.assertEqual(1, result["failed"])
        outcomes = {item["id"]: item for item in result["items"]}
        self.assertEqual("approved", outcomes[first["id"]].get("status"))
        self.assertIn("签名者本人", outcomes[second["id"]].get("error", ""))

    def test_sync_fills_signature_identity_from_account(self) -> None:
        self.conn.execute("UPDATE teachers SET identity_category = 'counselor' WHERE id = 2")
        result = signature_identity_service.sync_identity_for_signature(self.conn, 2)
        self.assertEqual({"signature": "counselor"}, result)
        self.assertEqual("counselor", self._signature(2)["identity_category"])

    def test_propagate_clears_verified_flag(self) -> None:
        self.conn.execute("UPDATE electronic_signatures SET identity_verified = 1 WHERE id = 2")
        signature_identity_service.propagate_account_identity(self.conn, "teacher", 2, "dean")
        row = self._signature(2)
        self.assertEqual("dean", row["identity_category"])
        self.assertEqual(0, int(row["identity_verified"]))

    def test_bound_signature_claim_requires_signer_and_blocks_admin(self) -> None:
        # Signature 2 is bound to teacher 2: only the signer may approve the
        # transfer; the platform admin cannot step in.
        created = signature_workflow_service.create_claim_request(
            self.conn, {"role": "student", "id": 1}, 2
        )
        self.assertEqual("request", created["mode"])
        request = created["request"]
        self.assertEqual(
            [("teacher", 2)],
            [(item["role"], item["id"]) for item in request["reviewers"]],
        )
        with self.assertRaises(signature_service.SignatureServiceError) as ctx:
            signature_workflow_service.review_access_request(
                self.conn, {"role": "teacher", "id": 9}, request["id"], action="approve"
            )
        self.assertEqual(403, ctx.exception.status_code)
        approved = signature_workflow_service.review_access_request(
            self.conn, {"role": "teacher", "id": 2}, request["id"], action="approve"
        )["request"]
        self.assertEqual("approved", approved["status"])
        row = self._signature(2)
        self.assertEqual("student", row["subject_role"])
        self.assertEqual(1, int(row["subject_id"]))

    def test_stale_request_reminder_and_escalation(self) -> None:
        created = signature_workflow_service.create_claim_request(
            self.conn, {"role": "student", "id": 1}, 1
        )
        request_id = int(created["request"]["id"])
        self.conn.execute(
            "UPDATE signature_access_requests SET requested_at = '2020-01-01 00:00:00' WHERE id = ?",
            (request_id,),
        )
        first = signature_workflow_service.remind_stale_signature_requests(self.conn)
        self.assertEqual(1, first["reminded"])
        self.assertEqual(1, first["escalated"])
        row = self.conn.execute(
            "SELECT last_reminded_at, escalated_at FROM signature_access_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        self.assertTrue(row["last_reminded_at"])
        self.assertTrue(row["escalated_at"])
        reminder_count = self.conn.execute(
            "SELECT COUNT(*) FROM message_center_notifications WHERE ref_type = 'signature_request_reminder'"
        ).fetchone()[0]
        self.assertGreater(int(reminder_count), 0)
        # Within the repeat window and already escalated: sweep is a no-op.
        second = signature_workflow_service.remind_stale_signature_requests(self.conn)
        self.assertEqual({"reminded": 0, "escalated": 0}, second)


if __name__ == "__main__":
    unittest.main()
