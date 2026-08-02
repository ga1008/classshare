"""Official DOCX renderers for JWXT-downloaded final materials."""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from .academic_final_material_service import (
    ACADEMIC_EXAM_ANALYSIS_LABEL,
    ACADEMIC_EXAM_ANALYSIS_TYPE,
    ACADEMIC_GRADE_REGISTER_LABEL,
    ACADEMIC_GRADE_REGISTER_TYPE,
)


_GRADE_REGISTER_TEMPLATE_PATH = Path(__file__).with_name("assets") / "gxufl_academic_grade_register_template.docx"
_GRADE_REGISTER_LEFT_CAPACITY = 40
_GRADE_REGISTER_RIGHT_CAPACITY = 30
_GRADE_REGISTER_MAX_STUDENTS = _GRADE_REGISTER_LEFT_CAPACITY + _GRADE_REGISTER_RIGHT_CAPACITY
_GRADE_REGISTER_STUDENT_KEYS = (
    "student_number",
    "student_name",
    "ordinary_score",
    "midterm_score",
    "experiment_online_score",
    "final_exam_score",
    "final_score",
    "remark",
)
_GRADE_REGISTER_LEFT_COLUMNS = (0, 1, 2, 3, 4, 5, 6, 7)
_GRADE_REGISTER_RIGHT_COLUMNS = (8, 10, 11, 12, 13, 14, 15, 16)


def _payload(parse_payload: dict[str, Any]) -> dict[str, Any]:
    export_payload = parse_payload.get("export_payload")
    return export_payload if isinstance(export_payload, dict) else parse_payload


def _safe_filename(value: Any) -> str:
    return re.sub(r'[\\/:*?"<>|\r\n]+', "_", str(value or "")).strip(" ._")[:100] or "期末材料"


def build_academic_final_material_filename(parse_payload: dict[str, Any], template_key: str) -> str:
    payload = _payload(parse_payload)
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    label = ACADEMIC_GRADE_REGISTER_LABEL if template_key == ACADEMIC_GRADE_REGISTER_TYPE else ACADEMIC_EXAM_ANALYSIS_LABEL
    course = _safe_filename(fields.get("course_name") or "课程")
    class_name = _safe_filename(fields.get("class_name") or "班级")
    return f"{label}-{course}-{class_name}.docx"


def _set_run_font(run: Any, size: float, *, bold: bool = False, name: str = "宋体") -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt

    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), name)


def _set_cell_text(
    cell: Any,
    value: Any,
    *,
    size: float = 8.5,
    bold: bool = False,
    align: int = 1,
    name: str = "宋体",
) -> None:
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT

    cell.text = ""
    paragraph = cell.paragraphs[0]
    paragraph.alignment = align
    paragraph.paragraph_format.space_before = 0
    paragraph.paragraph_format.space_after = 0
    paragraph.paragraph_format.line_spacing = 1
    run = paragraph.add_run("" if value is None else str(value))
    _set_run_font(run, size, bold=bold, name=name)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def _set_cell_width(cell: Any, width_cm: float) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm

    cell.width = Cm(width_cm)
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.find(qn("w:tcW"))
    if tc_w is None:
        tc_w = OxmlElement("w:tcW")
        tc_pr.append(tc_w)
    tc_w.set(qn("w:w"), str(round(width_cm * 567)))
    tc_w.set(qn("w:type"), "dxa")


def _set_table_borders(table: Any, *, size: int = 6) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tbl_pr = table._tbl.tblPr
    existing = tbl_pr.find(qn("w:tblBorders"))
    if existing is not None:
        tbl_pr.remove(existing)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), str(size))
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), "000000")
        borders.append(element)
    tbl_pr.append(borders)

def _add_title(document: Any, title: str, fields: dict[str, Any], *, size: float = 16) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_before = 0
    paragraph.paragraph_format.space_after = Pt(2)
    _set_run_font(paragraph.add_run(title), size, bold=True)

    period = document.add_paragraph()
    period.alignment = WD_ALIGN_PARAGRAPH.CENTER
    period.paragraph_format.space_before = 0
    period.paragraph_format.space_after = Pt(3)
    academic_year = str(fields.get("academic_year") or "")
    semester = str(fields.get("semester") or "")
    _set_run_font(period.add_run(f"{academic_year}学年{semester}".strip()), 10.5, bold=True)


