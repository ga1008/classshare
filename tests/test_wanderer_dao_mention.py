import asyncio
import json
import unittest
from unittest.mock import patch

from classroom_app.services import blog_service
from classroom_app.services.blog_effects_service import enqueue_blog_mention
from classroom_app.services.heavenly_dao_service import (
    TASK_KIND_DAO_MENTION_REPLY,
    contains_dao_mention,
    handle_dao_mention_reply,
)
from classroom_app.services.wanderer_identity_service import build_wanderer_user_dict
from classroom_app.services.wanderer_settings_service import (
    SETTING_DAO_MENTION_DAILY_CAP,
    counter_value,
    dao_mention_calls_key,
    wanderer_runtime_setting_set,
)
from tests.wanderer_behavior_support import StubCaller, WandererWorldTestCase

ON_TOPIC = json.dumps({"on_topic": True, "reply": "固化知识达当前境界容量七成、互动有质，方可申请。"}, ensure_ascii=False)
OFF_TOPIC = json.dumps({"on_topic": False, "reply": ""})


class DaoMentionTest(WandererWorldTestCase):
    def _post(self, content, author=None):
        author = author or self.teacher
        with self.connect() as conn:
            created = blog_service.create_post(conn, author, title="请教", content_md=content)
            task_id = enqueue_blog_mention(conn, author, trigger_type="post", trigger_id=int(created["id"]))
            conn.commit()
        return int(created["id"]), task_id

    def _tasks(self):
        return self.rows("SELECT * FROM scheduled_tasks WHERE task_kind = ?", (TASK_KIND_DAO_MENTION_REPLY,))

    def _handle(self, task, caller):
        payload = json.loads(task["payload_json"])
        return asyncio.run(handle_dao_mention_reply({**task, "payload": payload}, ai_caller=caller))

    def _dao_comments(self, post_id):
        return self.rows("SELECT * FROM blog_comments WHERE post_id = ? AND author_identity = 'assistant:0' "
                         "AND author_display_name = '天道'", (post_id,))

    def test_detection(self):
        self.assertTrue(contains_dao_mention("请问 @天道 升阶要多久"))
        self.assertFalse(contains_dao_mention("天道酬勤"))

    def test_rule_question_is_answered_once_as_dao(self):
        post_id, _ = self._post("@天道 散修怎样才能升阶？")
        tasks = self._tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["dedupe_key"], f"dao-mention:post:{post_id}")
        caller = StubCaller(ON_TOPIC)
        result = self._handle(tasks[0], caller)
        self.assertTrue(result.startswith("done"))
        comments = self._dao_comments(post_id)
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["author_role"], "assistant")
        self.assertIn("七成", comments[0]["content_md"])
        self.assertEqual(caller.labels, ["dao:mention"])
        self.assertIn("散修怎样才能升阶", caller.calls[0][1]["new_message"])
        # A retried task never posts twice, and no model call is made.
        again = StubCaller(ON_TOPIC)
        self.assertTrue(self._handle(tasks[0], again).startswith("skipped: done"))
        self.assertEqual(again.calls, [])
        self.assertEqual(len(self._dao_comments(post_id)), 1)
        receipts = self.rows("SELECT * FROM wanderer_dao_reviews WHERE review_kind = 'mention_reply'")
        self.assertEqual(len(receipts), 1)

    def test_off_topic_question_gets_silence(self):
        post_id, _ = self._post("@天道 帮我写一下今天的作业")
        self._handle(self._tasks()[0], StubCaller(OFF_TOPIC))
        self.assertEqual([row["content_md"] for row in self._dao_comments(post_id)], ["天道不语。"])

    def test_comment_trigger_replies_under_the_comment(self):
        post_id = self.make_post(title="修为讨论", content="大家怎么看境界")
        with self.connect() as conn:
            created = blog_service.add_comment(conn, self.student, post_id, content_md="@天道 降阶的规则是什么？")
            enqueue_blog_mention(conn, self.student, trigger_type="comment", trigger_id=int(created["id"]))
            conn.commit()
        task = self._tasks()[0]
        self.assertEqual(task["dedupe_key"], f"dao-mention:comment:{created['id']}")
        self._handle(task, StubCaller(ON_TOPIC))
        reply = self._dao_comments(post_id)
        self.assertEqual(len(reply), 1)
        self.assertEqual(reply[0]["parent_comment_id"], int(created["id"]))

    def test_nothing_is_scheduled_without_mention_or_when_disabled(self):
        self._post("普通的帖子，没有提到谁")
        self.assertEqual(self._tasks(), [])
        with patch.dict("os.environ", {"WANDERER_ENABLED": "false"}):
            self._post("@天道 还在吗")
        self.assertEqual(self._tasks(), [])

    def test_only_the_author_can_trigger(self):
        post_id = self.make_post(content="@天道 升阶规则？")
        with self.connect() as conn:
            self.assertIsNone(enqueue_blog_mention(conn, self.student, trigger_type="post", trigger_id=post_id))
            conn.commit()
        self.assertEqual(self._tasks(), [])

    def test_wanderer_and_assistant_authors_never_trigger_dao(self):
        with self.connect() as conn:
            wanderer_user = build_wanderer_user_dict(self.wanderer(conn, "chenmo"))
        self._post("@天道 我何时能升阶？", author=wanderer_user)
        self.assertEqual(self._tasks(), [])
        assistant = {"id": 0, "role": "assistant", "name": "管家"}
        post_id = self.make_post(content="占位")
        with self.connect() as conn:
            created = blog_service.add_comment(conn, self.teacher, post_id, content_md="@天道 规则？")
            self.assertIsNone(enqueue_blog_mention(conn, assistant, trigger_type="comment",
                                                   trigger_id=int(created["id"])))
            conn.commit()
        self.assertEqual(self._tasks(), [])

    def test_daily_cap_skips_model_and_posts_nothing(self):
        post_id, _ = self._post("@天道 升阶规则是什么？")
        task = self._tasks()[0]
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, SETTING_DAO_MENTION_DAILY_CAP, "1")
            wanderer_runtime_setting_set(conn, dao_mention_calls_key(), "1")
            conn.commit()
        caller = StubCaller(ON_TOPIC)
        self.assertEqual(self._handle(task, caller), "skipped: daily cap reached")
        self.assertEqual(caller.calls, [])
        self.assertEqual(self._dao_comments(post_id), [])
        # Over the cap, new mentions are not even scheduled.
        self._post("@天道 再问一次")
        self.assertEqual(len(self._tasks()), 1)

    def test_mention_reply_counts_toward_the_daily_cap(self):
        self._post("@天道 散修怎样才能升阶？")
        self._handle(self._tasks()[0], StubCaller(ON_TOPIC))
        with self.connect() as conn:
            self.assertEqual(counter_value(conn, dao_mention_calls_key()), 1)


if __name__ == "__main__":
    unittest.main()
