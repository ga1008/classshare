"""Unit tests for the Smart Classroom teacher course-schedule sync service.

Covers the remote payload parser (teacherSchedule/list), the teaching-class
matcher against check-in schedule rows, and the aggregated 课时统计 overview
(per-course hours, per-week deck, filters) on an in-memory SQLite database.
"""

import json
import sqlite3
import unittest

import classroom_app.db.schema_smart_schedule as schema_mod
from classroom_app.db.schema_smart_schedule import ensure_course_schedule_schema
from classroom_app.services import smart_classroom_schedule_sync_service as svc


SAMPLE_PAYLOAD = {
    "curXq": 5,
    "year": "2025-2026",
    "maxWeek": 19,
    "semester": "2",
    "curWeek": 17,
    "list": [
        {
            "classroom": "（知新楼B422）示例实验室",
            "course": "计算机网络",
            "courseCode": "E020204B6",
            "id": "remote-net-mon-23",
            "no": "2024010932",
            "sections": ["2", "3"],
            "semester": "2",
            "singleOrDoubleWeek": "DOUBLE",
            "stuNo": 49,
            "teacher": "示例教师",
            "week": "1-2周,4-8周(双)",
            "weeks": [1, 2, 4, 6, 8],
            "xqj": 1,
            "year": "2025-2026",
        },
        {
            "classroom": "（知新楼B320）示例实验室",
            "course": "动态Web程序设计",
            "courseCode": "E020141B4",
            "id": "remote-web-mon-89",
            "no": "2024010932",
            "sections": ["8", "9"],
            "semester": "2",
            "singleOrDoubleWeek": "NONE",
            "stuNo": 36,
            "teacher": "示例教师",
            "week": "1-8周",
            "weeks": [1, 2, 3, 4, 5, 6, 7, 8],
            "xqj": 1,
            "year": "2025-2026",
        },
        # Invalid rows must be skipped: no sections / no course name.
        {"course": "空节次课程", "sections": [], "weeks": [1], "xqj": 2},
        {"course": "", "sections": ["1"], "weeks": [1], "xqj": 2},
    ],
}


def _make_conn() -> sqlite3.Connection:
    schema_mod._SCHEMA_READY = False
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_course_schedule_schema(conn)
    return conn


def _insert_item(conn, **overrides):
    base = {
        "teacher_id": 1,
        "platform_code": svc.SMART_PLATFORM_CODE,
        "remote_id": "r1",
        "course_name": "计算机网络",
        "course_code": "E020204B6",
        "classroom": "（知新楼B422）示例实验室",
        "teaching_class_name": "",
        "teacher_name": "示例教师",
        "teacher_no": "2024010932",
        "academic_year": "2025-2026",
        "academic_term": "2",
        "weekday": 1,
        "sections_json": "[2,3]",
        "weeks_json": "[1,2,3]",
        "week_text": "1-3周",
        "single_or_double": "NONE",
        "student_count": 49,
        "metadata_json": "{}",
        "synced_at": "2026-07-01T10:00:00",
        "created_at": "2026-07-01T10:00:00",
    }
    base.update(overrides)
    columns = ", ".join(base)
    placeholders = ", ".join("?" for _ in base)
    conn.execute(
        f"INSERT INTO smart_classroom_course_schedule_items ({columns}) VALUES ({placeholders})",
        tuple(base.values()),
    )


def _insert_meta(conn, *, cur_week=2, max_week=8, item_count=1):
    conn.execute(
        """
        INSERT INTO smart_classroom_course_schedule_meta (
            teacher_id, platform_code, academic_year, academic_term,
            cur_week, max_week, cur_xq, item_count, synced_at, created_at, updated_at
        ) VALUES (1, ?, '2025-2026', '2', ?, ?, 3, ?, '2026-07-01T10:00:00',
                  '2026-07-01T10:00:00', '2026-07-01T10:00:00')
        """,
        (svc.SMART_PLATFORM_CODE, cur_week, max_week, item_count),
    )


