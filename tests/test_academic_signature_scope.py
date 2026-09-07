from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.services.academic_final_material_service import (
    hydrate_academic_final_material_signature_paths,
)


class AcademicSignatureScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE electronic_signatures (id INTEGER PRIMARY KEY, status TEXT, deleted_at TEXT)")
        self.conn.execute("INSERT INTO electronic_signatures VALUES (7, 'active', NULL)")
        actor = patch("classroom_app.services.academic_final_material_service.signature_service.build_signature_actor", return_value={"id": 1, "role": "teacher"})
        access = patch("classroom_app.services.academic_final_material_service.signature_workflow_service.signature_use_access_state", return_value={"can_use": True})
        actor.start()
        access.start()
        self.addCleanup(actor.stop)
        self.addCleanup(access.stop)
        self.conn.execute(
            """
            CREATE TABLE signature_point_bindings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                function_point_key TEXT NOT NULL,
                material_type TEXT NOT NULL,
                material_id TEXT NOT NULL,
                material_revision TEXT NOT NULL,
                signature_id INTEGER NOT NULL,
                display_order INTEGER NOT NULL DEFAULT 0
            )
            """
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_rebuilt_record_uses_current_binding_and_rejects_stale_payload_ids(self) -> None:
        payload = {
            "fields": {
                "teacher_signature_id": 99,
                "teacher_signature_ids": [99],
                "teacher_signature_image_path": "C:/stale/private.png",
            }
        }
        record = {
            "id": 88,
            "teacher_id": 1,
            "document_type": "academic_grade_register",
            "signature_revision": "revision-new",
        }

        with patch(
            "classroom_app.services.academic_final_material_service.resolve_signature_paths",
            return_value=[],
        ):
            without_binding = hydrate_academic_final_material_signature_paths(
                self.conn, payload, record=record
            )
        fields = without_binding["fields"]
        self.assertEqual([], fields["teacher_signature_ids"])
        self.assertIsNone(fields["teacher_signature_id"])
        self.assertNotIn("teacher_signature_image_path", fields)

        self.conn.execute(
            """
            INSERT INTO signature_point_bindings (
                function_point_key, material_type, material_id,
                material_revision, signature_id, display_order
            ) VALUES (?, 'academic_final_material', '88', 'revision-new', 7, 0)
            """,
            ("academic_final_material.grade_register.teacher_signature",),
        )
        with (
            patch(
                "classroom_app.services.academic_final_material_service.resolve_signature_paths",
                return_value=["C:/current/7.png"],
            ) as resolve_paths,
            patch(
                "classroom_app.services.academic_final_material_service.compose_signature_strip",
                return_value="C:/composed/current.png",
            ),
        ):
            hydrated = hydrate_academic_final_material_signature_paths(
                self.conn, payload, record=record
            )
        fields = hydrated["fields"]
        resolve_paths.assert_called_once_with(self.conn, [7])
        self.assertEqual([7], fields["teacher_signature_ids"])
        self.assertEqual(7, fields["teacher_signature_id"])
        self.assertEqual("C:/composed/current.png", fields["teacher_signature_image_path"])


if __name__ == "__main__":
    unittest.main()
