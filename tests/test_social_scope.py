"""境界门槛 (docs/social-friends-groups-2026-10-27.md §12.1, §12.5, §12.6)."""
from __future__ import annotations

import unittest

from classroom_app.services import social_friend_service as friends
from classroom_app.services import social_scope_service as scope
from classroom_app.services.social_common import SocialError
from tests.social_test_support import SocialDbTestCase, grant_tier

TARGETS = ("classmate", "teacher_same", "course", "college", "wanderer", "school", "other", "teacher_other")
EXPECTED = {
    0: set(),
    1: {"classmate", "teacher_same"},
    2: {"classmate", "teacher_same", "course"},
    3: {"classmate", "teacher_same", "course", "college", "wanderer"},
    4: {"classmate", "teacher_same", "course", "college", "wanderer", "school"},
    5: set(TARGETS),
}


def _insert(conn, sql, params):
    conn.execute(sql, params)
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


class ScopeWorld(SocialDbTestCase):
    """Fixture class (a..e, 外语学院, school gxufl, course 大学英语（下）) plus one person per scope ring."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with cls.connect() as conn:
            conn.execute("UPDATE classes SET college = '外语学院' WHERE id = ?", (cls.class_id,))
            english_down = _insert(conn, "INSERT INTO courses (name, created_by_teacher_id) VALUES (?, ?)",
                                   ("大学英语（下）", cls.teacher_id))
            conn.execute("INSERT INTO class_offerings (class_id, course_id, teacher_id) VALUES (?, ?, ?)",
                         (cls.class_id, english_down, cls.teacher_id))
            english_up = _insert(conn, "INSERT INTO courses (name, created_by_teacher_id) VALUES (?, ?)",
                                 ("大学 英语(上)", cls.teacher_id))

            def klass(name, college, school="gxufl"):
                return _insert(conn, "INSERT INTO classes (name, created_by_teacher_id, college, school_code) "
                                     "VALUES (?, ?, ?, ?)", (name, cls.teacher_id, college, school))

            def student(number, name, class_id, status="active", school="gxufl"):
                return _insert(conn, "INSERT INTO students (student_id_number, name, class_id, enrollment_status, "
                                     "school_code) VALUES (?, ?, ?, ?, ?)", (number, name, class_id, status, school))

            course_class = klass("商务一班", "商学院")
            conn.execute("INSERT INTO class_offerings (class_id, course_id, teacher_id) VALUES (?, ?, ?)",
                         (course_class, english_up, cls.teacher_id))
            other_school_class = klass("外校一班", "外语学院", "otherschool")
            cls.people = {
                "classmate": cls.b,
                "teacher_same": cls.teacher,
                "course": f"student:{student('S-RING-COURSE', '测范课程', course_class)}",
                "college": f"student:{student('S-RING-COLLEGE', '测范学院', klass('翻译一班', '外语学院'))}",
                "wanderer": f"student:{student('S-RING-WANDER', '测范散修', klass('散修班', ''), 'wanderer')}",
                "school": f"student:{student('S-RING-SCHOOL', '测范本校', klass('会计一班', '会计学院'))}",
                "other": f"student:{student('S-RING-OTHER', '测范外校', other_school_class, school='otherschool')}",
                "teacher_other": "teacher:" + str(_insert(
                    conn, "INSERT INTO teachers (name, email, hashed_password, school_code) VALUES (?, ?, ?, ?)",
                    ("测范外校老师", "ring-other@example.test", "synthetic-hash", "otherschool"))),
            }
            conn.commit()

    def set_tier(self, identity: str, tier: int) -> None:
        pk = int(identity.split(":")[1])
        with self.connect() as conn:
            conn.execute("DELETE FROM learning_certificates WHERE student_id = ?", (pk,))
            grant_tier(conn, self.offering_id, pk, tier)
            conn.commit()

    def allowed(self, requester: str) -> set[str]:
        with self.connect() as conn:
            ctx = scope.scope_context(conn, requester)
            verdicts = scope.evaluate_targets(conn, ctx, self.people.values())
        return {name for name, identity in self.people.items() if verdicts[identity][0]}

    def sql_allowed(self, requester: str) -> set[str]:
        with self.connect() as conn:
            ctx = scope.scope_context(conn, requester)
            where, params = scope.student_scope_sql(conn, ctx)
            students = {f"student:{row[0]}" for row in conn.execute(
                f"SELECT s.id FROM students s LEFT JOIN classes c ON c.id = s.class_id WHERE {where}", params)}
            twhere, tparams = scope.teacher_scope_sql(ctx)
            teachers = {f"teacher:{row[0]}" for row in conn.execute(
                f"SELECT t.id FROM teachers t WHERE {twhere}", tparams)}
        return {name for name, identity in self.people.items() if identity in students | teachers}


class CourseNameNormalizationTest(unittest.TestCase):
    def test_version_suffixes_and_widths_collapse(self):
        same = ["大学英语（上）", "大学英语(下)", "大学 英语Ⅱ", "大学英语2", "大学英语 上册", "大学英语（二）", "大学英语"]
        self.assertEqual({scope.normalize_course_name(name) for name in same}, {"大学英语"})
        self.assertEqual(scope.normalize_course_name("高等数学(I)"), scope.normalize_course_name("高等数学（II）"))
        self.assertEqual(scope.normalize_course_name("ＰＹＴＨＯＮ 程序设计"), "python程序设计")

    def test_real_names_are_not_truncated(self):
        self.assertEqual(scope.normalize_course_name("Linux"), "linux")
        self.assertEqual(scope.normalize_course_name("C语言"), "c语言")
        self.assertNotEqual(scope.normalize_course_name("计算机网络"), scope.normalize_course_name("计算机"))


class ScopeLadderTest(ScopeWorld):
    def test_each_tier_unlocks_exactly_its_ring_in_python_and_sql(self):
        for tier, expected in EXPECTED.items():
            with self.subTest(tier=tier):
                self.set_tier(self.a, tier)
                self.assertEqual(self.allowed(self.a), expected)
                self.assertEqual(self.sql_allowed(self.a), expected)

    def test_reason_names_the_unlocking_level(self):
        self.set_tier(self.a, 1)
        with self.connect() as conn:
            ok, reason = scope.can_add(conn, self.user(self.a), self.people["wanderer"])
        self.assertFalse(ok)
        self.assertEqual(reason, f"突破至「{scope.tier_name(3)}」后可添加")

    def test_teachers_and_wanderers_are_unrestricted(self):
        with self.connect() as conn:
            for target in self.people.values():
                if target != self.teacher:
                    self.assertEqual(scope.can_add(conn, self.user(self.teacher), target), (True, ""))
            ok, _ = scope.can_add(conn, self.user(self.people["wanderer"]), self.people["other"])
        self.assertTrue(ok)

    def test_scope_payload_ladder(self):
        self.set_tier(self.a, 2)
        payload = self.client_as(self.a).get("/api/social/scope").json()
        self.assertEqual(payload["tier"], 2)
        self.assertEqual([item["tier"] for item in payload["ladder"]], [0, 1, 2, 3, 4, 5])
        self.assertEqual([item["unlocked"] for item in payload["ladder"]], [True, True, True, False, False, False])
        self.assertEqual(payload["ladder"][1]["label"], "本班同学与本校教师")
        self.assertEqual(payload["next_unlock"]["tier"], 3)
        self.assertTrue(payload["can_add"])
        self.assertFalse(payload["ai_search"])
        teacher = self.client_as(self.teacher).get("/api/social/scope").json()
        self.assertTrue(teacher["is_teacher"] and teacher["ai_search"])
        self.assertIsNone(teacher["next_unlock"])


class ScopeEnforcementTest(ScopeWorld):
    def test_send_request_refused_with_unlock_hint(self):
        self.set_tier(self.a, 2)
        response = self.client_as(self.a).post("/api/social/friends/requests",
                                               json={"target_identity": self.people["college"]})
        self.assertEqual(response.status_code, 403)
        self.assertIn("当前境界尚不能添加该用户", response.json()["detail"])
        self.assertIn(f"突破至「{scope.tier_name(3)}」后可添加", response.json()["detail"])
        ok = self.client_as(self.a).post("/api/social/friends/requests",
                                         json={"target_identity": self.people["course"]})
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_tier_zero_cannot_send_but_can_accept_and_answer(self):
        self.set_tier(self.a, 0)
        with self.assertRaises(SocialError) as ctx:
            self.run_service(friends.send_request, self.user(self.a), self.c)
        self.assertEqual(ctx.exception.status_code, 403)
        # Incoming from an out-of-scope teacher: accepting is never gated.
        request, _ = self.run_service(friends.send_request, self.user(self.people["teacher_other"]), self.a)
        accepted, _ = self.run_service(friends.accept_request, self.user(self.a), request["request_id"])
        self.assertEqual(accepted["status"], "accepted")
        # Answering a pending request by sending one back is also never gated.
        self.set_tier(self.b, 1)
        self.run_service(friends.send_request, self.user(self.b), self.a)
        answered, _ = self.run_service(friends.send_request, self.user(self.a), self.b)
        self.assertEqual(answered["status"], "accepted")

    def test_search_is_scope_filtered_and_locked_at_tier_zero(self):
        self.set_tier(self.a, 0)
        client = self.client_as(self.a)
        locked = client.get("/api/social/users/search", params={"q": "测范"}).json()
        self.assertEqual((locked["items"], locked["locked"]), ([], True))
        self.assertTrue(locked["locked_hint"])
        self.set_tier(self.a, 3)
        found = client.get("/api/social/users/search", params={"q": "测范"}).json()
        names = {item["name"] for item in found["items"]}
        self.assertEqual(names, {"测范课程", "测范学院", "测范散修"})
        self.assertFalse(found["locked"])
        self.assertTrue(all(item["can_add"] for item in found["items"]))

    def test_card_and_public_user_report_can_add(self):
        self.set_tier(self.a, 1)
        client = self.client_as(self.a)
        card = client.get(f"/api/social/users/{self.people['school']}/card").json()
        self.assertFalse(card["can_add"])
        self.assertEqual(card["add_blocked_reason"], f"突破至「{scope.tier_name(4)}」后可添加")
        mate = client.get(f"/api/social/users/{self.c}/card").json()
        self.assertTrue(mate["can_add"])
        self.assertIsNone(mate["add_blocked_reason"])

    def test_suggestions_fallback_and_ai_search_respect_scope(self):
        self.set_tier(self.a, 1)
        client = self.client_as(self.a)
        items = client.get("/api/social/users/suggestions").json()["items"]
        allowed = {self.b, self.c, self.d, self.e, self.teacher}
        self.assertTrue(items)
        self.assertTrue({item["identity"] for item in items} <= allowed)
        denied = client.post("/api/social/users/ai-search", json={"prompt": "找外语学院的同学"})
        self.assertEqual(denied.status_code, 403)
        self.assertIn(scope.tier_name(5), denied.json()["detail"])


class ScopeUnlockNotificationTest(ScopeWorld):
    def notifications(self, identity: str) -> list[str]:
        pk = int(identity.split(":")[1])
        with self.connect() as conn:
            return [row[0] for row in conn.execute(
                "SELECT title FROM message_center_notifications WHERE recipient_user_pk = ? "
                "AND recipient_role = 'student' AND category = 'social' ORDER BY id", (pk,))]

    def test_first_look_is_a_silent_baseline_then_one_message_per_unlock(self):
        self.set_tier(self.d, 1)
        self.client_as(self.d).get("/api/social/scope")
        self.assertEqual(self.notifications(self.d), [])
        self.set_tier(self.d, 3)
        self.client_as(self.d).get("/api/social/scope")
        self.client_as(self.d).get("/api/social/scope")
        titles = self.notifications(self.d)
        self.assertEqual(len(titles), 1)
        self.assertIn("新的交友范围已解锁", titles[0])
        self.assertIn("同课程同学", titles[0])
        self.assertIn("本学院同学与散修", titles[0])

    def test_certificate_hook_notifies_even_without_a_prior_look(self):
        pk = int(self.e.split(":")[1])
        self.set_tier(self.e, 1)
        with self.connect() as conn:
            grant_tier(conn, self.offering_id, pk, 2)
            cert_id = conn.execute("SELECT id FROM learning_certificates WHERE student_id = ? AND tier = 2",
                                   (pk,)).fetchone()[0]
            scope.on_certificate_issued(conn, pk, cert_id)
            scope.on_certificate_issued(conn, pk, cert_id)  # repeat: deduped
            conn.commit()
        titles = self.notifications(self.e)
        self.assertEqual(len(titles), 1)
        self.assertIn("同课程同学", titles[0])


if __name__ == "__main__":
    unittest.main()
