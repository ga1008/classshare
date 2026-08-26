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
    text = '<select id="kkbm_id_cx"><option value="05C0F7E1701BB2D7E0630100007F5B5A">E02软件工程系</option></select>'

    def raise_for_status(self):
        return None


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
        self.assertEqual(roster.course_code, "")
        self.assertEqual(roster.course_internal_id, "0403J5")
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

    def test_empty_roster_teaching_classes_are_reported_with_actionable_warning(self):
        populated = roster_sync._teaching_class_from_row(teaching_class_row())
        populated.students = [
            roster_sync.AcademicRosterStudent(
                student_number="2400000001",
                name="测试学生",
                class_name="软工2301班",
            )
        ]
        empty = roster_sync._teaching_class_from_row(
            teaching_class_row(
                JXB_ID="TEST-JXB-2026-0001",
                KCMC="数据结构",
                JXBMC="数据结构-0001",
                JXBZC="网工2501班",
                RS="0",
                YSKXS="0",
            )
        )

        summaries = roster_sync.summarize_empty_teaching_classes([populated, empty])
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["teaching_class_name"], "数据结构-0001")
        self.assertEqual(summaries[0]["class_composition"], "网工2501班")

        warnings = roster_sync.build_empty_roster_warnings(summaries)
        self.assertEqual(len(warnings), 1)
        self.assertIn("数据结构-0001", warnings[0])
        self.assertIn("网工2501班", warnings[0])
        self.assertIn("教务同步", warnings[0])
        self.assertIn("暂无学生名单", warnings[0])

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

    def test_timetable_parser_never_promotes_kch_id_to_official_course_code(self):
        items = course_sync._parse_schedule_items_from_json(
            {
                "kbList": [
                    {
                        "kcmc": "Python程序设计",
                        "kch_id": "0403J5",
                        "jxb_id": "TEST-JXB-2024-0001",
                        "jxbmc": "Python程序设计-0010",
                    }
                ]
            },
            course_sync.ZF_TEACHER_TIMETABLE_QUERY_PATH,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].course_code, "")
        self.assertEqual(items[0].course_internal_id, "0403J5")

    def test_public_course_match_requires_exact_name_and_unique_real_code(self):
        rows = [
            {"kcmc": "服务器配置与管理", "kch": "E020185B3", "kkbmmc": "数字科技学院~E02软件工程系", "tkbj": "启用"},
            {"kcmc": "服务器配置与管理实验", "kch": "E020021B4", "kkbmmc": "数字科技学院~E02软件工程系", "tkbj": "启用"},
        ]
        candidate, reason, count = course_sync._select_public_course_candidate(
            "服务器配置与管理",
            rows,
            department_name="软件工程系",
        )
        self.assertEqual(candidate["kch"], "E020185B3")
        self.assertEqual(reason, "exact_unique_code")
        self.assertEqual(count, 1)

        rows.append({"kcmc": "服务器配置与管理", "kch": "E020999B3", "kkbmmc": "数字科技学院~E02软件工程系", "tkbj": "启用"})
        candidate, reason, count = course_sync._select_public_course_candidate(
            "服务器配置与管理",
            rows,
            department_name="软件工程系",
        )
        self.assertIsNone(candidate)
        self.assertEqual(reason, "ambiguous_official_codes")
        self.assertEqual(count, 2)

        candidate, reason, count = course_sync._select_public_course_candidate(
            "服务器配置与管理",
            rows,
            department_name="软件工程系",
            expected_course_code="E020999B3",
        )
        self.assertEqual(candidate["kch"], "E020999B3")
        self.assertEqual(reason, "exact_code_confirmed")
        self.assertEqual(count, 2)

        candidate, reason, count = course_sync._select_public_course_candidate(
            "服务器配置与管理",
            rows,
            department_name="软件工程系",
            expected_course_code="E020777B3",
        )
        self.assertIsNone(candidate)
        self.assertEqual(reason, "official_code_conflict")
        self.assertEqual(count, 2)

    def test_public_department_value_is_read_from_page_not_display_prefix(self):
        options = course_sync._public_department_options(
            """
            <select id="kkbm_id_cx">
              <option value="">全部</option>
              <option value="05C0F7E1701BB2D7E0630100007F5B5A">E02软件工程系</option>
              <option value="0409">E03网络工程系</option>
            </select>
            """
        )
        selected = course_sync._match_public_department_option(options, ["软件工程系"])
        self.assertEqual(selected["display_code"], "E02")
        self.assertEqual(selected["value"], "05C0F7E1701BB2D7E0630100007F5B5A")

    def test_legacy_roster_code_can_be_repaired_but_verified_code_cannot(self):
        legacy = {
            "academic_source": "gxufl_jwxt",
            "academic_course_code": "0403J5",
            "academic_metadata_json": '{"source_summary":[{"path":"/xsxkjk/xsxkcx_cxJxbxxList.html?doType=query&gnmkdm=N255005"}]}',
        }
        verified = {
            "academic_source": "gxufl_jwxt",
            "academic_course_code": "E020185B3",
            "academic_metadata_json": '{"course_code_sources":["teacher_timetable.kch"]}',
        }
        self.assertTrue(course_sync._name_match_is_safe(legacy, "E020999B3"))
        self.assertFalse(course_sync._name_match_is_safe(verified, "E020999B3"))

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

    async def test_same_jxb_id_resolves_official_timetable_course_number(self):
        roster = roster_sync._teaching_class_from_row(teaching_class_row())
        timetable_item = course_sync.AcademicCourseScheduleItem(
            course_name="Python程序设计",
            course_code="E020185B3",
            course_code_source="teacher_timetable.kch",
            teaching_class_id="TEST-JXB-2024-0001",
            teaching_class_name="Python程序设计-0010",
            class_composition="软工2301班",
            credits=3.0,
            course_total_hours_text="48",
            raw_json={"row": {"jxb_id": "TEST-JXB-2024-0001", "kch": "E020185B3", "xf": "3.0", "kczxs": "48"}},
        )
        with patch.object(
            course_sync,
            "_fetch_teacher_timetable",
            new=AsyncMock(return_value=([timetable_item], [])),
        ), patch.object(
            course_sync,
            "_fetch_public_course_rows",
            new=AsyncMock(
                return_value=[
                    {
                        "kcmc": "Python程序设计",
                        "kch": "E020185B3",
                        "kch_id": "PUBLIC-ROW-1",
                        "kkbmmc": "数字科技学院~E02软件工程系",
                        "tkbj": "启用",
                        "xf": "3.0",
                        "zxs": "48",
                    }
                ]
            ),
        ):
            sources, warnings = await course_sync.enrich_rosters_with_authoritative_course_data(
                FakeClient(),
                {"name": "2024-2025第二学期"},
                [roster],
                teacher_department="软件工程系",
            )

        self.assertEqual(roster.course_code, "E020185B3")
        self.assertEqual(roster.course_code_source, "teacher_timetable.kch")
        self.assertEqual(roster.raw_json["course_identity"]["roster_course_internal_id"], "0403J5")
        self.assertTrue(roster.raw_json["course_identity"]["public_course_verified"])
        self.assertEqual(warnings, [])
        timetable_summary = next(
            source
            for source in sources
            if source.get("parser") == "course_identity_reconciliation"
        )
        public_summary = next(
            source
            for source in sources
            if source.get("parser") == "public_course_cross_check"
        )
        self.assertEqual(timetable_summary["matched_count"], 1)
        self.assertEqual(public_summary["verified_count"], 1)
        schedule_items = course_sync.build_schedule_items_from_teaching_class_rosters(
            [roster],
            source_url=roster_sync.ZF_TEACHING_CLASS_LIST_PATH,
        )
        self.assertEqual({item.course_code for item in schedule_items}, {"E020185B3"})
        self.assertEqual({item.course_internal_id for item in schedule_items}, {"0403J5"})
        self.assertEqual({item.credits for item in schedule_items}, {3.0})
        self.assertEqual({item.course_total_hours_text for item in schedule_items}, {"48"})


class AcademicAutoSyncContractTests(unittest.IsolatedAsyncioTestCase):
    def test_conflict_required_is_reported_as_review_required(self):
        stage = auto_sync._stage_payload(
            key="courses",
            label="课程课表",
            result={
                "status": "conflict_required",
                "message": "检测到差异",
                "requires_confirmation": True,
                "plan_id": 27,
                "semester_name": "2025-2026学年第一学期",
            },
            counts={},
        )

        self.assertTrue(stage["requires_confirmation"])
        self.assertEqual(stage["plan_id"], 27)
        status, message = auto_sync._summarize_auto_sync([stage])
        self.assertEqual(status, "review_required")
        self.assertIn("暂停", message)
        self.assertIn("逐项比较", message)

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
