import asyncio
import random
import unittest
from datetime import datetime
from unittest.mock import patch

from classroom_app import database
from classroom_app.services import blog_service, message_center_service, wanderer_behavior_service
from classroom_app.services.blog_notifications import notify_new_comment
from classroom_app.services.wanderer_behavior_service import (
    DAO_DAILY_DEDUPE_KEY,
    TICK_DEDUPE_KEY,
    ensure_wanderer_scheduled_tasks,
    run_wanderer_tick_once,
    select_awake_wanderers,
)
from classroom_app.services.wanderer_identity_service import build_wanderer_user_dict, log_wanderer_action
from classroom_app.services.wanderer_settings_service import SETTING_DAILY_AI_CALL_CAP, wanderer_runtime_setting_set
from tests.wanderer_behavior_support import ONLY_BLOG, StubCaller, WandererWorldTestCase, ZeroSleep, turn

MONDAY_NIGHT = datetime(2026, 10, 26, 23, 0)
MONDAY_AFTERNOON = datetime(2026, 10, 26, 15, 0)


async def _noop(_ctx):
    return []


class AwakeSelectionTest(WandererWorldTestCase):
    def _awake_slugs(self, now, seed=1):
        with self.connect() as conn:
            return {item["slug"] for item in select_awake_wanderers(conn, now=now, rng=random.Random(seed), limit=20)}

    def test_active_hours_gate(self):
        self.set_wanderer("linwanzhou", schedule_json={"active_hours": [[22, 24], [0, 2]], "weekday_weights": {}})
        self.assertIn("linwanzhou", self._awake_slugs(MONDAY_NIGHT))
        self.assertNotIn("linwanzhou", self._awake_slugs(MONDAY_AFTERNOON))

    def test_wrapping_hours_and_weekday_weight_gate(self):
        self.set_wanderer("linwanzhou", schedule_json={"active_hours": [[22, 2]], "weekday_weights": {"0": 0}})
        self.assertNotIn("linwanzhou", self._awake_slugs(MONDAY_NIGHT))  # Monday weight 0
        self.set_wanderer("linwanzhou", schedule_json={"active_hours": [[22, 2]], "weekday_weights": {"0": 1}})
        self.assertIn("linwanzhou", self._awake_slugs(datetime(2026, 10, 27, 1, 0)))

    def test_daily_budget_and_status_gate(self):
        wanderer = self.set_wanderer("linwanzhou", daily_action_budget=2,
                                     schedule_json={"active_hours": [[0, 24]], "weekday_weights": {}})
        self.assertIn("linwanzhou", self._awake_slugs(datetime.now()))
        with self.connect() as conn:
            for _ in range(3):
                log_wanderer_action(conn, wanderer["id"], "browse_post", target_id=1)
            conn.commit()
        self.assertNotIn("linwanzhou", self._awake_slugs(datetime.now()))
        self.set_wanderer("atai", status="paused", schedule_json={"active_hours": [[0, 24]], "weekday_weights": {}})
        self.assertNotIn("atai", self._awake_slugs(datetime.now()))

    def test_batch_limit(self):
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_agents SET schedule_json = ?", ('{"active_hours": [[0, 24]]}',))
            conn.commit()
            picked = select_awake_wanderers(conn, now=datetime.now(), rng=random.Random(2), limit=6)
        self.assertEqual(len(picked), 6)


