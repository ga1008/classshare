"""Unit tests for the Smart Classroom teacher course-schedule sync service.

Covers the remote payload parser (teacherSchedule/list), the teaching-class
matcher against check-in schedule rows, and the aggregated 课时统计 overview
(per-course hours, per-week deck, filters) on an in-memory SQLite database.
"""

import json
import sqlite3
import unittest
from datetime import date
from unittest import mock

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


_MATCH_ITEM = {
    "course_name": "计算机网络",
    "course_code": "E020204B6",
    "weekday": 1,          # 周一 (remote 1-7)
    "sections": [2, 3],
    "student_count": 49,
}


def _candidate(**overrides):
    base = {
        "remote_course_id": "E020204B6",
        "remote_course_name": "计算机网络",
        "remote_teaching_class_name": "计算机网络-0002",
        "weekday": 0,      # 本地 0-6
        "sections_text": "2,3",
        "student_count": 49,
        "class_offering_id": None,
        "match_status": "unmatched",
        "local_class_name": "",
    }
    base.update(overrides)
    return base


class TeachingClassMatchTests(unittest.TestCase):
    def test_matches_by_code_weekday_sections_and_stu_count(self):
        candidates = [
            _candidate(remote_teaching_class_name="计科2401班"),
            _candidate(
                remote_teaching_class_name="计科2402班",
                weekday=2,
                sections_text="6,7",
                student_count=47,
            ),
        ]
        match = svc._match_teaching_class(_MATCH_ITEM, candidates)
        self.assertEqual(match["teaching_class_name"], "计科2401班")
        self.assertIsNone(match["class_offering_id"])

    def test_weak_match_returns_empty(self):
        item = {
            "course_name": "别的课",
            "course_code": "X",
            "weekday": 5,
            "sections": [1],
            "student_count": 10,
        }
        match = svc._match_teaching_class(item, [_candidate()])
        self.assertEqual(match["teaching_class_name"], "")
        self.assertEqual(match["local_class_name"], "")
        self.assertIsNone(match["class_offering_id"])

    def test_local_class_name_and_offering_from_matched_candidate(self):
        candidates = [
            _candidate(
                class_offering_id=77,
                match_status="matched",
                local_class_name="软件工程2303班",
            ),
        ]
        match = svc._match_teaching_class(_MATCH_ITEM, candidates)
        self.assertEqual(match["local_class_name"], "软件工程2303班")
        self.assertEqual(match["class_offering_id"], 77)

    def test_ambiguous_offerings_are_not_linked(self):
        # 两个同分候选映射到不同课堂：宁可不给链接也不能给错。
        candidates = [
            _candidate(class_offering_id=77, match_status="matched", local_class_name="软工2303班"),
            _candidate(class_offering_id=88, match_status="matched", local_class_name="软工2304班"),
        ]
        match = svc._match_teaching_class(_MATCH_ITEM, candidates)
        self.assertIsNone(match["class_offering_id"])
        self.assertEqual(match["local_class_name"], "")

    def test_low_score_match_keeps_name_but_not_offering(self):
        # 仅课程码 + 节次重叠（3+2=5 分）：可标注班级名，不足以关联课堂。
        candidates = [
            _candidate(
                weekday=4,
                student_count=0,
                class_offering_id=77,
                match_status="matched",
                local_class_name="软工2303班",
            ),
        ]
        match = svc._match_teaching_class(_MATCH_ITEM, candidates)
        self.assertEqual(match["local_class_name"], "软工2303班")
        self.assertIsNone(match["class_offering_id"])

    def test_unmatched_offering_status_is_ignored(self):
        candidates = [
            _candidate(class_offering_id=77, match_status="unmatched"),
        ]
        match = svc._match_teaching_class(_MATCH_ITEM, candidates)
        self.assertIsNone(match["class_offering_id"])


