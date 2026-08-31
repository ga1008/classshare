"""小程序订阅消息：字段清洗/载荷构建/额度台账/去重的单测。"""

import sqlite3
import unittest

from classroom_app.services import wechat_mp_subscribe_service as svc


class ClampTests(unittest.TestCase):
    def test_thing_truncates_to_20_chars(self):
        self.assertEqual(svc.clamp_thing("短标题"), "短标题")
        long = "这是一个非常非常非常长的作业标题超过二十个字了"
        clamped = svc.clamp_thing(long)
        self.assertEqual(len(clamped), 20)
        self.assertTrue(clamped.endswith("…"))
        self.assertEqual(svc.clamp_thing(""), "—")

    def test_phrase_and_number(self):
        self.assertEqual(svc.clamp_phrase("已批改"), "已批改")
        self.assertEqual(svc.clamp_phrase("超过五个字的短语"), "超过五个字")
        self.assertEqual(svc.clamp_number(87.0), "87")
        self.assertEqual(svc.clamp_number(87.5), "87.5")
        self.assertEqual(svc.clamp_number("bad"), "0")

    def test_datetime_format(self):
        self.assertEqual(svc.format_wechat_datetime("2026-09-01 23:59:00"), "2026年9月1日 23:59")
        self.assertEqual(svc.format_wechat_datetime("2026-09-01T08:05"), "2026年9月1日 08:05")
        self.assertEqual(svc.format_wechat_datetime(""), "—")


class PayloadBuilderTests(unittest.TestCase):
    def test_deadline_values_fields_and_tip(self):
        values = svc.build_deadline_values("实验报告8", "电信2501班", "2026-09-01 23:59", 5)
        self.assertEqual(
            set(values.keys()), {"thing10", "thing11", "date8", "thing3"}
        )
        self.assertIn("5小时", values["thing3"])
        urgent = svc.build_deadline_values("实验报告8", "电信2501班", "2026-09-01 23:59", 1)
        self.assertEqual(urgent["thing3"], "即将截止，请尽快提交")

    def test_graded_and_nudge_values(self):
        graded = svc.build_graded_values("实验报告8", 87, "NAT outbound接口书写与实际配置不符很长")
        self.assertEqual(graded["number7"], "87")
        self.assertEqual(graded["phrase3"], "已批改")
        self.assertLessEqual(len(graded["thing4"]), 20)
        nudge = svc.build_nudge_values("实验报告8", "计算机网络实验", "2026-09-01 23:59")
        self.assertEqual(set(nudge.keys()), {"thing1", "thing4", "time2", "thing3"})

    def test_deadline_stage_bands(self):
        self.assertEqual(svc.deadline_stage(3600), "stage2")
        self.assertEqual(svc.deadline_stage(3600 * 10), "stage24")
        self.assertIsNone(svc.deadline_stage(-1))
        self.assertIsNone(svc.deadline_stage(3600 * 30))


class GrantLedgerTests(unittest.TestCase):
    def setUp(self):
        svc.reset_schema_ready_for_tests()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        svc.ensure_mp_subscribe_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        svc.reset_schema_ready_for_tests()

    def test_record_and_consume_grants(self):
        balances = svc.record_subscribe_grants(
            self.conn, user_role="student", user_pk=7, template_keys=["deadline", "deadline", "graded", "bogus"]
        )
        self.assertEqual(balances["deadline"], 2)
        self.assertEqual(balances["graded"], 1)
        self.assertNotIn("bogus", balances)

        self.assertTrue(svc._consume_grant(self.conn, "student", 7, "deadline"))
        self.assertTrue(svc._consume_grant(self.conn, "student", 7, "deadline"))
        # 额度耗尽后拒绝
        self.assertFalse(svc._consume_grant(self.conn, "student", 7, "deadline"))
        # 其他人无额度
        self.assertFalse(svc._consume_grant(self.conn, "student", 8, "graded"))

    def test_dedupe_claim_is_once_only(self):
        first = svc._claim_dedupe(
            self.conn, template_key="deadline", user_role="student", user_pk=7,
            dedupe_key="deadline:1:7:stage24",
        )
        second = svc._claim_dedupe(
            self.conn, template_key="deadline", user_role="student", user_pk=7,
            dedupe_key="deadline:1:7:stage24",
        )
        self.assertTrue(first)
        self.assertFalse(second)


if __name__ == "__main__":
    unittest.main()
