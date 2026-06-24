"""Render a 教案 to standalone preview HTML mirroring the .docx layout.

Used by the "渲染查看效果" preview (shown in a modal/iframe and screenshot-able)
and by the editor's live preview. Reuses the shared Markdown renderer
(:mod:`lesson_plan_markdown`) for the 教学内容及过程 cell so the on-screen layout
matches the exported Word exactly.
"""

from __future__ import annotations

import html
from typing import Any

from . import lesson_plan_markdown as md

_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; background: #eef1f5; font-family: "Microsoft YaHei","PingFang SC",sans-serif; color: #1f2937; }
.lp-page { background: #fff; width: 820px; max-width: 96%; margin: 24px auto; padding: 48px 56px;
           box-shadow: 0 4px 24px rgba(15,23,42,.12); }
.lp-cover { text-align: center; min-height: 760px; display: flex; flex-direction: column; }
.lp-cover h1 { font-size: 52px; font-weight: 800; letter-spacing: 18px; margin: 90px 0 36px; font-family: "SimHei","Microsoft YaHei",sans-serif; }
.lp-cover .lp-sem { font-size: 20px; margin-bottom: 48px; }
.lp-cover .lp-imprint { font-size: 17px; margin-top: auto; padding-top: 60px; }
table.lp-cover-table { border-collapse: collapse; margin: 0 auto; width: 78%; }
table.lp-cover-table td { border: 1px solid #111; padding: 12px 14px; font-size: 15px; }
table.lp-cover-table td.lp-label { background: #f2f2f2; font-weight: 700; text-align: center; white-space: nowrap; width: 110px; }
.lp-session { margin: 0; }
.lp-session .lp-cap { font-weight: 700; color: #475569; margin: 0 0 8px; }
table.lp-session-table { border-collapse: collapse; width: 100%; table-layout: fixed; }
table.lp-session-table td { border: 1px solid #111; padding: 8px 10px; font-size: 13.5px; vertical-align: top; }
table.lp-session-table td.lp-label { background: #f2f2f2; font-weight: 700; text-align: center; width: 120px; white-space: nowrap; vertical-align: middle; }
table.lp-session-table td.lp-side { width: 150px; font-size: 12.5px; color: #374151; }
.lp-process h3, .lp-process h4, .lp-process h5 { margin: 10px 0 4px; color: #1f2937; }
.lp-process p { margin: 4px 0; line-height: 1.7; }
.lp-process ul, .lp-process ol { margin: 4px 0 4px 20px; }
.lp-process table.lp-md-table { border-collapse: collapse; width: 100%; margin: 8px 0; }
.lp-process table.lp-md-table th, .lp-process table.lp-md-table td { border: 1px solid #cbd5e1; padding: 5px 7px; font-size: 12.5px; vertical-align: top; }
.lp-process table.lp-md-table th { background: #eef2f7; }
.lp-process pre.lp-md-code { background: #0f172a; color: #e2e8f0; padding: 10px 12px; border-radius: 6px; overflow-x: auto; font-size: 12px; }
.lp-empty { color: #94a3b8; }
@media print { body { background: #fff; } .lp-page { box-shadow: none; margin: 0; } }
"""


def _esc(value: Any) -> str:
    return html.escape(str(value or ""))


def _multiline(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "<span class='lp-empty'>—</span>"
    return "<br>".join(_esc(line) for line in text.split("\n"))


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


def _key_difficult(session: dict[str, Any]) -> str:
    parts = []
    if session.get("key_points"):
        parts.append(f"<strong>重点：</strong><br>{_multiline(session['key_points'])}")
    if session.get("difficulties"):
        parts.append(f"<strong>难点：</strong><br>{_multiline(session['difficulties'])}")
    return "<br><br>".join(parts) or "<span class='lp-empty'>—</span>"


def _methods(session: dict[str, Any]) -> str:
    parts = []
    if session.get("methods"):
        parts.append(f"<strong>教学方法：</strong>{_esc(session['methods'])}")
    if session.get("means"):
        parts.append(f"<strong>教学手段：</strong>{_esc(session['means'])}")
    return "<br>".join(parts) or "<span class='lp-empty'>—</span>"


def _render_cover(cover: dict[str, Any]) -> str:
    def span_row(label: str, value: Any) -> str:
        return f"<tr><td class='lp-label'>{_esc(label)}</td><td colspan='3'>{_esc(value) or ''}</td></tr>"

    rows = [
        span_row("课程名称", cover.get("course_name")),
        span_row("课程类别", cover.get("course_category")),
        (
            f"<tr><td class='lp-label'>学　分</td><td>{_esc(cover.get('credits'))}</td>"
            f"<td class='lp-label'>学　时</td><td>{_esc(cover.get('total_hours'))}</td></tr>"
        ),
        span_row("授课教师", cover.get("teacher_name")),
        span_row("教学单位", cover.get("teaching_unit")),
        span_row("授课班级", cover.get("class_name")),
        span_row("使用教材", cover.get("textbook")),
        span_row("出版社", cover.get("publisher")),
    ]
    imprint = f"{_esc(cover.get('school_name'))}{_esc(cover.get('teaching_unit'))}　印制"
    return (
        "<section class='lp-page lp-cover'>"
        "<h1>教　案</h1>"
        f"<div class='lp-sem'>{_esc(cover.get('semester_label'))}</div>"
        f"<table class='lp-cover-table'>{''.join(rows)}</table>"
        f"<div class='lp-imprint'>{imprint}</div>"
        "</section>"
    )


def _render_session(session: dict[str, Any], index: int) -> str:
    process_html = md.markdown_to_html(session.get("process", "")) or "<span class='lp-empty'>（待补充）</span>"
    side = _multiline(session.get("side_notes"))
    return (
        "<section class='lp-session'>"
        f"<div class='lp-cap'>第 {session.get('index') or index} 次课</div>"
        "<table class='lp-session-table'>"
        f"<tr><td class='lp-label'>授课时间</td><td colspan='3'>{_esc(_schedule_text(session))}</td></tr>"
        f"<tr><td class='lp-label'>授课章节</td><td colspan='3'>{_esc(session.get('chapter'))}</td></tr>"
        f"<tr><td class='lp-label'>教学目的和要求</td><td colspan='3'>{_multiline(session.get('objectives'))}</td></tr>"
        f"<tr><td class='lp-label'>教学重点和难点</td><td colspan='3'>{_key_difficult(session)}</td></tr>"
        f"<tr><td class='lp-label'>教学方法和手段</td><td colspan='3'>{_methods(session)}</td></tr>"
        "<tr><td class='lp-label' colspan='3'>教学内容及过程</td><td class='lp-label'>旁批</td></tr>"
        f"<tr><td colspan='3' class='lp-process'>{process_html}</td><td class='lp-side'>{side}</td></tr>"
        f"<tr><td class='lp-label'>教学后记</td><td colspan='3'>{_multiline(session.get('post_notes'))}</td></tr>"
        "</table>"
        "</section>"
    )


def render_plan_body(plan: dict[str, Any]) -> str:
    """The cover + session sections only (no <html> wrapper)."""
    cover = plan.get("cover") or {}
    sessions = plan.get("sessions") or []
    parts = [_render_cover(cover)]
    if not sessions:
        parts.append("<section class='lp-page'><p class='lp-empty'>暂无课次内容。</p></section>")
    for idx, session in enumerate(sessions, start=1):
        parts.append("<div class='lp-page'>" + _render_session(session, idx) + "</div>")
    return "\n".join(parts)


def render_plan_html(plan: dict[str, Any]) -> str:
    """A full standalone HTML document for the preview iframe / screenshot."""
    title = _esc((plan.get("cover") or {}).get("course_name") or plan.get("title") or "教案预览")
    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title} · 教案预览</title><style>{_STYLE}</style></head>"
        f"<body>{render_plan_body(plan)}</body></html>"
    )