def _make_roster_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE classes (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
    conn.execute(
        """
        CREATE TABLE teacher_academic_roster_memberships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER, course_code TEXT, teaching_class_name TEXT,
            class_id INTEGER
        )
        """
    )
    return conn


class AcademicClassMappingTests(unittest.TestCase):
    """教务"班级与学生名单"同步（教学班↔行政班关系）对照。"""

    def test_mapping_loaded_with_code_and_name_keys(self):
        conn = _make_roster_conn()
        conn.execute("INSERT INTO classes (id, name) VALUES (9, '软工2302班')")
        conn.execute(
            "INSERT INTO teacher_academic_roster_memberships "
            "(teacher_id, course_code, teaching_class_name, class_id) "
            "VALUES (1, 'E020056B6', '计算机网络实验-0002', 9)"
        )
        mappings = svc._load_academic_class_mappings(conn, 1)
        self.assertEqual(mappings[("E020056B6", "计算机网络实验-0002")], "软工2302班")
        self.assertEqual(mappings["计算机网络实验-0002"], "软工2302班")
        conn.close()

    def test_ambiguous_teaching_class_is_not_mapped(self):
        # 同一教学班代号对应多个不同 class_id（合并班）→ 宁缺勿错，不采信。
        conn = _make_roster_conn()
        conn.execute("INSERT INTO classes (id, name) VALUES (9, '软工2302班'), (10, '软工2303班')")
        conn.executemany(
            "INSERT INTO teacher_academic_roster_memberships "
            "(teacher_id, course_code, teaching_class_name, class_id) VALUES (1, 'E020056B6', '合班-0009', ?)",
            [(9,), (10,)],
        )
        mappings = svc._load_academic_class_mappings(conn, 1)
        self.assertNotIn(("E020056B6", "合班-0009"), mappings)
        self.assertNotIn("合班-0009", mappings)
        conn.close()

    def test_missing_table_returns_empty(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.assertEqual(svc._load_academic_class_mappings(conn, 1), {})
        conn.close()


class LocalOfferingMatchTests(unittest.TestCase):
    """课表 → 平台课堂 宽松匹配（课程名/班级名互含 + 学期兼容 + 唯一）。"""

    def _item(self, **overrides):
        base = {
            "course_name": "计算机网络",
            "local_class_name": "软件工程2302班",
            "teaching_class_name": "计算机网络-0002",
        }
        base.update(overrides)
        return base

    def test_match_by_names_with_unparseable_semester(self):
        offerings = [
            {"id": 9, "course_name": "计算机网络", "class_name": "软件工程2302班",
             "semester": "P03-2026", "semester_name": ""},
        ]
        self.assertEqual(
            svc._match_local_offering(self._item(), offerings, ("2025-2026", "2")), 9
        )

    def test_semester_mismatch_rejects(self):
        offerings = [
            {"id": 9, "course_name": "计算机网络", "class_name": "软件工程2302班",
             "semester": "", "semester_name": "2024-2025第二学期"},
        ]
        self.assertIsNone(
            svc._match_local_offering(self._item(), offerings, ("2025-2026", "2"))
        )

    def test_ambiguous_matches_reject(self):
        offerings = [
            {"id": 9, "course_name": "计算机网络", "class_name": "软件工程2302班",
             "semester": "", "semester_name": ""},
            {"id": 10, "course_name": "计算机网络", "class_name": "软件工程2302班",
             "semester": "", "semester_name": ""},
        ]
        self.assertIsNone(
            svc._match_local_offering(self._item(), offerings, ("2025-2026", "2"))
        )

    def test_exact_course_beats_substring_sibling(self):
        # 线上真实场景：同班既有"计算机网络"又有"计算机网络实验"课堂，
        # 二者互为子串。精确匹配必须胜出，不能因子串串味判为歧义。
        offerings = [
            {"id": 4, "course_name": "计算机网络", "class_name": "软工2302班",
             "semester": "2025-2026第二学期", "semester_name": ""},
            {"id": 8, "course_name": "计算机网络实验", "class_name": "软工2302班",
             "semester": "2025-2026第二学期", "semester_name": ""},
        ]
        net = self._item(course_name="计算机网络", local_class_name="软工2302班")
        self.assertEqual(svc._match_local_offering(net, offerings, ("2025-2026", "2")), 4)
        lab = self._item(course_name="计算机网络实验", local_class_name="软工2302班")
        self.assertEqual(svc._match_local_offering(lab, offerings, ("2025-2026", "2")), 8)

    def test_case_insensitive_course_match(self):
        # 课表"动态Web程序设计" vs 平台课堂"动态web程序设计"（大小写不同）。
        offerings = [
            {"id": 6, "course_name": "动态web程序设计", "class_name": "软工2401班",
             "semester": "2025-2026第二学期", "semester_name": ""},
        ]
        item = self._item(course_name="动态Web程序设计", local_class_name="软工2401班")
        self.assertEqual(svc._match_local_offering(item, offerings, ("2025-2026", "2")), 6)

    def test_short_classroom(self):
        self.assertEqual(svc._short_classroom("（知新楼B414）网络渗透实验室"), "知新楼B414")
        self.assertEqual(svc._short_classroom("知新楼B414"), "知新楼B414")
        self.assertEqual(svc._short_classroom(""), "")


class ReadTimeClassResolutionTests(unittest.TestCase):
    """读取时用教务名单关系把教学班代号解析成真实行政班名（存量数据自愈）。"""

    def _item(self, **overrides):
        base = {
            "course_code": "E020204B6",
            "teaching_class_name": "计算机网络-0002",
            "local_class_name": "",
            "student_count": 49,
        }
        base.update(overrides)
        return base

    def test_resolves_code_to_real_name_by_tcn_fallback(self):
        # 教务名单的 course_code 是学术长 ID，与课表的 E020204B6 不同 →
        # 靠教学班名单键回退命中，仍能解析出真实班级名。
        item = self._item()
        academic_map = {"计算机网络-0002": "软工2302班"}
        svc._resolve_item_class_name(item, academic_map)
        self.assertEqual(item["local_class_name"], "软工2302班")
        self.assertEqual(item["class_label"], "软工2302班")
        self.assertFalse(item["class_is_fallback"])

    def test_no_mapping_keeps_code_label(self):
        item = self._item()
        svc._resolve_item_class_name(item, {})
        # 未解析时保持退回教学班代号（class_label 由 _apply_class_label 兜底）。
        svc._apply_class_label(item)
        self.assertEqual(item["local_class_name"], "")
        self.assertEqual(item["class_label"], "计算机网络-0002")

    def test_apply_class_label_priority(self):
        real = {"local_class_name": "软工2302班", "teaching_class_name": "计算机网络-0002", "student_count": 49}
        svc._apply_class_label(real)
        self.assertEqual(real["class_label"], "软工2302班")
        code_only = {"local_class_name": "", "teaching_class_name": "计算机网络-0002", "student_count": 49}
        svc._apply_class_label(code_only)
        self.assertEqual(code_only["class_label"], "计算机网络-0002")
        bare = {"local_class_name": "", "teaching_class_name": "", "student_count": 40}
        svc._apply_class_label(bare)
        self.assertTrue(bare["class_is_fallback"])
        self.assertIn("40", bare["class_label"])


class SchemaMigrationTests(unittest.TestCase):
    def test_extension_columns_added_to_legacy_table(self):
        schema_mod._SCHEMA_READY = False
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # 模拟第一轮上线的旧表（没有 local_class_name / class_offering_id）。
        conn.execute(
            """
            CREATE TABLE smart_classroom_course_schedule_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                teacher_id INTEGER NOT NULL,
                platform_code TEXT NOT NULL DEFAULT 'gxufl_smart_classroom',
                remote_id TEXT NOT NULL DEFAULT '',
                course_name TEXT NOT NULL DEFAULT '',
                course_code TEXT NOT NULL DEFAULT '',
                classroom TEXT NOT NULL DEFAULT '',
                teaching_class_name TEXT NOT NULL DEFAULT '',
                teacher_name TEXT NOT NULL DEFAULT '',
                teacher_no TEXT NOT NULL DEFAULT '',
                academic_year TEXT NOT NULL DEFAULT '',
                academic_term TEXT NOT NULL DEFAULT '',
                weekday INTEGER NOT NULL DEFAULT 0,
                sections_json TEXT NOT NULL DEFAULT '[]',
                weeks_json TEXT NOT NULL DEFAULT '[]',
                week_text TEXT NOT NULL DEFAULT '',
                single_or_double TEXT NOT NULL DEFAULT 'NONE',
                student_count INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                synced_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (teacher_id, platform_code, remote_id)
            )
            """
        )
        ensure_course_schedule_schema(conn)
        columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(smart_classroom_course_schedule_items)"
        ).fetchall()}
        self.assertIn("local_class_name", columns)
        self.assertIn("class_offering_id", columns)
        conn.close()


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
        # 动态Web：周一 8-9节，第2周 → 2 课时，带教学班 + 本地课堂映射
        _insert_item(
            self.conn,
            remote_id="r3",
            course_name="动态Web程序设计",
            course_code="E020141B4",
            classroom="（知新楼B320）示例实验室",
            teaching_class_name="动态Web程序设计-0001",
            local_class_name="软工2402班",
            class_offering_id=77,
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
        # 本地班级名优先于教学班代号；无匹配时用人数兜底
        labels = {lesson["class_label"] for lesson in week2["lessons"]}
        self.assertIn("软工2402班", labels)
        self.assertNotIn("动态Web程序设计-0001", labels)
        self.assertIn("教学班 · 49人", labels)
        # 已关联课堂的课程块带跳转链接，未关联的没有
        by_label = {lesson["class_label"]: lesson for lesson in week2["lessons"]}
        self.assertEqual(by_label["软工2402班"]["classroom_url"], "/classroom/77")
        self.assertEqual(by_label["软工2402班"]["class_offering_id"], 77)
        self.assertEqual(by_label["教学班 · 49人"]["classroom_url"], "")

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


class TermDerivationTests(unittest.TestCase):
    def test_previous_term_key(self):
        self.assertEqual(svc._previous_term_key("2025-2026", "2"), ("2025-2026", "1"))
        self.assertEqual(svc._previous_term_key("2025-2026", "1"), ("2024-2025", "2"))
        self.assertEqual(svc._previous_term_key("2025-2026", "3"), ("2025-2026", "2"))
        self.assertIsNone(svc._previous_term_key("bad-year", "2"))

    def test_history_term_keys(self):
        self.assertEqual(
            svc._history_term_keys("2025-2026", "2", 4),
            [("2025-2026", "1"), ("2024-2025", "2"), ("2024-2025", "1"), ("2023-2024", "2")],
        )
        self.assertEqual(svc._history_term_keys("", "", 4), [])


class AcademicHistoryConversionTests(unittest.TestCase):
    """ZF 教务历史课表 → 课表 item 的归一化（历史学期数据源）。"""

    def test_academic_term_to_zf_params(self):
        self.assertEqual(svc._academic_term_to_zf_params("2024-2025", "2"), {"xnm": "2024", "xqm": "12"})
        self.assertEqual(svc._academic_term_to_zf_params("2024-2025", "1"), {"xnm": "2024", "xqm": "3"})
        self.assertEqual(svc._academic_term_to_zf_params("2024-2025", "3"), {"xnm": "2024", "xqm": "16"})
        self.assertIsNone(svc._academic_term_to_zf_params("bad", "2"))

    def test_sections_from_text(self):
        from classroom_app.services import academic_course_sync_service as acs

        self.assertEqual(svc._sections_from_text("2-3", acs), [2, 3])
        self.assertEqual(svc._sections_from_text("5", acs), [5])
        self.assertEqual(svc._sections_from_text("", acs), [])

    def test_zf_item_to_schedule_item(self):
        from classroom_app.services import academic_course_sync_service as acs

        zf_item = acs.AcademicCourseScheduleItem(
            course_name="动态Web程序设计",
            course_code="E020141B4",
            teaching_class_name="动态Web-0001",
            weekday=4,  # ZF 周五 (0-based)
            section_text="2-3",
            weeks_text="1-2周,4-5周,7周",
            location="（知新楼B418）实验室",
            teacher_name="张海林",
            student_count=36,
        )
        item = svc._zf_item_to_schedule_item(zf_item, "2024-2025", "2", acs)
        self.assertIsNotNone(item)
        self.assertEqual(item["source"], "academic")
        self.assertEqual(item["weekday"], 5)  # 1-based: 周五
        self.assertEqual(item["sections"], [2, 3])
        self.assertEqual(item["weeks"], [1, 2, 4, 5, 7])
        self.assertEqual(item["academic_year"], "2024-2025")
        self.assertEqual(item["academic_term"], "2")
        self.assertEqual(item["teaching_class_name"], "动态Web-0001")
        self.assertTrue(item["remote_id"].startswith("zf-"))

    def test_zf_item_without_weekday_is_dropped(self):
        from classroom_app.services import academic_course_sync_service as acs

        zf_item = acs.AcademicCourseScheduleItem(
            course_name="动态Web程序设计",
            weekday=None,
            section_text="2-3",
            weeks_text="1-2周",
        )
        self.assertIsNone(svc._zf_item_to_schedule_item(zf_item, "2024-2025", "2", acs))


class WeekAnchorTests(unittest.TestCase):
    def test_derive_week1_monday(self):
        # 2026-07-03 是周五（所在周周一 2026-06-29），第 17 周 → 第 1 周周一 2026-03-09
        self.assertEqual(svc._derive_week1_monday("2026-07-03T09:00:00", 17), "2026-03-09")
        self.assertEqual(svc._derive_week1_monday("2026-07-03T09:00:00", 1), "2026-06-29")
        self.assertEqual(svc._derive_week1_monday("2026-07-03T09:00:00", 0), "")
        self.assertEqual(svc._derive_week1_monday("not-a-date", 3), "")

    def test_parse_platform_semester_name(self):
        self.assertEqual(svc._parse_platform_semester_name("2025-2026第二学期"), ("2025-2026", "2"))
        self.assertEqual(svc._parse_platform_semester_name("2025-2026学年第1学期"), ("2025-2026", "1"))
        self.assertEqual(svc._parse_platform_semester_name("2025-2026 第 一 学期"), ("2025-2026", "1"))
        self.assertIsNone(svc._parse_platform_semester_name("春季学期"))
        self.assertIsNone(svc._parse_platform_semester_name(""))

    def test_term_status(self):
        anchor = {"week1_monday": date(2026, 3, 2), "week_count_hint": 19}
        # 19 周 → 最后一天 2026-07-12
        self.assertEqual(svc._term_status(anchor, date(2026, 7, 3)), "current")
        self.assertEqual(svc._term_status(anchor, date(2026, 7, 12)), "current")
        self.assertEqual(svc._term_status(anchor, date(2026, 7, 13)), "ended")
        self.assertEqual(svc._term_status(anchor, date(2026, 3, 1)), "future")
        self.assertEqual(svc._term_status({"week1_monday": None, "week_count_hint": 19}, date(2026, 7, 3)), "unknown")


class OverviewAnchoringTests(unittest.TestCase):
    """学年学期/教学周对齐：动态本周、假期定位、进行中学期优先。"""

    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def _seed_term(self, year, term, *, week1="", max_week=19, cur_week=0, remote_prefix="r"):
        self.conn.execute(
            """
            INSERT INTO smart_classroom_course_schedule_meta (
                teacher_id, platform_code, academic_year, academic_term,
                cur_week, max_week, cur_xq, item_count, week1_monday_date,
                synced_at, created_at, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, 3, 1, ?, '2026-03-20T10:00:00',
                      '2026-03-20T10:00:00', '2026-03-20T10:00:00')
            """,
            (svc.SMART_PLATFORM_CODE, year, term, cur_week, max_week, week1),
        )
        _insert_item(
            self.conn,
            remote_id=f"{remote_prefix}-{year}-{term}",
            academic_year=year,
            academic_term=term,
            weeks_json=json.dumps(list(range(1, max_week + 1))),
        )

    def test_dynamic_current_week_overrides_stale_snapshot(self):
        # 同步时 cur_week=2（快照已过期）；锚点第 1 周周一 2026-03-02，
        # 今天 2026-03-25 → 实际第 4 周。
        self._seed_term("2025-2026", "2", week1="2026-03-02", max_week=19, cur_week=2)
        with mock.patch.object(svc, "_today_local", return_value=date(2026, 3, 25)):
            overview = svc.build_teacher_course_schedule_overview(self.conn, 1)
        self.assertEqual(overview["summary"]["cur_week"], 4)
        self.assertEqual(overview["summary"]["term_status"], "current")
        self.assertEqual(overview["selected_term"]["focus_week"], 4)
        self.assertTrue(overview["weeks"][3]["is_current"])
        self.assertFalse(overview["weeks"][1]["is_current"])  # 不再用过期快照第 2 周
        self.assertEqual(overview["weeks"][0]["date_range_label"], "3月2日 – 3月8日")
        self.assertEqual(overview["weeks"][0]["monday_date"], "2026-03-02")

    def test_holiday_focuses_last_week_of_ended_term(self):
        # 学期 19 周止于 2026-07-12；今天 2026-08-05（假期）→ 定位最后教学周。
        self._seed_term("2025-2026", "2", week1="2026-03-02", max_week=19, cur_week=19)
        with mock.patch.object(svc, "_today_local", return_value=date(2026, 8, 5)):
            overview = svc.build_teacher_course_schedule_overview(self.conn, 1)
        self.assertEqual(overview["summary"]["term_status"], "ended")
        self.assertEqual(overview["summary"]["cur_week"], 0)
        self.assertEqual(overview["selected_term"]["focus_week"], len(overview["weeks"]))
        self.assertFalse(any(week["is_current"] for week in overview["weeks"]))

    def test_current_term_selected_over_newer_future_term(self):
        # 新学期已同步但未开学（第 1 周周一 2026-09-07），进行中的旧学期优先。
        self._seed_term("2025-2026", "2", week1="2026-03-02", max_week=19, remote_prefix="cur")
        self._seed_term("2026-2027", "1", week1="2026-09-07", max_week=19, remote_prefix="next")
        with mock.patch.object(svc, "_today_local", return_value=date(2026, 6, 20)):
            overview = svc.build_teacher_course_schedule_overview(self.conn, 1)
        self.assertEqual(overview["selected_term"]["year"], "2025-2026")
        self.assertEqual(overview["selected_term"]["term"], "2")
        statuses = {(t["year"], t["term"]): t["status"] for t in overview["terms"]}
        self.assertEqual(statuses[("2026-2027", "1")], "future")

    def test_explicit_term_request_wins(self):
        self._seed_term("2025-2026", "2", week1="2026-03-02", max_week=19, remote_prefix="cur")
        self._seed_term("2025-2026", "1", max_week=18, remote_prefix="hist")
        with mock.patch.object(svc, "_today_local", return_value=date(2026, 6, 20)):
            overview = svc.build_teacher_course_schedule_overview(
                self.conn, 1, year="2025-2026", term="1"
            )
        self.assertEqual(overview["selected_term"]["term"], "1")
        # 历史学期无锚点 → unknown，退回快照 cur_week=0，无"本周"
        self.assertEqual(overview["summary"]["term_status"], "unknown")
        self.assertFalse(any(week["is_current"] for week in overview["weeks"]))

    def test_platform_anchor_overrides_sync_anchor(self):
        # 平台学期设置（含逐日日历的人工调整周次）优先于同步推算锚点。
        self._seed_term("2025-2026", "2", week1="2026-03-09", max_week=19, cur_week=2)
        platform_anchor = {
            ("2025-2026", "2"): {
                "semester_id": 55,
                "name": "2025-2026第二学期",
                "week1_monday": date(2026, 3, 2),
                "week_count": 19,
                "is_owned": True,
            }
        }
        self.conn.execute(
            """
            CREATE TABLE academic_semester_calendar_days (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                semester_id INTEGER, date TEXT, week_index INTEGER
            )
            """
        )
        # 平台日历把 2026-03-25 标为第 7 周（模拟调休/人工修正的权威值）。
        self.conn.execute(
            "INSERT INTO academic_semester_calendar_days (semester_id, date, week_index) VALUES (55, '2026-03-25', 7)"
        )
        with mock.patch.object(svc, "_today_local", return_value=date(2026, 3, 25)), \
                mock.patch.object(svc, "_load_platform_semester_anchors", return_value=platform_anchor):
            overview = svc.build_teacher_course_schedule_overview(self.conn, 1)
        self.assertEqual(overview["summary"]["anchor_source"], "platform")
        self.assertEqual(overview["summary"]["week1_monday"], "2026-03-02")
        self.assertEqual(overview["summary"]["cur_week"], 7)
        self.assertEqual(overview["selected_term"]["focus_week"], 7)


if __name__ == "__main__":
    unittest.main()