def _add_image_to_cell(
    cell: Any,
    path_value: Any,
    *,
    width_cm: float,
    height_cm: float | None = None,
    prefix: str = "",
) -> bool:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm

    path = Path(str(path_value or ""))
    if not path.is_file():
        return False
    cell.text = ""
    paragraph = cell.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_before = 0
    paragraph.paragraph_format.space_after = 0
    if prefix:
        run = paragraph.add_run(prefix)
        _set_run_font(run, 8)
    kwargs = {"width": Cm(width_cm)}
    if height_cm:
        kwargs["height"] = Cm(height_cm)
    paragraph.add_run().add_picture(str(path), **kwargs)
    return True


def _score_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    parsed = float(value)
    if abs(parsed - round(parsed)) < 0.001:
        return str(int(round(parsed)))
    return f"{parsed:.2f}"


def _set_template_cell_text(cell: Any, value: Any) -> None:
    from docx.oxml.ns import qn

    text_nodes = list(cell._tc.iter(qn("w:t")))
    text = "" if value is None else str(value)
    if not text_nodes:
        run = cell.paragraphs[0].add_run(text)
        _set_run_font(run, 9, name="微软雅黑")
        return
    text_nodes[0].text = text
    for node in text_nodes[1:]:
        node.text = ""


def _academic_period_text(fields: dict[str, Any]) -> str:
    academic_year = str(fields.get("academic_year") or "").strip()
    semester = str(fields.get("semester") or "").strip()
    if academic_year and not academic_year.endswith("学年"):
        academic_year = f"{academic_year}学年"
    if semester and not semester.endswith("学期"):
        semester = f"{semester}学期"
    return f"{academic_year}{semester}"


def _grade_student_value(student: dict[str, Any] | None, key: str) -> str:
    if not student:
        return ""
    value = student.get(key)
    if key == "final_score" and value not in (None, ""):
        parsed = float(value)
        return "100" if abs(parsed - 100) < 0.001 else f"{parsed:.2f}"
    if key in {
        "ordinary_score",
        "midterm_score",
        "experiment_online_score",
        "final_exam_score",
    }:
        return _score_text(value)
    return "" if value is None else str(value)


def _score_band_counts(students: list[dict[str, Any]]) -> list[int]:
    counts = [0] * 8
    for student in students:
        try:
            score = float(student.get("final_exam_score"))
        except (TypeError, ValueError):
            continue
        if score >= 90:
            index = 0
        elif score >= 80:
            index = 1
        elif score >= 70:
            index = 2
        elif score >= 60:
            index = 3
        elif score >= 50:
            index = 4
        elif score >= 40:
            index = 5
        elif score >= 30:
            index = 6
        else:
            index = 7
        counts[index] += 1
    return counts


def _grade_status_counts(students: list[dict[str, Any]]) -> dict[str, int]:
    aliases = {
        "免考(修)": ("免考", "免修"),
        "缓考": ("缓考",),
        "作弊": ("作弊",),
        "旷考": ("旷考",),
        "交流生": ("交流生",),
        "借读生": ("借读生",),
    }
    counts = {key: 0 for key in aliases}
    for student in students:
        remark = str(student.get("remark") or "").strip()
        for key, values in aliases.items():
            if any(value in remark for value in values):
                counts[key] += 1
                break
    return counts


