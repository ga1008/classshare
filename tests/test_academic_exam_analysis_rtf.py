from io import BytesIO
import unittest
from unittest.mock import patch

from PIL import Image, PngImagePlugin

from classroom_app.services import academic_exam_analysis_rtf_service as service
from classroom_app.services.academic_final_material_source_service import NativeAcademicSourceError


def _unicode_rtf(text: str, fallback: bytes = b"?") -> bytes:
    """Encode synthetic text without depending on a workstation code page."""
    result = bytearray()
    for character in text:
        if character in "{}\\":
            result.extend(b"\\" + character.encode("ascii"))
        elif 32 <= ord(character) < 127:
            result.extend(character.encode("ascii"))
        else:
            utf16 = character.encode("utf-16-le")
            for offset in range(0, len(utf16), 2):
                code_unit = int.from_bytes(utf16[offset:offset + 2], "little", signed=True)
                result.extend(b"\\u" + str(code_unit).encode("ascii") + fallback)
    return bytes(result)


def _png(size: tuple[int, int], color: str, binary_delimiters: bool = False) -> bytes:
    output = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    if binary_delimiters:
        metadata.add_text("synthetic-rtf-data", "These are image bytes: } { \\ bin.")
    Image.new("RGB", size, color).save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


def _picture(data: bytes, size: tuple[int, int]) -> bytes:
    width, height = size
    return (
        b"{\\pict\\pngblip\\picw" + str(width).encode("ascii")
        + b"\\pich" + str(height).encode("ascii")
        + b"\\picwgoal" + str(width * 15).encode("ascii")
        + b"\\pichgoal" + str(height * 15).encode("ascii")
        + b"\\bin" + str(len(data)).encode("ascii") + b" " + data + b"}"
    )


def _synthetic_rtf(
    pictures: tuple[bytes, ...],
    course: str = "合成测试课程",
    statistics: str = "平均分 80.00 人数 5 及格率 100.00%",
    fallback: bytes = b"?",
    analysis: str = "",
    review: str = "",
    check: str = "",
    marking_check: str = "",
    hidden_text: str = "synthetic invisible slot",
) -> bytes:
    """Synthetic rows use the public native slot map, with no personal data."""
    encode = lambda text: _unicode_rtf(text, fallback)
    rows = [
        ["广西外国语学院课程试卷分析表"],
        ["2026-2027 学年第 1 学期"],
        [hidden_text],
        ["课程名称", course, "学时数", "32", "开课单位", "合成教学单位"],
        ["教师姓名", "合成姓名", "课程性质", "选修", check, "必修", ""],
        ["命题形式(打√)", "试题库", "", "试卷库", "", "教师组题", ""],
        ["考试形式(打√)", "开卷", "", "闭卷", "", "教考分离(打√)", "是", "", "否", ""],
        ["学生班级", "合成班级"],
        ["", ""],
        [_picture(pictures[0], (29, 95)), "分数段", "<60", "60-69", "70-79", "80-89", "90-100"],
        ["", "人数", "1", "1", "1", "1", "1"],
        ["", "比例", "20.00%", "20.00%", "20.00%", "20.00%", "20.00%"],
        ["", "平均分", statistics, "标准差", "0.0"],
        ["", "最高分", "100", "最低分", "60", "及格率", "100.00%"],
        ["阅卷形式(打√)", "本人阅卷" + marking_check, "同行阅卷", "集体阅卷", "机器阅卷", "其他"],
        ["学生成绩分布图"],
        [_picture(pictures[1], (643, 200))],
        ["", "简要分析试题结构，成绩分布，学生掌握情况及其主要原因，提出教学改进意见与措施"],
        [_picture(pictures[2], (29, 190)), analysis],
        ["系（教研室）审核意见：  ", "教学院长审核意见：  "],
        [review, ""],
        ["签字：        ", "签字：        "],
        ["注：1、本表一式两份，一份交学生所在学院，一份交开课学院存档。"],
    ]
    parts = [
        b"{\\rtf1\\ansi\\ansicpg936\\uc1\\deff0",
        b"{\\fonttbl{\\f0\\fnil\\fcharset134 SimSun;}}",
        b"{\\colortbl;\\red0\\green0\\blue0;}",
        b"{\\stylesheet{\\s0\\f0\\fs24 Normal;}}",
        b"\\paperw11906\\paperh16838\\margl1080\\margr1080\\margt720\\margb720",
    ]
    for row_index, row in enumerate(rows):
        parts.append(b"\\trowd\\trgaph108\\trleft0\\trrh360")
        for column_index in range(len(row)):
            parts.append(b"\\clbrdrt\\brdrs\\brdrw10\\clbrdrb\\brdrs\\brdrw10\\cellx")
            parts.append(str(1800 * (column_index + 1)).encode("ascii"))
        for value in row:
            parts.append(b"\\pard\\plain\\intbl\\qc\\f0\\fs" + (b"32 " if row_index == 0 else b"24 "))
            parts.append(value if isinstance(value, bytes) else encode(value))
            parts.append(b"\\cell ")
        parts.append(b"\\row")
    parts.append(b"}")
    return b"".join(parts)


class AcademicExamAnalysisRtfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pictures = (
            _png((29, 95), "white", binary_delimiters=True),
            _png((643, 200), "blue"),
            _png((29, 190), "black"),
        )
        cls.source = _synthetic_rtf(cls.pictures)
        layout_hash, resource_hash, _, _, static_hash, _ = service._inspect_rtf(cls.source)
        cls.layout_hash = layout_hash
        cls.resource_hash = resource_hash
        cls.static_hash = static_hash

    def setUp(self):
        self.layout_patch = patch.object(service, "_EXPECTED_LAYOUT_SHA256", self.layout_hash)
        self.resource_patch = patch.object(service, "_EXPECTED_RESOURCE_SHA256", self.resource_hash)
        self.static_patch = patch.object(service, "_EXPECTED_STATIC_TEXT_SHA256", self.static_hash)
        self.layout_patch.start()
        self.resource_patch.start()
        self.static_patch.start()
        self.addCleanup(self.layout_patch.stop)
        self.addCleanup(self.resource_patch.stop)
        self.addCleanup(self.static_patch.stop)

    def assert_rejected(self, content: bytes):
        with self.assertRaises(NativeAcademicSourceError):
            service.inspect_native_exam_analysis_source(content)

    def test_binary_picture_bytes_are_preserved_in_full(self):
        self.assertIn(b"} { \\", self.pictures[0])
        inspected = service.inspect_native_exam_analysis_source(self.source)
        self.assertEqual(self.pictures[0], inspected["vertical_label_png"])
        self.assertEqual(self.pictures[1], inspected["chart_png"])
        self.assertEqual(self.pictures[2], inspected["analysis_label_png"])

    def test_course_statistics_and_unicode_text_can_change(self):
        content = _synthetic_rtf(
            self.pictures,
            course="另一门合成课程 {示例} \\ 实验",
            statistics="平均分 92.50 人数 12 及格率 91.67% 课程分析更新",
        )
        inspected = service.inspect_native_exam_analysis_source(content)
        self.assertEqual(self.pictures[1], inspected["chart_png"])
        _, _, visible_text, _, _, _ = service._inspect_rtf(content)
        self.assertIn("另一门合成课程 {示例} \\ 实验", visible_text)
        self.assertIn("92.50", visible_text)

    def test_updated_chart_pixels_are_returned_without_reencoding(self):
        replacement_chart = _png((643, 200), "green", binary_delimiters=True)
        content = _synthetic_rtf((self.pictures[0], replacement_chart, self.pictures[2]))
        inspected = service.inspect_native_exam_analysis_source(content)
        self.assertEqual(replacement_chart, inspected["chart_png"])

    def test_plain_ascii_course_and_statistics_can_change(self):
        content = _synthetic_rtf(
            self.pictures,
            course="Synthetic course B",
            statistics="Average 88.25 Count 8 Pass 87.50%",
        )
        inspected = service.inspect_native_exam_analysis_source(content)
        self.assertEqual(self.pictures[1], inspected["chart_png"])

    def test_dynamic_narrative_reviews_and_choice_checks_can_change(self):
        content = _synthetic_rtf(
            self.pictures, analysis="这是合成分析内容。", review="合成审核意见。",
            check="√", marking_check=" √",
        )
        self.assertEqual(self.pictures[1], service.inspect_native_exam_analysis_source(content)["chart_png"])

    def test_hidden_source_value_can_change_and_is_returned_verbatim(self):
        for hidden_text in ("32", "0", "", "  47  "):
            with self.subTest(hidden_text=hidden_text):
                content = _synthetic_rtf(self.pictures, hidden_text=hidden_text)
                inspected = service.inspect_native_exam_analysis_source(content)
                self.assertEqual(hidden_text, inspected["hidden_text"])
                self.assertEqual(self.pictures[1], inspected["chart_png"])

    def test_period_numbers_can_change_but_period_labels_cannot(self):
        content = self.source.replace(b"2026-2027", b"2028-2029", 1)
        content = content.replace(_unicode_rtf("第 1 学期"), _unicode_rtf("第 2 学期"), 1)
        self.assertEqual(self.pictures[1], service.inspect_native_exam_analysis_source(content)["chart_png"])
        self.assert_rejected(content.replace(_unicode_rtf("学期"), _unicode_rtf("学段"), 1))

    def test_fixed_cell_labels_notes_and_signature_padding_cannot_change(self):
        for original, replacement in (
            ("课程性质", "课程类型"),
            ("必修", "必选"),
            ("分数段", "分数区间"),
            ("60-69", "60-68"),
            ("本人阅卷", "教师阅卷"),
            ("简要分析试题结构", "简要介绍试题结构"),
            ("教学院长审核意见：  ", "教学院长审核意见： "),
            ("签字：        ", "签字：       "),
            ("本表一式两份", "本表一式三份"),
            ("一份交开课学院存档。", "一份交开课学院存档！"),
        ):
            with self.subTest(original=original):
                content = self.source.replace(_unicode_rtf(original), _unicode_rtf(replacement), 1)
                self.assertNotEqual(content, self.source)
                layout, resources, _, _, static, _ = service._inspect_rtf(content)
                # These are text-only changes. The independent static-cell
                # fingerprint must reject them even when layout/fonts match.
                self.assertEqual((layout, resources), (self.layout_hash, self.resource_hash))
                self.assertNotEqual(static, self.static_hash)
                self.assert_rejected(content)

    def test_fixed_label_cannot_be_recovered_from_a_dynamic_slot(self):
        content = _synthetic_rtf(self.pictures, course="课程性质")
        # The dynamic course value appears first; change only the later fixed
        # label, so a document-wide substring search would still find it.
        before, label, after = content.rpartition(_unicode_rtf("课程性质"))
        self.assertTrue(label)
        content = before + _unicode_rtf("课程类型") + after
        self.assertIn("课程性质", service._inspect_rtf(content)[2])
        self.assert_rejected(content)

    def test_unicode_fallback_can_be_an_escaped_rtf_delimiter(self):
        for fallback in (b"\\{", b"\\}", b"\\\\"):
            with self.subTest(fallback=fallback):
                content = _synthetic_rtf(self.pictures, fallback=fallback)
                inspected = service.inspect_native_exam_analysis_source(content)
                self.assertEqual(self.pictures[1], inspected["chart_png"])
                _, _, visible_text, _, _, _ = service._inspect_rtf(content)
                self.assertIn("广西外国语学院课程试卷分析表", visible_text)

    def test_border_font_size_and_page_margin_changes_are_rejected(self):
        for before, after in (
            (b"\\brdrw10", b"\\brdrw20"),
            (b"\\fs32", b"\\fs28"),
            (b"\\margl1080", b"\\margl1200"),
            (b"\\cellx1800", b"\\cellx1801"),
            (b"\\trrh360", b"\\trrh361"),
            (b"\\picwgoal435", b"\\picwgoal436"),
        ):
            with self.subTest(change=after):
                self.assert_rejected(self.source.replace(before, after, 1))

    def test_font_color_and_stylesheet_resources_are_validated(self):
        for before, after in (
            (b"SimSun;", b"SimHei;"),
            (b"\\red0", b"\\red64"),
            (b"\\colortbl;", b"\\colortbl;;"),
            (b"Normal;", b"Different Style;"),
        ):
            with self.subTest(change=after):
                self.assert_rejected(self.source.replace(before, after, 1))

    def test_missing_required_form_label_is_rejected(self):
        content = self.source.replace(_unicode_rtf("课程名称"), _unicode_rtf("占位字段"), 1)
        self.assert_rejected(content)

    def test_truncated_and_invalid_binary_lengths_are_rejected(self):
        first_binary = b"\\bin" + str(len(self.pictures[0])).encode("ascii") + b" "
        for content in (
            self.source.replace(first_binary, b"\\bin999999 ", 1),
            self.source.replace(first_binary, b"\\bin-1 ", 1),
            self.source.replace(first_binary, b"\\bin ", 1),
            self.source[:self.source.index(self.pictures[0]) + 15],
        ):
            with self.subTest(content_length=len(content)):
                self.assert_rejected(content)

    def test_unbalanced_or_non_rtf_document_is_rejected(self):
        for content in (b"", b"Not an RTF document", self.source[:-1], self.source + b"}"):
            with self.subTest(content_length=len(content)):
                self.assert_rejected(content)

    def test_corrupted_png_is_rejected_even_when_length_is_valid(self):
        corrupted = bytearray(self.pictures[1])
        corrupted[0] ^= 0xFF
        self.assert_rejected(self.source.replace(self.pictures[1], bytes(corrupted), 1))

    def test_png_with_corrupted_chunk_crc_is_rejected(self):
        corrupted = bytearray(self.pictures[1])
        # The IHDR CRC occupies bytes 29-32; preserve the signature and dimensions.
        corrupted[32] ^= 0xFF
        self.assert_rejected(self.source.replace(self.pictures[1], bytes(corrupted), 1))

    def test_text_after_binary_picture_is_rejected(self):
        corrupted = self.source.replace(self.pictures[1], self.pictures[1] + b"unexpected picture data", 1)
        self.assert_rejected(corrupted)

    def test_unexpected_png_dimensions_are_rejected(self):
        pictures = (self.pictures[0], _png((644, 200), "blue"), self.pictures[2])
        self.assert_rejected(_synthetic_rtf(pictures))


if __name__ == "__main__":
    unittest.main()
