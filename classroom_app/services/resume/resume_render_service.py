"""Résumé HTML assembly + Word/PDF export (WYSIWYG core).

The same HTML string is BOTH the on-screen preview (rendered in an iframe) and the
source LibreOffice converts to PDF/DOCX — so "what you preview is what you export".
Templates are deliberately table-based with inline styles (no flexbox/grid) so the
LibreOffice HTML import stays faithful. Three built-in templates live in
``RESUME_TEMPLATES``; the structure (a template registry) is extensible.

Public API:

* ``list_templates()``                         — registry metadata for the builder UI.
* ``assemble_resume_html(conn, sid, resume)``   — full HTML document string.
* ``export_resume_bytes(html, fmt)``            — bytes for 'pdf' or 'docx'.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any, Callable

from . import resume_attachment_service as attach
from . import resume_profile_service as profile
from ..libreoffice_service import convert_office_file


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------
def list_templates() -> list[dict[str, str]]:
    return [
        {"key": key, "label": meta["label"], "description": meta["description"], "accent": meta["accent"]}
        for key, meta in RESUME_TEMPLATES.items()
    ]


def _template_meta(template_key: str) -> dict[str, Any]:
    return RESUME_TEMPLATES.get(str(template_key or "").strip()) or RESUME_TEMPLATES["classic"]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _esc(value: Any) -> str:
    return html_lib.escape(str(value if value is not None else "")).strip()


def _bold(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


def _md_to_html(text: str) -> str:
    """Minimal, LibreOffice-safe markdown: paragraphs + **bold** + line breaks + bullets."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    blocks = [b.strip() for b in raw.replace("\r\n", "\n").split("\n\n") if b.strip()]
    out: list[str] = []
    for block in blocks:
        if block.lstrip().startswith(("- ", "* ")):
            items = [
                f"<li>{_bold(_esc(line.lstrip('-* ').strip()))}</li>"
                for line in block.split("\n") if line.strip()
            ]
            out.append(f"<ul style='margin:4px 0 8px 18px;padding:0'>{''.join(items)}</ul>")
        else:
            safe = _bold(_esc(block).replace("\n", "<br>"))
            out.append(f"<p style='margin:0 0 8px'>{safe}</p>")
    return "".join(out)


_PERSONAL_LABELS = {
    "name": "姓名", "gender": "性别", "birthday": "出生年月", "phone": "电话",
    "email": "邮箱", "qq": "QQ", "wechat": "微信", "address": "现居地址",
    "hometown": "籍贯", "id_card": "身份证号", "expected_position": "期望岗位",
    "expected_industry": "期望行业", "expected_salary": "期望薪资",
}
_DEFAULT_PERSONAL_FIELDS = ("gender", "birthday", "phone", "email", "expected_position")
_EXPERIENCE_KIND_LABEL = {"project": "项目经验", "competition": "比赛经历"}
_EDUCATION_KIND_LABEL = {"high_school": "高中", "university": "大学", "training": "培训"}


# ---------------------------------------------------------------------------
# Build a normalized content model from layout + DB
# ---------------------------------------------------------------------------
def _index_by_id(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(r["id"]): r for r in rows}


def _pick(index: dict[int, dict[str, Any]], ids: list[Any]) -> list[dict[str, Any]]:
    picked = []
    for raw in ids or []:
        try:
            item = index.get(int(raw))
        except (TypeError, ValueError):
            item = None
        if item:
            picked.append(item)
    return picked


