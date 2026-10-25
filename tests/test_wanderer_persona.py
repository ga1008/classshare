import unittest

from classroom_app.services import blog_persona_service, blog_service
from classroom_app.services.cultivation_display_service import load_student_emblems
from classroom_app.services.wanderer_identity_service import (
    WANDERER_CLASS_ACADEMIC_SOURCE,
    WANDERER_CLASS_NAME,
    WANDERER_ENROLLMENT_STATUS,
    build_wanderer_user_dict,
    is_wanderer,
    list_wanderers,
    load_wanderers_by_student_ids,
    mark_dead,
)
from classroom_app.services.wanderer_persona_seeds import WANDERER_SEEDS, render_persona_prompt, seed_wanderers
from tests.wanderer_test_support import WandererDbTestCase, reset_wanderer_caches


def _resolve(conn, student_id, *, display_mode="real_name"):
    identity = f"student:{student_id}"
    profiles = blog_persona_service.load_profiles(conn, [identity])
    return blog_persona_service.resolve(
        actor_identity=identity, actor_role="student", actor_user_pk=student_id, display_mode=display_mode,
        snapshot_name="", profiles=profiles, is_comment=True,
    )


class WandererPersonaSeedTest(unittest.TestCase):
    def test_twenty_distinct_seeds_with_required_fields(self):
        required = {
            "slug", "display_name", "archetype", "gender", "hometown", "childhood", "schooling", "interests",
            "speech_style", "temperament", "interaction_frequency", "reply_policy", "taboos", "initial_tier",
            "initial_knowledge", "daily_action_budget",
        }
        self.assertEqual(len(WANDERER_SEEDS), 20)
        self.assertEqual(len({seed["slug"] for seed in WANDERER_SEEDS}), 20)
        self.assertEqual(len({seed["archetype"] for seed in WANDERER_SEEDS}), 20)
        for seed in WANDERER_SEEDS:
            self.assertTrue(required <= set(seed), required - set(seed))
            self.assertEqual(len(seed["initial_knowledge"]), 3)
            self.assertIn(seed["initial_tier"], range(0, 4))
            frequency = seed["interaction_frequency"]
            self.assertIn(frequency["activity_level"], range(1, 6))
            self.assertTrue(frequency["active_hours"])

    def test_rendered_persona_prompt_is_a_coherent_sheet(self):
        for seed in WANDERER_SEEDS:
            prompt = render_persona_prompt(seed)
            self.assertGreaterEqual(len(prompt), 300, seed["slug"])
            self.assertLessEqual(len(prompt), 600, seed["slug"])
            self.assertTrue(prompt.startswith(f"你是{seed['display_name']}"))
            for marker in ("散修", "真实的学生", "其他散修", "【童年】", "【求学】", "【喜好】", "【说话方式】",
                           "【性格】", "【互动习惯】", "【禁忌】"):
                self.assertIn(marker, prompt, seed["slug"])


