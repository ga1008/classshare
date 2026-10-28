"""被评论 / 被回复 / 被 @ 的即时回复（docs/wanderer-reply-priority-2026-10-28.md）。

作息只限制主动探寻平台；被问时立刻回、不计互动限额，但每日 AI 调用上限仍然有效。
私信与课堂私信由 wanderer_inbox_worker 的 2 秒轮询负责，这里覆盖仍挂在 10 分钟节拍上的
博客通知通道。
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime

from classroom_app.services import blog_service
from classroom_app.services.blog_notifications import notify_new_comment
from classroom_app.services.wanderer_behavior_service import (
    TASK_KIND_WANDERER_REPLY_TICK,
    handle_wanderer_reply_tick,
    run_wanderer_tick_once,
    select_tick_wanderers,
)
from classroom_app.services.wanderer_identity_service import build_wanderer_user_dict, log_wanderer_action
from classroom_app.services.wanderer_reply_triggers import on_blog_notification, reply_tick_dedupe_key
from classroom_app.services.wanderer_settings_service import (
    SETTING_DAILY_AI_CALL_CAP,
    wanderer_runtime_setting_set,
)
from tests.wanderer_behavior_support import StubCaller, WandererWorldTestCase, ZeroSleep, turn

NEVER_AWAKE = {"active_hours": [], "weekday_weights": {}}


class ReplyPriorityTest(WandererWorldTestCase):
    def setUp(self):
        super().setUp()
        self.atai = self.set_wanderer(
            "atai", schedule_json=NEVER_AWAKE, daily_action_budget=1, behaviors_json={},
            reply_policy_json={"ignore_prob": 0, "emoji_only_prob": 0, "multi_part_prob": 0, "max_parts": 2},
        )
        self.ident = f"student:{self.atai['student_id']}"

    def _exhaust_budget(self) -> None:
        with self.connect() as conn:
            for _ in range(4):
                log_wanderer_action(conn, self.atai["id"], "browse_post", target_type="tick",
                                    detail={"reason": "test_budget"})
            conn.commit()

    def _student_reply(self, body: str = "学分认定那部分能再讲讲吗") -> dict:
        post_id = self.make_post()
        with self.connect() as conn:
            parent = blog_service.add_comment(conn, build_wanderer_user_dict(self.atai), post_id,
                                              content_md="我把复习方法写在这里了。")
            conn.commit()
        with self.connect() as conn:
            comment = blog_service.add_comment(conn, self.student, post_id, content_md=body,
                                               parent_comment_id=int(parent["id"]),
                                               notify_callback=notify_new_comment)
            conn.commit()
        return comment

    def _tasks(self) -> list[dict]:
        return self.rows("SELECT * FROM scheduled_tasks WHERE task_kind = ? ORDER BY id",
                         (TASK_KIND_WANDERER_REPLY_TICK,))

    def _run(self, caller, task: dict | None = None) -> str:
        row = task or self._tasks()[0]
        return asyncio.run(handle_wanderer_reply_tick(
            {"payload": json.loads(row["payload_json"])}, ai_caller=caller, sleep=ZeroSleep(),
        ))

    def test_blog_reply_schedules_a_reply_tick(self):
        self._student_reply()
        tasks = self._tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(json.loads(tasks[0]["payload_json"])["wanderer_id"], self.atai["id"])
        self.assertEqual(tasks[0]["dedupe_key"], reply_tick_dedupe_key(self.atai["id"]))

    def test_reply_tick_answers_while_asleep_and_over_budget(self):
        self._exhaust_budget()
        self._student_reply()
        caller = StubCaller(turn("reply", ["学分认定要先去教务系统提交，再找学院盖章"]))
        self._run(caller)
        replies = self.rows("SELECT content_md FROM blog_comments WHERE author_identity = ? ORDER BY id", (self.ident,))
        self.assertEqual([row["content_md"] for row in replies][-1], "学分认定要先去教务系统提交，再找学院盖章")
        kinds = [row["action_kind"] for row in self.actions(self.atai["id"])]
        self.assertIn("reply", kinds)
        self.assertEqual(kinds.count("browse_post"), 4)  # only the synthetic budget rows

    def test_ai_daily_cap_still_applies_to_blog_replies(self):
        self._student_reply()
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, SETTING_DAILY_AI_CALL_CAP, "0")
            conn.commit()
        caller = StubCaller(turn("reply", ["不该调用模型"]))
        self._run(caller)
        self.assertEqual(caller.calls, [])
        replies = self.rows("SELECT content_md FROM blog_comments WHERE author_identity = ? ORDER BY id", (self.ident,))
        self.assertEqual(len(replies), 2)  # original comment + one degraded emoji
        self.assertLessEqual(len(replies[-1]["content_md"]), 2)

    def test_wanderer_actor_never_wakes_another_wanderer(self):
        with self.connect() as conn:
            self.assertIsNone(on_blog_notification(
                conn, recipient_pk=self.atai["student_id"], actor_role="student",
                actor_pk=self.wanderer(conn, "momo")["student_id"],
            ))
        self.assertEqual(self._tasks(), [])

    def test_selection_marks_waiting_people_as_reply_only(self):
        self._exhaust_budget()
        self._student_reply()
        with self.connect() as conn:
            picked = select_tick_wanderers(conn, now=datetime.now(), rng=random.Random(3), limit=6)
        pairs = [(int(w["id"]), reply_only) for w, reply_only in picked]
        self.assertIn((int(self.atai["id"]), True), pairs)

    def test_reply_only_run_does_not_touch_other_actions(self):
        self._student_reply()
        caller = StubCaller(turn("reply", ["在的"]))
        summary = asyncio.run(run_wanderer_tick_once(
            self.connect, wanderer_ids=[self.atai["id"]], reply_only=True, social_upkeep=False,
            ai_caller=caller, sleep=ZeroSleep(),
        ))
        self.assertEqual(summary["processed"], 1)
        self.assertEqual([item["actions"] for item in summary["wanderers"]], [["reply"]])
        self.assertTrue(all(item.get("reply_only") for item in summary["wanderers"]))

    def test_reply_tick_without_wanderer_id_is_a_noop(self):
        result = asyncio.run(handle_wanderer_reply_tick({"payload": {}}, ai_caller=StubCaller(),
                                                        sleep=ZeroSleep()))
        self.assertEqual(result, "wanderer reply tick: status=ok processed=0 actions=0 ai_calls=0 failed=0")