def build_content_model(conn, student_id: int, resume: dict[str, Any]) -> dict[str, Any]:
    layout = resume.get("layout") if isinstance(resume.get("layout"), dict) else {}
    bundle = profile.collect_profile_bundle(conn, student_id)
    personal = bundle.get("personal") or {}

    fields = layout.get("personal_fields")
    if not isinstance(fields, list) or not fields:
        fields = list(_DEFAULT_PERSONAL_FIELDS)
    personal_pairs = [
        {"label": _PERSONAL_LABELS.get(f, f), "value": _esc(personal.get(f))}
        for f in fields if f != "name" and str(personal.get(f) or "").strip()
    ]
    avatar_uri = None
    if str(personal.get("avatar_file_hash") or "").strip():
        avatar_uri = attach.attachment_data_uri(personal["avatar_file_hash"], personal.get("avatar_mime_type") or "image/png")

    edu_index = _index_by_id(bundle.get("education", []))
    exp_index = _index_by_id(bundle.get("experience", []))
    skill_index = _index_by_id(bundle.get("skill", []))
    cert_index = _index_by_id(bundle.get("certificate", []))
    intro_index = _index_by_id(bundle.get("self_intro", []))

    blocks_spec = layout.get("blocks") if isinstance(layout.get("blocks"), list) else []
    blocks: list[dict[str, Any]] = []
    for spec in blocks_spec:
        if not isinstance(spec, dict):
            continue
        btype = str(spec.get("type") or "").strip()
        if btype == "self_intro":
            items = _pick(intro_index, spec.get("ids"))
            if items:
                blocks.append({"type": "self_intro", "title": "个人介绍",
                               "html": "".join(_md_to_html(i.get("content_md")) for i in items)})
        elif btype == "tech_stack":
            groups = resume.get("tech_stack") if isinstance(resume.get("tech_stack"), list) else []
            if groups:
                blocks.append({"type": "tech_stack", "title": "技术栈", "groups": groups})
        elif btype == "education":
            items = _pick(edu_index, spec.get("ids"))
            if items:
                blocks.append({"type": "education", "title": "教育/学习经历",
                               "items": [_education_view(i) for i in items]})
        elif btype == "experience":
            items = _pick(exp_index, spec.get("ids"))
            if items:
                blocks.append({"type": "experience", "title": "项目/比赛经验",
                               "items": [_experience_view(i) for i in items]})
        elif btype == "skill_cert":
            skills = _pick(skill_index, spec.get("skill_ids"))
            certs = _pick(cert_index, spec.get("cert_ids"))
            if skills or certs:
                blocks.append({"type": "skill_cert", "title": "技能与证书",
                               "skills": [s.get("name") for s in skills],
                               "certs": [_cert_view(c) for c in certs]})

    return {
        "name": _esc(personal.get("name")) or "姓名",
        "headline": _esc(personal.get("expected_position")),
        "avatar": avatar_uri,
        "personal_pairs": personal_pairs,
        "blocks": blocks,
    }


def _education_view(row: dict[str, Any]) -> dict[str, Any]:
    sub_parts = [_EDUCATION_KIND_LABEL.get(row.get("kind"), "")]
    if str(row.get("college") or "").strip():
        sub_parts.append(_esc(row.get("college")))
    if str(row.get("major") or "").strip():
        sub_parts.append(_esc(row.get("major")))
    period = " - ".join(p for p in (_esc(row.get("start_date")), _esc(row.get("end_date"))) if p)
    return {"head": _esc(row.get("school")), "sub": " · ".join(p for p in sub_parts if p),
            "period": period, "body": _esc(row.get("content"))}


def _experience_view(row: dict[str, Any]) -> dict[str, Any]:
    period = " - ".join(p for p in (_esc(row.get("start_date")), _esc(row.get("end_date"))) if p)
    detail_lines = []
    for label, key in (("角色", "role"), ("内容", "content"), ("贡献", "contribution"), ("成果", "achievement")):
        if str(row.get(key) or "").strip():
            detail_lines.append(f"<strong>{label}：</strong>{_esc(row.get(key))}")
    return {"head": _esc(row.get("title")), "sub": _EXPERIENCE_KIND_LABEL.get(row.get("kind"), ""),
            "period": period, "body": "<br>".join(detail_lines)}


def _cert_view(row: dict[str, Any]) -> dict[str, Any]:
    return {"name": _esc(row.get("name")), "period": _esc(row.get("acquired_date")),
            "desc": _esc(row.get("description"))}


# ---------------------------------------------------------------------------
# Section HTML (shared across templates)
# ---------------------------------------------------------------------------
def _section_title(title: str, theme: dict[str, str]) -> str:
    return (
        f"<div style='font-size:15px;font-weight:700;color:{theme['accent']};"
        f"border-bottom:2px solid {theme['accent']};padding-bottom:3px;margin:14px 0 8px'>{_esc(title)}</div>"
    )


def _render_block(block: dict[str, Any], theme: dict[str, str]) -> str:
    btype = block.get("type")
    inner = ""
    if btype == "self_intro":
        inner = f"<div style='font-size:12.5px;line-height:1.7;color:#333'>{block.get('html') or ''}</div>"
    elif btype == "tech_stack":
        rows = []
        for grp in block.get("groups", []):
            items = "、".join(_esc(i) for i in grp.get("items", []))
            rows.append(
                f"<div style='margin:3px 0'><strong style='color:#222'>{_esc(grp.get('group'))}：</strong>"
                f"<span style='color:#444'>{items}</span></div>"
            )
        inner = "".join(rows)
    elif btype in ("education", "experience"):
        cards = []
        for item in block.get("items", []):
            period = f"<span style='float:right;color:#888;font-weight:400;font-size:12px'>{item['period']}</span>" if item.get("period") else ""
            sub = f"<span style='color:#777;font-size:12px;margin-left:8px'>{item['sub']}</span>" if item.get("sub") else ""
            body = f"<div style='font-size:12.5px;line-height:1.65;color:#444;margin-top:3px'>{item['body']}</div>" if item.get("body") else ""
            cards.append(
                f"<div style='margin-bottom:10px'><div style='font-size:13.5px;font-weight:700;color:#222'>"
                f"{period}{_esc(item['head'])}{sub}</div>{body}</div>"
            )
        inner = "".join(cards)
    elif btype == "skill_cert":
        parts = []
        if block.get("skills"):
            chips = "".join(
                f"<span style='display:inline-block;background:{theme['soft']};color:{theme['accent']};"
                f"border-radius:4px;padding:2px 9px;margin:0 5px 5px 0;font-size:12px'>{_esc(s)}</span>"
                for s in block.get("skills", []) if str(s or "").strip()
            )
            parts.append(f"<div style='margin-bottom:6px'>{chips}</div>")
        for cert in block.get("certs", []):
            period = f" <span style='color:#888;font-size:12px'>（{cert['period']}）</span>" if cert.get("period") else ""
            desc = f"<span style='color:#666;font-size:12px'> — {cert['desc']}</span>" if cert.get("desc") else ""
            parts.append(f"<div style='font-size:12.5px;margin:2px 0;color:#333'>• <strong>{_esc(cert['name'])}</strong>{period}{desc}</div>")
        inner = "".join(parts)
    return _section_title(block.get("title", ""), theme) + inner