class SchedulerRegistrationTest(WandererWorldTestCase):
    def test_tasks_are_registered_once(self):
        with self.connect() as conn:
            first = ensure_wanderer_scheduled_tasks(conn)
            second = ensure_wanderer_scheduled_tasks(conn)
            conn.commit()
        self.assertEqual(first, second)
        rows = {row["dedupe_key"]: row for row in self.rows("SELECT * FROM scheduled_tasks")}
        self.assertEqual(rows[TICK_DEDUPE_KEY]["recurrence_seconds"], 600)
        self.assertEqual(rows[TICK_DEDUPE_KEY]["task_kind"], "wanderer_tick")
        self.assertEqual(rows[DAO_DAILY_DEDUPE_KEY]["recurrence_seconds"], 86400)
        self.assertIn("T03:30:00", rows[DAO_DAILY_DEDUPE_KEY]["run_at"])

    def test_review_is_rearmed_when_the_hour_changes(self):
        with self.connect() as conn:
            ensure_wanderer_scheduled_tasks(conn)
            conn.commit()
        rows = {row["dedupe_key"]: row for row in self.rows("SELECT * FROM scheduled_tasks")}
        self.assertEqual(rows[TICK_DEDUPE_KEY]["max_attempts"], 1000)
        tick_run_at = rows[TICK_DEDUPE_KEY]["run_at"]
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, "dao_review_hour", "05:45")
            ensure_wanderer_scheduled_tasks(conn)
            conn.commit()
        rows = {row["dedupe_key"]: row for row in self.rows("SELECT * FROM scheduled_tasks")}
        self.assertIn("T05:45:00", rows[DAO_DAILY_DEDUPE_KEY]["run_at"])
        self.assertEqual(rows[TICK_DEDUPE_KEY]["run_at"], tick_run_at)  # live tick keeps its clock
        armed = self.rows("SELECT value FROM wanderer_runtime_settings WHERE key = 'dao_review_hour_armed'")
        self.assertEqual(armed[0]["value"], "05:45")

    def test_disabled_tick_is_a_noop_unless_forced(self):
        with patch.dict("os.environ", {"WANDERER_ENABLED": "false"}):
            caller = StubCaller()
            summary = asyncio.run(run_wanderer_tick_once(database.get_db_connection, ai_caller=caller))
        self.assertEqual(summary["status"], "disabled")
        self.assertEqual(caller.calls, [])

    def test_handlers_are_registered(self):
        from classroom_app.services import scheduled_task_handlers  # noqa: F401 - registers handlers
        from classroom_app.services.scheduled_task_service import get_registered_task_kinds

        kinds = get_registered_task_kinds()
        for kind in ("wanderer_tick", "wanderer_dao_daily_review", "wanderer_dao_mention_reply"):
            self.assertIn(kind, kinds)


class BlogTickTest(WandererWorldTestCase):
    def _blog_only(self, slug, **extra):
        return self.set_wanderer(slug, behaviors_json=ONLY_BLOG, activity_level=1, **extra)

    def test_tick_comments_and_likes_a_post(self):
        atai = self._blog_only("atai")
        post_id = self.make_post()
        caller = StubCaller(turn("reply", ["冲！练起来！"], like=True, mood="兴奋"))
        summary = self.tick(caller, [atai["id"]])
        self.assertEqual(summary["processed"], 1)
        identity = f"student:{atai['student_id']}"
        comments = self.rows("SELECT * FROM blog_comments WHERE author_identity = ?", (identity,))
        self.assertEqual([row["content_md"] for row in comments], ["冲！练起来！"])
        self.assertEqual(comments[0]["post_id"], post_id)
        self.assertEqual(comments[0]["author_display_name"], "阿泰")
        likes = self.rows("SELECT * FROM blog_likes WHERE user_identity = ? AND target_type = 'post'", (identity,))
        self.assertEqual(len(likes), 1)
        kinds = [row["action_kind"] for row in self.actions(atai["id"])]
        for kind in ("browse_post", "like_post", "comment"):
            self.assertIn(kind, kinds)
        self.assertEqual(caller.labels, ["wanderer:atai:blog_post"])
        self.assertEqual(caller.calls[0][2]["priority"], "P2")
        threads = self.rows("SELECT thread_key FROM wanderer_memory_threads WHERE wanderer_id = ?", (atai["id"],))
        self.assertIn(f"blog_post:{post_id}", [row["thread_key"] for row in threads])
        # The post author (a real teacher) is notified like for any student comment.
        notes = self.rows("SELECT * FROM message_center_notifications WHERE recipient_identity = ?",
                          (f"teacher:{self.teacher_id}",))
        self.assertTrue(notes)

    def test_comment_switch_off_only_likes(self):
        behaviors = {**ONLY_BLOG, "blog_comment": 0}
        atai = self.set_wanderer("atai", behaviors_json=behaviors, activity_level=1)
        self.make_post()
        self.tick(StubCaller(turn("reply", ["想说的话"], like=True)), [atai["id"]])
        identity = f"student:{atai['student_id']}"
        self.assertEqual(self.rows("SELECT * FROM blog_comments WHERE author_identity = ?", (identity,)), [])
        self.assertEqual(len(self.rows("SELECT * FROM blog_likes WHERE user_identity = ?", (identity,))), 1)
        self.assertIn("skip", [row["action_kind"] for row in self.actions(atai["id"])])

    def test_capped_day_uses_no_ai(self):
        atai = self._blog_only("atai")
        self.make_post()
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, SETTING_DAILY_AI_CALL_CAP, "0")
            conn.commit()
        caller = StubCaller(turn("reply", ["不该出现"], like=True))
        summary = self.tick(caller, [atai["id"]])
        self.assertEqual(caller.calls, [])
        self.assertTrue(summary["capped"])
        kinds = {row["action_kind"] for row in self.actions(atai["id"])}
        self.assertTrue(kinds <= {"browse_post", "like_post", "skip"}, kinds)
        self.assertEqual(self.rows("SELECT * FROM blog_comments"), [])

    def test_skip_turn_logs_skip(self):
        atai = self._blog_only("atai")
        self.make_post()
        self.tick(StubCaller(turn("skip", [])), [atai["id"]])
        skips = [row for row in self.actions(atai["id"]) if row["action_kind"] == "skip"]
        self.assertEqual(len(skips), 1)
        self.assertEqual(skips[0]["outcome"], "skipped")
        self.assertEqual(skips[0]["ai_calls"], 1)

    def test_failing_action_is_logged_and_tick_continues(self):
        atai = self._blog_only("atai")
        momo = self._blog_only("momo")
        self.make_post()

        async def boom(_ctx):
            raise RuntimeError("synthetic failure")

        with patch.dict(wanderer_behavior_service.ACTIONS, {"blog": boom}):
            summary = self.tick(StubCaller(), [atai["id"], momo["id"]])
        self.assertEqual(summary["processed"], 2)
        for wanderer in (atai, momo):
            failed = [row for row in self.actions(wanderer["id"]) if row["outcome"] == "failed"]
            self.assertEqual(len(failed), 1)
            self.assertIn("synthetic failure", failed[0]["detail_json"])


