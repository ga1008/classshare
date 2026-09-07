"""Live visibility remains a prerequisite throughout signature authorization."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from classroom_app.services import assessment_plan_service, signature_point_service
from classroom_app.services import signature_service, signature_workflow_service as workflow
from tests import test_signature_point_service as point_test_support


class SignatureWorkflowScopeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = point_test_support.SignaturePointServiceTests()
        self.fixture.setUp()
        self.conn = self.fixture.conn
        # Keep the actual five-level permission policy, replacing only account
        # loading in this minimal in-memory workflow schema.
        self.fixture.patches[0].stop()
        self.fixture.patches[1].stop()
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(electronic_signatures)")}
        if "scope_level" not in columns:
            self.conn.execute("ALTER TABLE electronic_signatures ADD COLUMN scope_level TEXT DEFAULT 'department'")
        if "updated_at" not in columns:
            self.conn.execute("ALTER TABLE electronic_signatures ADD COLUMN updated_at TEXT")
        self.conn.execute(
            "UPDATE electronic_signatures SET school_code='school-a', college='college-a', "
            "department='department-a', scope_level='department'"
        )
        self.actors = {
            1: {"role": "teacher", "id": 1, "name": "申请教师", "is_super_admin": False,
                "scope": {"school_code": "school-a", "college": "college-a", "department": "department-a"}},
            2: {"role": "teacher", "id": 2, "name": "归属教师", "is_super_admin": False, "scope": {}},
            3: {"role": "teacher", "id": 3, "name": "签名教师", "is_super_admin": False, "scope": {}},
            9: {"role": "teacher", "id": 9, "name": "管理员", "is_super_admin": True, "scope": {}},
        }
        self.actor_patch = patch.object(
            signature_service, "build_signature_actor",
            side_effect=lambda _conn, user: {**self.actors[int(user["id"])], "role": user["role"]},
        )
        self.actor_patch.start()
        self.user = {"role": "teacher", "id": 1}
        self.point = self.fixture.point

    def tearDown(self):
        self.actor_patch.stop()
        self.fixture.tearDown()

    def signature(self):
        return workflow._signature_row(self.conn, 1)

    def create_request(self, *, legacy=False, point=None, material_type="academic_final_material", material_id="88", revision="revision-a"):
        scope = {} if legacy else {
            "material_type": material_type, "material_id": material_id, "material_revision": revision,
        }
        return workflow.create_access_request(
            self.conn, self.user, 1, function_point_keys=[point or self.point], **scope,
        )["request"]

    def approve(self, request):
        return workflow.review_access_request(
            self.conn, {"role": "teacher", "id": 2}, request["id"], action="approve",
        )

    def use(self, *, user=None, context_type="academic_final_material", context_id="88", point=None):
        return workflow.authorize_and_consume_signature_use(
            self.conn, user or self.user, 1, function_point_key=point or self.point,
            context_type=context_type, context_id=context_id,
        )

    def assert_denied(self, callback, status=403):
        with self.assertRaises(signature_service.SignatureServiceError) as raised:
            callback()
        self.assertEqual(status, raised.exception.status_code)

    def test_five_scopes_control_request_candidates_for_teachers_and_students(self):
        positions = [
            ({"school_code": "school-a", "college": "college-a", "department": "department-a"}, {"platform", "school", "college", "department"}),
            ({"school_code": "school-a", "college": "college-a", "department": "department-b"}, {"platform", "school", "college"}),
            ({"school_code": "school-a", "college": "college-b", "department": "department-a"}, {"platform", "school"}),
            ({"school_code": "school-b", "college": "college-a", "department": "department-a"}, {"platform"}),
        ]
        for role in ("teacher", "student"):
            for scope, accepted in positions:
                actor = {**self.actors[1], "role": role, "scope": scope}
                for level in ("platform", "school", "college", "department", "personal"):
                    with self.subTest(role=role, actor_scope=scope, level=level):
                        self.conn.execute("UPDATE electronic_signatures SET scope_level=? WHERE id=1", (level,))
                        state = workflow.access_state(self.conn, actor, self.signature(), self.point)
                        self.assertEqual(level in accepted, state["can_request"])
                        self.assertFalse(state["can_use"])

    def test_invisible_stamp_does_not_bypass_direct_access_or_point_request(self):
        self.conn.execute("UPDATE electronic_signatures SET signature_kind='stamp', scope_level='personal' WHERE id=1")
        self.assertEqual("", workflow.direct_authorization_mode(self.actors[1], self.signature()))
        self.assertFalse(workflow.access_state(self.conn, self.actors[1], self.signature(), self.point)["can_use"])
        self.assert_denied(lambda: signature_point_service.create_point_flow(
            self.conn, self.user, function_point_key=self.point,
            material_type="academic_final_material", material_id="88", signature_ids=[1],
        ))

    def test_pending_approval_rechecks_scope_but_rejection_remains_available(self):
        request = self.create_request()
        self.conn.execute("UPDATE electronic_signatures SET scope_level='personal' WHERE id=1")
        self.assert_denied(lambda: self.approve(request))
        self.assertEqual("pending", workflow.get_request(self.conn, request["id"])["status"])
        result = workflow.review_access_request(
            self.conn, {"role": "teacher", "id": 2}, request["id"], action="reject",
        )
        self.assertEqual("pending", result["request"]["status"])

    def test_deleted_signature_cannot_receive_a_new_approval(self):
        request = self.create_request()
        self.conn.execute("UPDATE electronic_signatures SET status='deleted' WHERE id=1")
        self.assert_denied(lambda: self.approve(request), 404)

    def test_scope_and_cancelled_grant_override_existing_idempotency_record(self):
        request = self.create_request()
        self.approve(request)
        first = self.use()
        self.assertTrue(self.use()["already_consumed"])
        self.conn.execute("UPDATE electronic_signatures SET scope_level='personal' WHERE id=1")
        self.assert_denied(self.use)
        self.conn.execute("UPDATE electronic_signatures SET scope_level='department' WHERE id=1")
        self.conn.execute("UPDATE signature_access_request_items SET status='cancelled' WHERE request_id=?", (request["id"],))
        self.assert_denied(self.use)
        replacement = self.create_request()
        self.approve(replacement)
        second = self.use()
        self.assertFalse(second["already_consumed"])
        self.assertNotEqual(first["usage_log_id"], second["usage_log_id"])

    def test_another_actor_and_admin_cannot_replay_an_owners_use(self):
        self.conn.execute("UPDATE electronic_signatures SET owner_id=1 WHERE id=1")
        self.use()
        self.conn.execute("UPDATE electronic_signatures SET owner_id=2 WHERE id=1")
        self.assert_denied(self.use)
        self.assert_denied(lambda: self.use(user={"role": "teacher", "id": 9}))

    def test_legacy_consumption_can_only_replay_the_same_actors_valid_grant(self):
        point = "assessment_plan.reviewer_signature"
        request = self.create_request(legacy=True, point=point)
        self.approve(request)
        first = self.use(point=point, context_type="assessment_plan", context_id="legacy-plan")
        repeated = self.use(point=point, context_type="assessment_plan", context_id="legacy-plan")
        self.assertEqual(first["usage_log_id"], repeated["usage_log_id"])
        self.assert_denied(lambda: self.use(point=point, context_type="assessment_plan", context_id="other-plan"))
        self.conn.execute("UPDATE signature_access_requests SET status='cancelled' WHERE id=?", (request["id"],))
        self.assert_denied(lambda: self.use(point=point, context_type="assessment_plan", context_id="legacy-plan"))

    def test_read_only_export_helper_does_not_consume_or_log(self):
        request = self.create_request()
        self.approve(request)
        before = self.conn.total_changes
        state = workflow.signature_use_access_state(
            self.conn, self.actors[1], self.signature(), self.point,
            material_type="academic_final_material", material_id="88", material_revision="revision-a",
        )
        self.assertTrue(state["can_use"])
        self.assertEqual(before, self.conn.total_changes)
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM signature_usage_logs").fetchone()[0])

    def test_claim_cannot_guess_hidden_same_name_but_platform_personal_is_claimable(self):
        self.conn.execute("UPDATE electronic_signatures SET scope_level='personal', subject_id=NULL, subject_name='申请教师' WHERE id=1")
        self.assert_denied(lambda: workflow.claim_signature(self.conn, self.user, 1))
        self.assert_denied(lambda: workflow.create_claim_request(self.conn, self.user, 1))
        self.conn.execute("UPDATE electronic_signatures SET scope_level='platform' WHERE id=1")
        with patch.object(workflow.signature_identity_service, "sync_identity_for_signature"):
            result = workflow.create_claim_request(self.conn, self.user, 1)
        self.assertEqual("direct", result["mode"])

    def test_point_hides_old_binding_after_scope_restriction(self):
        self.conn.execute(
            "INSERT INTO signature_point_bindings (function_point_key,material_type,material_id,material_revision,signature_id,display_order,bound_by_role,bound_by_id) "
            "VALUES (?, 'academic_final_material','88','revision-a',1,0,'teacher',1)", (self.point,),
        )
        self.conn.execute("UPDATE electronic_signatures SET scope_level='personal' WHERE id=1")
        with patch.object(signature_service, "list_signatures", return_value={"items": []}), \
             patch.object(signature_service, "_base_signature_select", return_value="SELECT s.* FROM electronic_signatures s"):
            state = signature_point_service.get_point_state(
                self.conn, self.user, function_point_key=self.point,
                material_type="academic_final_material", material_id="88",
            )
        self.assertEqual([], state["selected_signature_ids"])

    def test_plan_export_uses_persisted_owner_revision_and_current_scope(self):
        self.conn.executescript("""
            CREATE TABLE assessment_plans (
                id TEXT PRIMARY KEY, teacher_id INTEGER, title TEXT, signature_revision TEXT,
                examiner_signature_id INTEGER, reviewer_signature_id INTEGER,
                examiner_signature_ids_json TEXT, reviewer_signature_ids_json TEXT
            );
            INSERT INTO assessment_plans VALUES ('plan-a',1,'测试计划','plan-revision',NULL,1,'[]','[1]');
        """)
        request = self.create_request(
            point="assessment_plan.reviewer_signature", material_type="assessment_plan",
            material_id="plan-a", revision="plan-revision",
        )
        self.approve(request)
        # Caller data cannot substitute a different owner, revision or image.
        plan = {"id": "plan-a", "teacher_id": 2, "signature_revision": "forged", "reviewer_signature_ids": [2],
                "fields": {"reviewer_signature_image_path": "forged.png", "examiner_signature_image_path": "forged.png"}}
        with patch.object(assessment_plan_service, "compose_signature_strip", return_value="authorized.png"):
            before = self.conn.total_changes
            fields = assessment_plan_service.build_export_fields(self.conn, plan)
            self.assertEqual("authorized.png", fields["reviewer_signature_image_path"])
            self.assertEqual(1, fields["reviewer_signature_count"])
            self.assertNotIn("examiner_signature_image_path", fields)
            self.assertEqual(before, self.conn.total_changes)
            self.conn.execute("UPDATE electronic_signatures SET scope_level='personal' WHERE id=1")
            fields = assessment_plan_service.build_export_fields(self.conn, plan)
            self.assertNotIn("reviewer_signature_image_path", fields)
            self.assertEqual(0, fields["reviewer_signature_count"])


if __name__ == "__main__":
    unittest.main()
