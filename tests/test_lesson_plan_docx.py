"""Tests for the 教案 docx builder + Markdown parser.

Validates the document structure (cover table + one 8x4 table per session, the
旁批 column, and a nested table rendered from Markdown inside 教学内容及过程)
without needing LibreOffice (PDF/PNG conversion is exercised separately).
"""

import unittest
from io import BytesIO

from docx import Document
from docx.oxml.ns import qn

from classroom_app.services import lesson_plan_docx_service as docx_svc
from classroom_app.services import lesson_plan_markdown as md


_PROCESS_MD = """\
## 一、教学导入（8分钟）
回顾上节课内容。**高阶提问**：为什么需要规划？

## 二、讲授新课
| 教学环节 | 教学活动（教师引导） | 学生活动（主体） | 设计意图 |
| :--- | :--- | :--- | :--- |
| 任务1 | 手把手演示 | 动手实践 | 高阶性 |
| 任务2 | 设置陷阱 | 自主排错 | 挑战度 |

## 三、教学小结
- 知识梳理
- 反思展望
"""

_PLAN = {
    "cover": {
        "course_name": "服务器配置与管理",
        "course_category": "专业限选课程",
        "credits": "3.0",
        "total_hours": "48",
        "teacher_name": "张老师",
        "teaching_unit": "数字科技学院",
        "class_name": "软工2406班",
        "textbook": "《Linux服务器运维管理》",
        "publisher": "清华大学出版社",
        "semester_label": "2025—2026学年第一学期",
        "school_name": "广西外国语学院",
    },
    "sessions": [
        {
            "index": 1,
            "schedule": {"text": "2025年09月01日 第一周 星期一 第6-7节"},
            "chapter": "第1章 认识Linux",
            "objectives": "知识目标：了解Linux\n能力目标：能够安装系统",
            "key_points": "开源协议",
            "difficulties": "内核与发行版区别",
            "methods": "讲授法、案例法",
            "means": "PPT、虚拟机",
            "process": _PROCESS_MD,
            "side_notes": "提前准备饼图",
            "post_notes": "类比效果好",
        },
        {
            "index": 2,
            "schedule": {"text": "2025年09月05日 第一周 星期五 第2-3节"},
            "chapter": "第2章 系统安装",
            "objectives": "掌握虚拟机部署",
            "key_points": "分区",
            "difficulties": "网络配置",
            "methods": "任务驱动",
            "means": "PPT",
            "process": "## 一、教学导入\n情景创设。",
            "side_notes": "",
            "post_notes": "",
        },
    ],
}


class MarkdownParserTests(unittest.TestCase):
    def test_parses_heading_table_and_list(self):
        blocks = md.parse_blocks(_PROCESS_MD)
        types = [b["type"] for b in blocks]
        self.assertIn("heading", types)
        self.assertIn("table", types)
        self.assertIn("ul", types)
        table = next(b for b in blocks if b["type"] == "table")
        self.assertEqual(len(table["header"]), 4)
        self.assertEqual(len(table["rows"]), 2)

    def test_inline_bold(self):
        runs = md.inline_runs("普通**加粗**普通")
        self.assertTrue(any(r["bold"] and r["text"] == "加粗" for r in runs))

    def test_html_escapes(self):
        html_out = md.markdown_to_html("a < b & c")
        self.assertIn("&lt;", html_out)
        self.assertIn("&amp;", html_out)


class DocxBuilderTests(unittest.TestCase):
    def setUp(self):
        self.doc_bytes = docx_svc.build_lesson_plan_docx(_PLAN)
        self.document = Document(BytesIO(self.doc_bytes))

    def test_returns_bytes(self):
        self.assertIsInstance(self.doc_bytes, bytes)
        self.assertGreater(len(self.doc_bytes), 1000)

    def test_table_count_cover_plus_sessions(self):
        # python-docx .tables is top-level only: 1 cover + 2 session tables.
        self.assertEqual(len(self.document.tables), 3)

    def test_sessions_are_page_separated(self):
        breaks = self.document.element.body.findall(".//" + qn("w:br"))
        page_breaks = [br for br in breaks if br.get(qn("w:type")) == "page"]
        self.assertGreaterEqual(len(page_breaks), len(_PLAN["sessions"]) - 1)

    def test_markdown_table_rendered_as_nested_table(self):
        # The PBL Markdown table in session 1's 教学内容及过程 cell becomes a
        # nested docx table somewhere inside the session tables.
        nested_found = False
        for table in self.document.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.tables:
                        for nested in cell.tables:
                            header = " ".join(c.text for c in nested.rows[0].cells)
                            if "教学环节" in header:
                                nested_found = True
        self.assertTrue(nested_found, "PBL markdown table not rendered as nested docx table")

    def test_title_and_semester_present(self):
        full_text = "\n".join(p.text for p in self.document.paragraphs)
        self.assertIn("教", full_text)  # 教  案 title
        self.assertIn("2025—2026学年第一学期", full_text)

    def test_session_table_has_required_labels(self):
        all_cell_text = []
        for table in self.document.tables:
            for row in table.rows:
                for cell in row.cells:
                    all_cell_text.append(cell.text)
        joined = "\n".join(all_cell_text)
        for label in ("授课时间", "授课章节", "教学目的和要求", "教学重点和难点",
                      "教学方法和手段", "教学内容及过程", "旁批", "教学后记"):
            self.assertIn(label, joined, f"missing label {label}")

    def test_cover_fields_present(self):
        joined = "\n".join(c.text for t in self.document.tables for r in t.rows for c in r.cells)
        self.assertIn("服务器配置与管理", joined)
        self.assertIn("清华大学出版社", joined)
        self.assertIn("软工2406班", joined)


if __name__ == "__main__":
    unittest.main()