def _add_grade_signature_overlay(document: Any, table: Any, signature_path: Any) -> None:
    path = Path(str(signature_path or ""))
    if not path.is_file():
        return

    from docx.oxml import parse_xml

    relationship_id, _image = document.part.get_or_add_image(str(path))
    pict = parse_xml(
        f"""
        <w:pict
            xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            xmlns:v="urn:schemas-microsoft-com:vml"
            xmlns:o="urn:schemas-microsoft-com:office:office"
            w14:anchorId="4E0449E0">
          <v:shapetype id="_x0000_t75" coordsize="21600,21600" o:spt="75"
              o:preferrelative="t" path="m@4@5l@4@11@9@11@9@5xe" filled="f" stroked="f">
            <v:stroke joinstyle="miter"/>
            <v:formulas>
              <v:f eqn="if lineDrawn pixelLineWidth 0"/><v:f eqn="sum @0 1 0"/>
              <v:f eqn="sum 0 0 @1"/><v:f eqn="prod @2 1 2"/>
              <v:f eqn="prod @3 21600 pixelWidth"/><v:f eqn="prod @3 21600 pixelHeight"/>
              <v:f eqn="sum @0 0 1"/><v:f eqn="prod @6 1 2"/>
              <v:f eqn="prod @7 21600 pixelWidth"/><v:f eqn="sum @8 21600 0"/>
              <v:f eqn="prod @7 21600 pixelHeight"/><v:f eqn="sum @10 21600 0"/>
            </v:formulas>
            <v:path o:extrusionok="f" gradientshapeok="t" o:connecttype="rect"/>
            <o:lock v:ext="edit" aspectratio="t"/>
          </v:shapetype>
          <v:shape id="_x0000_s2050" type="#_x0000_t75"
              style="position:absolute;margin-left:18.4pt;margin-top:3.15pt;width:60.55pt;height:21.2pt;z-index:251659264;mso-position-horizontal-relative:text;mso-position-vertical-relative:text">
            <v:imagedata r:id="{relationship_id}" o:title=""/>
          </v:shape>
        </w:pict>
        """
    )
    anchor_paragraph = table.rows[45].cells[6].paragraphs[0]
    anchor_run = anchor_paragraph.runs[0] if anchor_paragraph.runs else anchor_paragraph.add_run()
    anchor_run._r.append(pict)


