from classroom_app.services.heavenly_dao_service import _one_line, warn
from tests.wanderer_test_support import WandererDbTestCase


class DaoTraceTest(WandererDbTestCase):
    def test_punishment_leaves_one_line_notice_and_a_located_action(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            warn(conn, wanderer, "你在课堂上嘲讽同学。附带的证据细节不该原样推给对方。", {"excerpt": "x"})
            conn.commit()
            notice = conn.execute("SELECT title, body_preview FROM message_center_notifications "
                                  "ORDER BY id DESC LIMIT 1").fetchone()
            action = conn.execute("SELECT action_kind, location, content_preview FROM wanderer_action_log "
                                  "WHERE wanderer_id = ? ORDER BY id DESC LIMIT 1", (wanderer["id"],)).fetchone()
            memory = conn.execute("SELECT kind, location FROM wanderer_episodic_memories "
                                  "WHERE wanderer_id = ? ORDER BY id DESC LIMIT 1", (wanderer["id"],)).fetchone()
        self.assertEqual(notice["body_preview"], "你在课堂上嘲讽同学。")
        self.assertEqual(action["action_kind"], "dao_verdict")
        self.assertEqual(action["location"], "天道台")
        self.assertIn("天道警告", action["content_preview"])
        self.assertEqual(memory["kind"], "life")
        self.assertEqual(memory["location"], "天道台")

    def test_one_line_never_reaches_the_limit_without_a_mark(self):
        self.assertEqual(_one_line("很短的一句话", 60), "很短的一句话")
        self.assertTrue(_one_line("很长" * 80, 20).endswith("…"))
