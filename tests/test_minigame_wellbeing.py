"""Foreground intervals, hour settlement and the actual authorized game lease."""

from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from classroom_app import dependencies
from classroom_app.services import learning_progress_service as learning
from classroom_app.services import minigame_platform as platform
from classroom_app.services import minigame_wellbeing as play
from classroom_app.services import student_insight_service as insights
from tests.test_minigame_platform import API, GamePlatformFixture


def stamp(local):
    return int(datetime.fromisoformat(local).replace(tzinfo=play.LOCAL).timestamp() * 1000)


def measured(conn, start, end, student=931):
    anchor = start + play.LEASE_MS
    return play.record(conn, student, anchor, {"usageAnchor": anchor, "playIntervals": [[0, end-start]]}, end)


def visit(conn, start, minutes, student=931):
    for minute in range(minutes):
        at = start + minute * 60_000
        measured(conn, at, at + 60_000, student)


class GameWellbeingTests(GamePlatformFixture):
    def setUp(self):
        super().setUp()
        with platform.game_db() as conn:
            conn.execute("INSERT INTO courses(id,name,created_by_teacher_id) VALUES (200,'演示课程',901)")
            conn.execute("INSERT INTO classes(id,name,created_by_teacher_id) VALUES (922,'合成乙班',901)")
            for key, group, semester in ((801, 921, ''), (802, 922, ''), (803, 921, '2025-2026-2')):
                conn.execute("INSERT INTO class_offerings(id,course_id,class_id,teacher_id,semester) VALUES (?,200,?,901,?)",
                             (key, group, semester))
            conn.execute("INSERT INTO mg_games(id,slug,owner_teacher_id,scope,status,current_version_id,created_at,updated_at) "
                         "VALUES('test','test',901,'public','listed','v1',0,0)")
            conn.execute("INSERT INTO mg_versions(id,game_id,status,created_at) VALUES('v1','test','ready',0)")

    def test_real_lease_overlap_retry_background_revocation_and_attendance(self):
        self.client.cookies.set("access_token", self.tokens[931])
        sid = dependencies.decode_token_payload(self.tokens[931])["session_id"]
        csrf = platform.csrf_token({"actor": "student:931", "session_id": sid})
        now = stamp("2026-11-24T15:00:00")
        clock = self.enterContext(patch.object(platform, "now_ms", return_value=now))

        def post(path, **body):
            return self.client.post(API + path, json=body, headers={"X-LanGame-CSRF": csrf,
                "Origin": "http://127.0.0.1", "Idempotency-Key": uuid4().hex})

        leases = []
        for instance in ("instance-one", "instance-two"):
            r = post("/games/test/launches", hostInstance=instance, purpose="play")
            self.assertEqual(r.status_code, 200, r.text)
            leases.append(("/launches/" + r.json()["leaseId"], instance, r.json()["expiresAt"]))
        clock.return_value += 50_000
        first, second = leases
        def sample(lease, intervals, operation="renew"):
            path, instance, anchor = lease
            return post(path + "/" + operation, hostInstance=instance, usageAnchor=anchor, playIntervals=intervals)
        result = sample(first, [[0, 45_000]])
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["playWellbeing"]["todaySeconds"], 45)
        self.assertEqual(sample(first, [[0, 45_000]]).json()["expiresAt"], result.json()["expiresAt"])
        self.assertEqual(sample(second, [[20_000, 90_000]]).json()["playWellbeing"]["todaySeconds"], 50)
        # A hidden tab earns no time. The last sample is clipped at the teacher's
        # verified decision even if the browser only learns about it afterwards.
        anchor = result.json()["expiresAt"]
        clock.return_value += 5_000
        first = first[:2] + (anchor,)
        self.assertEqual(sample(first, []).status_code, 200)
        first = first[:2] + (clock.return_value + play.LEASE_MS,)
        clock.return_value += 10_000
        with platform.game_db() as conn:
            conn.execute("""INSERT INTO mg_classroom_access(offering_id,teacher_id,updated_by,mode,games_json,
                duration_minutes,expires_at,revision) VALUES(801,901,901,'deny','[]',60,?,1)""", (now + 61_000 + 3_600_000,))
        self.assertEqual(sample(first, [[0, 10_000]]).status_code, 403)
        self.assertTrue(sample(first, [[0, 10_000]], "revoke").json()["revoked"])
        self.assertEqual(self.client.get(API + "/sidebar").json()["playWellbeing"]["todaySeconds"], 56)
        with platform.game_db() as conn:
            count = conn.execute("SELECT COUNT(*) FROM login_attendance_access WHERE student_id=931 AND source='authenticated_request' AND observed_at_ms=?",
                                 (now + 50_000,)).fetchone()[0]
            self.assertEqual(count, 1)
            activity = insights._load_activity_summary(conn, offering_ids=[801], student_id=931, include_games=True)
            self.assertEqual((activity["active_days"], activity["online_minutes"], activity["focus_minutes"]), (1, 0, 0))
            timeline = insights._build_student_cultivation_timeline(
                conn, offering_ids=[801], student_id=931, teacher_note={}, include_games=True)
            self.assertEqual([v["type"] for v in timeline], ["minigame_visit"])

    def test_daylight_limits_night_override_midnight_and_carry_to_next_day(self):
        start = stamp("2026-11-24T07:00:00")
        with platform.game_db() as conn:
            visit(conn, start, 60)
            self.assertEqual(play.course_points(conn, 801, 931), 1)
            self.assertEqual(play.course_points(conn, 802, 931), 0)
            self.assertEqual(play.course_points(conn, 803, 931), 0)
            learning.refresh_student_learning_state(conn, 801, 931)
            progress = learning.serialize_student_learning_progress(conn, 801, 931, include_details=False)
            self.assertEqual((progress["score"], progress["minigame"]["points"], progress["snapshot_status"]), (1, 1, "ready"))
            visit(conn, start + 3_600_000, 60)
            self.assertEqual(play.course_points(conn, 801, 931), 1)
            visit(conn, start + 7_200_000, 60)
            self.assertEqual(play.course_points(conn, 801, 931), 0)
            measured(conn, stamp("2026-11-24T23:59:30"), stamp("2026-11-25T00:00:30"))
            midnight = stamp("2026-11-25T00:00:30")
            previous = play.summary(conn, 931, midnight - 60_000)
            current = play.summary(conn, 931, midnight)
            self.assertEqual((previous["todaySeconds"], previous["nightSeconds"]), (10_830, 30))
            self.assertEqual((current["todaySeconds"], current["nightSeconds"]), (30, 30))
            visit(conn, midnight, 59)
            self.assertEqual(play.course_points(conn, 801, 931), -1)
            # Before 07:00, even today's very first hour was a penalty.
            mixed = stamp("2026-11-25T06:59:30")
            measured(conn, mixed, mixed + 60_000, student=932)
            dusk = stamp("2026-11-25T21:59:30")
            measured(conn, dusk, dusk + 60_000, student=932)
            split = play.summary(conn, 932, dusk + 60_000)
            self.assertEqual((split["rewardRemainderSeconds"], split["penaltyRemainderSeconds"]), (60, 60))

    def test_hourly_archive_and_sample_validation(self):
        now = stamp("2026-11-24T09:00:00")
        anchor = now + play.LEASE_MS
        with platform.game_db() as conn:
            for intervals in ([[0, 90_001]], [[-1, 100]], [[True, 100]], [[0, 100]] * 33):
                with self.assertRaises(HTTPException):
                    play.record(conn, 931, anchor, {"usageAnchor": anchor, "playIntervals": intervals}, now)
            self.assertIsNone(play.record(conn, 931, anchor, {"usageAnchor": 1, "playIntervals": [[0, 100]]}, now))
            self.assertIsNone(play.record(conn, 931, anchor, {"usageAnchor": anchor, "playIntervals": [[0, 100]]}, anchor))
            visit(conn, now, 60)
            learning.archive_cultivation_score_events(conn, as_of=datetime(2027, 11, 24), retention_days=30)
            self.assertEqual(play.course_points(conn, 801, 931), 1)
            self.assertEqual(conn.execute("SELECT dirty FROM learning_progress_snapshots WHERE student_id=931 AND class_offering_id=801").fetchone()[0], 1)
