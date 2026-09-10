import unittest
from unittest.mock import patch
from fastapi import HTTPException

from classroom_app.services import agent_action_registry as registry


class AgentActionIntListTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(registry.AGENT_ACTION_DEFINITIONS, {
            "batch_fixture": {"fields": {"ids": {"type": "int_list", "required": True,
                "min_items": 1, "max_items": 3, "minimum": 1, "maximum": 100,
                "canonical_sorted": True}}},
        }))

    def test_preserves_full_intent_with_canonical_order(self):
        original = [30, 1, 9]
        clean, errors = registry.validate_action_params("batch_fixture", {"ids": original}, reject_unknown=True)
        self.assertEqual((clean, errors), ({"ids": [1, 9, 30]}, []))
        self.assertEqual(original, [30, 1, 9])

    def test_rejects_wrong_types_duplicates_and_limits_without_truncating(self):
        for value in ([], [1, 2, 3, 4], [1, 1], [True], [1.0], ["1"], [0], [-1], [101],
                      [None], [[1]], [{"id": 1}], "1,2", {"ids": [1]}, None):
            with self.subTest(value=value):
                clean, errors = registry.validate_action_params("batch_fixture", {"ids": value}, reject_unknown=True)
                self.assertTrue(errors)
                self.assertNotIn("ids", clean)

    def test_confirmation_tokens_bind_all_ids_and_reject_changed_batch(self):
        issued = registry.issue_action_confirmation_token(teacher_id=7, task_id=9, action_index=0,
            action="batch_fixture", params={"ids": [30, 1]})
        self.assertEqual(issued["params"], {"ids": [1, 30]})
        with self.assertRaises(HTTPException):
            registry.verify_action_confirmation_token(token=issued["confirmation_token"], teacher_id=7,
                task_id=9, action_index=0, action="batch_fixture", params={"ids": [1]})


if __name__ == "__main__":
    unittest.main()
