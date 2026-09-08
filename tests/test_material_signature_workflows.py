from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from tests import test_signature_point_service as fixtures
from classroom_app.services import material_signature_revision_service as revision
from classroom_app.services import material_signature_service as materials
from classroom_app.services import signature_point_service as points, signature_workflow_service as workflow, signature_service


class MaterialSignatureWorkflowTests(unittest.TestCase):
    def setUp(self):
        fixtures.SignaturePointServiceTests.setUp(self)
        self.conn.execute("ALTER TABLE electronic_signatures ADD COLUMN file_hash TEXT NOT NULL DEFAULT 'image-v1'")
        self.conn.execute("ALTER TABLE material_ai_import_records ADD COLUMN document_type TEXT NOT NULL DEFAULT 'academic_exam_analysis'")
        self.user = {"role": "teacher", "id": 1}
        self.point = "academic_final_material.exam_analysis.department_review_signature"
        self.identity_patch = patch.object(workflow.signature_identity_service, "effective_identities_bulk", return_value={})
        self.identity_patch.start()
        self.addCleanup(self.identity_patch.stop)

    def tearDown(self):
        fixtures.SignaturePointServiceTests.tearDown(self)

    def snapshot(self):
        material = materials.load_material(self.conn, self.user, {"material_type": "academic_final_material", "material_id": "88"})
        return {**{key: material[key] for key in ("material_type", "material_id", "material_revision", "document_type", "title", "content_fingerprint", "owner_id")},
            "id": "frozen-v1", "owner_role": "teacher", "payload_json": materials.dumps(material["payload"]), "artifact_json": '{}'}

    def flow(self, ids=(1, 2)):
        return points.create_point_flow(self.conn, self.user, function_point_key=self.point, material_type="academic_final_material",
            material_id="88", signature_ids=list(ids), snapshot=self.snapshot())["flow"]

    def test_content_identity_ignores_signature_order_and_standard_marks_but_keeps_scores(self):
        original = {"fields": {"course_name": "网络", "teacher_signature_ids": [1], "department_review_opinion": "已核"}, "structured": {"scores": [80]}}
        reordered = {"fields": {"course_name": "网络", "teacher_signature_ids": [2, 1]}, "structured": {"scores": [80]}}
        self.assertEqual(revision.content_fingerprint(original), revision.content_fingerprint(reordered))
        reordered["structured"]["scores"] = [90]
        self.assertNotEqual(revision.content_fingerprint(original), revision.content_fingerprint(reordered))

    def test_mixed_direct_and_requested_signatures_preserve_full_order(self):
        self.conn.execute("UPDATE electronic_signatures SET signature_kind='stamp', owner_role='system', subject_role='system' WHERE id=2")
        flow = self.flow((2, 1))
        self.assertEqual([2, 1], [item["signature_id"] for item in flow["items"]])
        self.assertEqual(["approved", "pending"], [item["status"] for item in flow["items"]])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM signature_access_requests").fetchone()[0])
        self.assertEqual("frozen-v1", flow["snapshot_id"])

    def test_approval_rejects_document_modified_without_revision_rotation(self):
        flow = self.flow((1,))
        self.conn.execute("UPDATE material_ai_import_records SET export_payload_json = ? WHERE id=88", (json.dumps({"fields": {"course_name": "已变更"}}),))
        with self.assertRaises(signature_service.SignatureServiceError) as caught:
            workflow.review_access_request(self.conn, {"role": "teacher", "id": 2}, flow["items"][0]["request_id"], action="approve")
        self.assertEqual(409, caught.exception.status_code)
        self.assertEqual("pending", workflow.get_request(self.conn, flow["items"][0]["request_id"])["status"])

    def test_approved_grant_cannot_authorize_a_replaced_image(self):
        flow = self.flow((1,))
        workflow.review_access_request(self.conn, {"role": "teacher", "id": 2}, flow["items"][0]["request_id"], action="approve")
        self.conn.execute("UPDATE electronic_signatures SET file_hash='image-v2' WHERE id=1")
        actor = signature_service.build_signature_actor(self.conn, self.user)
        signature = workflow._signature_row(self.conn, 1)
        state = workflow.signature_use_access_state(self.conn, actor, signature, self.point,
            material_type="academic_final_material", material_id="88", material_revision="revision-a")
        self.assertFalse(state["can_use"])
        fresh = self.flow((1,))
        workflow.review_access_request(self.conn, {"role": "teacher", "id": 2}, fresh["items"][0]["request_id"], action="approve")
        self.assertTrue(workflow.signature_use_access_state(self.conn, actor, signature, self.point,
            material_type="academic_final_material", material_id="88", material_revision="revision-a")["can_use"])

    def test_manual_signature_reorder_blocks_late_application(self):
        from classroom_app.services.material_signature_apply_service import validate_application

        flow = self.flow((1,))
        workflow.review_access_request(self.conn, {"role": "teacher", "id": 2}, flow["items"][0]["request_id"], action="approve")
        points.bind_point_signatures(self.conn, self.user, function_point_key=self.point, material_type="academic_final_material", material_id="88", signature_ids=[1])
        with self.assertRaises(signature_service.SignatureServiceError) as caught:
            validate_application(self.conn, flow["id"])
        self.assertIn("已被调整", caught.exception.message)

    def test_render_token_cannot_drop_review_access_checks(self):
        import base64
        from classroom_app.services.document_render_service import issue_render_token, verify_render_token, render_token_signature_request, RENDER_TOKEN_PREFIX

        key = "a" * 64
        token = issue_render_token(key, user=self.user, signature_request_id=123)
        self.assertTrue(verify_render_token(key, token, user=self.user))
        self.assertEqual(123, render_token_signature_request(token))
        encoded, signed = token[len(RENDER_TOKEN_PREFIX):].split(".", 1)
        payload = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        payload.pop("signature_request_id")
        modified = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
        self.assertFalse(verify_render_token(key, RENDER_TOKEN_PREFIX + modified + '.' + signed, user=self.user))

    def test_content_edit_cancels_pending_requests_and_preserves_snapshot(self):
        flow = self.flow((1,))
        record = self.conn.execute("SELECT * FROM material_ai_import_records WHERE id=88").fetchone()
        revision.update_record_revision(self.conn, record, {"fields": {"course_name": "更新课程"}})
        request = workflow.get_request(self.conn, flow["items"][0]["request_id"])
        self.assertEqual("cancelled", request["status"])
        self.assertIn("失效", request["invalidation_reason"])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM signature_material_snapshots").fetchone()[0])

    def test_reviewer_list_filters_and_preview_do_not_expose_other_people_requests(self):
        flow = self.flow((1,))
        incoming = workflow.list_access_requests(self.conn, {"role": "teacher", "id": 2}, search="申请教师", document_type="academic_exam_analysis", limit=1)
        self.assertEqual(1, incoming["total"])
        self.assertTrue(incoming["items"][0]["can_review"])
        self.conn.execute("UPDATE signature_access_requests SET requester_identities_json = ?", ('["department_head"]',))
        self.assertEqual(1, workflow.list_access_requests(self.conn, {"role": "teacher", "id": 2}, identity="department_head")["total"])
        self.assertEqual(0, workflow.list_access_requests(self.conn, {"role": "teacher", "id": 2}, identity="dean")["total"])
        self.assertEqual(0, workflow.list_access_requests(self.conn, {"role": "teacher", "id": 4})["total"])
        with self.assertRaises(signature_service.SignatureServiceError):
            materials.authorized_request(self.conn, {"role": "teacher", "id": 4}, flow["items"][0]["request_id"])

    def test_wrong_concrete_document_type_is_rejected(self):
        with self.assertRaises(signature_service.SignatureServiceError) as caught:
            points.create_point_flow(self.conn, self.user, function_point_key="academic_final_material.grade_register.teacher_signature",
                material_type="academic_final_material", material_id="88", signature_ids=[1], snapshot=self.snapshot())
        self.assertEqual(400, caught.exception.status_code)

    def test_file_approval_includes_requests_outside_the_current_list_page(self):
        self.conn.execute("UPDATE electronic_signatures SET owner_id=2 WHERE id=2")
        flow = self.flow((1, 2))
        reviewer = {"role": "teacher", "id": 2}
        listed = workflow.list_access_requests(self.conn, reviewer, limit=1)
        self.assertEqual(1, len(listed["items"]))
        result = materials.review_document_requests(self.conn, reviewer, listed["items"][0]["id"], action="approve", expected_snapshot_id="frozen-v1")
        self.assertEqual(2, result["processed"])
        self.assertEqual(0, result["failed"])
        self.assertEqual("approved", self.conn.execute("SELECT status FROM signature_point_flows WHERE id=?", (flow["id"],)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
