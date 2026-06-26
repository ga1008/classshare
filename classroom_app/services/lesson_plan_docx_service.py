"""Render lesson plans to DOCX using the GXUFL printed 教案 layout.

The export intentionally follows the school Word template instead of a generic
report style: cover page with the school header image and underlined fields,
then one fixed-width lesson-plan table per session. The table dimensions,
margins, borders, and font sizes are taken from the reference teaching file so
Word/PDF/print output stays stable.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Pt, RGBColor

from .libreoffice_service import convert_docx_bytes_to_pdf as _safe_docx_to_pdf
from . import lesson_plan_markdown as md

_CN_FONT = "宋体"
_TITLE_FONT = "隶书"
_LATIN_FONT = "Times New Roman"
_MONO_FONT = "Consolas"
_HEADER_IMAGE_PATH = Path(__file__).with_name("assets") / "gxufl_lesson_plan_header.png"

# Reference DOCX section metrics, in English Metric Units.
_PAGE_WIDTH_EMU = 7_560_310
_PAGE_HEIGHT_EMU = 10_692_130
_MARGIN_TOP_EMU = 914_400
_MARGIN_BOTTOM_EMU = 810_260
_MARGIN_LEFT_EMU = 810_260
_MARGIN_RIGHT_EMU = 808_990
_HEADER_DISTANCE_EMU = 540_385
_FOOTER_DISTANCE_EMU = 629_920

# Reference table grids, in twips (dxa).
_OUTER_GRID = (1911, 324, 6095, 1678)
_OUTER_WIDTH = sum(_OUTER_GRID)
_NESTED_ACTIVITY_GRID = (1141, 2684, 2439, 1844)
_NESTED_ACTIVITY_WIDTH = sum(_NESTED_ACTIVITY_GRID)
_HEADER_IMAGE_WIDTH_EMU = 4_909_185
_HEADER_IMAGE_HEIGHT_EMU = 885_825


# ---------------------------------------------------------------------------
# OOXML helpers
# ---------------------------------------------------------------------------
def _set_run_font(
    run,
    *,
    size: float,
    bold: bool = False,
    cn: str = _CN_FONT,
    latin: str = _LATIN_FONT,
    color: str | None = None,
    underline: bool = False,
) -> None:
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = latin
    run.font.underline = underline
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), latin)
    rfonts.set(qn("w:hAnsi"), latin)
    rfonts.set(qn("w:eastAsia"), cn)
    if color:
        run.font.color.rgb = RGBColor.from_string(color)


def _set_paragraph_line(paragraph, *, line_twips: int = 400, rule: str = "exact") -> None:
    ppr = paragraph._p.get_or_add_pPr()
    spacing = ppr.find(qn("w:spacing"))
    if spacing is None:
        spacing = OxmlElement("w:spacing")
        ppr.append(spacing)
    spacing.set(qn("w:line"), str(line_twips))
    spacing.set(qn("w:lineRule"), rule)


def _set_first_line_indent(paragraph, twips: int) -> None:
    ppr = paragraph._p.get_or_add_pPr()
    ind = ppr.find(qn("w:ind"))
    if ind is None:
        ind = OxmlElement("w:ind")
        ppr.append(ind)
    ind.set(qn("w:firstLine"), str(twips))
    ind.set(qn("w:firstLineChars"), "708")


def _set_table_borders(table) -> None:
    tbl_pr = table._element.tblPr
    for existing in tbl_pr.findall(qn("w:tblBorders")):
        tbl_pr.remove(existing)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "auto")
        borders.append(el)
    tbl_pr.append(borders)


def _set_table_width_twips(table, width: int) -> None:
    tbl_pr = table._element.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.insert(0, tbl_w)
    tbl_w.set(qn("w:w"), str(width))
    tbl_w.set(qn("w:type"), "dxa")


def _set_table_fixed_layout(table) -> None:
    tbl_pr = table._element.tblPr
    layout = tbl_pr.find(qn("w:tblLayout"))
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        tbl_pr.append(layout)
    layout.set(qn("w:type"), "fixed")


def _set_table_indent_twips(table, indent: int) -> None:
    tbl_pr = table._element.tblPr
    tbl_ind = tbl_pr.find(qn("w:tblInd"))
    if tbl_ind is None:
        tbl_ind = OxmlElement("w:tblInd")
        tbl_pr.append(tbl_ind)
    tbl_ind.set(qn("w:w"), str(indent))
    tbl_ind.set(qn("w:type"), "dxa")


def _set_table_grid(table, widths: tuple[int, ...]) -> None:
    tbl = table._tbl
    for existing in tbl.findall(qn("w:tblGrid")):
        tbl.remove(existing)
    grid = OxmlElement("w:tblGrid")
    for width in widths:
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(width))
        grid.append(col)
    tbl.insert(1, grid)


def _configure_table(table, *, width: int, grid: tuple[int, ...]) -> None:
    table.autofit = False
    _set_table_width_twips(table, width)
    _set_table_fixed_layout(table)
    _set_table_grid(table, grid)
    _set_table_borders(table)
    for row in table.rows:
        for index, cell in enumerate(row.cells):
            if index < len(grid):
                _set_cell_width_twips(cell, grid[index])


def _set_cell_width_twips(cell, width: int, *, grid_span: int | None = None) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.find(qn("w:tcW"))
    if tc_w is None:
        tc_w = OxmlElement("w:tcW")
        tc_pr.append(tc_w)
    tc_w.set(qn("w:w"), str(width))
    tc_w.set(qn("w:type"), "dxa")
    if grid_span and grid_span > 1:
        span = tc_pr.find(qn("w:gridSpan"))
        if span is None:
            span = OxmlElement("w:gridSpan")
            tc_pr.append(span)
        span.set(qn("w:val"), str(grid_span))


def _set_row_height_twips(row, height: int) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    tr_height = tr_pr.find(qn("w:trHeight"))
    if tr_height is None:
        tr_height = OxmlElement("w:trHeight")
        tr_pr.append(tr_height)
    tr_height.set(qn("w:val"), str(height))


def _clear_cell(cell) -> None:
    cell.text = ""


def _cell_paragraph(cell, *, first: bool = True):
    if first and cell.paragraphs:
        para = cell.paragraphs[0]
        para.text = ""
        return para
    return cell.add_paragraph()


def _write_runs(
    paragraph,
    text: Any,
    *,
    size: float = 12,
    bold: bool = False,
    cn: str = _CN_FONT,
    latin: str = _LATIN_FONT,
    color: str | None = None,
) -> None:
    for run_spec in md.inline_runs(text):
        run = paragraph.add_run(run_spec["text"])
        _set_run_font(
            run,
            size=size,
            bold=bold or run_spec["bold"],
            cn=cn,
            latin=_MONO_FONT if run_spec.get("code") else latin,
            color=color,
        )


def _write_plain(
    cell,
    text: Any,
    *,
    size: float = 12,
    bold: bool = False,
    align=WD_ALIGN_PARAGRAPH.LEFT,
    vertical=WD_CELL_VERTICAL_ALIGNMENT.TOP,
    line_twips: int = 400,
) -> None:
    _clear_cell(cell)
    cell.vertical_alignment = vertical
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines:
        lines = [""]
    for idx, line in enumerate(lines):
        para = _cell_paragraph(cell, first=(idx == 0))
        para.alignment = align
        para.paragraph_format.space_before = Pt(0)
        para.paragraph_format.space_after = Pt(0)
        _set_paragraph_line(para, line_twips=line_twips)
        _write_runs(para, line, size=size, bold=bold)


def _write_label(cell, text: str) -> None:
    _write_plain(
        cell,
        text,
        size=12,
        bold=True,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        vertical=WD_CELL_VERTICAL_ALIGNMENT.CENTER,
        line_twips=400,
    )


# ---------------------------------------------------------------------------
# Markdown -> DOCX inside 教学内容及过程
# ---------------------------------------------------------------------------
def _render_markdown_into_cell(cell, markdown: Any) -> None:
    _clear_cell(cell)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
    blocks = md.parse_blocks(markdown)
    first = True
    if not blocks:
        para = _cell_paragraph(cell, first=True)
        _set_paragraph_line(para)
        run = para.add_run("（待补充）")
        _set_run_font(run, size=12)
        return

    for block in blocks:
        btype = block.get("type")
        if btype == "heading":
            para = _cell_paragraph(cell, first=first)
            para.paragraph_format.space_before = Pt(0 if first else 3)
            para.paragraph_format.space_after = Pt(0)
            _set_paragraph_line(para)
            run = para.add_run(str(block.get("text") or ""))
            _set_run_font(run, size=12, bold=True)
        elif btype == "para":
            para = _cell_paragraph(cell, first=first)
            para.paragraph_format.space_before = Pt(0)
            para.paragraph_format.space_after = Pt(0)
            _set_paragraph_line(para)
            _write_runs(para, block.get("text", ""), size=12)
        elif btype in ("ul", "ol"):
            for i, item in enumerate(block.get("items", []), start=1):
                para = _cell_paragraph(cell, first=(first and i == 1))
                para.paragraph_format.space_before = Pt(0)
                para.paragraph_format.space_after = Pt(0)
                _set_paragraph_line(para)
                prefix = f"{i}. " if btype == "ol" else "•  "
                run = para.add_run(prefix)
                _set_run_font(run, size=12)
                _write_runs(para, item, size=12)
        elif btype == "table":
            _render_md_table_into_cell(cell, block, first=first)
        elif btype == "code":
            para = _cell_paragraph(cell, first=first)
            para.paragraph_format.space_before = Pt(0)
            para.paragraph_format.space_after = Pt(0)
            _set_paragraph_line(para)
            run = para.add_run(block.get("text", ""))
            _set_run_font(run, size=10.5, cn=_CN_FONT, latin=_MONO_FONT)
        first = False


def _render_md_table_into_cell(cell, block: dict[str, Any], *, first: bool) -> None:
    header = block.get("header", [])
    rows = block.get("rows", [])
    col_count = max(len(header), max((len(r) for r in rows), default=0)) or 1
    if not first:
        spacer = cell.add_paragraph()
        spacer.paragraph_format.space_after = Pt(0)
        _set_paragraph_line(spacer, line_twips=120)

    nested = cell.add_table(rows=1, cols=col_count)
    nested.alignment = WD_TABLE_ALIGNMENT.LEFT
    if col_count == 4:
        grid = _NESTED_ACTIVITY_GRID
    else:
        per_col = max(900, _NESTED_ACTIVITY_WIDTH // col_count)
        grid = tuple(per_col for _ in range(col_count))
    _configure_table(nested, width=sum(grid), grid=grid)

    head_cells = nested.rows[0].cells
    for idx in range(col_count):
        text = header[idx] if idx < len(header) else ""
        _set_cell_width_twips(head_cells[idx], grid[idx])
        _write_plain(head_cells[idx], text, size=12, bold=True, line_twips=400)

    for row in rows:
        body_cells = nested.add_row().cells
        for idx in range(col_count):
            text = row[idx] if idx < len(row) else ""
            _set_cell_width_twips(body_cells[idx], grid[idx])
            _write_plain(
                body_cells[idx],
                str(text).replace("<br>", "\n"),
                size=12,
                bold=False,
                line_twips=400,
            )


# ---------------------------------------------------------------------------
# Cover page
# ---------------------------------------------------------------------------
def _setup_document(document: Document) -> None:
    section = document.sections[0]
    section.page_width = Emu(_PAGE_WIDTH_EMU)
    section.page_height = Emu(_PAGE_HEIGHT_EMU)
    section.top_margin = Emu(_MARGIN_TOP_EMU)
    section.bottom_margin = Emu(_MARGIN_BOTTOM_EMU)
    section.left_margin = Emu(_MARGIN_LEFT_EMU)
    section.right_margin = Emu(_MARGIN_RIGHT_EMU)
    section.header_distance = Emu(_HEADER_DISTANCE_EMU)
    section.footer_distance = Emu(_FOOTER_DISTANCE_EMU)

    normal = document.styles["Normal"]
    normal.font.name = _LATIN_FONT
    normal.font.size = Pt(12)
    rpr = normal.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), _LATIN_FONT)
    rfonts.set(qn("w:hAnsi"), _LATIN_FONT)
    rfonts.set(qn("w:eastAsia"), _CN_FONT)


def _add_blank_paragraph(document: Document, *, line_twips: int = 240) -> None:
    para = document.add_paragraph()
    para.paragraph_format.space_before = Pt(0)
    para.paragraph_format.space_after = Pt(0)
    _set_paragraph_line(para, line_twips=line_twips)


def _normalize_semester_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace(" ", "")
    text = text.replace("第一学期", "第 一 学期")
    text = text.replace("第二学期", "第 二 学期")
    text = text.replace("第三学期", "第 三 学期")
    return text + " "


def _cover_value_run(paragraph, value: Any, *, min_chars: int, pad_mode: str = "center") -> None:
    raw = str(value or "").strip()
    pad = max(0, min_chars - len(raw))
    if pad_mode == "right":
        left = 0
        right = pad
    else:
        left = pad // 2
        right = pad - left
    # Word does not reliably draw underline for trailing regular spaces. NBSP
    # keeps the school-template field line visible at the intended length.
    blank = "\u00a0"
    text = f"{blank * left}{raw}{blank * right}"
    run = paragraph.add_run(text)
    _set_run_font(run, size=16, underline=True)


def _cover_line(document: Document, label: str, value: Any, *, min_chars: int = 24) -> None:
    para = document.add_paragraph()
    _set_first_line_indent(para, 2266)
    _set_paragraph_line(para, line_twips=600)
    para.paragraph_format.space_before = Pt(0)
    para.paragraph_format.space_after = Pt(0)
    label_run = para.add_run(label)
    _set_run_font(label_run, size=16)
    _cover_value_run(para, value, min_chars=min_chars)


def _cover_continuation_line(document: Document, value: str, *, min_chars: int = 30) -> None:
    para = document.add_paragraph()
    _set_first_line_indent(para, 2266)
    _set_paragraph_line(para, line_twips=600)
    para.paragraph_format.space_before = Pt(0)
    para.paragraph_format.space_after = Pt(0)
    _cover_value_run(para, value, min_chars=min_chars)


def _split_textbook_line(textbook: Any, publisher: Any) -> list[str]:
    text = str(textbook or "").strip()
    pub = str(publisher or "").strip()
    if pub and pub not in text:
        text = f"{text}{pub}"
    if len(text) <= 34:
        return [text]
    split_at = text.rfind(" ", 0, 36)
    if split_at < 18:
        split_at = 34
    return [text[:split_at].rstrip(), text[split_at:].lstrip()]


def _write_cover(document: Document, cover: dict[str, Any]) -> None:
    _add_blank_paragraph(document, line_twips=700)
    header_para = document.add_paragraph()
    header_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    header_para.paragraph_format.space_after = Pt(0)
    if _HEADER_IMAGE_PATH.exists():
        header_para.add_run().add_picture(
            str(_HEADER_IMAGE_PATH),
            width=Emu(_HEADER_IMAGE_WIDTH_EMU),
            height=Emu(_HEADER_IMAGE_HEIGHT_EMU),
        )

    for _ in range(4):
        _add_blank_paragraph(document, line_twips=315)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_after = Pt(13)
    title.add_run()
    for part in ("教", "  ", "案"):
        run = title.add_run(part)
        _set_run_font(run, size=48, bold=True, cn=_TITLE_FONT)

    semester = document.add_paragraph()
    semester.alignment = WD_ALIGN_PARAGRAPH.CENTER
    semester.paragraph_format.space_after = Pt(15.6)
    run = semester.add_run(_normalize_semester_label(cover.get("semester_label")))
    _set_run_font(run, size=16)

    _add_blank_paragraph(document, line_twips=900)
    _cover_line(document, "课程名称：", cover.get("course_name"), min_chars=25)
    _cover_line(document, "课程类别：", cover.get("course_category"), min_chars=30)

    credit_para = document.add_paragraph()
    _set_first_line_indent(credit_para, 2266)
    _set_paragraph_line(credit_para, line_twips=600)
    credit_label = credit_para.add_run("学    分：")
    _set_run_font(credit_label, size=16)
    spacer = credit_para.add_run("\u00a0" * 4)
    _set_run_font(spacer, size=16)
    _cover_value_run(credit_para, cover.get("credits", ""), min_chars=11, pad_mode="right")
    spacer = credit_para.add_run("\u00a0" * 4)
    _set_run_font(spacer, size=16)
    hours_label = credit_para.add_run("学   时：")
    _set_run_font(hours_label, size=16)
    _cover_value_run(credit_para, cover.get("total_hours", ""), min_chars=14, pad_mode="right")

    _cover_line(document, "授课教师：", cover.get("teacher_name"), min_chars=39)
    _cover_line(document, "教学单位：", cover.get("teaching_unit"), min_chars=30)
    _cover_line(document, "授课班级：", cover.get("class_name"), min_chars=33)

    textbook_lines = _split_textbook_line(cover.get("textbook"), cover.get("publisher"))
    if textbook_lines:
        _cover_line(document, "使用教材：", textbook_lines[0], min_chars=22)
        for line in textbook_lines[1:]:
            _cover_continuation_line(document, line, min_chars=28)

    for _ in range(2):
        _add_blank_paragraph(document, line_twips=720)
    footer = document.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.paragraph_format.space_after = Pt(0)
    imprint = "广西外国语学院教务处 印制"
    run = footer.add_run(imprint)
    _set_run_font(run, size=15)


# ---------------------------------------------------------------------------
# Session tables
# ---------------------------------------------------------------------------
def _schedule_text(session: dict[str, Any]) -> str:
    schedule = session.get("schedule") or {}
    if schedule.get("text"):
        return str(schedule["text"])
    parts = [str(schedule.get("date") or "")]
    if schedule.get("week_index"):
        parts.append(f"第 {schedule['week_index']} 周")
    if schedule.get("weekday"):
        parts.append(str(schedule["weekday"]))
    if schedule.get("sections"):
        parts.append(f"第 {schedule['sections']} 节")
    return " ".join(p for p in parts if p)


def _combine_key_difficult(session: dict[str, Any]) -> str:
    parts = []
    if session.get("key_points"):
        key = str(session["key_points"]).strip()
        parts.append(key if key.startswith("重点") else f"重点： {key}")
    if session.get("difficulties"):
        difficult = str(session["difficulties"]).strip()
        parts.append(difficult if difficult.startswith("难点") else f"难点： {difficult}")
    return "\n".join(parts)


def _combine_methods(session: dict[str, Any]) -> str:
    parts = []
    if session.get("methods"):
        parts.append(str(session["methods"]).strip())
    if session.get("means"):
        parts.append(str(session["means"]).strip())
    return "\n".join(p for p in parts if p)


def _merge_width(cells, start: int, end: int):
    merged = cells[start]
    for idx in range(start + 1, end + 1):
        merged = merged.merge(cells[idx])
    width = sum(_OUTER_GRID[start : end + 1])
    _set_cell_width_twips(merged, width, grid_span=end - start + 1)
    return merged


def _write_label_content_row(
    row,
    *,
    label: str,
    content: Any,
    height: int,
    markdown: bool = False,
) -> None:
    _set_row_height_twips(row, height)
    cells = row.cells
    label_cell = _merge_width(cells, 0, 1)
    content_cell = _merge_width(cells, 2, 3)
    _write_label(label_cell, label)
    if markdown:
        _render_markdown_into_cell(content_cell, content)
    else:
        _write_plain(content_cell, content, size=12, line_twips=400)


def _write_session_table(document: Document, session: dict[str, Any]) -> None:
    table = document.add_table(rows=8, cols=4)
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    _configure_table(table, width=_OUTER_WIDTH, grid=_OUTER_GRID)
    _set_table_indent_twips(table, 115)
    rows = table.rows

    _write_label_content_row(rows[0], label="授课时间", content=_schedule_text(session), height=453)
    _write_label_content_row(rows[1], label="授课章节", content=session.get("chapter", ""), height=453)
    _write_label_content_row(rows[2], label="教学目的和要求", content=session.get("objectives", ""), height=460)
    _write_label_content_row(rows[3], label="教学重点和难点", content=_combine_key_difficult(session), height=450)
    _write_label_content_row(rows[4], label="教学方法和手段", content=_combine_methods(session), height=450)

    _set_row_height_twips(rows[5], 631)
    header_cells = rows[5].cells
    process_header = _merge_width(header_cells, 0, 2)
    _set_cell_width_twips(header_cells[3], _OUTER_GRID[3])
    _write_label(process_header, "教学内容及过程")
    _write_label(header_cells[3], "旁批")

    _set_row_height_twips(rows[6], 1134)
    body_cells = rows[6].cells
    process_cell = _merge_width(body_cells, 0, 2)
    _set_cell_width_twips(body_cells[3], _OUTER_GRID[3])
    _render_markdown_into_cell(process_cell, session.get("process", ""))
    _write_plain(body_cells[3], session.get("side_notes", ""), size=12, line_twips=400)

    _write_label_content_row(rows[7], label="教学后记", content=session.get("post_notes", ""), height=390)


def build_lesson_plan_docx(plan: dict[str, Any]) -> bytes:
    """Build DOCX bytes for a hydrated lesson-plan dict."""
    cover = plan.get("cover") or {}
    sessions = plan.get("sessions") or []
    document = Document()
    _setup_document(document)
    _write_cover(document, cover)
    for session in sessions:
        document.add_page_break()
        _write_session_table(document, session)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# PDF / PNG previews (LibreOffice)
# ---------------------------------------------------------------------------
def convert_docx_to_pdf(docx_bytes: bytes, *, base_name: str = "lesson_plan") -> bytes:
    return _safe_docx_to_pdf(docx_bytes, timeout=120)


def convert_docx_to_png(docx_bytes: bytes, *, base_name: str = "lesson_plan", max_pages: int = 4) -> bytes:
    """Render the first few pages to a single stacked PNG preview."""
    pdf_bytes = convert_docx_to_pdf(docx_bytes, base_name=base_name)
    try:
        import fitz  # PyMuPDF
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(f"缺少 PNG 渲染依赖：{exc}") from exc

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        images = []
        for page_index in range(min(max_pages, doc.page_count)):
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6))
            images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    finally:
        doc.close()
    if not images:
        raise RuntimeError("PDF 没有可渲染的页面。")
    width = max(img.width for img in images)
    gap = 16
    height = sum(img.height for img in images) + gap * (len(images) - 1)
    canvas = Image.new("RGB", (width, height), "white")
    y = 0
    for img in images:
        canvas.paste(img, (0, y))
        y += img.height + gap
    out = BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()