def build_grade_register_docx(parse_payload: dict[str, Any]) -> bytes:
    from docx import Document

    payload = _payload(parse_payload)
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    structured = payload.get("structured") if isinstance(payload.get("structured"), dict) else {}
    students = structured.get("students") if isinstance(structured.get("students"), list) else []
    students = [student for student in students if isinstance(student, dict)]
    if len(students) > _GRADE_REGISTER_MAX_STUDENTS:
        raise ValueError(f"期末成绩登记表模板最多容纳 {_GRADE_REGISTER_MAX_STUDENTS} 名学生，当前为 {len(students)} 名。")
    if not _GRADE_REGISTER_TEMPLATE_PATH.is_file():
        raise RuntimeError("期末成绩登记表官方版式模板缺失，无法导出。")

    document = Document(str(_GRADE_REGISTER_TEMPLATE_PATH))
    if len(document.tables) != 1 or len(document.tables[0].rows) != 47:
        raise RuntimeError("期末成绩登记表官方版式模板结构已损坏。")
    table = document.tables[0]

    metadata_cells = {
        (0, 0): "广西外国语学院期末成绩登记表",
        (1, 0): _academic_period_text(fields),
        (2, 0): f"开课部门：{fields.get('department') or ''}",
        (2, 4): f"班级：{fields.get('class_name') or ''}",
        (2, 10): f"任课教师：{fields.get('teacher_name') or ''}",
        (2, 14): f"学分：{fields.get('credits') or ''}",
        (3, 0): f"课程名称：{fields.get('course_name') or ''}",
        (3, 4): f"课程性质：{fields.get('course_nature') or ''}",
        (3, 8): f"考核方式：{fields.get('assessment_method') or ''}",
        (3, 11): f"填表日期：{fields.get('date') or ''}",
    }
    for (row_index, column_index), value in metadata_cells.items():
        _set_template_cell_text(table.rows[row_index].cells[column_index], value)

    padded_students: list[dict[str, Any] | None] = [*students]
    padded_students.extend([None] * (_GRADE_REGISTER_MAX_STUDENTS - len(padded_students)))
    for slot in range(_GRADE_REGISTER_LEFT_CAPACITY):
        row = table.rows[5 + slot]
        student = padded_students[slot]
        for key, column_index in zip(_GRADE_REGISTER_STUDENT_KEYS, _GRADE_REGISTER_LEFT_COLUMNS):
            _set_template_cell_text(row.cells[column_index], _grade_student_value(student, key))
    for slot in range(_GRADE_REGISTER_RIGHT_CAPACITY):
        row = table.rows[5 + slot]
        student = padded_students[_GRADE_REGISTER_LEFT_CAPACITY + slot]
        for key, column_index in zip(_GRADE_REGISTER_STUDENT_KEYS, _GRADE_REGISTER_RIGHT_COLUMNS):
            _set_template_cell_text(row.cells[column_index], _grade_student_value(student, key))

    formula = str(structured.get("formula") or "平时*40% + 期末*60%").strip()
    _set_template_cell_text(table.rows[35].cells[9], f"总评成绩 = {formula}")

    band_counts = _score_band_counts(students)
    total = len(students)
    for offset, count in enumerate(band_counts):
        row = table.rows[37 + offset]
        _set_template_cell_text(row.cells[10], count)
        ratio = count * 100 / total if total else 0
        _set_template_cell_text(row.cells[12], "0%" if count == 0 else f"{ratio:.2f}%")

    examined = sum(
        1
        for student in students
        if student.get("final_exam_score") not in (None, "")
    )
    status_counts = _grade_status_counts(students)
    statistics = structured.get("statistics") if isinstance(structured.get("statistics"), dict) else {}
    numeric_scores = [
        float(student["final_exam_score"])
        for student in students
        if student.get("final_exam_score") not in (None, "")
    ]
    average = float(statistics.get("average") or (sum(numeric_scores) / len(numeric_scores) if numeric_scores else 0))
    summary_values = [
        f"{total}/{examined}",
        status_counts["免考(修)"],
        status_counts["缓考"],
        status_counts["作弊"],
        status_counts["旷考"],
        status_counts["交流生"],
        status_counts["借读生"],
        f"{average:.2f}",
    ]
    for offset, value in enumerate(summary_values):
        _set_template_cell_text(table.rows[37 + offset].cells[16], value)

    _set_template_cell_text(table.rows[46].cells[0], "教师：______________________________签字")
    _add_grade_signature_overlay(document, table, fields.get("teacher_signature_image_path"))
    if "{{" in document._element.xml:
        raise RuntimeError("期末成绩登记表仍包含未替换的模板占位符。")

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _choice(options: list[str], selected: Any) -> str:
    selected_text = str(selected or "").strip()
    return "   ".join(f"{'√' if option == selected_text else '□'} {option}" for option in options)


