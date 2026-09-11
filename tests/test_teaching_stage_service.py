import unittest
from datetime import date

from classroom_app.services.teaching_stage_service import build_teaching_stage, resolve_teaching_phase


SEMESTER = {"id": 7, "name": "2025-2026第一学期", "start_date": "2026-09-01", "end_date": "2027-01-10", "week_count": 18}


def _stage(today: date, **overrides):
    params = {
        "semesters": [SEMESTER],
        "default_semester_id": 7,
        "hub_stats": {"current_offering_count": 3},
        "hub_todo": {"missing_textbook": 1, "missing_ai": 0, "unscheduled": 0},
        "hub_bootstrap": None,
        "today": today,
    }
    params.update(overrides)
    return build_teaching_stage(**params)


class TeachingStageServiceTests(unittest.TestCase):
    def test_phase_boundaries(self):
        start, end = date(2026, 9, 1), date(2027, 1, 10)
        self.assertEqual("start", resolve_teaching_phase(start, end, date(2026, 8, 25)))
        self.assertEqual("start", resolve_teaching_phase(start, end, date(2026, 9, 15)))
        self.assertEqual("middle", resolve_teaching_phase(start, end, date(2026, 9, 16)))
        self.assertEqual("middle", resolve_teaching_phase(start, end, date(2026, 12, 19)))
        self.assertEqual("end", resolve_teaching_phase(start, end, date(2026, 12, 20)))
        self.assertEqual("end", resolve_teaching_phase(start, end, date(2027, 1, 20)))
        self.assertEqual("none", resolve_teaching_phase(None, end, date(2026, 10, 1)))

    def test_semester_start_expands_checklist_with_bootstrap_candidates(self):
        stage = _stage(date(2026, 9, 3), hub_stats={"current_offering_count": 0}, hub_todo={},
                       hub_bootstrap={"summary": {"candidate_count": 4}})
        self.assertEqual("start", stage["phase"])
        self.assertTrue(stage["expanded"])
        self.assertEqual("第 1 周 / 18 周", stage["week_label"])
        by_key = {item["key"]: item for item in stage["checklist"]}
        self.assertTrue(by_key["semester"]["done"])
        self.assertFalse(by_key["offerings"]["done"])
        self.assertIn("4 个教学班可一键开课", by_key["offerings"]["detail"])
        self.assertEqual("/manage/teaching/offerings", stage["next_href"])
        self.assertEqual(4, stage["candidate_count"])

    def test_mid_semester_collapses_and_counts_gaps(self):
        stage = _stage(date(2026, 10, 20))
        self.assertEqual("middle", stage["phase"])
        self.assertFalse(stage["expanded"])
        self.assertIn("3 个课堂进行中", stage["headline"])
        self.assertIn("1 项配置缺口", stage["headline"])
        self.assertEqual("第 8 周 / 18 周", stage["week_label"])

    def test_semester_end_points_to_archive(self):
        stage = _stage(date(2027, 1, 5))
        self.assertEqual("end", stage["phase"])
        self.assertEqual("/manage/archive/ordinary-grade-records", stage["next_href"])

    def test_no_semester_asks_to_confirm_one(self):
        stage = _stage(date(2026, 10, 1), semesters=[], default_semester_id=None)
        self.assertEqual("none", stage["phase"])
        self.assertTrue(stage["expanded"])
        self.assertEqual("/manage/teaching/semesters", stage["next_href"])
        self.assertFalse(stage["checklist"][0]["done"])


if __name__ == "__main__":
    unittest.main()
