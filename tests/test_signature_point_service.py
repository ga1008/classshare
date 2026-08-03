from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from classroom_app.db import schema_signature_workflow
from classroom_app.services import signature_point_service, signature_service, signature_workflow_service


class SignaturePointServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(
            """
            CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT, email TEXT);
            CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO teachers VALUES (1, '申请教师', 'one@example.test');
            INSERT INTO teachers VALUES (2, '甲归属人', 'two@example.test');
            INSERT INTO teachers VALUES (3, '甲签名者', 'three@example.test');
            INSERT INTO teachers VALUES (4, '乙归属人', 'four@example.test');
            INSERT INTO teachers VALUES (5, '乙签名者', 'five@example.test');

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
                recipient_identity TEXT, recipient_role TEXT, recipient_user_pk INTEGER,
                category TEXT, severity TEXT, actor_identity TEXT, actor_role TEXT,
                actor_user_pk INTEGER, actor_display_name TEXT, title TEXT, body_preview TEXT,
                link_url TEXT, class_offering_id INTEGER, ref_type TEXT, ref_id TEXT,
                metadata_json TEXT, created_at TEXT
            );
            CREATE TABLE material_ai_import_records (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER NOT NULL,
                source_file_hash TEXT NOT NULL DEFAULT '',
                signature_revision TEXT NOT NULL DEFAULT '',
                document_type_label TEXT NOT NULL DEFAULT '',
                export_payload_json TEXT NOT NULL DEFAULT '{}',
                parse_status TEXT NOT NULL DEFAULT 'completed'
            );
            INSERT INTO material_ai_import_records VALUES (
                88, 1, 'hash-a', 'revision-a', '试卷分析表',
                '{"fields":{"course_name":"服务器配置与管理","class_name":"软工2406班"}}',
                'completed'
            );
            """
        )
        schema_signature_workflow._SCHEMA_READY = False
        with patch.object(schema_signature_workflow, "get_configured_db_engine", return_value="sqlite"):
            schema_signature_workflow.ensure_signature_workflow_schema(self.conn)
        self.conn.executescript(
            """
            INSERT INTO electronic_signatures (
                name, subject_name, subject_role, subject_id,
                owner_role, owner_id, owner_name_snapshot
            ) VALUES ('甲签名', '甲签名者', 'teacher', 3, 'teacher', 2, '甲归属人');
            INSERT INTO electronic_signatures (
                name, subject_name, subject_role, subject_id,
                owner_role, owner_id, owner_name_snapshot
            ) VALUES ('乙签名', '乙签名者', 'teacher', 5, 'teacher', 4, '乙归属人');
            """
        )
        self.conn.commit()
        names = {1: "申请教师", 2: "甲归属人", 3: "甲签名者", 4: "乙归属人", 5: "乙签名者"}
        self.patches = [
            patch.object(
                signature_service,
                "build_signature_actor",
                side_effect=lambda _conn, user: {
                    "role": "teacher", "id": int(user["id"]), "name": names[int(user["id"])],
                    "is_super_admin": False, "scope": {}, "memberships": [],
                },
            ),
            patch.object(signature_service, "can_view_signature", return_value=True),
            patch.object(signature_service, "resolve_signature_file_path", return_value="C:/fake/signature.png"),
            patch.object(signature_workflow_service, "get_configured_db_engine", return_value="sqlite"),
            patch.object(signature_point_service, "get_configured_db_engine", return_value="sqlite"),
            patch("classroom_app.services.message_center_service.get_configured_db_engine", return_value="sqlite"),
            patch("classroom_app.services.message_center_service.queue_notification_email_if_applicable"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.conn.close()
        schema_signature_workflow._SCHEMA_READY = False

    @property
    def user(self) -> dict[str, object]:
        return {"role": "teacher", "id": 1}

    @property
    def point(self) -> str:
        return "academic_final_material.exam_analysis.department_review_signature"

    def _approve_all(self, flow: dict[str, object]) -> None:
        reviewer_by_signature = {1: 2, 2: 4}
        for item in flow["items"]:
            signature_workflow_service.review_access_request(
                self.conn,
                {"role": "teacher", "id": reviewer_by_signature[item["signature_id"]]},
                item["request_id"],
                action="approve",
            )

    def test_ordered_multi_signature_flow_grants_unlimited_material_use(self) -> None:
        created = signature_point_service.create_point_flow(
            self.conn,
            self.user,
            function_point_key=self.point,
            material_type="academic_final_material",
            material_id="88",
            signature_ids=[2, 1],
            note="两位审核人按此顺序签名",
        )["flow"]
        self.assertEqual([2, 1], [item["signature_id"] for item in created["items"]])
        self.assertEqual("pending", created["status"])

        self._approve_all(created)
        flow_status = self.conn.execute("SELECT status FROM signature_point_flows WHERE id = ?", (created["id"],)).fetchone()[0]
        self.assertEqual("approved", flow_status)

        bound = signature_point_service.bind_point_signatures(
            self.conn,
            self.user,
            function_point_key=self.point,
            material_type="academic_final_material",
            material_id="88",
            signature_ids=[2, 1],
        )
        self.assertEqual([2, 1], bound)
        binding_rows = self.conn.execute(
            "SELECT signature_id FROM signature_point_bindings ORDER BY display_order"
        ).fetchall()
        self.assertEqual([2, 1], [row[0] for row in binding_rows])

        repeated = signature_workflow_service.authorize_and_consume_signature_use(
            self.conn,
            self.user,
            2,
            function_point_key=self.point,
            context_type="academic_final_material",
            context_id="88",
        )
        self.assertTrue(repeated["already_consumed"])
        available = self.conn.execute(
            "SELECT status, consumed_at FROM signature_access_request_items WHERE request_id = ?",
            (created["items"][0]["request_id"],),
        ).fetchone()
        self.assertEqual("available", available["status"])
        self.assertIsNone(available["consumed_at"])

        listed = {
            "items": [
                {
                    "id": 1,
                    "name": "甲签名",
                    "owner_role": "teacher",
                    "owner_id": 2,
                    "subject_role": "teacher",
                    "subject_id": 3,
                },
                {
                    "id": 2,
                    "name": "乙签名",
                    "owner_role": "teacher",
                    "owner_id": 4,
                    "subject_role": "teacher",
                    "subject_id": 5,
                },
            ]
        }
        with patch.object(signature_service, "list_signatures", return_value=listed):
            state = signature_point_service.get_point_state(
                self.conn,
                self.user,
                function_point_key=self.point,
                material_type="academic_final_material",
                material_id="88",
            )
        self.assertIsNone(state["active_flow"])
        self.assertEqual([2, 1], state["selected_signature_ids"])
        self.assertEqual([1, 2], [item["id"] for item in state["usable_signatures"]])
        self.assertTrue(all(item["authorization_mode"] == "approval" for item in state["usable_signatures"]))

        cleared = signature_point_service.bind_point_signatures(
            self.conn,
            self.user,
            function_point_key=self.point,
            material_type="academic_final_material",
            material_id="88",
            signature_ids=[],
        )
        self.assertEqual([], cleared)
        self.assertEqual(
            0,
            self.conn.execute("SELECT COUNT(*) FROM signature_point_bindings").fetchone()[0],
        )

    def test_rebuild_revision_invalidates_grants_and_end_returns_to_creation(self) -> None:
        first = signature_point_service.create_point_flow(
            self.conn,
            self.user,
            function_point_key=self.point,
            material_type="academic_final_material",
            material_id="88",
            signature_ids=[1],
        )["flow"]
        self._approve_all(first)
        actor = signature_service.build_signature_actor(self.conn, self.user)
        signature = signature_workflow_service._signature_row(self.conn, 1)
        before = signature_workflow_service.access_state(
            self.conn, actor, signature, self.point,
            material_type="academic_final_material", material_id="88", material_revision="revision-a",
        )
        self.assertTrue(before["can_use"])

        self.conn.execute("UPDATE material_ai_import_records SET signature_revision = 'revision-b' WHERE id = 88")
        after = signature_workflow_service.access_state(
            self.conn, actor, signature, self.point,
            material_type="academic_final_material", material_id="88", material_revision="revision-b",
        )
        self.assertFalse(after["can_use"])

        second = signature_point_service.create_point_flow(
            self.conn,
            self.user,
            function_point_key=self.point,
            material_type="academic_final_material",
            material_id="88",
            signature_ids=[1],
        )["flow"]
        ended = signature_point_service.end_point_flow(self.conn, self.user, second["id"])
        self.assertEqual(second["id"], ended["flow_id"])
        actor = signature_service.build_signature_actor(self.conn, self.user)
        scope = signature_workflow_service.resolve_material_scope(
            self.conn, actor, function_point_key=self.point,
            material_type="academic_final_material", material_id="88",
        )
        self.assertIsNone(signature_point_service._active_flow_row(self.conn, actor, scope))

    def test_active_flow_requests_are_loaded_in_one_batch(self) -> None:
        created = signature_point_service.create_point_flow(
            self.conn,
            self.user,
            function_point_key=self.point,
            material_type="academic_final_material",
            material_id="88",
            signature_ids=[1, 2],
        )["flow"]
        listed = {
            "items": [
                {"id": 1, "name": "甲签名", "owner_role": "teacher", "owner_id": 2, "subject_role": "teacher", "subject_id": 3},
                {"id": 2, "name": "乙签名", "owner_role": "teacher", "owner_id": 4, "subject_role": "teacher", "subject_id": 5},
            ]
        }
        statements: list[str] = []
        self.conn.set_trace_callback(statements.append)
        try:
            with patch.object(signature_service, "list_signatures", return_value=listed):
                state = signature_point_service.get_point_state(
                    self.conn,
                    self.user,
                    function_point_key=self.point,
                    material_type="academic_final_material",
                    material_id="88",
                )
        finally:
            self.conn.set_trace_callback(None)

        self.assertEqual(created["id"], state["active_flow"]["id"])
        self.assertTrue(all(item["request"]["reviewers"] for item in state["active_flow"]["items"]))
        aggregate_queries = [
            statement for statement in statements
            if "SELECT REQUEST.*" in " ".join(statement.upper().split())
        ]
        self.assertEqual(1, len(aggregate_queries), aggregate_queries)

    def test_postgres_binding_replacement_takes_scope_lock_before_delete(self) -> None:
        conn = MagicMock()
        scope = {
            "function_point_key": self.point,
            "material_type": "academic_final_material",
            "material_id": "88",
            "material_revision": "revision-a",
        }
        actor = {"role": "teacher", "id": 1}
        with (
            patch.object(signature_point_service, "_scope", return_value=(actor, scope)),
            patch.object(signature_point_service, "get_configured_db_engine", return_value="postgres"),
        ):
            result = signature_point_service.bind_point_signatures(
                conn,
                self.user,
                function_point_key=self.point,
                material_type="academic_final_material",
                material_id="88",
                signature_ids=[],
            )

        self.assertEqual([], result)
        statements = [call.args[0] for call in conn.execute.call_args_list]
        lock_index = next(index for index, statement in enumerate(statements) if "pg_advisory_xact_lock" in statement)
        delete_index = next(index for index, statement in enumerate(statements) if "DELETE FROM signature_point_bindings" in statement)
        self.assertLess(lock_index, delete_index)
        lock_params = conn.execute.call_args_list[lock_index].args[1]
        self.assertIn("signature-point-binding", lock_params[0])
        self.assertIn("revision-a", lock_params[0])


if __name__ == "__main__":
    unittest.main()
