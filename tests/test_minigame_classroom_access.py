"""Synthetic timetable, scoped teacher changes, and pre-existing game leases."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi import HTTPException

from classroom_app import dependencies
from classroom_app.services import minigame_classroom_access as access
from classroom_app.services import minigame_launch as launch
from classroom_app.services import minigame_platform as p
from classroom_app.services.login_attendance_rules import to_ms
from tests.test_minigame_platform import API, GamePlatformFixture


class ClassroomGameAccessTests(GamePlatformFixture):
    def setUp(self):
        super().setUp()
        self.day = "2026-11-24"
        self.at("08:40")
        self.enterContext(patch.object(p, "now_ms", lambda: self.now))
        with p.game_db() as c:
            c.execute("INSERT INTO teachers(id,name,email,hashed_password) VALUES(9902,'乙','9902@example.invalid','x')")
            c.execute("INSERT INTO classes(id,name,created_by_teacher_id) VALUES(922,'合班成员班',901),(923,'其他班',9902)")
            c.execute("UPDATE students SET class_id=922 WHERE id=931")
            c.execute("UPDATE students SET class_id=923 WHERE id=932")
            c.execute("INSERT INTO courses(id,name,created_by_teacher_id) VALUES(951,'课甲',901),(952,'课乙',9902)")
            c.execute("INSERT INTO class_offerings(id,course_id,class_id,teacher_id) VALUES(961,951,921,901),(962,952,923,9902)")
            c.execute("INSERT INTO class_offering_class_links(offering_id,class_id,teacher_id,is_primary) VALUES(961,922,901,0)")
            c.execute("""INSERT INTO class_offering_sessions(class_offering_id,order_index,title,session_date,weekday,
                      academic_section_text,academic_time_text) VALUES(961,1,'第一课',?,2,'2-3','08:30-10:00')""",
                      (self.day,))
            for gid in ("game-a", "game-b"):
                c.execute("""INSERT INTO mg_games(id,slug,title,owner_teacher_id,status,scope,current_version_id,
                         created_at,updated_at) VALUES(?,?,?,-931,'listed','public',?,1,1)""", (gid, gid, gid, gid))
                c.execute("INSERT INTO mg_versions(id,game_id,status,created_at) VALUES(?,?,'ready',1)", (gid, gid))
            self.tokens[9902] = dependencies.create_access_token({"id": "9902", "role": "teacher", "name": "乙"},
                                                                "127.0.0.1", conn=c)
            c.commit()

    def at(self, clock):
        self.now = to_ms(self.day + "T" + clock)

    def actor(self, uid):
        user = dependencies.decode_token_payload(self.tokens[uid])
        return {**user, "id": uid, "actor": user["role"] + ":" + str(uid)}

    def request(self, uid, method, path, **kwargs):
        self.client.cookies.set("access_token", self.tokens[uid])
        return self.client.request(method, API + path, headers={
            "Origin": "http://127.0.0.1", "X-LanGame-CSRF": p.csrf_token(self.actor(uid)),
            "Idempotency-Key": "test-" + str(self.now),
        }, **kwargs)

    def write(self, mode, *, ids=(), revision=0, minutes=20, uid=901, course=961):
        return self.request(uid, "PUT", f"/classroom-access/{course}", json={
            "mode": mode, "gameIds": list(ids), "durationMinutes": minutes, "expectedRevision": revision})

    def check(self, game="game-a", uid=931):
        return self.request(uid, "GET", "/games/" + game)

    def test_exact_bells_breaks_and_cancelled_or_moved_sessions(self):
        for clock, status in (("08:29:59",200), ("08:30",403), ("09:09:59",403), ("09:10",200),
                              ("09:19:59",200), ("09:20",403), ("10:00",200)):
            with self.subTest(clock=clock):
                self.at(clock)
                self.assertEqual(self.check().status_code, status)
        self.at("08:45")
        self.assertEqual(self.check(uid=932).status_code, 200)
        self.assertEqual(self.check(uid=901).status_code, 200)
        with p.game_db() as c:
            c.execute("UPDATE class_offering_sessions SET academic_section_text='6-7', schedule_metadata_json=? WHERE class_offering_id=961",
                      ('{"section_text":"2-3"}',))
            c.commit()
        self.assertEqual(self.check().status_code, 200)
        self.at("14:30")
        self.assertEqual(self.check().status_code, 403)
        with p.game_db() as c:
            c.execute("UPDATE class_offering_sessions SET schedule_status='cancelled' WHERE class_offering_id=961")
            c.commit()
        self.assertEqual(self.check().status_code, 200)

    def test_course_rules_roles_idempotency_and_selection(self):
        opts = self.request(901, "GET", "/classroom-access").json()
        self.assertEqual([c["id"] for c in opts["classrooms"]], [961])
        self.assertEqual(opts["csrfToken"], p.csrf_token(self.actor(901)))
        self.assertEqual(self.write("deny", uid=931).status_code, 403)
        self.assertEqual(self.write("deny", uid=9902).status_code, 404)
        self.assertEqual(self.write("allow", ids=["missing"]).status_code, 422)
        self.assertEqual(self.write("allow", minutes=241).status_code, 422)
        path = "/classroom-access/961"
        initial = self.request(901, "GET", path).json()
        self.assertTrue(initial["scheduledNow"])
        self.assertEqual(initial["nextChangeAt"], to_ms(self.day + "T09:10"))
        rule = self.write("allow", ids=["game-b"]).json()
        self.assertEqual(rule["games"], [{"id":"game-b","title":"game-b"}])
        self.at("08:43")
        self.assertEqual(self.write("allow", ids=["game-b"]).json()["expiresAt"], rule["expiresAt"])
        self.assertEqual(self.write("deny").status_code, 409)
        self.assertEqual(self.check("game-b").status_code, 200)
        self.assertEqual(self.check().status_code, 403)
        cards = self.request(931,"GET","/catalog").json()["items"]
        self.assertEqual({c["id"]:c["access"]["allowed"] for c in cards}, {"game-a":False,"game-b":True})
        self.at("09:00")
        self.assertEqual(self.check("game-b").status_code, 403)
        self.assertEqual(self.request(901, "GET", path).json()["mode"], "auto")
        self.at("12:15")
        self.assertEqual(self.write("deny", ids=["game-a"], revision=1).status_code, 200)
        self.assertEqual(self.check().status_code, 403)
        self.assertEqual(self.check("game-b").status_code, 200)
        self.assertEqual(self.check(uid=932).status_code, 200)
        self.assertEqual(self.write("auto", revision=2).json()["mode"], "auto")
        self.assertEqual(self.check().status_code, 200)
        self.assertEqual(self.write("deny", revision=3).status_code, 200)
        self.assertEqual(self.check("game-b").status_code, 403)
        self.at("12:35")
        self.assertEqual(self.check().status_code, 200)

    def test_admin_controls_any_course_and_old_rule_ends_on_reassignment(self):
        with p.game_db() as c:
            c.execute("UPDATE teachers SET is_super_admin=1 WHERE id=9902")
            c.commit()
        self.assertEqual(self.write("allow", uid=9902).status_code, 200)
        self.assertEqual(self.check().status_code, 200)
        self.assertEqual(self.check(uid=9902).status_code, 200)
        with p.game_db() as c:
            c.execute("UPDATE class_offerings SET teacher_id=9902 WHERE id=961")
            c.commit()
        self.assertEqual(self.check().status_code, 403)
        self.assertEqual(self.write("allow", revision=1).status_code, 404)

    def test_overlapping_classrooms_deny_only_matching_games(self):
        with p.game_db() as c:
            c.execute("INSERT INTO class_offering_class_links(offering_id,class_id,teacher_id,is_primary) VALUES(962,922,9902,0)")
        self.assertEqual(self.write("allow").status_code, 200)
        self.assertEqual(self.write("deny", ids=["game-b"], uid=9902, course=962).status_code, 200)
        self.assertEqual(self.check("game-a").status_code, 200)
        self.assertEqual(self.check("game-b").status_code, 403)
        self.assertEqual(self.check("game-b", uid=932).status_code, 403)

    def test_running_lease_assets_runtime_and_owner_preview_all_obey_new_denial(self):
        self.at("08:29:30")
        user = self.actor(931)
        play = launch.launch(user, "game-a", {"hostInstance": "game-window"}, "first")
        self.assertEqual(play["accessUntil"], to_ms(self.day + "T08:30"))
        handle = urlsplit(play["contentEntryUrl"]).path.split("/")[2]
        with p.game_db() as c:
            c.execute("INSERT INTO mg_version_files VALUES('game-a','main.html','text/html',1,'test')")
        folder = p.game_root() / "versions/game-a/game-a/runtime"
        folder.mkdir(parents=True)
        (folder / "main.html").write_text("x",encoding="utf8")
        self.assertEqual(launch.resolve_asset(handle, "main.html")[0], folder / "main.html")
        self.at("08:30")
        for run in (
                lambda: launch.resolve_asset(handle, "main.html"),
                lambda: launch.change_grant(user, play["leaseId"], "game-window", False),
                lambda: launch.launch(user, "game-a", {"purpose":"preview","versionId":"game-a","hostInstance":"preview-tab"}, "preview")):
            with self.assertRaises(access.Restricted):
                run()
        response = self.request(931, "POST", "/games/game-a/runs", json={"hostInstance": "game-window"})
        self.assertEqual(response.status_code, 403, response.text[:200])
        self.assertTrue(launch.change_grant(user, play["leaseId"], "game-window", True)["revoked"])

    def test_two_simultaneous_first_edits_have_a_single_winner(self):
        barrier = Barrier(2)
        actor = self.actor(901)
        def submit(mode):
            barrier.wait(5)
            try:
                return access.save(actor, 961, {"mode":mode,"durationMinutes":10,"expectedRevision":0})["revision"]
            except HTTPException as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(submit, ("allow","deny"))), [1,409])

    def test_undated_period_and_historical_course_do_not_restrict_current_students(self):
        with p.game_db() as conn:
            conn.execute("UPDATE class_offering_sessions SET academic_section_text='',academic_time_text=''")
            conn.execute("UPDATE class_offerings SET semester='2025-2026-1' WHERE id=961")
        self.assertEqual(self.check().status_code, 200)
        self.assertEqual(self.write("deny").status_code, 404)

    def test_interrupted_lease_counts_only_authorized_prefix(self):
        def cutoff(clock, duration):
            self.at(clock)
            with p.game_db() as conn:
                return access.grant_end_on_restriction(conn, self.actor(931), "game-a",
                                                      self.now - duration, self.now)

        self.assertEqual(cutoff("08:30:15", 60_000), to_ms(self.day + "T08:30"))
        self.at("08:37")
        self.assertEqual(self.write("allow", minutes=1).status_code, 200)
        self.assertEqual(cutoff("08:38:10", 45_000), to_ms(self.day + "T08:38"))
        self.at("12:05")
        self.assertEqual(self.write("deny", revision=1).status_code, 200)
        self.assertEqual(cutoff("12:05:10", 30_000), to_ms(self.day + "T12:04:40"))
