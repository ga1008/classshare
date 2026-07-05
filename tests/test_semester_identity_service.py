"""Unit tests for the canonical semester identity module (学年学期 single source)."""

import unittest
from datetime import date

from classroom_app.services import semester_identity_service as sis


class CanonicalNameTests(unittest.TestCase):
    def test_canonical_name(self):
        self.assertEqual(sis.canonical_semester_name(2025, 2), "2025-2026第二学期")
        self.assertEqual(sis.canonical_semester_name(2025, 1), "2025-2026第一学期")
        self.assertEqual(sis.canonical_semester_name("2024", "1"), "2024-2025第一学期")

    def test_identity_properties(self):
        identity = sis.SemesterIdentity(2025, 2)
        self.assertEqual(identity.canonical_name, "2025-2026第二学期")
        self.assertEqual(identity.code, "2025-2026-2")
        self.assertEqual(identity.sort_key, (2025, 2))
        self.assertEqual(identity.as_year_term(), ("2025-2026", "2"))
        self.assertEqual(identity.as_xnm_xqm(), ("2025", "12"))


class ParseTests(unittest.TestCase):
    def test_parses_every_legacy_shape_to_same_identity(self):
        shapes = [
            "2025-2026第二学期",
            "2025-2026学年第2学期",
            "2025-2026学年 第2学期",
            "2025-2026第2学期",
            "2025-2026-2",
            "2025—2026第二学期",
            "  2025-2026 第二学期  ",
        ]
        for shape in shapes:
            with self.subTest(shape=shape):
                identity = sis.parse_semester_identity(shape)
                self.assertIsNotNone(identity, shape)
                self.assertEqual(identity.code, "2025-2026-2")

    def test_term1_shapes(self):
        for shape in ["2025-2026第一学期", "2025-2026学年第1学期", "2025-2026-1"]:
            with self.subTest(shape=shape):
                identity = sis.parse_semester_identity(shape)
                self.assertIsNotNone(identity, shape)
                self.assertEqual(identity.code, "2025-2026-1")

    def test_unparseable_returns_none(self):
        self.assertIsNone(sis.parse_semester_identity(""))
        self.assertIsNone(sis.parse_semester_identity(None))
        self.assertIsNone(sis.parse_semester_identity("第一学期"))  # 无学年
        self.assertIsNone(sis.parse_semester_identity("P03-2026"))
        self.assertIsNone(sis.parse_semester_identity("2025-2026"))  # 无学期号

    def test_first_resolvable_source_wins(self):
        identity = sis.parse_semester_identity("", "P03", "2024-2025第一学期")
        self.assertEqual(identity.code, "2024-2025-1")

    def test_pair_source(self):
        identity = sis.parse_semester_identity(("2025-2026", "2"))
        self.assertEqual(identity.code, "2025-2026-2")


class ConversionTests(unittest.TestCase):
    def test_identity_from_year_term(self):
        self.assertEqual(sis.identity_from_year_term("2025-2026", "2").code, "2025-2026-2")
        self.assertEqual(sis.identity_from_year_term("2025-2026", "1").code, "2025-2026-1")
        self.assertIsNone(sis.identity_from_year_term("", "2"))
        self.assertIsNone(sis.identity_from_year_term("2025-2026", ""))

    def test_identity_from_xnm_xqm(self):
        self.assertEqual(sis.identity_from_xnm_xqm("2024", "12").code, "2024-2025-2")
        self.assertEqual(sis.identity_from_xnm_xqm("2024", "3").code, "2024-2025-1")
        self.assertIsNone(sis.identity_from_xnm_xqm("", "12"))

    def test_infer_from_dates(self):
        self.assertEqual(sis.infer_identity_from_dates("2026-03-09").code, "2025-2026-2")
        self.assertEqual(sis.infer_identity_from_dates("2025-09-01").code, "2025-2026-1")
        self.assertEqual(sis.infer_identity_from_dates("2026-01-05").code, "2025-2026-1")
        self.assertEqual(sis.infer_identity_from_dates(date(2026, 3, 1)).code, "2025-2026-2")

    def test_infer_prefers_parseable_name_over_dates(self):
        # 名称能解析出第一学期就用名称，忽略日期推断
        self.assertEqual(
            sis.infer_identity_from_dates("2026-03-01", name="2025-2026第一学期").code,
            "2025-2026-1",
        )


class GroupingTests(unittest.TestCase):
    def test_semester_group_collapses_variants(self):
        key_a, label_a = sis.semester_group("2025-2026学年第2学期")
        key_b, label_b = sis.semester_group("2025-2026第二学期")
        self.assertEqual(key_a, key_b)
        self.assertEqual(label_a, "2025-2026第二学期")
        self.assertEqual(label_b, "2025-2026第二学期")

    def test_semester_group_orphan_term1_normalizes(self):
        # "2025-2026-1" 现在应规范成第一学期，而不是 raw 单列
        key, label = sis.semester_group("2025-2026-1")
        self.assertEqual(key, "2025-2026-1")
        self.assertEqual(label, "2025-2026第一学期")

    def test_semester_group_unset(self):
        self.assertEqual(sis.semester_group("", None), ("none", "未设学期"))

    def test_semester_group_raw_when_unparseable(self):
        key, label = sis.semester_group("P03-runtime")
        self.assertEqual(key, "raw:P03-runtime")
        self.assertEqual(label, "P03-runtime")

    def test_semester_group_prefers_first_source(self):
        # semester_id 的 name 优先于 offering.semester 文本
        key, label = sis.semester_group("2024-2025第二学期", "2025-2026第一学期")
        self.assertEqual(label, "2024-2025第二学期")


class NormalizeTests(unittest.TestCase):
    def test_normalize_semester_text(self):
        self.assertEqual(sis.normalize_semester_text("2025-2026学年第2学期"), "2025-2026第二学期")
        self.assertEqual(sis.normalize_semester_text("2025-2026-1"), "2025-2026第一学期")

    def test_normalize_keeps_original_when_unparseable(self):
        self.assertEqual(sis.normalize_semester_text("第一学期"), "第一学期")
        self.assertEqual(sis.normalize_semester_text("", fallback="未设学期"), "未设学期")


if __name__ == "__main__":
    unittest.main()
