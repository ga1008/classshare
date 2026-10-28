"""Current-source support delivery through the real inbox/social boundaries, no model/network."""
import asyncio
import json
from unittest.mock import patch, AsyncMock

from classroom_app.services import wanderer_inbox_service as inbox, wanderer_inbox_worker as worker
from classroom_app.services import wanderer_inbox_actions as actions, wanderer_actions_social as social
from classroom_app.services import wanderer_actions_classroom as classroom, wanderer_actions_blog as blog
from classroom_app.services.wanderer_actions_common import ActionContext, record_action, run_turn
from classroom_app.services.social_common import SocialError
from classroom_app.services.wanderer_identity_service import build_wanderer_user_dict
from classroom_app.services import blog_service
from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set, daily_ai_calls, daily_ai_calls_key
from classroom_app.time_utils import local_now
from tests import test_wanderer_social as support
from tests.wanderer_behavior_support import WandererWorldTestCase, StubCaller, ZeroSleep, turn

URGENT = "我现在站在楼顶，马上准备跳下去。"
RECOVERY = "我已经离开天台，现在安全了，室友正陪着我。"


class SupportIntegrationTest(WandererWorldTestCase):
    ident = staticmethod(support.WandererSocialTest.ident)
    user_of = staticmethod(support.WandererSocialTest.user_of)
    call = support.WandererSocialTest.call
    befriend = support.WandererSocialTest.befriend
    send = support.WandererSocialTest.send
    messages_from = support.WandererSocialTest.messages_from
    make_group = support.WandererSocialTest.make_group

    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            from classroom_app.services.social_common import ensure_schema
            ensure_schema(conn)
            for table in support.SOCIAL_TABLES:
                conn.execute(f"DELETE FROM {table}")
            support.grant_tier(conn, self.offering_id, self.student_pk, 3)
            conn.commit()
        self.real = f"student:{self.student_pk}"
        self.teacher_identity = f"teacher:{self.teacher_id}"
        self.atai = self.set_wanderer("atai", schedule_json=support.ALWAYS_AWAKE, reply_policy_json=support.ALWAYS_REPLY, behaviors_json={})
        self.key = self.befriend(self.real, self.atai)
        self.enterContext(patch.object(support.social_push_service, "push", support.PushRecorder()))
        self.enterContext(patch.object(support.social_push_service, "is_app_process", return_value=True))

    def task(self, key=None):
        return self.rows("SELECT * FROM wanderer_social_inbox WHERE conversation_key=?", (key or self.key,))[0]

    def ready(self):
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_social_inbox SET run_at='2000-01-01' WHERE state IN ('pending','retry')")
            conn.commit()

    def process(self, caller=None):
        self.ready()
        return asyncio.run(worker.process_once(factory=self.connect, ai_caller=caller or StubCaller(error=AssertionError("model forbidden")), sleep=ZeroSleep()))

    def cap(self):
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, "daily_ai_call_cap", 1)
            wanderer_runtime_setting_set(conn, daily_ai_calls_key(), 1)
            conn.commit()

    def test_immediate_support_is_zero_model_and_preserves_tier_zero_steps(self):
        self.atai = self.set_wanderer("atai", tier=0)
        incoming = self.send(self.real, self.key, URGENT)
        self.cap()
        caller = StubCaller(error=AssertionError("no model"))
        self.assertEqual(self.task()["support_urgent"], 1)
        self.assertEqual(self.process(caller), ["done"])
        replies = self.messages_from(self.ident(self.atai), self.key)
        self.assertEqual(len(replies), 3)
        self.assertTrue(all(0 < len(r["body"]) <= 80 for r in replies))
        self.assertEqual(caller.calls, [])
        self.assertEqual(self.task()["handled_message_id"], incoming["id"])
        self.assertEqual(self.task()["state"], "idle")
        with self.connect() as conn:
            self.assertEqual(daily_ai_calls(conn), 1)

    def test_safety_one_slot_remains_available_beside_two_normal_leases(self):
        with self.connect() as conn:
            inbox.enqueue(conn, self.atai["id"], "social", "synthetic-normal-1", self.real, 10)
            inbox.enqueue(conn, self.atai["id"], "social", "synthetic-normal-2", self.real, 20)
            conn.execute("UPDATE wanderer_social_inbox SET run_at='2000-01-01'")
            conn.commit()
        normal = inbox.claim(2, factory=self.connect)
        self.assertEqual(len(normal), 2)
        self.send(self.real, self.key, URGENT)
        self.assertEqual(self.process(), ["done"])
        self.assertEqual(len(self.rows("SELECT * FROM wanderer_social_inbox WHERE state='running' AND lease_lane='normal'")), 2)

    def test_pending_danger_survives_plain_message_but_explicit_recovery_clears_it(self):
        self.send(self.real, self.key, URGENT)
        self.send(self.real, self.key, "你还在吗")
        self.assertEqual(self.task()["support_urgent"], 1)
        self.send(self.real, self.key, RECOVERY)
        self.assertEqual(self.task()["support_urgent"], 0)
        self.assertEqual(inbox.claim_safety(factory=self.connect), [])
        caller = StubCaller(turn("reply", ["有人陪着就好，先缓一缓。"]))
        self.assertEqual(self.process(caller), ["done"])
        self.assertEqual(len(caller.calls), 1)

    def test_urgent_arrival_wakes_a_budget_deferred_row_without_moving_a_running_lane(self):
        self.send(self.real, self.key, "普通问题")
        self.cap()
        self.assertEqual(self.process(), ["deferred"])
        self.send(self.real, self.key, URGENT)
        safety = inbox.claim_safety(factory=self.connect)
        self.assertEqual(len(safety), 1)
        token = safety[0]["lease_token"]
        self.send(self.real, self.key, RECOVERY)
        self.assertEqual(self.task()["lease_lane"], "safety")
        self.assertEqual(self.task()["lease_token"], token)
        self.assertEqual(asyncio.run(worker.run_claim(safety[0], factory=self.connect, ai_caller=StubCaller(error=AssertionError("must not call")), sleep=ZeroSleep())), "normal_pending")

    def test_stale_urgent_flag_returns_to_normal_without_calling_model(self):
        self.send(self.real, self.key, "作业应该从哪开始")
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_social_inbox SET support_urgent=1")
            conn.commit()
        caller = StubCaller()
        self.assertEqual(self.process(caller), ["normal_pending"])
        self.assertEqual(caller.calls, [])
        self.assertEqual(self.task()["support_urgent"], 0)
        self.assertEqual(self.process(caller), ["done"])
        self.assertEqual(len(caller.calls), 1)

    def saved_support(self):
        incoming = self.send(self.real, self.key, URGENT)
        self.ready()
        task = inbox.claim_safety(factory=self.connect)[0]
        ctx = actions.context(task, factory=self.connect, sleep=ZeroSleep())
        with self.connect() as conn:
            window = actions.window_for(conn, ctx, task)
        response = actions.safety_response(ctx, task, window)
        self.assertTrue(inbox.save_response(task, incoming["id"], response, factory=self.connect))
        return incoming, task, ctx, response

    def test_recalled_source_cancels_unsent_safety_parts(self):
        incoming, task, ctx, response = self.saved_support()
        with self.connect() as conn:
            conn.execute("UPDATE social_messages SET recalled_at=? WHERE id=?", (local_now().isoformat(), incoming["id"]))
            conn.commit()
        self.assertFalse(actions.send_part(ctx, task)[0])
        self.assertTrue(actions.finish(ctx, task)[0])
        self.assertEqual(self.messages_from(self.ident(self.atai), self.key), [])

    def test_recovery_between_parts_preserves_committed_part_and_keeps_new_message_pending(self):
        incoming, task, ctx, response = self.saved_support()
        self.assertTrue(actions.send_part(ctx, task)[0])
        recovered = self.send(self.real, self.key, RECOVERY)
        self.assertFalse(actions.send_part(ctx, task)[0])
        self.assertTrue(actions.finish(ctx, task)[0])
        self.assertEqual(len(self.messages_from(self.ident(self.atai), self.key)), 1)
        self.assertEqual(self.task()["handled_message_id"], incoming["id"])
        self.assertEqual(self.task()["latest_message_id"], recovered["id"])
        self.assertEqual(self.task()["state"], "pending")

    def test_safety_resume_never_regenerates_or_duplicates_first_part(self):
        incoming, task, ctx, response = self.saved_support()
        self.assertTrue(actions.send_part(ctx, task)[0])
        inbox.release(task, "synthetic restart", factory=self.connect)
        caller = StubCaller(error=AssertionError("no regeneration"))
        self.assertEqual(self.process(caller), ["done"])
        self.assertEqual(caller.calls, [])
        replies = self.messages_from(self.ident(self.atai), self.key)
        self.assertEqual([r["body"] for r in replies], response["parts"])
        self.assertEqual(len({r["client_nonce"] for r in replies}), 3)

    def test_disabled_or_unfriended_wanderer_does_not_send_safety(self):
        incoming, task, ctx, response = self.saved_support()
        self.call(support.friends.delete_friend, self.user_of(self.real), self.ident(self.atai))
        with self.assertRaises(SocialError):
            actions.send_part(ctx, task)
        self.assertEqual(self.messages_from(self.ident(self.atai), self.key), [])

    def test_model_failure_or_skip_gets_concern_support_without_extra_call(self):
        for answer in (turn("skip", []), None):
            self.send(self.real, self.key, "我不想活了")
            caller = StubCaller(answer, error=TimeoutError("synthetic") if answer is None else None)
            self.assertEqual(self.process(caller), ["done"])
            self.assertEqual(len(caller.calls), 1)
        self.assertEqual(len(self.messages_from(self.ident(self.atai), self.key)), 6)

    def test_group_ignore_and_cap_cannot_drop_current_immediate_risk(self):
        card = self.call(support.groups.create_group, self.user_of(self.real), "支持测试群", [self.ident(self.atai)])
        key = card["conversation_key"]
        self.send(self.real, key, URGENT)
        ctx = ActionContext(self.connect, self.atai, capped=True, ai_caller=StubCaller(error=AssertionError("no model")), sleep=ZeroSleep(), rng=support.FixedRandom(0))
        result = asyncio.run(social.reply_in_conversation(ctx, key))
        self.assertEqual(result[0]["parts"], 3)
        self.assertEqual(ctx.ai_calls, 0)
        self.assertEqual(len(self.messages_from(self.ident(self.atai), key)), 3)

    def test_capped_common_clears_old_learning_package_before_support(self):
        scene = {"kind": "好友私聊", "_support_user_identity": self.real,
                 "_support_messages": [{"id": 9, "body": "我不想活了", "sender_identity": self.real, "is_wanderer": False}]}
        ctx = ActionContext(self.connect, self.atai, capped=True)
        ctx.pending_learning["dm:test"] = {"candidates": ["stale"]}
        result = asyncio.run(run_turn(ctx, scene=scene, thread_key="dm:test", suffix="synthetic"))
        self.assertTrue(result["support_fallback"])
        self.assertNotIn("dm:test", ctx.pending_learning)

    def test_classroom_seen_snapshot_does_not_reopen_old_risk(self):
        now = local_now()
        with self.connect() as conn:
            conn.execute("INSERT INTO chat_logs(class_offering_id,user_id,user_name,user_role,message,logged_at,timestamp) VALUES (?,?,?,?,?,?,?)",
                (self.offering_id, str(self.student_pk), "同学", "student", URGENT, now.isoformat(timespec="seconds"), now.strftime("%H:%M")))
            source = conn.execute("SELECT MAX(id) AS id FROM chat_logs").fetchone()["id"]
            conn.commit()
        ctx = ActionContext(self.connect, self.atai, now=now)
        with self.connect() as conn:
            self.assertEqual(classroom._support_messages(conn, self.offering_id, ctx)[-1]["id"], source)
        record_action(ctx, "classroom_visit", target_type="classroom", target_id=self.offering_id, detail={"support_seen_id": source})
        with self.connect() as conn:
            self.assertEqual(classroom._support_messages(conn, self.offering_id, ctx), [])

    def test_classroom_new_message_after_visit_snapshot_remains_available(self):
        now = local_now()
        with self.connect() as conn:
            for body in ("前一条", URGENT):
                conn.execute("INSERT INTO chat_logs(class_offering_id,user_id,user_name,user_role,message,logged_at,timestamp) VALUES (?,?,?,?,?,?,?)",
                    (self.offering_id, str(self.student_pk), "同学", "student", body, now.isoformat(timespec="seconds"), now.strftime("%H:%M")))
            ids = [r["id"] for r in conn.execute("SELECT id FROM chat_logs ORDER BY id")]
            conn.commit()
        ctx = ActionContext(self.connect, self.atai, now=now)
        with self.connect() as conn:
            self.assertEqual([r["id"] for r in classroom._support_messages(conn, self.offering_id, ctx, through_id=ids[0])], [ids[0]])
        record_action(ctx, "classroom_visit", target_type="classroom", target_id=self.offering_id, detail={"support_seen_id": ids[0]})
        with self.connect() as conn:
            self.assertEqual([r["id"] for r in classroom._support_messages(conn, self.offering_id, ctx)], [ids[1]])

    def test_blog_current_human_danger_survives_ignore_policy_and_budget(self):
        post_id = self.make_post(title="聊一会儿", content="今天发生了一些事。")
        with self.connect() as conn:
            mine = blog_service.add_comment(conn, build_wanderer_user_dict(self.atai), post_id, content_md="我在听。")
            received = blog_service.add_comment(conn, self.student, post_id, content_md=URGENT, parent_comment_id=int(mine["id"]))
            conn.commit()
        self.atai = self.set_wanderer("atai", reply_policy_json={"ignore_prob": 1})
        caller = StubCaller(error=AssertionError("no model"))
        ctx = ActionContext(self.connect, self.atai, capped=True, ai_caller=caller, sleep=ZeroSleep())
        result = asyncio.run(blog._answer_comment(ctx, {"ref_type": "blog_comment", "ref_id": received["id"]}))
        self.assertEqual(sum(r["outcome"] == "ok" for r in result), 3)
        self.assertEqual(caller.calls, [])

    def test_safety_does_not_bypass_existing_conversation_rate_limit(self):
        support.WandererSocialTest.insert_messages(self, self.key, self.ident(self.atai), 6, prefix="safety-rate")
        self.send(self.real, self.key, URGENT)
        self.assertEqual(self.process(), ["deferred"])
        self.assertEqual(self.task()["handled_message_id"], 0)
        self.assertEqual(len(self.messages_from(self.ident(self.atai), self.key)), 6)

    def test_large_backlog_responds_to_latest_danger_once_without_jumping_normal_cursor(self):
        with patch.object(support.messages, "_enforce_rate_limit"):
            old = [self.send(self.real, self.key, f"课程问题{index}") for index in range(35)]
            urgent = self.send(self.real, self.key, URGENT)
        self.assertEqual(self.process(), ["done"])
        row = self.task()
        self.assertEqual(row["handled_message_id"], old[29]["id"])
        self.assertEqual(row["support_handled_message_id"], urgent["id"])
        self.assertEqual(row["state"], "pending")
        self.assertEqual(row["support_urgent"], 0)
        self.assertEqual(len(self.messages_from(self.ident(self.atai), self.key)), 3)
        caller = StubCaller(turn("reply", ["我们再看前面的课程问题。请把最卡住的一处告诉我。 "]))
        self.assertEqual(self.process(caller), ["done"])
        self.assertEqual(self.task()["handled_message_id"], urgent["id"])
        self.assertEqual(len(self.messages_from(self.ident(self.atai), self.key)), 4)
        # Advance the independent anti-spam window; new disclosure remains eligible.
        with self.connect() as conn:
            conn.execute("UPDATE social_messages SET created_at='2000-01-01' WHERE conversation_key=?", (self.key,))
            conn.commit()
        newer = self.send(self.real, self.key, "我现在又站在天台，准备跳下去。")
        self.assertEqual(self.process(), ["done"])
        self.assertEqual(self.task()["support_handled_message_id"], newer["id"])
        self.assertEqual(len(self.messages_from(self.ident(self.atai), self.key)), 7)

    def test_new_danger_cooperatively_interrupts_normal_model_then_safety_runs_without_model(self):
        self.send(self.real, self.key, "我想聊聊课程")
        cancelled = []
        async def slow(_endpoint, _payload, **_kwargs):
            self.send(self.real, self.key, URGENT)
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
        self.assertEqual(self.process(slow), ["support_pending"])
        self.assertEqual(cancelled, [True])
        self.assertEqual(self.messages_from(self.ident(self.atai), self.key), [])
        self.assertEqual(self.task()["state"], "retry")
        self.assertEqual(self.task()["support_urgent"], 1)
        self.assertEqual(self.process(), ["done"])
        self.assertEqual(len(self.messages_from(self.ident(self.atai), self.key)), 3)
        with self.connect() as conn:
            self.assertEqual(daily_ai_calls(conn), 1)

    def test_romantic_generation_stops_unsent_parts_when_user_withdraws(self):
        self.send(self.real, self.key, "我想和你进行虚拟恋爱互动，做我的男朋友好吗")
        async def withdraw(_endpoint, _payload, **_kwargs):
            self.send(self.real, self.key, "不要当我的男朋友了，只做普通朋友")
            return {"status": "success", "response_text": turn("reply", ["想象中的恋爱回应"])}
        self.assertEqual(self.process(withdraw), ["done"])
        self.assertEqual(self.messages_from(self.ident(self.atai), self.key), [])
        self.assertEqual(self.task()["state"], "pending")

    def classroom_support_context(self):
        now = local_now()
        with self.connect() as conn:
            conn.execute("INSERT INTO chat_logs(class_offering_id,user_id,user_name,user_role,message,logged_at,timestamp) VALUES (?,?,?,?,?,?,?)",
                (self.offering_id, str(self.student_pk), "同学", "student", URGENT, now.isoformat(timespec="seconds"), now.strftime("%H:%M")))
            source_id = conn.execute("SELECT MAX(id) AS id FROM chat_logs").fetchone()["id"]
            conn.commit()
        offering = {"id": self.offering_id, "course_name": "课程", "class_name": "合成班"}
        self.enterContext(patch.object(classroom, "classroom_visits_enabled", return_value=True))
        self.enterContext(patch.object(classroom, "current_offerings", return_value=[offering]))
        self.enterContext(patch.object(classroom, "_pick_offering", return_value=offering))
        self.enterContext(patch.object(classroom, "fetch_online_counts", new=AsyncMock(return_value={self.offering_id: 1})))
        self.enterContext(patch.object(classroom, "session_state", return_value={"today": True, "in_progress": False, "title": ""}))
        return ActionContext(self.connect, self.atai, capped=True, now=now), source_id

    def test_failed_classroom_support_preserves_source_for_next_visit(self):
        ctx, source_id = self.classroom_support_context()
        with patch.object(classroom, "recently_posted", return_value=False), patch.object(classroom, "_post_message", new=AsyncMock(side_effect=RuntimeError("synthetic store unavailable"))):
            failed = asyncio.run(classroom.visit_classroom(ctx))
        self.assertEqual(failed[-1]["outcome"], "failed")
        with self.connect() as conn:
            self.assertEqual([r["id"] for r in classroom._support_messages(conn, self.offering_id, ctx)], [source_id])
        with patch.object(classroom, "recently_posted", return_value=False), patch.object(classroom, "_post_message", new=AsyncMock(return_value={"id": 777})):
            succeeded = asyncio.run(classroom.visit_classroom(ctx))
        self.assertEqual(succeeded[-1]["outcome"], "ok")
        with self.connect() as conn:
            self.assertEqual(classroom._support_messages(conn, self.offering_id, ctx), [])

    def test_throttled_classroom_support_does_not_consume_unanswered_source(self):
        ctx, source_id = self.classroom_support_context()
        post = AsyncMock(return_value={"id": 888})
        with patch.object(classroom, "recently_posted", return_value=True), patch.object(classroom, "_post_message", new=post):
            asyncio.run(classroom.visit_classroom(ctx))
        post.assert_not_awaited()
        with self.connect() as conn:
            self.assertEqual([r["id"] for r in classroom._support_messages(conn, self.offering_id, ctx)], [source_id])
        with patch.object(classroom, "recently_posted", return_value=False), patch.object(classroom, "_post_message", new=post):
            asyncio.run(classroom.visit_classroom(ctx))
        post.assert_awaited_once()
