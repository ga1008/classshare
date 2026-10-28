"""Durable DM lifecycle with owned SQLite, fake models and no network."""
import asyncio
import json
from unittest.mock import patch

from classroom_app.services import wanderer_inbox_service as inbox
from classroom_app.services import wanderer_inbox_worker as worker
from classroom_app.services import wanderer_inbox_actions as actions
from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set, SETTING_DAILY_AI_CALL_CAP
from tests import test_wanderer_social as support
from tests.wanderer_behavior_support import WandererWorldTestCase, StubCaller, ZeroSleep, turn


class WandererInboxTest(WandererWorldTestCase):
    ident = staticmethod(support.WandererSocialTest.ident)
    user_of = staticmethod(support.WandererSocialTest.user_of)
    call = support.WandererSocialTest.call
    befriend = support.WandererSocialTest.befriend
    send = support.WandererSocialTest.send
    messages_from = support.WandererSocialTest.messages_from

    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            from classroom_app.services.social_common import ensure_schema
            ensure_schema(conn)
            for table in support.SOCIAL_TABLES:
                conn.execute(f"DELETE FROM {table}")
            support.grant_tier(conn, self.offering_id, self.student_pk, 3)
            conn.commit()
        self.pushes = support.PushRecorder()
        self.enterContext(patch.object(support.social_push_service, "push", self.pushes))
        self.enterContext(patch.object(support.social_push_service, "is_app_process", return_value=True))
        self.real = f"student:{self.student_pk}"
        self.teacher_identity = f"teacher:{self.teacher_id}"
        self.atai = self.set_wanderer("atai", schedule_json=support.ALWAYS_AWAKE,
                                      reply_policy_json=support.ALWAYS_REPLY, behaviors_json={})

    def ready(self):
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_social_inbox SET run_at='2000-01-01' WHERE state IN ('pending','retry')")
            conn.commit()

    def task(self, key):
        return self.rows("SELECT * FROM wanderer_social_inbox WHERE conversation_key=?", (key,))[0]

    def process(self, caller):
        self.ready()
        return asyncio.run(worker.process_once(factory=self.connect, ai_caller=caller, sleep=ZeroSleep()))

    def test_night_message_queues_immediately_and_personality_reaches_model(self):
        self.set_wanderer("atai", schedule_json={"active_hours": []}, reply_policy_json={"ignore_prob": 1, "emoji_only_prob": 1})
        key = self.befriend(self.real, self.atai)
        incoming = self.send(self.real, key, "今天心情不好，能聊聊吗")
        caller = StubCaller(turn("reply", ["怎么了，慢慢说。"]))
        self.assertEqual(self.process(caller), ["done"])
        self.assertEqual(self.task(key)["handled_message_id"], incoming["id"])
        self.assertEqual(caller.calls[0][2]["priority"], "P0")
        self.assertIn("今天心情不好", caller.calls[0][1]["system_prompt"])
        self.assertEqual(len(self.messages_from(self.ident(self.atai), key)), 1)

    def test_failure_and_malformed_json_do_not_ack_and_recover(self):
        key = self.befriend(self.real, self.atai)
        message = self.send(self.real, key)
        for caller in (StubCaller(error=TimeoutError("synthetic")), StubCaller("not json")):
            self.assertEqual(self.process(caller), ["retry"])
            self.assertEqual(self.task(key)["handled_message_id"], 0)
            self.assertEqual(self.rows("SELECT * FROM social_dm_reads"), [])
        self.assertEqual(self.process(StubCaller(turn())), ["done"])
        self.assertEqual(self.task(key)["handled_message_id"], message["id"])

    def test_cap_defers_without_random_emoji(self):
        key = self.befriend(self.real, self.atai)
        self.send(self.real, key)
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, SETTING_DAILY_AI_CALL_CAP, 0)
            conn.commit()
        caller = StubCaller()
        self.assertEqual(self.process(caller), ["deferred"])
        self.assertEqual(caller.calls, [])
        self.assertEqual(self.task(key)["handled_message_id"], 0)
        self.assertEqual(self.messages_from(self.ident(self.atai), key), [])

    def test_real_silence_records_private_reason_and_emotion(self):
        key = self.befriend(self.real, self.atai)
        self.send(self.real, key, "又来打扰你啦")
        decision = json.dumps({"action": "skip", "parts": [], "mood": "疲惫", "reason": "想安静一会儿"})
        self.assertEqual(self.process(StubCaller(decision)), ["done"])
        self.assertEqual(self.task(key)["decision_reason"], "想安静一会儿")
        self.send(self.real, key, "现在好点了吗")
        caller = StubCaller()
        self.process(caller)
        self.assertIn("想安静一会儿", caller.calls[0][1]["system_prompt"])
        self.assertNotIn("想安静一会儿", self.messages_from(self.ident(self.atai), key)[0]["body"])

    def test_arrival_during_generation_is_not_consumed(self):
        key = self.befriend(self.real, self.atai)
        first = self.send(self.real, key, "第一条")
        async def caller(*args, **kwargs):
            self.send(self.real, key, "后来这一条")
            self.assertEqual(self.task(key)["state"], "running")
            self.assertEqual(inbox.claim(2, factory=self.connect), [])
            return {"status": "success", "response_text": turn()}
        self.assertEqual(self.process(caller), ["done"])
        row = self.task(key)
        self.assertEqual(row["handled_message_id"], first["id"])
        self.assertGreater(row["latest_message_id"], first["id"])
        self.assertEqual(row["state"], "pending")
        self.assertEqual(self.process(StubCaller()), ["done"])

    def test_saved_parts_resume_without_regeneration(self):
        key = self.befriend(self.real, self.atai)
        sent = self.send(self.real, key)
        self.ready()
        task = inbox.claim(1, factory=self.connect)[0]
        response = {"status": "ok", "parts": ["第一句", "第二句"], "action": "multi", "mood": "开心", "reason": "想聊"}
        self.assertTrue(inbox.save_response(task, sent["id"], response, factory=self.connect))
        ctx = actions.context(task, factory=self.connect)
        self.assertTrue(actions.send_part(ctx, task)[0])
        inbox.release(task, "synthetic restart", factory=self.connect)
        caller = StubCaller(error=AssertionError("must not regenerate"))
        self.assertEqual(self.process(caller), ["done"])
        self.assertEqual(caller.calls, [])
        self.assertEqual([r["body"] for r in self.messages_from(self.ident(self.atai), key)], ["第一句", "第二句"])

    def test_old_lease_cannot_send_or_finish(self):
        key = self.befriend(self.real, self.atai)
        msg = self.send(self.real, key)
        self.ready()
        old = inbox.claim(1, factory=self.connect)[0]
        inbox.save_response(old, msg["id"], {"parts": ["迟到的回复"]}, factory=self.connect)
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_social_inbox SET lease_until='2000-01-01'")
            conn.commit()
        new = inbox.claim(1, factory=self.connect)[0]
        self.assertNotEqual(old["lease_token"], new["lease_token"])
        ctx = actions.context(old, factory=self.connect)
        self.assertFalse(actions.send_part(ctx, old)[0])
        self.assertFalse(actions.finish(ctx, old)[0])

    def test_two_friends_generate_concurrently_with_isolated_context(self):
        first = self.befriend(self.real, self.atai)
        second = self.befriend(self.teacher_identity, self.atai)
        self.send(self.real, first, "同学的私密话题一")
        self.send(self.teacher_identity, second, "老师的话题二")
        entered = []
        async def exercise():
            gate = asyncio.Event()
            async def caller(endpoint, payload, **kwargs):
                entered.append(payload["system_prompt"])
                if len(entered) == 2:
                    gate.set()
                await asyncio.wait_for(gate.wait(), 2)
                return {"status": "success", "response_text": turn()}
            return await worker.process_once(factory=self.connect, ai_caller=caller, sleep=ZeroSleep())
        self.ready()
        self.assertEqual(asyncio.run(exercise()), ["done", "done"])
        self.assertEqual(sum("同学的私密话题一" in p for p in entered), 1)
        self.assertEqual(sum("老师的话题二" in p for p in entered), 1)

    def test_more_than_thirty_messages_are_paginated_without_cursor_loss(self):
        key = self.befriend(self.real, self.atai)
        from classroom_app.services.social_conversation_service import insert_message
        with self.connect() as conn:
            ids = [insert_message(conn, key=key, sender=self.real, body=f"第{i:02}条", client_nonce=f"batch-{i}") for i in range(37)]
            inbox.enqueue_social(conn, self.atai, key, self.real)
            conn.commit()
        caller = StubCaller(turn())
        self.process(caller)
        self.assertEqual(self.task(key)["handled_message_id"], ids[29])
        self.assertIn("第00条", caller.calls[0][1]["system_prompt"])
        self.process(caller)
        self.assertEqual(self.task(key)["handled_message_id"], ids[-1])
        self.assertIn("第36条", caller.calls[1][1]["system_prompt"])

    def test_legacy_hook_and_bounded_read_receipt(self):
        from classroom_app.services.message_center_service import create_private_message
        with self.connect() as conn:
            create_private_message(conn, self.student, contact_identity=self.ident(self.atai), content="旧入口来信")
            conn.commit()
        row = self.rows("SELECT * FROM wanderer_social_inbox WHERE source='legacy'")[0]
        def incoming():
            with self.connect() as conn:
                create_private_message(conn, self.student, contact_identity=self.ident(self.atai), content="生成期间的新消息")
                conn.commit()
        async def caller(*args, **kwargs):
            incoming()
            return {"status": "success", "response_text": turn()}
        self.assertEqual(self.process(caller), ["done"])
        self.assertEqual(self.task(row["conversation_key"])["handled_message_id"], row["latest_message_id"])
        unread = self.rows("SELECT content FROM private_messages WHERE recipient_identity=? AND read_at IS NULL", (self.ident(self.atai),))
        self.assertEqual([r["content"] for r in unread], ["生成期间的新消息"])
        stats = json.loads(self.rows("SELECT stats_json FROM wanderer_agents WHERE id=?", (self.atai["id"],))[0]["stats_json"])
        self.assertEqual(stats["dm_replies"], 1)
        self.assertIn("dm_reply", [r["action_kind"] for r in self.actions(self.atai["id"])])

    def test_recovery_preserves_running_lease_and_does_not_reopen_idle(self):
        key = self.befriend(self.real, self.atai)
        self.send(self.real, key)
        with self.connect() as conn:
            conn.execute("DELETE FROM wanderer_social_inbox")
            conn.commit()
        self.assertEqual(inbox.recover(factory=self.connect), 1)
        self.ready()
        claim = inbox.claim(1, factory=self.connect)[0]
        self.assertEqual(inbox.recover(factory=self.connect), 0)
        self.assertEqual(self.task(key)["lease_token"], claim["lease_token"])

    def test_long_model_outage_cools_down_then_recovers_without_new_message(self):
        key = self.befriend(self.real, self.atai)
        self.send(self.real, key)
        for _ in range(6):
            self.assertEqual(self.process(StubCaller(error=TimeoutError("synthetic outage"))), ["retry"])
        row = self.task(key)
        self.assertEqual(row["state"], "retry")
        self.assertEqual(row["attempts"], 6)
        self.assertEqual(row["handled_message_id"], 0)
        self.assertEqual(self.process(StubCaller()), ["done"])
        self.assertEqual(self.task(key)["attempts"], 0)

    def test_unfriend_during_model_does_not_send_or_consume(self):
        key = self.befriend(self.real, self.atai)
        self.send(self.real, key)
        async def caller(*args, **kwargs):
            with self.connect() as conn:
                conn.execute("UPDATE social_friendships SET status='removed'")
                conn.commit()
            return {"status": "success", "response_text": turn()}
        self.assertEqual(self.process(caller), ["blocked"])
        self.assertEqual(self.messages_from(self.ident(self.atai), key), [])
        self.assertEqual(self.task(key)["handled_message_id"], 0)

    def test_pause_after_generation_resumes_saved_answer_without_model(self):
        key = self.befriend(self.real, self.atai)
        self.send(self.real, key)
        async def caller(*args, **kwargs):
            self.set_wanderer("atai", status="paused")
            return {"status": "success", "response_text": turn("reply", ["等我回来接着说"])}
        self.assertEqual(self.process(caller), ["retry"])
        self.assertEqual(self.messages_from(self.ident(self.atai), key), [])
        self.set_wanderer("atai", status="active")
        forbidden = StubCaller(error=AssertionError("saved answer must be reused"))
        self.assertEqual(self.process(forbidden), ["done"])
        self.assertEqual(forbidden.calls, [])

    def test_send_and_progress_are_one_transaction_on_failure(self):
        key = self.befriend(self.real, self.atai)
        message = self.send(self.real, key)
        self.ready()
        task = inbox.claim(1, factory=self.connect)[0]
        inbox.save_response(task, message["id"], {"parts": ["原子分段"]}, factory=self.connect)
        ctx = actions.context(task, factory=self.connect)
        from classroom_app.services import social_message_service
        original = social_message_service.send_message
        def interrupt(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("crash before advancing part")
        with patch.object(social_message_service, "send_message", side_effect=interrupt):
            with self.assertRaises(RuntimeError):
                actions.send_part(ctx, task)
        self.assertEqual(self.messages_from(self.ident(self.atai), key), [])
        self.assertEqual(self.task(key)["next_part"], 0)
        self.assertTrue(actions.send_part(ctx, task)[0])
        self.assertEqual(len(self.messages_from(self.ident(self.atai), key)), 1)

    def test_health_snapshot_never_exposes_private_content(self):
        key = self.befriend(self.real, self.atai)
        self.send(self.real, key, "仅彼此可见的内容")
        snapshot = inbox.health_snapshot(factory=self.connect)
        self.assertEqual(snapshot["counts"], {"pending": 1})
        self.assertEqual(set(snapshot), {"counts", "active_leases", "concurrency", "oldest_due_seconds", "active_by_lane", "safety_concurrency"})
        self.assertEqual(snapshot["safety_concurrency"], 1)
        self.assertNotIn(key, json.dumps(snapshot))

    def test_legacy_resume_only_counts_successfully_committed_parts(self):
        from classroom_app.services import message_center_service
        with self.connect() as conn:
            message_center_service.create_private_message(conn, self.student, contact_identity=self.ident(self.atai), content="在吗")
            conn.commit()
        self.ready()
        task = inbox.claim(1, factory=self.connect)[0]
        inbox.save_response(task, task["latest_message_id"], {"parts": ["第一句", "第二句"]}, factory=self.connect)
        ctx = actions.context(task, factory=self.connect)
        original = message_center_service.create_private_message
        def broken(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("synthetic rollback")
        with patch.object(message_center_service, "create_private_message", side_effect=broken):
            with self.assertRaises(RuntimeError):
                actions.send_part(ctx, task)
        self.assertEqual(self.rows("SELECT * FROM private_messages WHERE sender_identity=?", (ctx.identity,)), [])
        self.assertTrue(actions.send_part(ctx, task)[0])
        inbox.release(task, "restart", factory=self.connect)
        self.assertEqual(self.process(StubCaller(error=AssertionError("not regenerated"))), ["done"])
        stats = json.loads(self.rows("SELECT stats_json FROM wanderer_agents WHERE id=?", (self.atai["id"],))[0]["stats_json"])
        self.assertEqual(stats["dm_replies"], 2)
        self.assertEqual(len(self.rows("SELECT * FROM private_messages WHERE sender_identity=?", (ctx.identity,))), 2)

    def test_heartbeat_transient_database_failure_preserves_renewal_loop(self):
        waits = []
        async def sleep(seconds):
            waits.append(seconds)
        with patch.object(worker.asyncio, "sleep", side_effect=sleep), patch.object(
                inbox, "renew", side_effect=[RuntimeError("temporary database outage"), False]) as renew:
            asyncio.run(worker._heartbeat({"id": 123}, self.connect))
        self.assertEqual(renew.call_count, 2)
        self.assertEqual(waits, [20, 20])

    def test_recovery_does_not_starve_behind_outgoing_only_conversations(self):
        from classroom_app.services.social_conversation_service import insert_message
        old = self.befriend(self.real, self.atai)
        other = self.set_wanderer("lele", behaviors_json={})
        key = self.befriend(self.real, other)
        with self.connect() as conn:
            insert_message(conn, key=old, sender=self.ident(self.atai), body="只有散修主动发过消息", client_nonce="outgoing-only")
            insert_message(conn, key=key, sender=self.real, body="真正需要回复", client_nonce="incoming-only")
            conn.commit()
        self.assertEqual(inbox.recover(factory=self.connect, limit=1), 1)
        self.assertEqual(self.rows("SELECT conversation_key FROM wanderer_social_inbox")[0]["conversation_key"], key)