class TickGuardTest(WandererWorldTestCase):
    def test_tick_wall_clock_limit_returns_timeout_with_partial_progress(self):
        atai = self.set_wanderer("atai", behaviors_json=ONLY_BLOG, activity_level=1)
        momo = self.set_wanderer("momo", behaviors_json=ONLY_BLOG, activity_level=1)
        calls = []

        async def slow(ctx):
            calls.append(ctx.wanderer_id)
            if len(calls) > 1:
                await asyncio.sleep(5)
            return []

        with patch.dict(wanderer_behavior_service.ACTIONS, {"blog": slow}),                 patch.object(wanderer_behavior_service, "tick_max_seconds", lambda _conn: 0.5):
            summary = self.tick(StubCaller(), [atai["id"], momo["id"]])
        self.assertEqual(summary["status"], "timeout")
        self.assertEqual(summary["processed"], 1)
        self.assertEqual(len(calls), 2)

    def test_wanderer_paused_mid_tick_stops_before_next_action(self):
        atai = self.set_wanderer("atai", behaviors_json=ONLY_BLOG, activity_level=5)
        calls = []

        async def pause_self(ctx):
            calls.append(1)
            with ctx.connect() as conn:
                conn.execute("UPDATE wanderer_agents SET status = 'paused' WHERE id = ?", (ctx.wanderer_id,))
                conn.commit()
            return []

        with patch.dict(wanderer_behavior_service.ACTIONS, {"blog": pause_self}),                 patch.object(wanderer_behavior_service, "actions_per_tick", lambda _level, _rng: 3):
            summary = self.tick(StubCaller(), [atai["id"]])
        self.assertEqual(calls, [1])
        self.assertTrue(summary["wanderers"][0]["stopped"])

    def test_behavior_change_mid_tick_stops_the_wanderer(self):
        atai = self.set_wanderer("atai", behaviors_json=ONLY_BLOG, activity_level=5)
        calls = []

        async def change_behaviors(ctx):
            calls.append(1)
            with ctx.connect() as conn:
                conn.execute("UPDATE wanderer_agents SET behaviors_json = ? WHERE id = ?",
                             ('{"blog_comment": 0}', ctx.wanderer_id))
                conn.commit()
            return []

        with patch.dict(wanderer_behavior_service.ACTIONS, {"blog": change_behaviors}),                 patch.object(wanderer_behavior_service, "actions_per_tick", lambda _level, _rng: 3):
            self.tick(StubCaller(), [atai["id"]])
        self.assertEqual(calls, [1])