class ParsePayloadTests(unittest.TestCase):
    def test_parse_normalizes_items_and_skips_invalid(self):
        parsed = svc._parse_schedule_payload(SAMPLE_PAYLOAD)
        self.assertEqual(parsed["year"], "2025-2026")
        self.assertEqual(parsed["semester"], "2")
        self.assertEqual(parsed["cur_week"], 17)
        self.assertEqual(parsed["max_week"], 19)
        self.assertEqual(len(parsed["items"]), 2)
        first = parsed["items"][0]
        self.assertEqual(first["sections"], [2, 3])
        self.assertEqual(first["weeks"], [1, 2, 4, 6, 8])
        self.assertEqual(first["weekday"], 1)
        self.assertEqual(first["student_count"], 49)
        self.assertEqual(first["single_or_double"], "DOUBLE")

    def test_parse_rejects_non_dict(self):
        with self.assertRaises(ValueError):
            svc._parse_schedule_payload([1, 2, 3])

    def test_hours_math(self):
        parsed = svc._parse_schedule_payload(SAMPLE_PAYLOAD)
        hours = sum(len(i["sections"]) * len(i["weeks"]) for i in parsed["items"])
        # 计算机网络 2节×5周 + 动态Web 2节×8周 = 26 课时
        self.assertEqual(hours, 26)

    def test_missing_remote_id_gets_derived_fingerprint(self):
        payload = json.loads(json.dumps(SAMPLE_PAYLOAD))
        del payload["list"][0]["id"]
        parsed = svc._parse_schedule_payload(payload)
        self.assertTrue(parsed["items"][0]["remote_id"].startswith("derived-"))


