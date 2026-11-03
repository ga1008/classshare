import asyncio
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.routers import wanderer_internal
from classroom_app.services import chat_handler, wanderer_actions_classroom
from classroom_app.services.wanderer_actions_classroom import fetch_online_counts, session_state
from classroom_app.services.wanderer_identity_service import log_wanderer_action
from classroom_app.services.wanderer_settings_service import INTERNAL_TOKEN_HEADER, wanderer_internal_token
from tests.wanderer_behavior_support import ONLY_CLASSROOM, StubCaller, WandererWorldTestCase, turn


class ClassroomVisitTest(WandererWorldTestCase):
    def setUp(self):
        super().setUp()
        self.online = AsyncMock(return_value={self.offering_id: 7})
        self.broadcast = AsyncMock(return_value=True)
        for name, mock in (("fetch_online_counts", self.online), ("broadcast_classroom_message", self.broadcast)):
            patcher = patch.object(wanderer_actions_classroom, name, mock)
            patcher.start()
            self.addCleanup(patcher.stop)
        speak = patch.object(wanderer_actions_classroom, "SPEAK_PROBABILITY_IDLE", 1.0)
        speak.start()
        self.addCleanup(speak.stop)
        self.chen = self.set_wanderer("chenmo", behaviors_json=ONLY_CLASSROOM, activity_level=1)

    def _chat_rows(self):
        return self.rows("SELECT * FROM chat_logs WHERE class_offering_id = ? ORDER BY id", (self.offering_id,))

    def _mine(self):
        return [row for row in self._chat_rows() if row["user_id"] == str(self.chen["student_id"])]

    def test_message_is_saved_with_the_wanderer_identity_and_broadcast(self):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO chat_logs (class_offering_id, user_id, user_name, user_role, message, timestamp, logged_at) "
                "VALUES (?, ?, ?, 'student', ?, '20:00', ?)",
                (self.offering_id, str(self.student_pk), "真实同学", "列表推导式怎么写？", datetime.now().isoformat()),
            )
            conn.commit()
        caller = StubCaller(turn("reply", ["[x*x for x in data]"]))
        self.tick(caller, [self.chen["id"]])
        rows = self._mine()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_role"], "student")
        self.assertEqual(rows[0]["user_name"], "陈默")
        self.assertEqual(rows[0]["message"], "[x*x for x in data]")
        self.assertTrue(rows[0]["logged_at"])
        self.broadcast.assert_awaited_once()
        offering_id, stored = self.broadcast.await_args.args
        self.assertEqual((offering_id, stored["id"]), (self.offering_id, rows[0]["id"]))
        self.assertEqual(caller.labels, ["wanderer:chenmo:classroom"])
        system_prompt = caller.calls[0][1]["system_prompt"]
        self.assertIn("当前在线 7 人", system_prompt)
        self.assertIn("列表推导式怎么写", system_prompt)
        self.assertIn("可以只看不说", system_prompt)
        self.assertIn("不重复别人已经说过的建议", system_prompt)
        kinds = [row["action_kind"] for row in self.actions(self.chen["id"])]
        self.assertEqual(sorted(kinds), ["classroom_message", "classroom_visit"])
        stats = self.rows("SELECT stats_json FROM wanderer_agents WHERE id = ?", (self.chen["id"],))[0]
        self.assertIn(self.offering_id, json.loads(stats["stats_json"])["visited_offerings"])

    def test_duplicate_suggestion_is_not_posted(self):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO chat_logs (class_offering_id, user_id, user_name, user_role, message, timestamp, logged_at) "
                "VALUES (?, ?, ?, 'student', ?, '20:00', ?)",
                (self.offering_id, str(self.student_pk), "真实同学",
                 "别在真页面上试，本地起个环境随便折腾。", datetime.now().isoformat()),
            )
            conn.commit()
        caller = StubCaller(turn("reply", ["别在别人页面上试，自己本地搭个环境最稳。"]))
        self.tick(caller, [self.chen["id"]])
        self.assertEqual(self._mine(), [])
        self.broadcast.assert_not_awaited()
        skips = [row for row in self.actions(self.chen["id"]) if row["action_kind"] == "skip"]
        self.assertEqual(len(skips), 1)
        self.assertEqual(json.loads(skips[0]["detail_json"])["reason"], "echo")
        self.assertEqual(sum(int(row["ai_calls"] or 0) for row in self.actions(self.chen["id"])), 1)

    def test_repeated_own_phrase_is_not_posted(self):
        with self.connect() as conn:
            log_wanderer_action(conn, self.chen["id"], "comment",
                                content_preview="这章我读了，读到一半就饿得不行。")
            conn.commit()
        caller = StubCaller(turn("reply", ["这章我读了，读到一半就饿得不行。"]))
        self.tick(caller, [self.chen["id"]])
        self.assertEqual(self._mine(), [])
        self.broadcast.assert_not_awaited()
        skips = [row for row in self.actions(self.chen["id"]) if row["action_kind"] == "skip"]
        self.assertTrue(skips)
        self.assertEqual(json.loads(skips[-1]["detail_json"])["reason"], "repetition")
        self.assertEqual(sum(int(row["ai_calls"] or 0) for row in self.actions(self.chen["id"])), 1)

    def test_style_guard_removes_teaching_and_advice(self):
        self.tick(StubCaller(turn("reply", ["补充一点：这章我读了。建议你早点睡。"])), [self.chen["id"]])
        self.assertEqual([row["message"] for row in self._mine()], ["这章我读了。"])
        messages = [row for row in self.actions(self.chen["id"]) if row["action_kind"] == "classroom_message"]
        style = json.loads(messages[0]["detail_json"])["style"]
        self.assertIn("teaching", style)
        self.assertIn("advice", style)

    def test_five_minute_throttle(self):
        caller = StubCaller(turn("reply", ["第一句"]), turn("reply", ["第二句"]))
        self.tick(caller, [self.chen["id"]])
        self.tick(caller, [self.chen["id"]], seed=11)
        self.assertEqual([row["message"] for row in self._mine()], ["第一句"])
        self.assertEqual(len(caller.calls), 1)  # the throttled visit never asks the model
        visits = [row for row in self.actions(self.chen["id"]) if row["action_kind"] == "classroom_visit"]
        self.assertEqual(len(visits), 2)

    def test_multi_part_turn_becomes_one_message(self):
        self.tick(StubCaller(turn("multi", ["上课了", "我来旁听"])), [self.chen["id"]])
        self.assertEqual([row["message"] for row in self._mine()], ["上课了 我来旁听"])

    def test_session_in_progress_detection(self):
        now = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO class_offering_sessions (class_offering_id, order_index, title, session_date, weekday, "
                "academic_time_text) VALUES (?, 1, ?, ?, ?, ?)",
                (self.offering_id, "第一讲 列表", now.date().isoformat(), now.weekday(), "09:40-11:20"),
            )
            conn.commit()
            running = session_state(conn, self.offering_id, now)
            later = session_state(conn, self.offering_id, now + timedelta(hours=3))
            conn.execute("DELETE FROM class_offering_sessions WHERE class_offering_id = ?", (self.offering_id,))
            conn.commit()
        self.assertEqual((running["today"], running["in_progress"], running["title"]), (True, True, "第一讲 列表"))
        self.assertEqual((later["today"], later["in_progress"]), (True, False))


class InternalEndpointTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(wanderer_internal.router)
        self.client = TestClient(app)
        self.saved = dict(chat_handler.manager.room_participants)
        chat_handler.manager.room_participants.clear()
        chat_handler.manager.room_participants.update({41: {"student:1": {"c1"}, "student:2": {"c2"}}, 42: {}})

    def tearDown(self):
        chat_handler.manager.room_participants.clear()
        chat_handler.manager.room_participants.update(self.saved)

    def test_rooms_requires_token(self):
        self.assertEqual(self.client.get("/api/internal/wanderers/rooms").status_code, 401)
        bad = self.client.get("/api/internal/wanderers/rooms", headers={INTERNAL_TOKEN_HEADER: "nope"})
        self.assertEqual(bad.status_code, 401)
        good = self.client.get("/api/internal/wanderers/rooms", headers={INTERNAL_TOKEN_HEADER: wanderer_internal_token()})
        self.assertEqual(good.status_code, 200)
        self.assertEqual(good.json()["rooms"], {"41": 2})

    def test_broadcast_requires_token_and_a_stored_message(self):
        body = {"class_offering_id": 41, "message": {"id": 5, "type": "chat", "message": "hi"}}
        self.assertEqual(self.client.post("/api/internal/wanderers/classroom-broadcast", json=body).status_code, 401)
        headers = {INTERNAL_TOKEN_HEADER: wanderer_internal_token()}
        ok = self.client.post("/api/internal/wanderers/classroom-broadcast", json=body, headers=headers)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["recipients"], 0)
        bad = self.client.post("/api/internal/wanderers/classroom-broadcast",
                               json={"class_offering_id": 41, "message": {}}, headers=headers)
        self.assertEqual(bad.status_code, 400)

    def test_explicit_env_token_wins(self):
        with patch.dict("os.environ", {"WANDERER_INTERNAL_TOKEN": "synthetic-token"}):
            self.assertEqual(wanderer_internal_token(), "synthetic-token")
            ok = self.client.get("/api/internal/wanderers/rooms", headers={INTERNAL_TOKEN_HEADER: "synthetic-token"})
        self.assertEqual(ok.status_code, 200)

    def test_unreachable_app_reads_as_nobody_online(self):
        with patch.dict("os.environ", {"MAIN_APP_INTERNAL_URL": "http://127.0.0.1:9"}):
            self.assertEqual(asyncio.run(fetch_online_counts()), {})


if __name__ == "__main__":
    unittest.main()