class WandererSeedingTest(WandererDbTestCase):
    def test_seed_creates_twenty_hidden_students(self):
        self.assertEqual(len(self.seed_report["created"]), 20)
        self.assertEqual(self.seed_report["knowledge_added"], 60)
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT s.enrollment_status, s.hashed_password, s.student_id_number, c.name AS class_name, "
                "c.academic_source FROM students s JOIN classes c ON c.id = s.class_id "
                "WHERE s.student_id_number LIKE 'WANDERER-%'"
            ).fetchall()
        self.assertEqual(len(rows), 20)
        for row in rows:
            self.assertEqual(row["enrollment_status"], WANDERER_ENROLLMENT_STATUS)
            self.assertIsNone(row["hashed_password"])
            self.assertEqual(row["class_name"], WANDERER_CLASS_NAME)
            self.assertEqual(row["academic_source"], WANDERER_CLASS_ACADEMIC_SOURCE)

    def test_seeding_uses_select_then_insert_without_on_conflict(self):
        from classroom_app.services.wanderer_identity_service import ensure_wanderer_students

        class Recorder:
            def __init__(self, conn):
                self.conn, self.statements = conn, []

            def execute(self, sql, params=()):
                self.statements.append(" ".join(str(sql).split()))
                return self.conn.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self.conn, name)

        seed = {**WANDERER_SEEDS[0], "slug": "seedcheck", "display_name": "种子校验", "persona_prompt": "测试人格"}
        with self.connect() as conn:
            recorder = Recorder(conn)
            first = ensure_wanderer_students(recorder, [seed])
            second = ensure_wanderer_students(recorder, [seed])
            # A row created by a concurrent seeder is adopted, not duplicated.
            conn.execute("INSERT INTO students (student_id_number, name, class_id) "
                         "SELECT 'WANDERER-racer', '抢先者', id FROM classes WHERE name = ?", (WANDERER_CLASS_NAME,))
            racer_pk = conn.execute("SELECT id FROM students WHERE student_id_number = 'WANDERER-racer'").fetchone()[0]
            raced = ensure_wanderer_students(recorder, [{**seed, "slug": "racer"}])
            conn.rollback()
        self.assertEqual(first, second)
        self.assertEqual(raced["racer"], racer_pk)
        touching = [sql for sql in recorder.statements if "INTO classes" in sql or "INTO students" in sql]
        self.assertTrue(touching)
        self.assertFalse([sql for sql in touching if "ON CONFLICT" in sql.upper()])

    def test_reseed_never_overwrites_an_edited_persona(self):
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_agents SET persona_prompt = ? WHERE slug = 'atai'", ("管理员改过的人格",))
            conn.commit()
            report = seed_wanderers(conn)
            conn.commit()
            prompt = conn.execute("SELECT persona_prompt FROM wanderer_agents WHERE slug = 'atai'").fetchone()[0]
            agent_count = conn.execute("SELECT COUNT(*) FROM wanderer_agents").fetchone()[0]
        self.assertEqual(report["created"], [])
        self.assertEqual(report["knowledge_added"], 0)
        self.assertEqual(prompt, "管理员改过的人格")
        self.assertEqual(agent_count, 20)

    def test_seed_defaults_and_behavior_overrides(self):
        with self.connect() as conn:
            bailu = self.wanderer(conn, "bailu")
            lele = self.wanderer(conn, "lele")
        self.assertEqual(bailu["behaviors"]["promotion"], 0)
        self.assertEqual(lele["activity_level"], 5)
        self.assertEqual(lele["daily_action_budget"], 20)
        self.assertGreater(lele["cultivation_score"], 0)
        self.assertEqual(lele["schedule"]["active_hours"][0], [8, 12])

    def test_blog_persona_resolves_wanderer_with_label_and_badge(self):
        with self.connect() as conn:
            suqinghe = self.wanderer(conn, "suqinghe")
            persona = _resolve(conn, suqinghe["student_id"])
            anonymous_attempt = _resolve(conn, suqinghe["student_id"], display_mode="anonymous")
        self.assertEqual(persona["kind"], blog_persona_service.KIND_WANDERER)
        self.assertEqual(persona["role"], "student")
        self.assertEqual(persona["role_label"], "散修")
        self.assertEqual(persona["display_name"], "苏青禾")
        self.assertEqual(persona["avatar_kind"], "wanderer")
        self.assertEqual(persona["avatar_url"], f"/api/profile/avatar?role=student&user_id={suqinghe['student_id']}")
        self.assertEqual(persona["homepage_url"], "/wanderers/suqinghe")
        self.assertTrue(persona["can_follow"])
        self.assertTrue(persona["can_message"])
        self.assertTrue(persona["ref"])
        badge = persona["cultivation_badge"]
        self.assertTrue(badge["wanderer"])
        self.assertEqual(badge["tier"], 3)
        self.assertEqual(badge["label"], "散修 · 筑基")
        self.assertEqual(badge["emblem_url"], "/static/img/cultivation/emblems/foundation.svg")
        self.assertEqual(anonymous_attempt["kind"], blog_persona_service.KIND_WANDERER)

    def test_real_student_is_not_marked(self):
        with self.connect() as conn:
            class_id = conn.execute("SELECT id FROM classes WHERE name = ?", (WANDERER_CLASS_NAME,)).fetchone()[0]
            cursor = conn.execute(
                "INSERT INTO students (student_id_number, name, class_id) VALUES ('S-REAL-1', '真实同学', ?)", (class_id,)
            )
            real_id = cursor.lastrowid
            persona = _resolve(conn, real_id)
            self.assertFalse(is_wanderer(conn, real_id))
            conn.rollback()
        self.assertEqual(persona["kind"], blog_persona_service.KIND_REAL)
        self.assertEqual(persona["role_label"], "学生")

    def test_dead_wanderer_gets_suffix(self):
        with self.connect() as conn:
            laozhou = self.wanderer(conn, "laozhou")
            mark_dead(conn, laozhou["id"], "测试陨落")
            reset_wanderer_caches()
            persona = _resolve(conn, laozhou["student_id"])
            dead = [item["slug"] for item in list_wanderers(conn, statuses=["dead"])]
            conn.rollback()
        self.assertEqual(persona["display_name"], "老周（已陨落）")
        self.assertEqual(dead, ["laozhou"])

    def test_emblems_come_from_wanderer_tier(self):
        with self.connect() as conn:
            suqinghe = self.wanderer(conn, "suqinghe")
            xiaoman = self.wanderer(conn, "xiaoman")
            emblems = load_student_emblems(conn, [suqinghe["student_id"], xiaoman["student_id"]])
        self.assertEqual(emblems[suqinghe["student_id"]]["key"], "foundation")
        self.assertTrue(emblems[suqinghe["student_id"]]["wanderer"])
        self.assertIsNone(emblems[suqinghe["student_id"]]["sect"])
        self.assertNotIn(xiaoman["student_id"], emblems)

    def test_user_dict_matches_student_shape(self):
        with self.connect() as conn:
            atai = self.wanderer(conn, "atai")
            cached = load_wanderers_by_student_ids(conn, [atai["student_id"]])
        user = build_wanderer_user_dict(atai)
        self.assertEqual(user["role"], "student")
        self.assertEqual(user["id"], atai["student_id"])
        self.assertEqual(user["student_id_number"], "WANDERER-atai")
        self.assertEqual(user["username"], "WANDERER-atai")
        self.assertTrue(user["is_wanderer"])
        self.assertIn(atai["student_id"], cached)

    def test_blog_snapshot_has_no_class_tag(self):
        with self.connect() as conn:
            atai = self.wanderer(conn, "atai")
            snapshot = blog_service._load_user_profile_snapshot(conn, "student", atai["student_id"])
        self.assertEqual(snapshot["name"], "阿泰")
        self.assertEqual(snapshot["class_name"], "")


if __name__ == "__main__":
    unittest.main()
