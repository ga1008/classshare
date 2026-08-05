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
        # A real pending claim plus a bogus request id: the batch approves the
        # first and reports the failure on the second without aborting.
        first = signature_workflow_service.create_claim_request(
            self.conn, {"role": "student", "id": 1}, 1
        )["request"]
        result = signature_workflow_service.batch_review_access_requests(
            self.conn,
            {"role": "teacher", "id": 9},
            [first["id"], 987654],
            action="approve",
        )
        self.assertEqual(1, result["processed"])
        self.assertEqual(1, result["failed"])
        outcomes = {item["id"]: item for item in result["items"]}
        self.assertEqual("approved", outcomes[first["id"]].get("status"))
        self.assertTrue(outcomes[987654].get("error"))

    def _insert_duplicate(self, *, subject_id: int | None = None) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO electronic_signatures (
                name, subject_name, subject_role, subject_id,
                owner_role, owner_id, owner_name_snapshot, identity_category
            ) VALUES ('陈忠伟', '陈忠伟', 'teacher', ?, 'teacher', 2, '归属教师', '')
            """,
            (subject_id,),
        )
        return int(cursor.lastrowid)

    def test_merge_repoints_references_and_soft_deletes(self) -> None:
        duplicate_id = self._insert_duplicate()
        self.conn.execute(
            """
            INSERT INTO signature_point_bindings (
                function_point_key, material_type, material_id, material_revision,
                signature_id, display_order, bound_by_role, bound_by_id
            ) VALUES ('assessment_plan.examiner_signature', 'assessment_plan', 'p1', 'rev1', ?, 0, 'teacher', 2)
            """,
            (duplicate_id,),
        )
        self.conn.execute(
            "INSERT INTO signature_usage_logs (signature_id, actor_role, actor_id, action) VALUES (?, 'teacher', 2, 'use')",
            (duplicate_id,),
        )
        pending = signature_workflow_service.create_claim_request(
            self.conn, {"role": "student", "id": 1}, duplicate_id
        )["request"]

        with self.assertRaises(signature_service.SignatureServiceError) as forbidden:
            signature_service.merge_duplicate_signatures(
                self.conn, {"role": "teacher", "id": 2}, 1, [duplicate_id]
            )
        self.assertEqual(403, forbidden.exception.status_code)

        result = signature_service.merge_duplicate_signatures(
            self.conn, {"role": "teacher", "id": 9}, 1, [duplicate_id]
        )
        self.assertEqual(1, result["merged"])
        dup_row = self._signature(duplicate_id)
        self.assertEqual("deleted", dup_row["status"])
        self.assertEqual("merged:1", dup_row["legacy_source"])
        binding = self.conn.execute(
            "SELECT signature_id FROM signature_point_bindings LIMIT 1"
        ).fetchone()
        self.assertEqual(1, int(binding["signature_id"]))
        usage = self.conn.execute(
            "SELECT signature_id FROM signature_usage_logs LIMIT 1"
        ).fetchone()
        self.assertEqual(1, int(usage["signature_id"]))
        request_row = self.conn.execute(
            "SELECT status FROM signature_access_requests WHERE id = ?", (pending["id"],)
        ).fetchone()
        self.assertEqual("cancelled", request_row["status"])

    def test_merge_guards_names_and_conflicting_bindings(self) -> None:
        with self.assertRaises(signature_service.SignatureServiceError) as name_guard:
            # Signature 2 carries a different subject name (归属教师).
            signature_service.merge_duplicate_signatures(
                self.conn, {"role": "teacher", "id": 9}, 1, [2]
            )
        self.assertEqual(400, name_guard.exception.status_code)

        # Duplicate bound to teacher 1 migrates its binding onto the unbound primary.
        duplicate_id = self._insert_duplicate(subject_id=1)
        signature_service.merge_duplicate_signatures(
            self.conn, {"role": "teacher", "id": 9}, 1, [duplicate_id]
        )
        primary = self._signature(1)
        self.assertEqual(1, int(primary["subject_id"]))

        # Now a duplicate bound to a different account must be rejected.
        conflicting_id = self._insert_duplicate(subject_id=2)
        self.conn.execute(
            "UPDATE electronic_signatures SET subject_name = '陈忠伟' WHERE id = ?",
            (conflicting_id,),
        )
        with self.assertRaises(signature_service.SignatureServiceError) as conflict:
            signature_service.merge_duplicate_signatures(
                self.conn, {"role": "teacher", "id": 9}, 1, [conflicting_id]
            )
        self.assertEqual(422, conflict.exception.status_code)

    def test_stamp_signatures_bypass_requests_and_management_flows(self) -> None:
        # A remark stamp registered by an admin (owner = teacher 9, not system).
        cursor = self.conn.execute(
            """
            INSERT INTO electronic_signatures (
                name, subject_name, subject_role, subject_id,
                owner_role, owner_id, owner_name_snapshot, signature_kind
            ) VALUES ('同意', '同意', 'other', NULL, 'teacher', 9, '平台管理员', 'stamp')
            """
        )
        stamp_id = int(cursor.lastrowid)
        stamp_row = self._signature(stamp_id)
        # Any teacher may use it directly, no request needed.
        self.assertTrue(
            signature_service.can_use_signature(
                {"role": "teacher", "id": 1, "is_super_admin": False}, stamp_row
            )
        )
        self.assertEqual(
            "platform",
            signature_workflow_service.direct_authorization_mode(
                {"role": "teacher", "id": 1}, stamp_row
            ),
        )
        # Students do not embed remark stamps.
        self.assertFalse(
            signature_service.can_use_signature(
                {"role": "student", "id": 1, "is_super_admin": False}, stamp_row
            )
        )
        # Stamps never enter the claim or merge flows.
        with self.assertRaises(signature_service.SignatureServiceError) as claim_guard:
            signature_workflow_service.create_claim_request(
                self.conn, {"role": "teacher", "id": 1}, stamp_id
            )
        self.assertEqual(400, claim_guard.exception.status_code)
        with self.assertRaises(signature_service.SignatureServiceError) as merge_guard:
            signature_service.merge_duplicate_signatures(
                self.conn, {"role": "teacher", "id": 9}, stamp_id, [1]
            )
        self.assertEqual(400, merge_guard.exception.status_code)

    def test_startup_migration_marks_system_rows_as_stamps(self) -> None:
        cursor = self.conn.execute(
            """
            INSERT INTO electronic_signatures (
                name, subject_name, subject_role, owner_role, owner_id, signature_kind
            ) VALUES ('教学院长审核意见·同意', '同意', 'system', 'system', NULL, 'personal')
            """
        )
        legacy_id = int(cursor.lastrowid)
        schema_signature_workflow._SCHEMA_READY = False
        with patch.object(schema_signature_workflow, "get_configured_db_engine", return_value="sqlite"):
            schema_signature_workflow.ensure_signature_workflow_schema(self.conn)
        self.assertEqual("stamp", self._signature(legacy_id)["signature_kind"])

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

    def test_bound_signature_claim_signer_first_admin_fallback(self) -> None:
        # Signature 2 is bound to teacher 2: the signer is the (only) listed
        # reviewer, but a super admin may step in so onboarding-phase gaps
        # never deadlock the flow. The former signer gets a transfer notice.
        created = signature_workflow_service.create_claim_request(
            self.conn, {"role": "student", "id": 1}, 2
        )
        self.assertEqual("request", created["mode"])
        request = created["request"]
        self.assertEqual(
            [("teacher", 2)],
            [(item["role"], item["id"]) for item in request["reviewers"]],
        )
        approved = signature_workflow_service.review_access_request(
            self.conn, {"role": "teacher", "id": 9}, request["id"], action="approve"
        )["request"]
        self.assertEqual("approved", approved["status"])
        row = self._signature(2)
        self.assertEqual("student", row["subject_role"])
        self.assertEqual(1, int(row["subject_id"]))
        transfer_notice = self.conn.execute(
            "SELECT COUNT(*) FROM message_center_notifications "
            "WHERE ref_type = 'signature_claim_transfer' AND recipient_role = 'teacher' AND recipient_user_pk = 2"
        ).fetchone()[0]
        self.assertEqual(1, int(transfer_notice))

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
