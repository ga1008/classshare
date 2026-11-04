import unittest
from unittest.mock import patch

from classroom_app.dependencies import decode_token_payload
from classroom_app.services import material_render_grants as grants


class MaterialRenderGrantTests(unittest.TestCase):
    def test_render_grant_is_node_scoped_and_dies_with_the_session(self):
        user = {"id": 7, "role": "student", "session_id": "old-session"}
        token = grants.issue_material_render_grant(23, user)
        self.assertIsNotNone(token)
        self.assertIsNone(decode_token_payload(token))
        with patch.object(grants, "get_user_session", return_value={"session_id": "old-session"}):
            self.assertEqual(grants.resolve_material_render_grant(token), (23, {"id": 7, "role": "student"}))
        with patch.object(grants, "get_user_session", return_value={"session_id": "new-session"}):
            self.assertIsNone(grants.resolve_material_render_grant(token))

    def test_cookie_free_render_grant_requires_a_live_web_session(self):
        self.assertIsNone(grants.issue_material_render_grant(23, {"id": 7, "role": "student"}))
        with patch.object(grants, "get_user_session") as lookup:
            self.assertIsNone(grants.resolve_material_render_grant("not-signed"))
        lookup.assert_not_called()
