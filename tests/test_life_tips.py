from __future__ import annotations

import sqlite3
import unittest

from classroom_app.services import life_tip_service as service
from classroom_app.services.life_tip_generation_service import _validated_tips
from classroom_app.services.life_tip_seed_data import LIFE_TIP_SEED_PACK, TEACHER_TIP_SEED_PACK


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


class LifeTipRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        # 服务模块用进程级守卫；测试之间彻底复位，确保每个用例独立播种。
        service._seeded = False
        service.invalidate_pool_cache()
        import classroom_app.db.schema_life_tips as schema

        schema._SCHEMA_READY = False
        self.conn = _fresh_conn()
        service.ensure_life_tip_runtime(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        service._seeded = False
        service.invalidate_pool_cache()

    def test_seed_pack_is_inserted_once_and_idempotent(self) -> None:
        expected = len(LIFE_TIP_SEED_PACK) + len(TEACHER_TIP_SEED_PACK)
        count = self.conn.execute("SELECT COUNT(*) AS c FROM life_tips").fetchone()["c"]
        self.assertEqual(count, expected)

        service._seeded = False
        service.ensure_life_tip_runtime(self.conn)
        recount = self.conn.execute("SELECT COUNT(*) AS c FROM life_tips").fetchone()["c"]
        self.assertEqual(recount, expected)

    def test_insert_life_tip_dedupes_by_normalised_content(self) -> None:
        created = service.insert_life_tip(
            self.conn,
            scope="school",
            school_code="gxufl",
            category="学业规则",
            tip_text="补考报名截止在开学第二周周五。",
        )
        self.assertTrue(created)
        # 同文案加空白差异 → 归一化后 hash 相同 → 不重复入库。
        duplicated = service.insert_life_tip(
            self.conn,
            scope="school",
            school_code="gxufl",
            category="学业规则",
            tip_text="补考报名截止在 开学第二周 周五。",
        )
        self.assertFalse(duplicated)

    def test_scope_resolution_layers(self) -> None:
        service.insert_life_tip(
            self.conn, scope="school", school_code="gxufl",
            category="奖学金", tip_text="本校国奖申报每年 9 月 10 日前交材料到学工处。",
        )
        service.insert_life_tip(
            self.conn, scope="department", school_code="gxufl", department="信息工程学院",
            category="毕业条件", tip_text="本系毕业设计要求大四上学期完成开题答辩。",
        )

        base = len(LIFE_TIP_SEED_PACK)
        dept_pool = service._load_pool_from_db(
            self.conn, school_code="gxufl", department="信息工程学院", audience_role="student",
        )
        school_pool = service._load_pool_from_db(
            self.conn, school_code="gxufl", department="东语学院", audience_role="student",
        )
        other_school_pool = service._load_pool_from_db(
            self.conn, school_code="another", department="信息工程学院", audience_role="student",
        )
        self.assertEqual(len(dept_pool), base + 2)   # global + school + department
        self.assertEqual(len(school_pool), base + 1)  # global + school
        self.assertEqual(len(other_school_pool), base)  # global only

    def test_payload_contains_candidates_with_expected_keys(self) -> None:
        payload = service.build_login_tip_payload(
            self.conn, school_code="gxufl", department="信息工程学院",
        )
        self.assertIsNotNone(payload)
        tips = payload["tips"]
        self.assertEqual(len(tips), service.TIP_CANDIDATE_COUNT)
        for tip in tips:
            self.assertLessEqual(
                {"id", "category", "text", "source_ref", "image_url"},
                set(tip.keys()),
            )
            self.assertTrue(tip["text"])

    def test_teacher_audience_gets_teacher_pool(self) -> None:
        pool = service._load_pool_from_db(
            self.conn, school_code="gxufl", department="", audience_role="teacher",
        )
        self.assertEqual(len(pool), len(TEACHER_TIP_SEED_PACK))
        payload = service.build_login_tip_payload(
            self.conn, school_code="gxufl", department="", role="teacher",
        )
        self.assertIsNotNone(payload)

    def test_feedback_updates_weight_and_zero_weight_leaves_pool(self) -> None:
        tip_id = self.conn.execute("SELECT id FROM life_tips LIMIT 1").fetchone()["id"]
        result = service.record_tip_feedback(
            self.conn, tip_id=tip_id, user_role="student", user_pk=1, verdict=1,
        )
        self.assertEqual(result["weight"], 2)
        # 同一学生改票 → 覆盖而不是累计
        result = service.record_tip_feedback(
            self.conn, tip_id=tip_id, user_role="student", user_pk=1, verdict=-1,
        )
        self.assertEqual(result["weight"], 0)
        self.assertEqual(result["votes"], 1)
        pool = service._load_pool_from_db(
            self.conn, school_code="", department="", audience_role="student",
        )
        self.assertNotIn(tip_id, [tip["id"] for tip in pool])

    def test_feedback_rejects_unknown_tip(self) -> None:
        with self.assertRaises(ValueError):
            service.record_tip_feedback(
                self.conn, tip_id=999999, user_role="student", user_pk=1, verdict=1,
            )

    def test_manage_list_filters_and_status_toggle(self) -> None:
        listing = service.list_life_tips_for_manage(self.conn, audience="teacher")
        self.assertEqual(listing["total"], len(TEACHER_TIP_SEED_PACK))
        tip_id = listing["items"][0]["id"]

        self.assertTrue(service.set_life_tip_status(self.conn, tip_id=tip_id, status="retired"))
        retired = service.list_life_tips_for_manage(self.conn, status="retired")
        self.assertEqual(retired["total"], 1)

        keyword_hit = service.list_life_tips_for_manage(
            self.conn, keyword=listing["items"][0]["tip_text"][:8],
        )
        self.assertGreaterEqual(keyword_hit["total"], 1)

        with self.assertRaises(ValueError):
            service.set_life_tip_status(self.conn, tip_id=tip_id, status="bogus")


class LifeTipGenerationValidationTests(unittest.TestCase):
    def test_validated_tips_filters_and_normalises(self) -> None:
        payload = {
            "tips": [
                {"tip_text": "本校奖学金申报每年 9 月截止，材料交学工处，逾期不补。", "category": "奖学金", "scope": "school"},
                # department scope 但缺 department → 降级为 school
                {"tip_text": "毕业设计开题需在大四上学期第 8 周前完成，逾期延毕。", "category": "毕业条件", "scope": "department", "department": ""},
                # 未知 category → 归入学业规则
                {"tip_text": "教务系统每学期第 1-2 周开放退改选，错过不受理。", "category": "不存在的分类", "scope": "school"},
                # 过短 → 剔除
                {"tip_text": "太短", "category": "奖学金", "scope": "school"},
                # 非法结构 → 剔除
                "not-a-dict",
            ],
        }
        cleaned = _validated_tips(payload)
        self.assertEqual(len(cleaned), 3)
        self.assertEqual(cleaned[0]["scope"], "school")
        self.assertEqual(cleaned[1]["scope"], "school")
        self.assertEqual(cleaned[1]["department"], "")
        self.assertEqual(cleaned[2]["category"], "学业规则")

    def test_validated_tips_rejects_non_dict_payload(self) -> None:
        self.assertEqual(_validated_tips(None), [])
        self.assertEqual(_validated_tips({"tips": "oops"}), [])


if __name__ == "__main__":
    unittest.main()
