import os
import sqlite3
import unittest

os.environ.setdefault("DB_ENGINE", "sqlite")

import classroom_app.db.schema_resume as schema_mod
from classroom_app.db.schema_resume import ensure_resume_schema
from classroom_app.services.resume import resume_job_target_service as jobs
from classroom_app.services.resume import resume_profile_service as profile


class ResumeJobTargetTests(unittest.TestCase):
    def setUp(self):
        schema_mod._SCHEMA_READY = False
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        ensure_resume_schema(self.conn)
        profile.update_personal_info(self.conn, 1, {
            "name": "测试学生",
            "email": "student@example.com",
            "expected_position": "数据分析实习生",
        })
        profile.create_section_item(self.conn, 1, "skill", {
            "name": "Python", "level": "熟练", "acquired_date": "2025-01",
            "description": "使用 Python 清洗课程调研数据",
        })
        profile.create_section_item(self.conn, 1, "experience", {
            "kind": "project", "title": "校园消费调研", "start_date": "2025-03", "end_date": "2025-06",
            "role": "数据分析", "content": "使用 SQL 整理问卷数据", "contribution": "设计指标并完成可视化",
            "achievement": "完成 500 份问卷分析并向 3 位教师汇报",
        })

    def tearDown(self):
        self.conn.close()

    def _description(self):
        return (
            "岗位职责：负责业务数据清洗、分析与可视化，使用 Python 和 SQL 输出周报。\n"
            "任职要求：具备团队协作和沟通能力，能够解释分析结论。\n"
            "加分项：熟悉 Excel 或 Power BI，有市场调研经验优先。"
        )

    def test_analysis_explains_evidence_and_real_gaps(self):
        bundle = profile.collect_profile_bundle(self.conn, 1)
        analysis = jobs.analyze_job_description(bundle, self._description())
        by_name = {item["name"]: item for item in analysis["capabilities"]}
        self.assertTrue(by_name["Python"]["matched"])
        self.assertTrue(by_name["SQL"]["matched"])
        self.assertFalse(by_name["团队协作"]["matched"])
        self.assertIn("不要直接写成已掌握", next(gap["suggestion"] for gap in analysis["gaps"] if gap["name"] == "团队协作"))
        self.assertGreater(analysis["coverage_score"], 0)
        self.assertLess(analysis["coverage_score"], 100)
        self.assertIn("数据可视化", analysis["experience_feedback"][0]["supported_capabilities"])
        self.assertEqual(analysis["experience_feedback"][0]["suggestions"], [])

    def test_create_list_get_and_ownership(self):
        item = jobs.create_job_target(
            self.conn,
            1,
            target_position="数据分析实习生",
            company_name="示例公司",
            job_description=self._description(),
        )
        self.assertGreater(item["id"], 0)
        self.assertIn("job_description", item)
        listed = jobs.list_job_targets(self.conn, 1)
        self.assertEqual(len(listed), 1)
        self.assertNotIn("job_description", listed[0])
        with self.assertRaises(LookupError):
            jobs.get_job_target(self.conn, 2, item["id"])
        jobs.delete_job_target(self.conn, 1, item["id"])
        self.assertEqual(jobs.list_job_targets(self.conn, 1), [])

    def test_short_description_is_rejected(self):
        bundle = profile.collect_profile_bundle(self.conn, 1)
        with self.assertRaises(ValueError):
            jobs.analyze_job_description(bundle, "熟悉 Python")


if __name__ == "__main__":
    unittest.main()
