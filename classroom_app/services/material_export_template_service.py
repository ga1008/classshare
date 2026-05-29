import io
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class MaterialExportArtifact:
    content: bytes
    filename: str
    media_type: str


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


TEMPLATE_CONFIGS: dict[str, dict[str, str]] = {
    "teaching_document": {"title": "教学文档", "preferred_format": "docx"},
    "teaching_summary": {"title": "教师教学工作总结", "preferred_format": "docx"},
    "lesson_plan": {"title": "教案", "preferred_format": "docx"},
    "teaching_calendar": {"title": "教学日历", "preferred_format": "xlsx"},
    "evaluation_sheet": {"title": "评学表", "preferred_format": "docx"},
    "final_syllabus": {"title": "课程教学大纲", "preferred_format": "docx"},
    "assessment_plan": {"title": "课程考核计划表", "preferred_format": "docx"},
    "grading_rubric": {"title": "课程考核评分细则", "preferred_format": "docx"},
    "exam_paper": {"title": "课程考核试卷", "preferred_format": "docx"},
    "final_teaching_summary": {"title": "教师教学工作总结", "preferred_format": "docx"},
}

FIELD_LABELS = {
    "school": "学校",
    "college": "学院",
    "department": "系部",
    "course_name": "课程名称",
    "course_code": "课程代码",
    "class_name": "授课班级",
    "teacher_name": "授课教师",
    "academic_year": "学年",
    "semester": "学期",
    "date": "日期",
    "title": "标题",
}


def build_material_export_artifact(
    parse_payload: dict[str, Any],
    *,
    fallback_filename: str,
    requested_format: str | None = None,
) -> MaterialExportArtifact:
    payload = _coerce_payload(parse_payload)
    export_payload = _as_dict(payload.get("export_payload"))
    template_key = str(export_payload.get("template_key") or payload.get("document_type") or "teaching_document")
    config = TEMPLATE_CONFIGS.get(template_key, TEMPLATE_CONFIGS.get(str(payload.get("document_type")), {}))
    output_format = (requested_format or config.get("preferred_format") or "docx").strip().lower()
    if output_format not in {"docx", "xlsx"}:
        output_format = "docx"

    title = _resolve_title(payload, config, fallback_filename)
    base_name = _safe_filename(title or fallback_filename or "材料导出")
    if output_format == "xlsx":
        return MaterialExportArtifact(
            content=_build_xlsx_export(payload, title=title),
            filename=f"{base_name}.xlsx",
            media_type=XLSX_MEDIA_TYPE,
        )
    return MaterialExportArtifact(
        content=_build_docx_export(payload, title=title),
        filename=f"{base_name}.docx",
        media_type=DOCX_MEDIA_TYPE,
    )


