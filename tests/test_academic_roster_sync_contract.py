import unittest
from unittest.mock import AsyncMock, patch

from classroom_app.services import academic_auto_sync_service as auto_sync
from classroom_app.services import academic_course_sync_service as course_sync
from classroom_app.services import academic_roster_sync_service as roster_sync


def teaching_class_row(**overrides):
    row = {
        "JXB_ID": "TEST-JXB-2024-0001",
        "XNM": "2024",
        "XNMMC": "2024-2025",
        "XQM": "12",
        "XQMMC": "2",
        "KCH_ID": "0403J5",
        "KCMC": "Python程序设计",
        "JXBMC": "Python程序设计-0010",
        "JXBZC": "软工2301班",
        "JSXM": "测试教师",
        "SKSJ": "星期一第4-5节{1-5周,8周};星期三第6-7节{2-4周}",
        "JXDD": "时习楼B205;计算机实验室（三）",
        "RS": "47",
        "YSKXS": "47",
    }
    row.update(overrides)
    return row


class FakeIndexResponse:
    status_code = 200
    url = roster_sync.ZF_STUDENT_ROSTER_INDEX_PATH


class FakeClient:
    async def get(self, *_args, **_kwargs):
        return FakeIndexResponse()


class AcademicRosterSourceContractTests(unittest.TestCase):
    def test_verified_term_parameters_have_no_dangerous_ordinal_fallback(self):
        self.assertEqual(
            roster_sync._term_param_candidates({"name": "2024-2025第二学期"}),
            [{"xnm": "2024", "xqm": "12"}],
        )
        self.assertEqual(
            course_sync._term_param_candidates({"name": "2024-2025第一学期"}),
            [{"xnm": "2024", "xqm": "3"}],
        )

    def test_course_name_alias_and_real_class_composition_keep_distinct_meanings(self):
        roster = roster_sync._teaching_class_from_row(teaching_class_row())
        self.assertEqual(roster.course_name, "Python程序设计")
        self.assertEqual(roster.teaching_class_name, "Python程序设计-0010")
        self.assertEqual(roster.class_composition, "软工2301班")

        student = roster_sync._student_from_row(
            {"XH": "2400000001", "XM": "测试学生", "BJ": "错误回退班名"},
            roster,
        )
        self.assertIsNotNone(student)
        self.assertEqual(student.class_name, "软工2301班")

    def test_multi_class_composition_uses_student_class_only_to_disambiguate(self):
        roster = roster_sync._teaching_class_from_row(
            teaching_class_row(JXBZC="软工2301班、软工2302班")
        )
        student = roster_sync._student_from_row(
            {"XH": "2400000002", "XM": "测试学生", "BJ": "软工2302班"},
            roster,
        )
        self.assertEqual(student.class_name, "软工2302班")

    def test_student_class_cannot_override_declared_class_composition(self):
        roster = roster_sync._teaching_class_from_row(
            teaching_class_row(JXBZC="软工2301班、软工2302班")
        )
        student = roster_sync._student_from_row(
            {"XH": "2400000003", "XM": "测试学生", "BJ": "教学班代号-0010"},
            roster,
        )
        self.assertEqual(student.class_name, "软工2301班")

    def test_teaching_class_rows_generate_course_schedule_and_occurrence_inputs(self):
        roster = roster_sync._teaching_class_from_row(teaching_class_row())
        items = course_sync.build_schedule_items_from_teaching_class_rosters(
            [roster],
            source_url=roster_sync.ZF_TEACHING_CLASS_LIST_PATH,
        )
        self.assertEqual(len(items), 2)
        self.assertEqual({item.course_name for item in items}, {"Python程序设计"})
        self.assertEqual({item.teaching_class_name for item in items}, {"Python程序设计-0010"})
        self.assertEqual({item.class_composition for item in items}, {"软工2301班"})
        self.assertEqual({item.weekday for item in items}, {0, 2})
        self.assertEqual({item.section_text for item in items}, {"4-5", "6-7"})
        self.assertEqual({item.location for item in items}, {"时习楼B205", "计算机实验室（三）"})

    def test_semester_options_expose_exact_query_mapping(self):
        options = roster_sync.build_academic_sync_semester_options(
            [
                {
                    "id": 8,
                    "name": "2024-2025第二学期",
                    "start_date": "2025-02-17",
                    "end_date": "2025-07-06",
                    "is_current": True,
                }
            ]
        )
        self.assertEqual(options[0]["academic_year"], "2024-2025")
        self.assertEqual(options[0]["term_number"], 2)
        self.assertEqual(options[0]["xnm"], "2024")
        self.assertEqual(options[0]["xqm"], "12")

    def test_query_parameter_contract_distinguishes_business_filters_from_jqgrid_plumbing(self):
        roles = roster_sync.ACADEMIC_QUERY_PARAMETER_CONTRACT
        self.assertIn("start year", roles["xnm"])
        self.assertIn("3=first, 12=second, 16=third", roles["xqm"])
        self.assertIn("required only", roles["jxb_id"])
        self.assertIn("not a business filter", roles["nd"])
        self.assertIn("not a business filter", roles["time"])


class AcademicRosterFetchSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_rows_from_a_different_term_are_rejected(self):
        payload = {
            "items": [
                teaching_class_row(),
                teaching_class_row(JXB_ID="WRONG", XQM="3", XQMMC="1"),
            ],
            "totalCount": 2,
            "totalPage": 1,
        }
        sources = []
        with patch.object(roster_sync, "_fetch_json", new=AsyncMock(return_value=payload)):
            rosters, params = await roster_sync._fetch_teaching_classes(
                FakeClient(),
                {"name": "2024-2025第二学期"},
                sources,
            )

        self.assertEqual(params, {"xnm": "2024", "xqm": "12"})
        self.assertEqual([roster.teaching_class_id for roster in rosters], [teaching_class_row()["JXB_ID"]])
        self.assertEqual(sources[-1]["rejected_term_mismatch_count"], 1)


class AcademicAutoSyncContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_course_and_roster_stage_views_share_one_remote_sync(self):
        shared_result = {
            "status": "success",
            "message": "同步完成",
            "course_count": 2,
            "occurrence_count": 6,
            "teaching_class_count": 3,
            "touched_class_count": 2,
            "roster_student_count": 95,
        }
        runner = AsyncMock(return_value=shared_result)
        with patch.object(
            auto_sync,
            "sync_current_teacher_rosters_from_academic_system",
            new=runner,
        ):
            stages = await auto_sync._run_course_roster_stages(42)

        runner.assert_awaited_once_with(42)
        self.assertEqual([stage["key"] for stage in stages], ["courses", "rosters"])
        self.assertEqual(stages[0]["counts"]["course_count"], 2)
        self.assertEqual(stages[0]["counts"]["occurrence_count"], 6)
        self.assertEqual(stages[1]["counts"]["touched_class_count"], 2)
        self.assertEqual(stages[1]["counts"]["roster_student_count"], 95)


if __name__ == "__main__":
    unittest.main()