def _personal_table(model: dict[str, Any]) -> str:
    pairs = model.get("personal_pairs") or []
    if not pairs:
        return ""
    rows = []
    for i in range(0, len(pairs), 3):
        tds = "".join(
            f"<td style='padding:3px 16px 3px 0;font-size:12.5px;color:#333;white-space:nowrap'>"
            f"<span style='color:#999'>{p['label']}：</span>{p['value']}</td>"
            for p in pairs[i:i + 3]
        )
        rows.append(f"<tr>{tds}</tr>")
    return f"<table style='border-collapse:collapse;margin-top:4px'>{''.join(rows)}</table>"


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
def _doc_shell(body: str) -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>body{margin:0;padding:0;font-family:'Microsoft YaHei','PingFang SC',sans-serif;color:#222}"
        "table{border-collapse:collapse}</style></head>"
        f"<body><div style='width:760px;margin:0 auto;padding:34px 40px;background:#fff'>{body}</div></body></html>"
    )


def _render_classic(model: dict[str, Any], theme: dict[str, str]) -> str:
    header = (
        f"<div style='font-size:26px;font-weight:800;color:#1a1a1a'>{model['name']}</div>"
        + (f"<div style='font-size:14px;color:{theme['accent']};font-weight:600;margin-top:2px'>{model['headline']}</div>" if model.get("headline") else "")
        + _personal_table(model)
    )
    avatar = (
        f"<td style='width:88px;vertical-align:top;text-align:right'>"
        f"<img src='{model['avatar']}' style='width:78px;height:100px;object-fit:cover;border-radius:6px'></td>"
        if model.get("avatar") else ""
    )
    head_row = (
        f"<table style='width:100%'><tr><td style='vertical-align:top'>{header}</td>{avatar}</tr></table>"
        f"<div style='height:2px;background:{theme['accent']};margin:12px 0 4px'></div>"
    )
    body = head_row + "".join(_render_block(b, theme) for b in model["blocks"])
    return _doc_shell(body)


def _render_modern(model: dict[str, Any], theme: dict[str, str]) -> str:
    avatar = (
        f"<img src='{model['avatar']}' style='width:74px;height:96px;object-fit:cover;border-radius:8px;border:2px solid #fff'>"
        if model.get("avatar") else ""
    )
    banner = (
        f"<table style='width:100%;background:{theme['accent']};border-radius:10px'><tr>"
        f"<td style='padding:18px 22px;vertical-align:middle'>"
        f"<div style='font-size:25px;font-weight:800;color:#fff'>{model['name']}</div>"
        + (f"<div style='font-size:13.5px;color:#eef;margin-top:3px'>{model['headline']}</div>" if model.get("headline") else "")
        + "</td>"
        + (f"<td style='padding:14px 22px 14px 0;text-align:right;width:96px'>{avatar}</td>" if avatar else "")
        + "</tr></table>"
    )
    info = f"<div style='margin:8px 0'>{_personal_table(model)}</div>" if model.get("personal_pairs") else ""
    body = banner + info + "".join(_render_block(b, theme) for b in model["blocks"])
    return _doc_shell(body)