def _chart_image(distribution: list[dict[str, Any]]) -> io.BytesIO:
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1100, 300
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    left, top, right, bottom = 75, 20, width - 25, height - 50
    draw.line((left, top, left, bottom), fill="#6B7280", width=2)
    draw.line((left, bottom, right, bottom), fill="#6B7280", width=2)
    counts = [int(item.get("count") or 0) for item in distribution[:5]]
    maximum = max(counts or [1]) or 1
    labels = ["<60", "60-69", "70-79", "80-89", "90-100"]
    colors = ["#94A3B8", "#64748B", "#F59E0B", "#FB7185", "#14B8A6"]
    slot = (right - left) / 5
    for index, count in enumerate(counts + [0] * (5 - len(counts))):
        bar_width = slot * 0.45
        x0 = left + index * slot + slot * 0.275
        x1 = x0 + bar_width
        bar_height = (bottom - top - 20) * count / maximum
        y0 = bottom - bar_height
        draw.rounded_rectangle((x0, y0, x1, bottom), radius=8, fill=colors[index])
        count_box = draw.textbbox((0, 0), str(count), font=font)
        draw.text(((x0 + x1 - (count_box[2] - count_box[0])) / 2, max(top, y0 - 18)), str(count), fill="#111827", font=font)
        label_box = draw.textbbox((0, 0), labels[index], font=font)
        draw.text(((x0 + x1 - (label_box[2] - label_box[0])) / 2, bottom + 12), labels[index], fill="#374151", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer


def _merge_row(table: Any, row_index: int, start: int, end: int) -> Any:
    cell = table.rows[row_index].cells[start]
    if end > start:
        cell = cell.merge(table.rows[row_index].cells[end])
    return cell


def _add_analysis_form(document: Any, fields: dict[str, Any], structured: dict[str, Any]) -> None:
    from docx.shared import Cm

    table = document.add_table(rows=6, cols=6)
    table.autofit = False
    _set_table_borders(table, size=5)
    widths = [2.0, 3.0, 1.55, 2.5, 1.7, 3.1]
    for row in table.rows:
        for index, cell in enumerate(row.cells):
            _set_cell_width(cell, widths[index])
    rows = [
        ["课程名称", fields.get("course_name") or "", "学时数", fields.get("course_hours") or "", "开课单位", fields.get("department") or ""],
        ["教师姓名", fields.get("teacher_name") or "", "课程性质", _choice(["选修", "必修"], fields.get("course_nature")), "", ""],
        ["命题形式(打√)", _choice(["试题库", "试卷库", "教师组题"], fields.get("proposition_form")), "", "", "", ""],
        ["考试形式(打√)", _choice(["开卷", "闭卷"], fields.get("exam_form")), "", "教考分离(打√)", _choice(["是", "否"], fields.get("separate_teaching_exam")), ""],
        ["学生班级", fields.get("class_name") or "", "", "", "", ""],
        ["阅卷形式(打√)", _choice(["本人阅卷", "同行阅卷", "集体阅卷", "机器阅卷", "其他"], fields.get("marking_form")), "", "", "", ""],
    ]
    merge_specs = {
        1: [(3, 5)],
        2: [(1, 5)],
        3: [(1, 2), (4, 5)],
        4: [(1, 5)],
        5: [(1, 5)],
    }
    for row_index, specs in merge_specs.items():
        for start, end in specs:
            _merge_row(table, row_index, start, end)
    for row_index, values in enumerate(rows):
        for column_index, value in enumerate(values):
            cell = table.rows[row_index].cells[column_index]
            if cell._tc is not table.rows[row_index].cells[column_index]._tc:
                continue
            if column_index > 0 and any(start < column_index <= end for start, end in merge_specs.get(row_index, [])):
                continue
            _set_cell_text(cell, value, size=8, bold=column_index in {0, 2, 3, 4} and bool(value))

    distribution = structured.get("score_distribution") if isinstance(structured.get("score_distribution"), list) else []
    stats = structured.get("statistics") if isinstance(structured.get("statistics"), dict) else {}
    score_table = document.add_table(rows=5, cols=6)
    score_table.autofit = False
    _set_table_borders(score_table, size=5)
    score_values = [
        ["分数段", "<60", "60-69", "70-79", "80-89", "90-100"],
        ["人数", *[int(item.get("count") or 0) for item in distribution[:5]]],
        ["比例", *[f"{float(item.get('ratio') or 0):.2f}%" for item in distribution[:5]]],
        ["平均分", f"{float(stats.get('average') or 0):.2f}", "标准差", f"{float(stats.get('standard_deviation') or 0):.2f}", "", ""],
        ["最高分", _score_text(stats.get("maximum")), "最低分", _score_text(stats.get("minimum")), "及格率", f"{float(stats.get('pass_rate') or 0):.2f}%"],
    ]
    for row_index, values in enumerate(score_values):
        for column_index, value in enumerate(values):
            _set_cell_width(score_table.rows[row_index].cells[column_index], 2.2)
            _set_cell_text(score_table.rows[row_index].cells[column_index], value, size=8, bold=column_index % 2 == 0 or row_index == 0)

    chart_header = document.add_table(rows=1, cols=1)
    _set_table_borders(chart_header, size=5)
    _set_cell_text(chart_header.rows[0].cells[0], "学生成绩分布图", size=8.5, bold=True)
    chart_table = document.add_table(rows=1, cols=1)
    _set_table_borders(chart_table, size=5)
    chart_cell = chart_table.rows[0].cells[0]
    chart_cell.text = ""
    paragraph = chart_cell.paragraphs[0]
    paragraph.alignment = 1
    paragraph.paragraph_format.space_before = 0
    paragraph.paragraph_format.space_after = 0
    paragraph.add_run().add_picture(_chart_image(distribution), width=Cm(16.2), height=Cm(4.4))


def _add_analysis_text_and_signatures(document: Any, fields: dict[str, Any], structured: dict[str, Any]) -> None:
    from docx.enum.table import WD_ROW_HEIGHT_RULE
    from docx.shared import Cm, Pt

    heading = document.add_table(rows=1, cols=1)
    _set_table_borders(heading, size=5)
    _set_cell_text(
        heading.rows[0].cells[0],
        "简要分析试题结构，成绩分布，学生掌握情况及其主要原因，提出教学改进意见与措施",
        size=8.2,
        bold=True,
        align=0,
    )
    body = document.add_table(rows=1, cols=1)
    _set_table_borders(body, size=5)
    body.rows[0].height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
    body.rows[0].height = Cm(3.0)
    text = structured.get("analysis_text") or fields.get("analysis_text") or ""
    _set_cell_text(body.rows[0].cells[0], text, size=9, align=0)

    review = document.add_table(rows=2, cols=2)
    review.autofit = False
    _set_table_borders(review, size=5)
    labels = ["系（教研室）审核意见：", "教学院长审核意见："]
    consent_paths = [fields.get("department_consent_image_path"), fields.get("dean_consent_image_path")]
    signature_paths = [fields.get("department_signature_image_path"), fields.get("dean_signature_image_path")]
    for column_index in range(2):
        cell = review.rows[0].cells[column_index]
        cell.text = ""
        paragraph = cell.paragraphs[0]
        paragraph.alignment = 0
        paragraph.paragraph_format.space_before = 0
        paragraph.paragraph_format.space_after = 0
        _set_run_font(paragraph.add_run(labels[column_index]), 8.2, bold=True)
        if consent_paths[column_index] and Path(str(consent_paths[column_index])).is_file():
            paragraph.add_run().add_picture(str(consent_paths[column_index]), width=Cm(2.0))
        signature_cell = review.rows[1].cells[column_index]
        signature_cell.text = ""
        signature_paragraph = signature_cell.paragraphs[0]
        signature_paragraph.alignment = 0
        _set_run_font(signature_paragraph.add_run("签字："), 8.2, bold=True)
        if signature_paths[column_index] and Path(str(signature_paths[column_index])).is_file():
            signature_paragraph.add_run().add_picture(str(signature_paths[column_index]), width=Cm(2.5))
        else:
            _set_run_font(signature_paragraph.add_run("________________"), 8.2)

    note = document.add_paragraph()
    note.paragraph_format.space_before = Pt(1)
    note.paragraph_format.space_after = 0
    _set_run_font(note.add_run("注：1、本表一式两份，一份交学生所在学院，一份交开课学院存档。"), 7.5)


def build_exam_analysis_docx(parse_payload: dict[str, Any]) -> bytes:
    from docx import Document
    from docx.shared import Cm, Pt

    payload = _payload(parse_payload)
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    structured = payload.get("structured") if isinstance(payload.get("structured"), dict) else {}
    document = Document()
    section = document.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(0.75)
    section.bottom_margin = Cm(0.55)
    section.left_margin = Cm(1.05)
    section.right_margin = Cm(1.05)
    section.header_distance = Cm(0.3)
    section.footer_distance = Cm(0.3)
    document.styles["Normal"].paragraph_format.space_after = Pt(0)

    _add_title(document, "广西外国语学院课程试卷分析表", fields, size=15)
    _add_analysis_form(document, fields, structured)
    _add_analysis_text_and_signatures(document, fields, structured)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def build_academic_final_material_docx(parse_payload: dict[str, Any], template_key: str) -> bytes:
    if template_key == ACADEMIC_GRADE_REGISTER_TYPE:
        return build_grade_register_docx(parse_payload)
    if template_key == ACADEMIC_EXAM_ANALYSIS_TYPE:
        return build_exam_analysis_docx(parse_payload)
    raise ValueError("不支持的教务期末材料类型。")