class PendingReplyTest(WandererWorldTestCase):
    def _setup_thread(self, slug, policy):
        wanderer = self.set_wanderer(slug, behaviors_json=ONLY_BLOG, activity_level=1, reply_policy_json=policy)
        post_id = self.make_post(title="一起聊聊二次元", content="最近在看什么番？")
        with self.connect() as conn:
            mine = blog_service.add_comment(conn, build_wanderer_user_dict(wanderer), post_id, content_md="我在补老番～")
            reply = blog_service.add_comment(conn, self.student, post_id, content_md="哪部老番？推荐一下",
                                             parent_comment_id=int(mine["id"]), notify_callback=notify_new_comment)
            conn.commit()
        return wanderer, post_id, int(reply["id"])

    def _pending(self, wanderer):
        return self.rows("SELECT * FROM message_center_notifications WHERE recipient_identity = ? AND read_at IS NULL",
                         (f"student:{wanderer['student_id']}",))

    def test_multi_part_reply_is_posted_in_order_and_marks_read(self):
        policy = {"ignore_prob": 0, "emoji_only_prob": 0, "multi_part_prob": 1, "max_parts": 3}
        momo, _post_id, reply_id = self._setup_thread("momo", policy)
        self.assertEqual(len(self._pending(momo)), 1)
        sleep = ZeroSleep()
        caller = StubCaller(turn("multi", ["《星之梦》！", "超级好哭", "快去看～"]))
        self.tick(caller, [momo["id"]], sleep=sleep)
        rows = self.rows("SELECT content_md FROM blog_comments WHERE author_identity = ? "
                         "AND parent_comment_id = ? ORDER BY id", (f"student:{momo['student_id']}", reply_id))
        self.assertEqual([row["content_md"] for row in rows], ["《星之梦》！", "超级好哭", "快去看～"])
        self.assertGreaterEqual(len(sleep.calls), 2)
        self.assertTrue(all(1.0 <= value <= 4.0 for value in sleep.calls))
        self.assertEqual(self._pending(momo), [])
        self.assertEqual(caller.labels[0], "wanderer:momo:blog_reply")
        # The real student hears back.
        notes = self.rows("SELECT * FROM message_center_notifications WHERE recipient_identity = ?",
                          (f"student:{self.student_pk}",))
        self.assertTrue(notes)

    def test_ignore_policy_marks_read_and_logs_skip_without_ai(self):
        policy = {"ignore_prob": 1, "emoji_only_prob": 0, "multi_part_prob": 0, "max_parts": 2}
        momo, _post_id, _reply_id = self._setup_thread("momo", policy)
        caller = StubCaller(turn("skip", []))
        with patch.dict(wanderer_behavior_service.ACTIONS, {"blog": _noop}):
            self.tick(caller, [momo["id"]])
        self.assertEqual(caller.calls, [])
        self.assertEqual(self._pending(momo), [])
        skips = [row for row in self.actions(momo["id"]) if row["action_kind"] == "skip"]
        self.assertTrue(any("ignore" in row["detail_json"] for row in skips))

    def test_emoji_only_policy_needs_no_ai(self):
        policy = {"ignore_prob": 0, "emoji_only_prob": 1, "multi_part_prob": 0, "max_parts": 2}
        momo, _post_id, reply_id = self._setup_thread("momo", policy)
        caller = StubCaller()
        with patch.dict(wanderer_behavior_service.ACTIONS, {"blog": _noop}):
            self.tick(caller, [momo["id"]])
        self.assertEqual(caller.calls, [])
        rows = self.rows("SELECT content_md FROM blog_comments WHERE parent_comment_id = ?", (reply_id,))
        self.assertEqual(len(rows), 1)
        self.assertLessEqual(len(rows[0]["content_md"]), 2)

    def test_dm_reply_to_real_student(self):
        momo = self.set_wanderer("momo", behaviors_json=ONLY_BLOG, activity_level=1,
                                 reply_policy_json={"ignore_prob": 0, "emoji_only_prob": 0, "multi_part_prob": 0})
        wanderer_identity = f"student:{momo['student_id']}"
        with self.connect() as conn:
            message_center_service.create_private_message(conn, self.student, contact_identity=wanderer_identity,
                                                          content="你好，散修也会看番吗？")
            conn.commit()
        emailed = self.rows("SELECT email_status FROM message_center_notifications WHERE recipient_identity = ?",
                            (wanderer_identity,))
        self.assertTrue(emailed)
        self.assertTrue(all(row["email_status"] != "queued" for row in emailed))
        caller = StubCaller(turn("reply", ["当然看！最近在追新番～"]))
        from classroom_app.services.wanderer_inbox_worker import process_once
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_social_inbox SET run_at='2000-01-01'")
            conn.commit()
        asyncio.run(process_once(factory=self.connect, ai_caller=caller, sleep=ZeroSleep()))
        replies = self.rows("SELECT * FROM private_messages WHERE sender_identity = ?", (wanderer_identity,))
        self.assertEqual([row["content"] for row in replies], ["当然看！最近在追新番～"])
        self.assertEqual(replies[0]["recipient_identity"], f"student:{self.student_pk}")
        unread = self.rows("SELECT * FROM private_messages WHERE recipient_identity = ? AND read_at IS NULL",
                           (wanderer_identity,))
        self.assertEqual(unread, [])
        self.assertEqual(caller.labels, ["wanderer:momo:dm_inbox"])
        self.assertIn("dm_reply", [row["action_kind"] for row in self.actions(momo["id"])])
        # The conversation stays open both ways (no read-only "历史联系人").
        with self.connect() as conn:
            message_center_service.create_private_message(conn, self.student, contact_identity=wanderer_identity,
                                                          content="推荐一部？")
            conn.commit()


if __name__ == "__main__":
    unittest.main()
