"""Render a 教案 (lesson plan) to a .docx that mirrors the校方模板 exactly:

* a cover page — 「教 案」(48pt 加粗) + 「YYYY—YYYY学年第X学期」(16pt) + an info table
  (课程名称/类别/学分·学时/授课教师/教学单位/授课班级/使用教材/出版社) + 「…印制」footer;
* one 8-row × 4-col table per session — 授课时间 / 授课章节 / 教学目的和要求 /
  教学重点和难点 / 教学方法和手段 / (表头 教学内容及过程 | 旁批) / 正文 + 旁批 /
  教学后记. The 教学内容及过程 cell renders the session's Markdown (incl. PBL tables).

PDF / PNG previews reuse the LibreOffice (``soffice``) toolchain already present
in the image — the same approach as ``material_export_template_service``.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from . import lesson_plan_markdown as md

_CN_FONT = "宋体"
_CN_TITLE_FONT = "黑体"
_LATIN_FONT = "Times New Roman"
# Column widths (cm), sum ≈ usable width on A4 with the margins below.
_COL_WIDTHS = (2.6, 0.8, 10.6, 3.4)


# ---------------------------------------------------------------------------
# Low-level font / cell helpers
# ---------------------------------------------------------------------------
def _set_run_font(run, *, size: float, bold: bool = False, cn: str = _CN_FONT,
                  latin: str = _LATIN_FONT, color: str | None = None) -> None:
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = latin
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


def _shade_cell(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def _set_table_borders(table) -> None:
    tbl_pr = table._element.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "000000")
        borders.append(el)
    tbl_pr.append(borders)


def _clear_cell(cell) -> None:
    cell.text = ""


def _cell_paragraph(cell, *, first: bool = True):
    if first and cell.paragraphs:
        para = cell.paragraphs[0]
        para.text = ""
        return para
    return cell.add_paragraph()


def _write_plain(cell, text: str, *, size: float = 10.5, bold: bool = False,
                 align=WD_ALIGN_PARAGRAPH.LEFT) -> None:
    _clear_cell(cell)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    lines = str(text or "").split("\n")
    for idx, line in enumerate(lines):
        para = _cell_paragraph(cell, first=(idx == 0))
        para.alignment = align
        para.paragraph_format.space_after = Pt(2)
        for run_spec in md.inline_runs(line):
            run = para.add_run(run_spec["text"])
            _set_run_font(run, size=size, bold=bold or run_spec["bold"])


def _write_label(cell, text: str) -> None:
    _clear_cell(cell)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    _shade_cell(cell, "F2F2F2")
    para = cell.paragraphs[0]
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run(str(text or ""))
    _set_run_font(run, size=10.5, bold=True)


def _set_col_widths(table) -> None:
    for row in table.rows:
        for idx, cell in enumerate(row.cells):
            if idx < len(_COL_WIDTHS):
                cell.width = Cm(_COL_WIDTHS[idx])


# ---------------------------------------------------------------------------
# Markdown → docx (inside the 教学内容及过程 cell)
# ---------------------------------------------------------------------------
def _render_markdown_into_cell(cell, markdown: str) -> None:
    _clear_cell(cell)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
    blocks = md.parse_blocks(markdown)
    first = True
    if not blocks:
        para = _cell_paragraph(cell, first=True)
        run = para.add_run("（待补充）")
        _set_run_font(run, size=10.5)
        return
    for block in blocks:
        btype = block.get("type")
        if btype == "heading":
            para = _cell_paragraph(cell, first=first)
            para.paragraph_format.space_before = Pt(4)
            para.paragraph_format.space_after = Pt(2)
            run = para.add_run(block.get("text", ""))
            level = int(block.get("level", 3))
            _set_run_font(run, size=12 if level <= 2 else 11, bold=True, color="1F2937")
        elif btype == "para":
            para = _cell_paragraph(cell, first=first)
            para.paragraph_format.space_after = Pt(2)
            for run_spec in md.inline_runs(block.get("text", "")):
                run = para.add_run(run_spec["text"])
                _set_run_font(run, size=10.5, bold=run_spec["bold"])
        elif btype in ("ul", "ol"):
            for i, item in enumerate(block.get("items", []), start=1):
                para = _cell_paragraph(cell, first=(first and i == 1))
                para.paragraph_format.left_indent = Cm(0.5)
                para.paragraph_format.space_after = Pt(1)
                prefix = f"{i}. " if btype == "ol" else "• "
                run = para.add_run(prefix)
                _set_run_font(run, size=10.5)
                for run_spec in md.inline_runs(item):
                    r = para.add_run(run_spec["text"])
                    _set_run_font(r, size=10.5, bold=run_spec["bold"])
        elif btype == "table":
            _render_md_table_into_cell(cell, block, first=first)
        elif btype == "code":
            para = _cell_paragraph(cell, first=first)
            run = para.add_run(block.get("text", ""))
            _set_run_font(run, size=9.5, cn=_CN_FONT, latin="Consolas", color="334155")
        first = False


def _render_md_table_into_cell(cell, block: dict[str, Any], *, first: bool) -> None:
    header = block.get("header", [])
    rows = block.get("rows", [])
    col_count = max(len(header), max((len(r) for r in rows), default=0)) or 1
    if not first:
        cell.add_paragraph()
    nested = cell.add_table(rows=1, cols=col_count)
    nested.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_borders(nested)
    head_cells = nested.rows[0].cells
    for idx in range(col_count):
        text = header[idx] if idx < len(header) else ""
        _clear_cell(head_cells[idx])
        _shade_cell(head_cells[idx], "EEF2F7")
        head_cells[idx].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        p = head_cells[idx].paragraphs[0]
        for run_spec in md.inline_runs(text):
            run = p.add_run(run_spec["text"])
            _set_run_font(run, size=9.5, bold=True)
    for row in rows:
        body_cells = nested.add_row().cells
        for idx in range(col_count):
            text = row[idx] if idx < len(row) else ""
            _clear_cell(body_cells[idx])
            body_cells[idx].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            p = body_cells[idx].paragraphs[0]
            # markdown table cells may use <br> for line breaks
            for li, segment in enumerate(str(text).replace("<br>", "\n").split("\n")):
                seg_para = p if li == 0 else body_cells[idx].add_paragraph()
                for run_spec in md.inline_runs(segment):
                    run = seg_para.add_run(run_spec["text"])
                    _set_run_font(run, size=9.5, bold=run_spec["bold"])


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------
def _setup_document(document: Document) -> None:
    section = document.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(2.0)
    section.left_margin = Cm(1.8)
    section.right_margin = Cm(1.8)
    normal = document.styles["Normal"]
    normal.font.name = _LATIN_FONT
    normal.font.size = Pt(10.5)
    rpr = normal.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), _CN_FONT)


def _write_cover(document: Document, cover: dict[str, Any]) -> None:
    for _ in range(2):
        document.add_paragraph()
    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("教  案")
    _set_run_font(run, size=48, bold=True, cn=_CN_TITLE_FONT)
    title.paragraph_format.space_after = Pt(24)

    semester = document.add_paragraph()
    semester.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = semester.add_run(str(cover.get("semester_label") or ""))
    _set_run_font(run, size=16, bold=False)
    semester.paragraph_format.space_after = Pt(28)

    table = document.add_table(rows=0, cols=4)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_borders(table)

    def add_span_row(label: str, value: str) -> None:
        cells = table.add_row().cells
        _write_label(cells[0], label)
        merged = cells[1].merge(cells[2]).merge(cells[3])
        _write_plain(merged, value, size=11)

    def add_quad_row(l1: str, v1: str, l2: str, v2: str) -> None:
        cells = table.add_row().cells
        _write_label(cells[0], l1)
        _write_plain(cells[1], v1, size=11)
        _write_label(cells[2], l2)
        _write_plain(cells[3], v2, size=11)

    add_span_row("课程名称", cover.get("course_name", ""))
    add_span_row("课程类别", cover.get("course_category", ""))
    add_quad_row("学　分", cover.get("credits", ""), "学　时", cover.get("total_hours", ""))
    add_span_row("授课教师", cover.get("teacher_name", ""))
    add_span_row("教学单位", cover.get("teaching_unit", ""))
    add_span_row("授课班级", cover.get("class_name", ""))
    add_span_row("使用教材", cover.get("textbook", ""))
    add_span_row("出版社", cover.get("publisher", ""))
    _set_col_widths(table)

    for _ in range(3):
        document.add_paragraph()
    footer = document.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    imprint = f"{cover.get('school_name') or ''}{cover.get('teaching_unit') or ''}  印制".strip()
    run = footer.add_run(imprint)
    _set_run_font(run, size=15, bold=False)


def _schedule_text(session: dict[str, Any]) -> str:
    schedule = session.get("schedule") or {}
    if schedule.get("text"):
        return str(schedule["text"])
    parts = [str(schedule.get("date") or "")]
    if schedule.get("week_index"):
        parts.append(f"第{schedule['week_index']}周")
    if schedule.get("sections"):
        parts.append(f"第{schedule['sections']}节")
    return " ".join(p for p in parts if p)


def _combine_key_difficult(session: dict[str, Any]) -> str:
    parts = []
    if session.get("key_points"):
        kp = str(session["key_points"]).strip()
        parts.append(kp if kp.startswith("重点") else f"重点：\n{kp}")
    if session.get("difficulties"):
        df = str(session["difficulties"]).strip()
        parts.append(df if df.startswith("难点") else f"难点：\n{df}")
    return "\n".join(parts)


def _combine_methods(session: dict[str, Any]) -> str:
    parts = []
    if session.get("methods"):
        parts.append(f"教学方法：{session['methods']}")
    if session.get("means"):
        parts.append(f"教学手段：{session['means']}")
    return "\n".join(parts)


def _write_session_table(document: Document, session: dict[str, Any]) -> None:
    table = document.add_table(rows=8, cols=4)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_borders(table)
    rows = table.rows

    def label_content(row_idx: int, label: str, content: str, *, markdown: bool = False) -> None:
        cells = rows[row_idx].cells
        label_cell = cells[0].merge(cells[1])
        _write_label(label_cell, label)
        content_cell = cells[2].merge(cells[3])
        if markdown:
            _render_markdown_into_cell(content_cell, content)
        else:
            _write_plain(content_cell, content, size=10.5)

    label_content(0, "授课时间", _schedule_text(session))
    label_content(1, "授课章节", session.get("chapter", ""))
    label_content(2, "教学目的和要求", session.get("objectives", ""))
    label_content(3, "教学重点和难点", _combine_key_difficult(session))
    label_content(4, "教学方法和手段", _combine_methods(session))

    # Row 5: header "教学内容及过程 | 旁批"
    header_cells = rows[5].cells
    process_header = header_cells[0].merge(header_cells[1]).merge(header_cells[2])
    _write_label(process_header, "教学内容及过程")
    _write_label(header_cells[3], "旁批")

    # Row 6: process body | side notes
    body_cells = rows[6].cells
    process_cell = body_cells[0].merge(body_cells[1]).merge(body_cells[2])
    _render_markdown_into_cell(process_cell, session.get("process", ""))
    _write_plain(body_cells[3], session.get("side_notes", ""), size=9.5,
                 align=WD_ALIGN_PARAGRAPH.LEFT)

    # Row 7: 教学后记
    label_content(7, "教学后记", session.get("post_notes", ""))
    _set_col_widths(table)


def build_lesson_plan_docx(plan: dict[str, Any]) -> bytes:
    """Build the .docx bytes for a hydrated lesson-plan dict (cover + sessions)."""
    cover = plan.get("cover") or {}
    sessions = plan.get("sessions") or []
    document = Document()
    _setup_document(document)
    _write_cover(document, cover)
    for index, session in enumerate(sessions):
        document.add_page_break()
        caption = document.add_paragraph()
        caption.alignment = WD_ALIGN_PARAGRAPH.LEFT
        run = caption.add_run(f"第 {session.get('index') or index + 1} 次课")
        _set_run_font(run, size=11, bold=True, color="475569")
        caption.paragraph_format.space_after = Pt(4)
        _write_session_table(document, session)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# PDF / PNG previews (LibreOffice)
# ---------------------------------------------------------------------------
def convert_docx_to_pdf(docx_bytes: bytes, *, base_name: str = "lesson_plan") -> bytes:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("当前服务器未安装 LibreOffice，无法导出 PDF；请改用 Word 导出。")
    with tempfile.TemporaryDirectory(prefix="lanshare-lessonplan-") as temp_dir:
        work = Path(temp_dir)
        docx_path = work / f"{base_name}.docx"
        docx_path.write_bytes(docx_bytes)
        result = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(work), str(docx_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            stderr = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"LibreOffice PDF 转换失败：{stderr[:240] or '未知错误'}")
        pdf_path = docx_path.with_suffix(".pdf")
        if not pdf_path.exists():
            pdfs = sorted(work.glob("*.pdf"))
            pdf_path = pdfs[0] if pdfs else pdf_path
        if not pdf_path.exists():
            raise RuntimeError("LibreOffice PDF 转换未生成文件。")
        return pdf_path.read_bytes()


def convert_docx_to_png(docx_bytes: bytes, *, base_name: str = "lesson_plan", max_pages: int = 4) -> bytes:
    """Render the first few pages to a single stacked PNG (preview).

    Uses PyMuPDF (``fitz``) on the PDF — already available for PDF page rendering.
    """
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