class TeachingClassMatchTests(unittest.TestCase):
    def test_matches_by_code_weekday_sections_and_stu_count(self):
        item = {
            "course_name": "计算机网络",
            "course_code": "E020204B6",
            "weekday": 1,          # 周一 (remote 1-7)
            "sections": [2, 3],
            "student_count": 49,
        }
        candidates = [
            {
                "remote_course_id": "E020204B6",
                "remote_course_name": "计算机网络",
                "remote_teaching_class_name": "计科2401班",
                "weekday": 0,      # 本地 0-6
                "sections_text": "2,3",
                "student_count": 49,
            },
            {
                "remote_course_id": "E020204B6",
                "remote_course_name": "计算机网络",
                "remote_teaching_class_name": "计科2402班",
                "weekday": 2,
                "sections_text": "6,7",
                "student_count": 47,
            },
        ]
        self.assertEqual(svc._match_teaching_class_name(item, candidates), "计科2401班")

    def test_weak_match_returns_empty(self):
        item = {
            "course_name": "别的课",
            "course_code": "X",
            "weekday": 5,
            "sections": [1],
            "student_count": 10,
        }
        candidates = [
            {
                "remote_course_id": "E020204B6",
                "remote_course_name": "计算机网络",
                "remote_teaching_class_name": "计科2401班",
                "weekday": 0,
                "sections_text": "2,3",
                "student_count": 49,
            }
        ]
        self.assertEqual(svc._match_teaching_class_name(item, candidates), "")


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        _insert_meta(self.conn, cur_week=2, max_week=8, item_count=3)
        # 计算机网络：周一 2-3节，1-3周 → 6 课时
        _insert_item(self.conn, remote_id="r1")
        # 计算机网络：周三 6-7节，1-2周 → 4 课时
        _insert_item(
            self.conn,
            remote_id="r2",
            weekday=3,
            sections_json="[6,7]",
            weeks_json="[1,2]",
            week_text="1-2周",
            student_count=47,
        )
        # 动态Web：周一 8-9节，第2周 → 2 课时，带教学班
        _insert_item(
            self.conn,
            remote_id="r3",
            course_name="动态Web程序设计",
            course_code="E020141B4",
            classroom="（知新楼B320）示例实验室",
            teaching_class_name="软工2402班",
            sections_json="[8,9]",
            weeks_json="[2]",
            week_text="2周",
            student_count=36,
        )

    def tearDown(self):
        self.conn.close()

    def test_summary_and_course_stats(self):
        overview = svc.build_teacher_course_schedule_overview(self.conn, 1)
        self.assertTrue(overview["has_data"])
        summary = overview["summary"]
        self.assertEqual(summary["total_hours"], 12)
        self.assertEqual(summary["course_count"], 2)
        self.assertEqual(summary["cur_week"], 2)
        # 第2周：r1(2) + r2(2) + r3(2) = 6 课时
        self.assertEqual(summary["current_week_hours"], 6)
        courses = {c["course_name"]: c for c in overview["courses"]}
        self.assertEqual(courses["计算机网络"]["total_hours"], 10)
        self.assertEqual(courses["动态Web程序设计"]["total_hours"], 2)

    def test_week_deck_placement(self):
        overview = svc.build_teacher_course_schedule_overview(self.conn, 1)
        weeks = overview["weeks"]
        self.assertEqual(len(weeks), 8)  # max_week
        week1 = weeks[0]
        self.assertEqual(week1["lesson_count"], 2)  # r1 + r2
        week2 = weeks[1]
        self.assertTrue(week2["is_current"])
        self.assertEqual(week2["lesson_count"], 3)
        lesson = week2["lessons"][0]
        self.assertEqual(lesson["weekday"], 1)
        self.assertEqual(lesson["section_label"], "第2-3节")
        # 教学班兜底标签
        labels = {lesson["class_label"] for lesson in week2["lessons"]}
        self.assertIn("软工2402班", labels)
        self.assertIn("教学班 · 49人", labels)

    def test_course_filter_changes_aggregation_and_deck(self):
        overview = svc.build_teacher_course_schedule_overview(self.conn, 1, course="计算机网络")
        self.assertEqual(overview["summary"]["total_hours"], 10)
        self.assertEqual(overview["summary"]["course_count"], 1)
        week2 = overview["weeks"][1]
        self.assertEqual(week2["lesson_count"], 2)
        self.assertEqual(overview["filters"]["course"], "计算机网络")

    def test_class_filter(self):
        overview = svc.build_teacher_course_schedule_overview(self.conn, 1, class_label="软工2402班")
        self.assertEqual(overview["summary"]["total_hours"], 2)
        self.assertEqual(overview["summary"]["course_count"], 1)

    def test_unknown_filter_value_is_dropped(self):
        overview = svc.build_teacher_course_schedule_overview(self.conn, 1, course="不存在的课程")
        self.assertEqual(overview["filters"]["course"], "")
        self.assertEqual(overview["summary"]["total_hours"], 0)

    def test_empty_teacher_gets_empty_state(self):
        overview = svc.build_teacher_course_schedule_overview(self.conn, 999)
        self.assertFalse(overview["has_data"])
        self.assertEqual(overview["status"], "empty")
        self.assertEqual(overview["terms"], [])


class HelperTests(unittest.TestCase):
    def test_int_list_variants(self):
        self.assertEqual(svc._int_list(["2", "3"]), [2, 3])
        self.assertEqual(svc._int_list("2,3"), [2, 3])
        self.assertEqual(svc._int_list("6，7"), [6, 7])
        self.assertEqual(svc._int_list(None), [])
        self.assertEqual(svc._int_list(["3", "2", "2"]), [2, 3])

    def test_section_label(self):
        self.assertEqual(svc._section_label([2, 3]), "第2-3节")
        self.assertEqual(svc._section_label([5]), "第5节")
        self.assertEqual(svc._section_label([2, 5]), "第2,5节")
        self.assertEqual(svc._section_label([]), "")


if __name__ == "__main__":
    unittest.main()