def _render_sidebar(model: dict[str, Any], theme: dict[str, str]) -> str:
    side_blocks = [b for b in model["blocks"] if b.get("type") in ("skill_cert", "tech_stack")]
    main_blocks = [b for b in model["blocks"] if b.get("type") not in ("skill_cert", "tech_stack")]
    avatar = (
        f"<div style='text-align:center;margin-bottom:10px'><img src='{model['avatar']}' "
        f"style='width:96px;height:120px;object-fit:cover;border-radius:8px'></div>"
        if model.get("avatar") else ""
    )
    contact = "".join(
        f"<div style='font-size:12px;color:#eaf;margin:3px 0'><span style='opacity:.75'>{p['label']}</span><br>{p['value']}</div>"
        for p in model.get("personal_pairs", [])
    )
    side_inner = avatar + (f"<div style='margin-bottom:10px'>{contact}</div>" if contact else "")
    for b in side_blocks:
        side_inner += _render_block_sidebar(b, theme)
    side = (
        f"<td style='width:208px;vertical-align:top;background:{theme['accent']};color:#fff;padding:22px 16px'>"
        f"<div style='font-size:21px;font-weight:800;margin-bottom:2px'>{model['name']}</div>"
        + (f"<div style='font-size:12.5px;color:#eef;margin-bottom:12px'>{model['headline']}</div>" if model.get("headline") else "<div style='height:8px'></div>")
        + side_inner + "</td>"
    )
    main = "<td style='vertical-align:top;padding:22px 24px'>" + "".join(_render_block(b, theme) for b in main_blocks) + "</td>"
    body = f"<table style='width:100%;border-collapse:collapse'><tr>{side}{main}</tr></table>"
    return _doc_shell(body)


def _render_block_sidebar(block: dict[str, Any], theme: dict[str, str]) -> str:
    title = f"<div style='font-size:13px;font-weight:700;color:#fff;border-bottom:1px solid rgba(255,255,255,.4);padding-bottom:3px;margin:10px 0 6px'>{_esc(block.get('title'))}</div>"
    if block.get("type") == "tech_stack":
        rows = "".join(
            f"<div style='font-size:11.5px;color:#eef;margin:2px 0'><strong>{_esc(g.get('group'))}</strong>：{'、'.join(_esc(i) for i in g.get('items', []))}</div>"
            for g in block.get("groups", [])
        )
        return title + rows
    chips = "".join(
        f"<span style='display:inline-block;background:rgba(255,255,255,.2);color:#fff;border-radius:4px;padding:2px 8px;margin:0 4px 4px 0;font-size:11.5px'>{_esc(s)}</span>"
        for s in block.get("skills", []) if str(s or "").strip()
    )
    certs = "".join(
        f"<div style='font-size:11.5px;color:#eef;margin:2px 0'>• {_esc(c['name'])}</div>"
        for c in block.get("certs", [])
    )
    return title + (f"<div>{chips}</div>" if chips else "") + certs


RESUME_TEMPLATES: dict[str, dict[str, Any]] = {
    "classic": {
        "label": "经典单栏",
        "description": "稳重的单栏排版，适合通用求职与校招。",
        "accent": "#2563eb",
        "soft": "#e8efff",
        "render": _render_classic,
    },
    "sidebar": {
        "label": "双栏侧边",
        "description": "左侧彩色信息栏 + 右侧主体，重点突出技能。",
        "accent": "#0d9488",
        "soft": "#d9f3ee",
        "render": _render_sidebar,
    },
    "modern": {
        "label": "现代强调",
        "description": "彩色标题横幅 + 强调色分节，富有设计感。",
        "accent": "#7c3aed",
        "soft": "#efe7fe",
        "render": _render_modern,
    },
}


def assemble_resume_html(conn, student_id: int, resume: dict[str, Any]) -> str:
    """Build the full résumé HTML document (preview === export source)."""
    meta = _template_meta(resume.get("template_key"))
    theme = {"accent": meta["accent"], "soft": meta["soft"]}
    model = build_content_model(conn, student_id, resume)
    render_fn: Callable[[dict[str, Any], dict[str, str]], str] = meta["render"]
    return render_fn(model, theme)


def export_resume_bytes(render_html: str, fmt: str) -> bytes:
    """Convert stored résumé HTML to PDF/DOCX bytes via LibreOffice."""
    import tempfile
    from pathlib import Path

    fmt = str(fmt or "pdf").strip().lower()
    output_format = "pdf" if fmt == "pdf" else "docx:MS Word 2007 XML"
    with tempfile.TemporaryDirectory(prefix="lanshare-resume-") as root:
        html_path = Path(root) / "resume.html"
        html_path.write_text(render_html or "<html><body></body></html>", encoding="utf-8")
        return convert_office_file(html_path, output_format, timeout=120).output_bytes


def parse_resume_row(row: dict[str, Any]) -> dict[str, Any]:
    """Decode JSON columns on a raw ``resumes`` row."""
    item = dict(row)
    try:
        item["layout"] = json.loads(item.get("layout_json") or "{}")
    except (TypeError, ValueError):
        item["layout"] = {}
    try:
        item["tech_stack"] = json.loads(item.get("tech_stack_json") or "[]")
    except (TypeError, ValueError):
        item["tech_stack"] = []
    return item
