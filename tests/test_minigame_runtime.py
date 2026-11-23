"""Isolated service tests, with synthetic actors and a controllable server clock."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import TestCase
from unittest.mock import patch
import json
import uuid

from fastapi import HTTPException

from classroom_app import config
from classroom_app.services import minigame_platform as platform
from classroom_app.services import minigame_runtime as rt
from classroom_app.services import minigame_runtime_rooms as rooms
from classroom_app.services import minigame_runtime_rules as rules


class GameRuntimeTest(TestCase):
    def prepare_database(self):
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.object(config, "DB_PATH", Path(directory) / "game.sqlite"))
        self.enterContext(patch.object(config, "DB_ENGINE", "sqlite"))

    def setUp(self):
        self.prepare_database()
        self.time = 1000000
        self.enterContext(patch.object(platform, "now_ms", lambda: self.time))
        self.enterContext(patch.object(platform, "assert_session", lambda c, u: None))
        self.enterContext(patch.object(platform, "load_actor", lambda c, a: self.user(a)))
        self.enterContext(patch.object(platform, "require_play", self.play))
        self.games = {}
        self.disabled = set()
        example = Path(__file__).resolve().parents[1] / "docs/minigames/example"
        read = lambda file: json.loads((example / file).read_text(encoding="utf-8"))
        for gid in ("a", "b", "c", "d", "e", "f"):
            self.games[gid] = {
                "id": gid,
                "version_id": "v1",
                "config": read("config.json"),
                "rules": read("contracts/rules.json"),
                "writeback": read("contracts/writeback.json"),
            }
        with platform.game_db() as conn:
            rt.ensure_runtime_schema(conn)
            conn.execute(
                "CREATE TABLE user_sessions(session_user_key TEXT PRIMARY KEY,session_id TEXT,expires_at TEXT)"
            )
            for role in ("student", "teacher"):
                for i in range(1, 12):
                    actor = f"{role}:{i}"
                    conn.execute(
                        "INSERT INTO user_sessions VALUES(?,?,?)", (actor, "session-" + actor, "2099-01-01")
                    )
            for table in ("private_message_blocks", "social_blocks"):
                conn.execute(f"CREATE TABLE {table} (owner_identity TEXT,blocked_identity TEXT)")
            conn.commit()
        self.addCleanup(rt._mail.clear)

    def user(self, actor):
        return {
            "actor": actor,
            "id": int(actor.split(":")[1]),
            "role": actor.split(":")[0],
            "display_name": "Player-" + actor,
            "session_id": "session-" + actor,
        }

    def play(self, c, user, g, v=None, *, definition=True):
        if g not in self.games or (user["actor"], g) in self.disabled:
            raise HTTPException(404)
        return self.games[g]

    def write(self, actor, op, kind, target, body=None, key=None):
        return rt.write(
            self.user(actor),
            op,
            kind,
            target,
            {"hostInstance": "tab-" + actor, **(body or {})},
            key or uuid.uuid4().hex,
            "192.0.2.1",
        )

    def db(self, sql, args=()):
        with platform.game_db() as c:
            return [dict(x) for x in c.execute(sql, args)]

    def must_fail(self, code, call, *args):
        with self.assertRaises(HTTPException) as cm:
            call(*args)
        self.assertEqual(cm.exception.detail, {"code": code})

    def test_concurrent_last_seat_and_replay(self):
        a = "student:1"
        room = self.write(a, "create", "game", "a", {"targetSize": 2}, "create")
        self.assertEqual(
            self.write(a, "create", "game", "a", {"targetSize": 2}, "create")["roomId"], room["roomId"]
        )
        barrier = Barrier(2)

        def join(who):
            barrier.wait(3)
            try:
                return self.write(who, "join", "game", "a", {"code": room["code"].lower()})
            except HTTPException as exc:
                return exc.detail

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(join, ("student:2", "student:3")))
        accepted = [v for v in results if v.get("phase") == "countdown"]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(sum(x.get("code") == "ROOM_UNAVAILABLE" for x in results), 1)
        self.assertEqual(accepted[0]["startsAt"], self.time + 5000)
        self.time += 5001
        rt.maintenance(self.time)
        rt.maintenance(self.time)
        query = {"roomId": room["roomId"], "hostInstance": "tab-" + a}
        snap = rt.poll(self.user(a), query, False)["snapshot"]
        self.assertEqual(snap["phase"], "running")
        self.assertEqual(len(self.db("SELECT * FROM mg_rt_events WHERE kind='run.started'")), 1)
        args = {
            "expectedRevision": snap["revision"],
            "controlEpoch": room["controlEpoch"],
            "intent": {"type": "light", "payload": {"bearing": "east"}},
        }
        first = self.write(a, "intent", "run", snap["runId"], args, "move")
        again = self.write(a, "intent", "run", snap["runId"], args, "move")
        self.assertEqual(first["revision"], again["revision"])
        self.assertEqual(first["publicState"]["rank"], 1)
        self.assertEqual(len(self.db("SELECT * FROM mg_rt_claims")), 2)
        self.must_fail(
            "IDEMPOTENCY_MISMATCH",
            self.write,
            a,
            "intent",
            "run",
            snap["runId"],
            {**args, "expectedRevision": 88},
            "move",
        )

    def test_progress_rollback_and_replay(self):
        actors = ["student:1", "teacher:1", "student:2"]
        with platform.game_db() as c:
            for i, a in enumerate(actors):
                rt.advance_progress(c, self.games["a"], a, i, "seed", self.time)
                rt.save_private(
                    c, self.games["a"], a, {"mapStyle": "paper", "sound": bool(i), "mapPins": []}, 0
                )
            c.commit()
        room = self.write(actors[0], "create", "game", "a", {"targetSize": 3})
        for a in actors[1:]:
            self.write(a, "join", "game", "a", {"code": room["code"]})
        self.time += 5100
        before = self.db("SELECT * FROM mg_rt_progress ORDER BY actor")
        original = rooms.advance_progress
        count = 0

        def interrupt(*args):
            nonlocal count
            original(*args)
            count += 1
            if count == 2:
                raise ValueError("synthetic fault")

        with patch.object(rooms, "advance_progress", interrupt), self.assertRaises(ValueError):
            rt.maintenance(self.time)
        self.assertEqual(self.db("SELECT * FROM mg_rt_progress ORDER BY actor"), before)
        self.assertIsNone(self.db("SELECT run FROM mg_rt_activity")[0]["run"])
        rt.maintenance(self.time)
        after = self.db("SELECT * FROM mg_rt_progress ORDER BY actor")
        self.assertEqual([x["rank"] for x in after], [2, 2, 2])
        self.assertEqual(
            [(x["priv"], x["private_rev"]) for x in before], [(x["priv"], x["private_rev"]) for x in after]
        )

    def test_shared_progress_locks_roster_in_actor_order_and_retries(self):
        owner, partner = "teacher:2", "student:1"
        room = self.write(owner, "create", "game", "a", {"targetSize": 2})
        self.write(partner, "join", "game", "a", {"code": room["code"]})
        self.time += 5100
        with patch.object(rooms, "advance_progress", wraps=rooms.advance_progress) as started:
            rt.maintenance(self.time)
        self.assertEqual([call.args[2] for call in started.call_args_list], [partner, owner])
        run = rt.poll(self.user(owner), {"roomId": room["roomId"]}, False)["snapshot"]
        intent = {
            "controlEpoch": run["controlEpoch"],
            "expectedRevision": run["revision"],
            "intent": {"type": "light", "payload": {"bearing": "east"}},
        }
        # A storage timeout must roll back the move and leave its original command key reusable.
        from classroom_app.db.connection import get_db_connection

        with get_db_connection() as blocker:
            if config.DB_ENGINE == "postgres":
                blocker.execute(
                    "SELECT actor FROM mg_rt_progress WHERE game=? AND actor=? FOR UPDATE",
                    ("a", partner),
                )
            else:
                blocker.execute("BEGIN IMMEDIATE")
            with self.assertRaises(HTTPException) as busy:
                self.write(owner, "intent", "run", run["runId"], intent, "repeated-step")
            self.assertEqual(busy.exception.status_code, 503)
        self.assertFalse(self.db("SELECT id FROM mg_rt_changes WHERE run=?", (run["runId"],)))
        self.assertFalse(self.db("SELECT k FROM mg_rt_receipts WHERE k='repeated-step'"))
        with patch.object(rt, "advance_progress", wraps=rt.advance_progress) as moved:
            self.write(owner, "intent", "run", run["runId"], intent, "repeated-step")
            self.write(owner, "intent", "run", run["runId"], intent, "repeated-step")
        self.assertEqual([call.args[2] for call in moved.call_args_list], [partner, owner])
        self.assertEqual(
            self.db(
                "SELECT actor,before_rank,after_rank,revision FROM mg_rt_changes WHERE run=? ORDER BY actor",
                (run["runId"],),
            ),
            [{"actor": a, "before_rank": 0, "after_rank": 1, "revision": 1} for a in (partner, owner)],
        )

    def test_atomic_switch_and_epoch(self):
        me = "student:1"
        solo = self.write(me, "solo", "game", "a")
        old = {
            "runId": solo["runId"],
            "controlEpoch": solo["controlEpoch"],
            "expectedPrivateRevision": 0,
            "pendingPrivateSave": {"sound": False, "mapStyle": "night", "mapPins": []},
        }
        self.must_fail(
            "INVALID_SIZE", self.write, me, "create", "game", "a", {"targetSize": 90, "fromSolo": old}
        )
        other = self.write(me, "create", "game", "b", {"targetSize": 2})
        self.must_fail(
            "MULTIPLAYER_ACTIVITY_EXISTS",
            self.write,
            me,
            "create",
            "game",
            "a",
            {"targetSize": 2, "fromSolo": old},
        )
        self.assertEqual(rt.read(self.user(me), "progress", "a", {})["privateRevision"], 0)
        self.assertEqual(
            self.db("SELECT phase FROM mg_rt_activity WHERE id=?", (solo["runId"],))[0]["phase"], "running"
        )
        self.write(me, "leave", "room", other["roomId"], {"controlEpoch": other["controlEpoch"]})
        private = self.write(me, "create", "game", "a", {"targetSize": 2, "fromSolo": old})
        self.assertEqual(rt.read(self.user(me), "progress", "a", {})["privateSave"]["mapStyle"], "night")
        ctx = {
            "roomId": private["roomId"],
            "controlEpoch": private["controlEpoch"],
            "hostInstance": "tab-" + me,
        }
        rt.poll(self.user(me), ctx, True)
        self.write(me, "takeover", "game", "a", {**ctx, "hostInstance": "new-tab"})
        self.must_fail("CONTROL_MOVED", rt.poll, self.user(me), ctx, True)
        self.time += 2000
        rt.maintenance(self.time, True)
        self.assertEqual(
            self.db("SELECT host,epoch FROM mg_rt_claims WHERE actor=?", (me,))[0],
            {"host": "new-tab", "epoch": private["controlEpoch"] + 1},
        )

    def test_random_and_cancel_during_countdown(self):
        a, b, c = "student:1", "student:2", "student:3"
        with platform.game_db() as con:
            con.execute("INSERT INTO social_blocks VALUES(?,?)", (a, b))
            con.commit()
        queued = self.write(a, "queue", "game", "a", {"targetSize": 2})
        blocked = self.write(b, "queue", "game", "a", {"targetSize": 2})
        self.assertEqual(blocked["phase"], "waiting")
        first = self.write(c, "queue", "game", "a", {"targetSize": 2})
        self.assertEqual(first["phase"], "countdown")
        self.write(c, "cancel", "ticket", first["ticketId"], {"controlEpoch": first["controlEpoch"]}, "cancel")
        self.assertEqual(
            self.db("SELECT phase,queued FROM mg_rt_tickets WHERE id=?", (queued["ticketId"],))[0],
            {"phase": "waiting", "queued": self.time},
        )
        remaining = rt.read(self.user(a), "activity", None, {"gameId": "a", "hostInstance": "tab-" + a})
        self.assertEqual(remaining["multiplayer"]["ticketId"], queued["ticketId"])
        self.assertFalse(remaining["multiplayer"]["readOnly"])
        self.assertIsNone(rt.read(self.user(c), "activity", None, {"gameId": "a"})["multiplayer"])
        with platform.game_db() as conn:
            rt.prune_expired(conn, self.time + 86400001)
            conn.commit()
        self.assertFalse(self.db("SELECT k FROM mg_rt_receipts WHERE k='cancel'"))
        self.assertTrue(self.db("SELECT k FROM mg_rt_receipts WHERE scope_ref=?", (queued["ticketId"],)))

    def test_background_match_releases_ineligible_player_and_starts_remaining_pair(self):
        a, b, c = "student:1", "student:2", "student:3"
        with platform.game_db() as conn:
            conn.executemany("INSERT INTO social_blocks VALUES(?,?)", [(a, b), (a, c), (b, c)])
            conn.commit()
        for actor in (a, b, c):
            queued = self.write(actor, "queue", "game", "a", {"targetSize": 2})
            self.assertEqual(queued["phase"], "waiting")
        self.disabled.add((a, "a"))
        with platform.game_db() as conn:
            conn.execute("DELETE FROM social_blocks WHERE owner_identity=? AND blocked_identity=?", (b, c))
            conn.commit()

        rt.maintenance(self.time, flush=True)
        phases = self.db("SELECT actor,phase FROM mg_rt_tickets ORDER BY actor")
        self.assertEqual([row["phase"] for row in phases], ["expired", "assigned", "assigned"])
        self.assertEqual(self.db("SELECT actor FROM mg_rt_seats ORDER BY actor"), [{"actor": b}, {"actor": c}])
        self.time += 5000
        rt.maintenance(self.time)
        self.assertEqual(self.db("SELECT phase FROM mg_rt_activity"), [{"phase": "running"}])

    def test_lru_partial_progress_and_cleanup(self):
        a = "student:1"
        for g in "abcdef":
            self.time += 10
            self.write(a, "solo", "game", g)
        self.assertEqual(len(self.db("SELECT actor FROM mg_rt_claims")), 5)
        self.assertEqual(self.db("SELECT phase FROM mg_rt_activity WHERE game='a'")[0]["phase"], "paused")
        with platform.game_db() as c:
            rt.on_unlist(c, "b")
            rt.purge_game(c, "b")
            c.commit()
        self.assertFalse(self.db("SELECT game FROM mg_rt_activity WHERE game='b'"))
        self.assertEqual(len(self.db("SELECT game FROM mg_rt_activity")), 5)

    def test_grid_and_document_limits(self):
        game = self.games["a"]
        game["rules"] = {"engine": "grid-line-v1", "size": 3, "line": 3}
        s = rules.initial_state(game, 0)
        for i, (x, y) in enumerate([(0, 0), (0, 1), (1, 0), (1, 1), (2, 0)]):
            outcome = rules.apply_intent(
                game, s, i % 2 + 1, 2, {"type": "place", "payload": {"x": x, "y": y}}
            )
        self.assertEqual(outcome, "actorWins")
        self.assertEqual(s["winnerSeat"], 1)
        shape = {"type": "array", "items": {"type": "enum", "values": ["blue", "white"]}, "maxItems": 3}
        self.assertTrue(rules.matches_shape(["blue"], shape))
        self.assertFalse(rules.matches_shape([False], shape))
        self.assertFalse(rules.matches_shape([1, 2, 3, 4], shape))

    def test_private_replay_and_chat_respect_new_block(self):
        a, b = "student:1", "student:2"
        initial = self.write(a, "create", "game", "a", {"targetSize": 2})
        args = {"code": initial["code"]}
        joined = self.write(b, "join", "game", "a", args, "join")
        rid = initial["roomId"]
        self.write(b, "chat", "room", rid, {"controlEpoch": joined["controlEpoch"], "text": "a message"})
        self.time += 5001
        rt.maintenance(self.time)
        before = rt.poll(self.user(a), {"roomId": rid}, False)
        self.assertEqual(before["messages"][0]["text"], "a message")
        with platform.game_db() as conn:
            conn.execute("INSERT INTO private_message_blocks VALUES(?,?)", (a, b))
            conn.commit()
        filtered = rt.poll(self.user(a), {"roomId": rid}, False)
        self.assertEqual(filtered["messages"], [])
        self.assertEqual(len(filtered["snapshot"]["members"]), 1)
        replay = self.write(b, "join", "game", "a", args, "join")
        self.assertEqual(len(replay["members"]), 1)
        snap = filtered["snapshot"]
        interrupted = self.write(
            a,
            "intent",
            "run",
            snap["runId"],
            {
                "expectedRevision": snap["revision"],
                "controlEpoch": initial["controlEpoch"],
                "intent": {"type": "light", "payload": {"bearing": "east"}},
            },
        )
        self.assertEqual(interrupted["outcome"], "eligibility_changed")
        self.assertFalse(self.db("SELECT actor FROM mg_rt_claims"))

    def test_countdown_logout_preserves_saves_and_private_room_can_refill(self):
        owner, guest, replacement = "student:1", "student:2", "student:3"
        with platform.game_db() as conn:
            rt.advance_progress(conn, self.games["a"], owner, 2, "seed", self.time)
            conn.commit()
        room = self.write(owner, "create", "game", "a", {"targetSize": 2})
        self.write(guest, "join", "game", "a", {"code": room["code"]})
        before = self.db("SELECT * FROM mg_rt_progress ORDER BY actor")
        with platform.game_db() as conn:
            conn.execute("DELETE FROM user_sessions WHERE session_user_key=?", (guest,))
            conn.commit()
        self.time += 5000
        rt.maintenance(self.time)
        self.assertEqual(self.db("SELECT phase,run FROM mg_rt_activity"), [{"phase": "waiting", "run": None}])
        self.assertEqual(self.db("SELECT actor FROM mg_rt_seats"), [{"actor": owner}])
        self.assertEqual(self.db("SELECT * FROM mg_rt_progress ORDER BY actor"), before)
        self.assertFalse(self.db("SELECT id FROM mg_rt_changes WHERE actor=?", (guest,)))
        waiting = rt.poll(self.user(owner), {"roomId": room["roomId"], "hostInstance": "tab-" + owner}, False)["snapshot"]
        self.write(replacement, "join", "game", "a", {"code": waiting["code"]})
        self.time += 5000
        rt.maintenance(self.time)
        self.assertEqual(self.db("SELECT actor,rank FROM mg_rt_progress ORDER BY actor"),
                         [{"actor": owner, "rank": 2}, {"actor": replacement, "rank": 2}])

    def test_rotated_teammate_session_blocks_shared_move(self):
        a, b = "student:1", "student:2"
        room = self.write(a, "create", "game", "a", {"targetSize": 2})
        self.write(b, "join", "game", "a", {"code": room["code"]})
        self.time += 5000
        rt.maintenance(self.time)
        run = rt.poll(self.user(a), {"roomId": room["roomId"]}, False)["snapshot"]
        with platform.game_db() as conn:
            conn.execute("UPDATE user_sessions SET session_id='replaced' WHERE session_user_key=?", (b,))
            conn.commit()
        result = self.write(a, "intent", "run", run["runId"], {
            "controlEpoch": room["controlEpoch"], "expectedRevision": run["revision"],
            "intent": {"type": "light", "payload": {"bearing": "east"}},
        })
        self.assertEqual((result["phase"], result["outcome"]), ("interrupted", "eligibility_changed"))
        self.assertFalse(self.db("SELECT id FROM mg_rt_changes WHERE run=?", (run["runId"],)))

    def test_expired_session_ticket_releases_its_place(self):
        gone, a, b = "student:1", "student:2", "student:3"
        self.write(gone, "queue", "game", "a", {"targetSize": 2})
        with platform.game_db() as conn:
            conn.execute("UPDATE user_sessions SET expires_at='1970-01-01' WHERE session_user_key=?", (gone,))
            conn.commit()
        self.assertEqual(self.write(a, "queue", "game", "a", {"targetSize": 2})["phase"], "waiting")
        self.assertEqual(self.write(b, "queue", "game", "a", {"targetSize": 2})["phase"], "countdown")
        self.assertEqual(self.db("SELECT phase FROM mg_rt_tickets WHERE actor=?", (gone,)), [{"phase": "expired"}])
        self.assertEqual(self.db("SELECT actor FROM mg_rt_seats ORDER BY actor"), [{"actor": a}, {"actor": b}])

    def test_logout_during_flush_and_receipt_retention(self):
        me = "student:1"
        opened = self.write(me, "solo", "game", "a", key="open")
        self.time += 1000
        rt.poll(
            self.user(me),
            {"runId": opened["runId"], "hostInstance": "tab-" + me, "controlEpoch": opened["controlEpoch"]},
            True,
        )
        before = self.db("SELECT expires FROM mg_rt_claims")
        with platform.game_db() as c:
            c.execute("UPDATE user_sessions SET session_id='replacement' WHERE session_user_key=?", (me,))
            c.commit()
        rt.maintenance(self.time, True)
        self.assertEqual(self.db("SELECT expires FROM mg_rt_claims"), before)
        with platform.game_db() as c:
            rt.prune_expired(c, self.time + 86400001)
            c.commit()
        self.assertTrue(self.db("SELECT k FROM mg_rt_receipts WHERE k='open'"))
        self.write(me, "leave", "run", opened["runId"], {"controlEpoch": opened["controlEpoch"]})
        with platform.game_db() as c:
            rt.prune_expired(c, self.time + 86400001)
            c.commit()
        self.assertFalse(self.db("SELECT k FROM mg_rt_receipts"))

    def test_replay_at_lower_rank_and_private_save_rollback(self):
        a, b, c = "student:1", "student:2", "student:3"
        with platform.game_db() as conn:
            rt.advance_progress(conn, self.games["a"], a, 2, "seed", self.time)
            rt.advance_progress(conn, self.games["a"], b, 2, "seed", self.time)
            conn.commit()
        solo = self.write(a, "solo", "game", "a")
        form = {
            "startRank": 1,
            "privateSave": {"mapStyle": "paper", "sound": False, "mapPins": [3]},
            "controlEpoch": solo["controlEpoch"],
            "expectedPrivateRevision": 9,
        }
        self.must_fail("STALE_REVISION", self.write, a, "solo", "game", "a", form)
        self.assertIsNone(
            self.db("SELECT ended FROM mg_rt_activity WHERE id=?", (solo["runId"],))[0]["ended"]
        )
        self.write(a, "leave", "run", solo["runId"], {"controlEpoch": solo["controlEpoch"]})
        room = self.write(a, "create", "game", "a", {"targetSize": 3, "startRank": 1})
        for actor in (b, c):
            full = self.write(actor, "join", "game", "a", {"code": room["code"]})
        self.assertEqual(full["startRank"], 1)
        self.time += 5000
        rt.maintenance(self.time)
        started = rt.poll(self.user(a), {"roomId": room["roomId"]}, False)["snapshot"]
        self.assertEqual(started["publicState"]["rank"], 1)
        self.assertEqual(
            [r["rank"] for r in self.db("SELECT rank FROM mg_rt_progress ORDER BY actor")], [2, 2, 1]
        )

    def test_long_poll_coalesces_shared_rooms(self):
        import asyncio
        from classroom_app.routers.minigame_runtime import StreamWaiters

        async def scenario():
            collector = StreamWaiters()
            calls = []

            async def check(fn, keys):
                calls.append(tuple(keys))
                return {key: 42 for key in keys}

            keys = [("activity", str(i % 4)) for i in range(40)]
            with patch.object(platform, "run_db", check):
                await asyncio.gather(*(collector.changed(k, 41, 0.5) for k in keys))
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(calls[0]), 4)
                self.assertFalse(collector.streams)
                collector.task.cancel()
                await asyncio.gather(collector.task, return_exceptions=True)

        asyncio.run(scenario())

    def test_two_hundred_random_players_blocked_pairs_sizes_and_release(self):
        game = self.games["a"]
        game["config"]["modes"]["multiplayer"]["randomSizes"] = [2, 4]
        with platform.game_db() as conn:
            conn.executemany("INSERT INTO user_sessions VALUES(?,?,?)",
                             [(f"student:{i}", f"session-student:{i}", "2099-01-01") for i in range(12, 204)])
            banned = {(f"student:{i}", f"student:{i + 1}") for i in range(1, 200, 8)}
            conn.executemany("INSERT INTO social_blocks VALUES(?,?)", sorted(banned))
            conn.commit()
        for n in range(1, 201):
            self.time += 1
            self.write(
                f"student:{n}", "queue", "game", "a", {"targetSize": 2 if n % 8 in (1, 2, 3, 4) else 4}
            )
        active = self.db("""SELECT a.id,a.version,a.capacity,s.actor FROM mg_rt_activity a
            JOIN mg_rt_seats s ON s.activity=a.id WHERE a.phase='countdown'""")
        groups = {}
        for row in active:
            groups.setdefault((row["id"], row["capacity"]), set()).add(row["actor"])
            self.assertEqual(row["version"], "v1")
        self.assertGreaterEqual(len(active), 196)
        for (_, size), actors in groups.items():
            self.assertEqual(len(actors), size)
            self.assertFalse(any(a in actors and b in actors for a, b in banned))
        self.time += 5000
        rt.maintenance(self.time)
        rt.maintenance(self.time)
        old_runs = len(self.db("SELECT id FROM mg_rt_activity WHERE phase='running'"))
        self.assertEqual(old_runs, len(groups))
        waiting = self.write("student:201", "queue", "game", "a", {"targetSize": 4})
        self.assertEqual(waiting["phase"], "waiting")
        with platform.game_db() as conn:
            rt.on_publish(conn, "a", "v2")
            conn.commit()
        self.assertEqual(
            self.db("SELECT phase FROM mg_rt_tickets WHERE id=?", (waiting["ticketId"],))[0]["phase"],
            "canceled",
        )
        self.assertEqual(len(self.db("SELECT id FROM mg_rt_activity WHERE phase='running'")), old_runs)
        game["version_id"] = "v2"
        for actor in ("student:202", "student:203"):
            new = self.write(actor, "queue", "game", "a", {"targetSize": 2})
        self.assertEqual(new["phase"], "countdown")
        self.assertEqual(new["versionId"], "v2")

    def test_idle_coordinator_rechecks_before_five_second_deadline(self):
        self.assertEqual(rt.maintenance(self.time), self.time + 4000)
        a, b = "student:1", "student:2"
        with patch.object(rt, "wake_scheduler") as wake:
            room = self.write(a, "create", "game", "a", {"targetSize": 2})
            full = self.write(b, "join", "game", "a", {"code": room["code"]})
            self.assertEqual(wake.call_count, 2)
        self.time += 4000
        self.assertEqual(rt.maintenance(self.time), full["startsAt"])
        self.time = full["startsAt"]
        rt.maintenance(self.time)
        self.assertEqual(self.db("SELECT started FROM mg_rt_activity")[0]["started"], self.time)

    def test_new_release_switch_flushes_the_running_versions_private_fields(self):
        from copy import deepcopy

        actor = "student:1"
        solo = self.write(actor, "solo", "game", "a")
        old = deepcopy(self.games["a"])
        current = deepcopy(old)
        current["version_id"] = "v2"
        current["config"]["modes"]["solo"] = False
        current["writeback"]["privateFields"]["captions"] = {"type": "boolean", "default": True}

        def release(conn, user, game, version=None, **options):
            self.assertIn(version, (None, "v1", "v2"))
            return old if version == "v1" else current

        with patch.object(platform, "require_play", release):
            reopened = self.write(actor, "solo", "game", "a")
            self.assertEqual(reopened["runId"], solo["runId"])
            room = self.write(
                actor,
                "create",
                "game",
                "a",
                {
                    "targetSize": 2,
                    "fromSolo": {
                        "runId": solo["runId"],
                        "controlEpoch": solo["controlEpoch"],
                        "expectedPrivateRevision": 0,
                        "pendingPrivateSave": {"mapStyle": "night", "sound": False, "mapPins": [4]},
                    },
                },
            )
            self.assertEqual(room["versionId"], "v2")
            data = rt.read(self.user(actor), "progress", "a", {})
            self.assertEqual(
                data["privateSave"], {"mapStyle": "night", "sound": False, "mapPins": [4], "captions": True}
            )
