"""Sidebar visibility, deterministic ranking and bounded projections on a synthetic catalog."""

import json
import sqlite3
from contextlib import closing
from unittest import TestCase
from unittest.mock import patch

from classroom_app.routers.minigames import sidebar_data
from classroom_app.services import minigame_platform as platform
from classroom_app.services.minigame_packages import ensure_package_schema
from classroom_app.services.minigame_runtime_store import ensure_runtime_schema


class SidebarTests(TestCase):
    def test_sqlite_sidebar(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            self.verify_sidebar(conn)

    def verify_sidebar(self, conn):
        platform.ensure_catalog_schema(conn)
        ensure_package_schema(conn)
        ensure_runtime_schema(conn)
        for sql in (
            "CREATE TABLE students(id INTEGER PRIMARY KEY,class_id INTEGER, school_code TEXT, college TEXT,department TEXT,enrollment_status TEXT,control_mode TEXT)",
            "CREATE TABLE teachers(id INTEGER PRIMARY KEY,is_active INTEGER)",
            "CREATE TABLE class_offerings(id INTEGER PRIMARY KEY,class_id INTEGER)",
            "CREATE TABLE class_offering_class_links(offering_id INTEGER,class_id INTEGER)",
            "CREATE TABLE learning_certificates(id INTEGER PRIMARY KEY,student_id INTEGER,class_offering_id INTEGER,tier INTEGER)",
            "CREATE TABLE social_friendships(identity_a TEXT,identity_b TEXT,status TEXT)",
            "CREATE TABLE social_blocks(owner_identity TEXT,blocked_identity TEXT)",
            "CREATE TABLE private_message_blocks(owner_identity TEXT,blocked_identity TEXT)",
            "CREATE TABLE wanderer_agents(student_id INTEGER PRIMARY KEY)",
        ):
            conn.execute(sql)
        viewer = {"id": 1, "role": "student", "actor": "student:1"}
        conn.execute("INSERT INTO students(id,class_id) VALUES(1,921)")
        now = platform.now_ms()

        def game(gid, updated, *, tags=(), featured=0, scope="public", ready=True):
            conn.execute(
                """INSERT INTO mg_games(id,slug,owner_teacher_id,title,tags_json,scope,targets_json,
                featured,status,current_version_id,created_at,updated_at)
                VALUES(?,?,1,?,?,?,'[921]',?,'listed',?,1,?)""",
                (gid, gid, gid, json.dumps(tags), scope, featured, "v-" + gid, updated),
            )
            conn.execute(
                """INSERT INTO mg_versions(id,game_id,status,created_at,config_json)
                VALUES(?,?,?,1,'{"modes":{"solo":true}}')""",
                ("v-" + gid, gid, "ready" if ready else "rejected"),
            )

        counter = 0

        def activity(gid, players, at, *, running=False, mode="solo"):
            nonlocal counter
            counter += 1
            aid = "activity-" + str(counter)
            conn.execute(
                """INSERT INTO mg_rt_activity(id,game,version,mode,phase,owner,capacity,run,created,started)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    aid,
                    gid,
                    "v-" + gid,
                    mode,
                    "running" if running else "ended",
                    players[0],
                    len(players),
                    aid,
                    at,
                    at,
                ),
            )
            for seat, actor in enumerate(players):
                conn.execute(
                    """INSERT INTO mg_rt_seats(activity,actor,seat,alias,name,joined)
                    VALUES(?,?,?,?,?,?)""",
                    (aid, actor, seat, "alias-" + str(seat), actor, at),
                )
                if running:
                    conn.execute(
                        """INSERT INTO mg_rt_claims(actor,slot,game,target,host,epoch,session_key,expires,touched,interacted)
                        VALUES(?,?,?,?,'host',1,'session',?,?,?)""",
                        (
                            actor,
                            gid if mode == "solo" else "multi",
                            gid,
                            aid,
                            now + 90000,
                            now,
                            at,
                        ),
                    )

        for index in range(65):
            private = f"hidden-{index}"
            game(
                private,
                10000 + index,
                featured=1,
                scope="private",
                tags=("private-tag",),
            )
            activity(
                private,
                ["student:1", "student:2", "student:3", "student:4"],
                now - index,
            )
            game(f"visible-{index}", index)
        game("rejected", 50000, featured=1, ready=False)
        game("editor", 100, featured=1, tags=("图形",))
        game("tag-pick", 1000, tags=("其他", "图形"))
        game("my-old", 300, tags=("图形",))
        game("hot", 400)
        game("new", 2000)
        game("older", 1500, tags=("private-tag",))
        activity("my-old", [viewer["actor"]], now - 9 * 86400000)
        activity("hot", ["student:2", "student:3", "student:4"], now - 50)
        activity("hot", ["student:2"], now - 20)
        with patch.object(
            platform, "public_game", wraps=platform.public_game
        ) as projected:
            board = sidebar_data(conn, viewer)
        self.assertLessEqual(projected.call_count, 15)
        self.assertEqual([g["id"] for g in board["recent"]], ["my-old"])
        self.assertEqual(
            [(g["id"], g["playerCount"]) for g in board["popular"]], [("hot", 3)]
        )
        self.assertEqual(
            [(g["id"], g["reason"]) for g in board["recommended"]],
            [
                ("editor", "教师精选"),
                ("tag-pick", "同标签 · 图形"),
                ("my-old", "同标签 · 图形"),
                ("hot", "近期热门"),
                ("new", "最近更新"),
            ],
        )
        self.assertEqual(board["friends"], [])

        game("my-class", 200, scope="class")
        for n in range(10, 41):
            who = f"student:{n}"
            conn.execute("INSERT INTO students(id,class_id) VALUES(?,99)", (n,))
            conn.execute(
                "INSERT INTO social_friendships VALUES(?,?,'accepted')",
                (who, viewer["actor"]) if n % 2 else (viewer["actor"], who),
            )
            conn.execute("INSERT INTO mg_rt_prefs(actor,sharing) VALUES(?,1)", (who,))
            activity("my-class", [who], now - n, running=True, mode="private")
        # Everyone's earlier-ranked multiplayer room is visible to the viewer but no longer to the friend.
        # The public solo run for this same friend appears after more than one page of ineligible candidates.
        activity("hot", ["student:11"], now - 80, running=True)
        activity("older", ["student:11"], now - 100, running=True)
        for n in range(12, 17):
            activity("new", [f"student:{n}"], now - n, running=True)
        conn.execute("INSERT INTO social_blocks VALUES('student:12','student:1')")
        conn.execute(
            "INSERT INTO private_message_blocks VALUES('student:1','student:13')"
        )
        conn.execute("UPDATE mg_rt_prefs SET sharing=0 WHERE actor='student:14'")
        conn.execute("INSERT INTO wanderer_agents VALUES(15)")
        conn.execute(
            "UPDATE social_friendships SET status='pending' WHERE identity_b='student:16'"
        )
        with (
            patch.object(
                platform, "public_game", wraps=platform.public_game
            ) as projected,
            patch.object(platform, "_audience", wraps=platform._audience) as audiences,
        ):
            visible = sidebar_data(conn, viewer)
        self.assertLessEqual(projected.call_count, 20)
        self.assertEqual(audiences.call_count, 1)
        self.assertEqual(
            [(g["displayName"], g["id"]) for g in visible["friends"]],
            [("student:11", "hot")],
        )
        conn.execute("UPDATE students SET class_id=921 WHERE id=11")
        self.assertEqual(
            [g["id"] for g in sidebar_data(conn, viewer)["friends"]], ["my-class"]
        )
        activity("hot", [viewer["actor"]], now - 1000)
        activity("older", [viewer["actor"]], now - 1000)
        self.assertEqual(
            [g["id"] for g in sidebar_data(conn, viewer)["recent"]][:2],
            ["hot", "older"],
        )