def _build_docx_export(payload: dict[str, Any], *, title: str) -> bytes:
    try:
        from docx import Document
        from docx.enum.section import WD_ORIENT
        from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Cm, Pt
    except ImportError as exc:
        raise RuntimeError(f"缺少 DOCX 导出依赖 python-docx: {exc}") from exc

    document = Document()
    section = document.sections[0]
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(1.8)
    section.left_margin = Cm(2.1)
    section.right_margin = Cm(2.1)
    if str(payload.get("document_type")) in {"teaching_calendar"}:
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height = section.page_height, section.page_width

    styles = document.styles
    styles["Normal"].font.name = "Microsoft YaHei"
    styles["Normal"].font.size = Pt(10.5)
    styles["Normal"]._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")

    heading = document.add_paragraph()
    heading.alignment = 1
    run = heading.add_run(title)
    _set_run(run, size=18, bold=True)
    heading.paragraph_format.space_after = Pt(14)

    metadata = _as_dict(payload.get("metadata"))
    _add_meta_table(document, metadata)

    content = str(payload.get("content_markdown") or "").strip()
    sections = _normalize_sections(payload, content)
    for section_item in sections:
        section_title = str(section_item.get("title") or "").strip()
        section_content = str(section_item.get("content") or "").strip()
        if not section_content:
            continue
        if section_title and section_title != "正文":
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_before = Pt(8)
            paragraph.paragraph_format.space_after = Pt(4)
            _set_run(paragraph.add_run(section_title), size=13, bold=True)
        _add_markdown_like_blocks(document, section_content)

    for table_payload in _normalize_tables(payload):
        _add_payload_table(document, table_payload)

    footer = document.sections[0].footer.paragraphs[0]
    footer.alignment = 1
    footer_run = footer.add_run(f"由 LanShare 根据解析内容生成 · {datetime.now().strftime('%Y-%m-%d')}")
    _set_run(footer_run, size=8.5, color="64748B")

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _build_xlsx_export(payload: dict[str, Any], *, title: str) -> bytes:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError(f"缺少 XLSX 导出依赖 openpyxl: {exc}") from exc

    wb = Workbook()
    ws = wb.active
    ws.title = "材料内容"
    thin = Side(style="thin", color="D8E0EA")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill("solid", fgColor="EAF2FF")
    label_fill = PatternFill("solid", fgColor="F8FAFC")

    ws.merge_cells("A1:F1")
    ws["A1"] = title
    ws["A1"].font = Font(name="Microsoft YaHei", size=16, bold=True)
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30

    row = 3
    metadata = _as_dict(payload.get("metadata"))
    for key, value in _iter_visible_metadata(metadata):
        ws.cell(row=row, column=1, value=FIELD_LABELS.get(key, key))
        ws.cell(row=row, column=2, value=_stringify(value))
        ws.cell(row=row, column=1).fill = label_fill
        for col in range(1, 3):
            cell = ws.cell(row=row, column=col)
            cell.border = border
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        row += 1

    row += 1
    for section in _normalize_sections(payload, str(payload.get("content_markdown") or "")):
        title_cell = ws.cell(row=row, column=1, value=section.get("title") or "正文")
        title_cell.fill = header_fill
        title_cell.font = Font(name="Microsoft YaHei", bold=True)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        row += 1
        content_cell = ws.cell(row=row, column=1, value=str(section.get("content") or "").strip())
        content_cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        ws.row_dimensions[row].height = min(220, max(48, len(str(content_cell.value or "")) // 6))
        row += 2

    for table_payload in _normalize_tables(payload):
        rows = _table_rows(table_payload)
        if not rows:
            continue
        title_text = str(table_payload.get("title") or "结构化表格")
        ws.cell(row=row, column=1, value=title_text).font = Font(name="Microsoft YaHei", bold=True)
        row += 1
        for row_values in rows:
            for col_index, value in enumerate(row_values, start=1):
                cell = ws.cell(row=row, column=col_index, value=value)
                cell.border = border
                cell.alignment = Alignment(wrap_text=True, vertical="center")
                if row_values is rows[0]:
                    cell.fill = header_fill
                    cell.font = Font(name="Microsoft YaHei", bold=True)
            row += 1
        row += 1

    for col in range(1, 9):
        ws.column_dimensions[get_column_letter(col)].width = 18 if col <= 2 else 24
    ws.freeze_panes = "A3"

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _add_meta_table(document: Any, metadata: dict[str, Any]) -> None:
    visible = list(_iter_visible_metadata(metadata))
    if not visible:
        return
    table = document.add_table(rows=0, cols=4)
    table.style = "Table Grid"
    table.alignment = 1
    for index in range(0, len(visible), 2):
        cells = table.add_row().cells
        pair = visible[index:index + 2]
        for pair_index, (key, value) in enumerate(pair):
            label_cell = cells[pair_index * 2]
            value_cell = cells[pair_index * 2 + 1]
            label_cell.text = FIELD_LABELS.get(key, key)
            value_cell.text = _stringify(value)
            _shade_docx_cell(label_cell, "F8FAFC")
            for paragraph in label_cell.paragraphs:
                for run in paragraph.runs:
                    _set_run(run, size=9.5, bold=True)
            for paragraph in value_cell.paragraphs:
                for run in paragraph.runs:
                    _set_run(run, size=9.5)
    document.add_paragraph()


def _add_markdown_like_blocks(document: Any, content: str) -> None:
    lines = str(content or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    table_buffer: list[str] = []
    paragraph_buffer: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_buffer
        if not paragraph_buffer:
            return
        paragraph = document.add_paragraph(" ".join(item.strip() for item in paragraph_buffer if item.strip()))
        paragraph.paragraph_format.space_after = _pt(4)
        for run in paragraph.runs:
            _set_run(run, size=10.5)
        paragraph_buffer = []

    def flush_table() -> None:
        nonlocal table_buffer
        if len(table_buffer) >= 2:
            _add_payload_table(document, {"title": "", "rows": [_split_markdown_row(line) for line in table_buffer if "|" in line]})
        table_buffer = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            flush_table()
            continue
        if "|" in stripped and re.match(r"^\|?.+\|.+\|?$", stripped):
            flush_paragraph()
            if not re.match(r"^\|?\s*:?-{3,}:?", stripped):
                table_buffer.append(stripped)
            continue
        flush_table()
        heading = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            paragraph = document.add_paragraph()
            _set_run(paragraph.add_run(heading.group(2)), size=13 - min(2, len(heading.group(1))), bold=True)
            continue
        bullet = re.match(r"^[-*+]\s+(.+)$", stripped)
        if bullet:
            flush_paragraph()
            paragraph = document.add_paragraph(style="List Bullet")
            paragraph.add_run(bullet.group(1))
            for run in paragraph.runs:
                _set_run(run, size=10.5)
            continue
        paragraph_buffer.append(stripped)
    flush_paragraph()
    flush_table()


def _add_payload_table(document: Any, table_payload: dict[str, Any]) -> None:
    try:
        from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
    except ImportError as exc:
        raise RuntimeError(f"缺少 DOCX 导出依赖 python-docx: {exc}") from exc
    rows = _table_rows(table_payload)
    if not rows:
        return
    title = str(table_payload.get("title") or "").strip()
    if title:
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_before = _pt(6)
        paragraph.paragraph_format.space_after = _pt(3)
        _set_run(paragraph.add_run(title), size=11.5, bold=True)

    column_count = max(len(row) for row in rows)
    table = document.add_table(rows=1, cols=column_count)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for col_index in range(column_count):
        cell = table.rows[0].cells[col_index]
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        _shade_docx_cell(cell, "EAF2FF")
        cell.text = rows[0][col_index] if col_index < len(rows[0]) else ""
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                _set_run(run, size=9, bold=True)
    for row_values in rows[1:]:
        cells = table.add_row().cells
        for col_index in range(column_count):
            cells[col_index].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            cells[col_index].text = row_values[col_index] if col_index < len(row_values) else ""
            for paragraph in cells[col_index].paragraphs:
                for run in paragraph.runs:
                    _set_run(run, size=8.5)
    document.add_paragraph()


def _normalize_sections(payload: dict[str, Any], content: str) -> list[dict[str, str]]:
    export_payload = _as_dict(payload.get("export_payload"))
    sections = export_payload.get("sections")
    if isinstance(sections, list) and sections:
        normalized = []
        for item in sections:
            if isinstance(item, dict):
                normalized.append({"title": str(item.get("title") or "正文"), "content": str(item.get("content") or "")})
        if normalized:
            return normalized
    return [{"title": "正文", "content": content}]


def _normalize_tables(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tables = payload.get("tables")
    if not isinstance(tables, list):
        tables = _as_dict(payload.get("export_payload")).get("tables")
    if not isinstance(tables, list):
        return []
    return [item for item in tables if isinstance(item, dict)]


def _table_rows(table_payload: dict[str, Any]) -> list[list[str]]:
    rows = table_payload.get("rows")
    if not isinstance(rows, list):
        return []
    normalized = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append([_stringify(value) for value in row.values()])
        elif isinstance(row, list):
            normalized.append([_stringify(value) for value in row])
    max_width = max((len(row) for row in normalized), default=0)
    return [row + [""] * (max_width - len(row)) for row in normalized if any(cell.strip() for cell in row)]


def _iter_visible_metadata(metadata: dict[str, Any]):
    skipped = {"source_filename", "document_group", "document_type"}
    preferred = [
        "school",
        "college",
        "department",
        "course_name",
        "course_code",
        "class_name",
        "teacher_name",
        "academic_year",
        "semester",
        "date",
    ]
    yielded = set()
    for key in preferred:
        value = metadata.get(key)
        if value not in (None, "", [], {}):
            yielded.add(key)
            yield key, value
    for key, value in metadata.items():
        if key in yielded or key in skipped or value in (None, "", [], {}):
            continue
        yield key, value


def _resolve_title(payload: dict[str, Any], config: dict[str, str], fallback_filename: str) -> str:
    metadata = _as_dict(payload.get("metadata"))
    for key in ("title", "course_name"):
        value = str(metadata.get(key) or "").strip()
        if value:
            template_title = config.get("title") or str(payload.get("document_type_label") or "")
            return value if template_title in value else f"{value}-{template_title}" if template_title else value
    return config.get("title") or str(payload.get("document_type_label") or "") or fallback_filename


def _coerce_payload(payload: dict[str, Any] | str | None) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str) and payload.strip():
        try:
            value = json.loads(payload)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "-", str(value or "材料导出")).strip(" .")
    return cleaned[:120] or "材料导出"


def _split_markdown_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _set_run(run: Any, *, size: float = 10.5, bold: bool = False, color: str = "111827") -> None:
    try:
        from docx.oxml.ns import qn
        from docx.shared import Pt, RGBColor
    except ImportError as exc:
        raise RuntimeError(f"缺少 DOCX 导出依赖 python-docx: {exc}") from exc
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)


def _shade_docx_cell(cell: Any, fill: str) -> None:
    try:
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
    except ImportError as exc:
        raise RuntimeError(f"缺少 DOCX 导出依赖 python-docx: {exc}") from exc
    tc_pr = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill)
    tc_pr.append(shading)


def _pt(value: float):
    try:
        from docx.shared import Pt
    except ImportError as exc:
        raise RuntimeError(f"缺少 DOCX 导出依赖 python-docx: {exc}") from exc
    return Pt(value)
