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
import os
import hashlib
import tempfile
from datetime import date, timedelta
from pathlib import Path
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
_EXPERIENCE_KIND_LABEL = profile.EXPERIENCE_KINDS
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


def _capability_groups(raw: Any) -> list[dict[str, Any]]:
    """Keep legacy flat skill lists renderable without trusting malformed AI JSON."""
    if isinstance(raw, dict):
        raw = raw.get("groups")
    if not isinstance(raw, list):
        return []
    groups, flat = [], []
    for item in raw[:40]:
        if isinstance(item, str) and item.strip():
            flat.append(item.strip()[:160])
        elif isinstance(item, dict) and isinstance(item.get("items"), list):
            values = [value.strip()[:160] for value in item["items"][:24] if isinstance(value, str) and value.strip()]
            if values:
                groups.append({"group": str(item.get("group") or "相关技能")[:80], "items": values})
    if flat:
        groups.insert(0, {"group": "相关技能", "items": flat[:24]})
    return groups[:16]


def build_content_model(conn, student_id: int, resume: dict[str, Any]) -> dict[str, Any]:
    layout = resume.get("layout") if isinstance(resume.get("layout"), dict) else {}
    bundle = resume.get("content_snapshot")
    if not isinstance(bundle, dict):
        bundle = profile.collect_profile_bundle(conn, student_id)
    personal = bundle.get("personal") or {}
    target_position = str(resume.get("target_position") or personal.get("expected_position") or "").strip()

    fields = layout.get("personal_fields")
    if not isinstance(fields, list) or not fields:
        fields = list(_DEFAULT_PERSONAL_FIELDS)
    personal_pairs = [
        {
            "label": _PERSONAL_LABELS.get(f, f),
            "value": _esc(target_position if f == "expected_position" else personal.get(f)),
        }
        for f in fields
        if f != "name" and str((target_position if f == "expected_position" else personal.get(f)) or "").strip()
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
            groups = _capability_groups(resume.get("tech_stack"))
            if groups:
                blocks.append({"type": "tech_stack", "title": "专业能力 / 相关技能", "groups": groups})
        elif btype == "education":
            items = _pick(edu_index, spec.get("ids"))
            if items:
                blocks.append({"type": "education", "title": "教育/学习经历",
                               "items": [_education_view(i) for i in items]})
        elif btype == "experience":
            items = _pick(exp_index, spec.get("ids"))
            if items:
                blocks.append({"type": "experience", "title": "实践经历",
                               "items": [_experience_view(i) for i in items]})
        elif btype == "skill_cert":
            skills = _pick(skill_index, spec.get("skill_ids"))
            certs = _pick(cert_index, spec.get("cert_ids"))
            if skills or certs:
                blocks.append({"type": "skill_cert", "title": "技能与证书",
                               "skills": [s.get("name") for s in skills],
                               "certs": [_cert_view(c) for c in certs]})

    optimized_summary = str(resume.get("optimized_summary_md") or "").strip()
    if optimized_summary:
        optimized_block = {
            "type": "self_intro",
            "title": "个人介绍",
            "html": _md_to_html(optimized_summary),
            "optimized": True,
        }
        for index, block in enumerate(blocks):
            if block.get("type") == "self_intro":
                blocks[index] = optimized_block
                break
        else:
            blocks.insert(0, optimized_block)

    return {
        "name": _esc(personal.get("name")) or "姓名",
        "headline": _esc(target_position),
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
    if str(row.get("degree") or "").strip():
        sub_parts.append(_esc(row.get("degree")))
    period = " - ".join(p for p in (_esc(row.get("start_date")), _esc(row.get("end_date"))) if p)
    return {"head": _esc(row.get("school")), "sub": " · ".join(p for p in sub_parts if p),
            "period": period, "body": _esc(row.get("content"))}


def _experience_view(row: dict[str, Any]) -> dict[str, Any]:
    period = " - ".join(p for p in (_esc(row.get("start_date")), _esc(row.get("end_date"))) if p)
    lead = ""
    detail_lines = []
    for label, key in (("角色", "role"), ("内容", "content"), ("贡献", "contribution"), ("成果", "achievement")):
        value = str(row.get(key) or "").strip()
        if not value:
            continue
        if not lead:
            # A table cell can ignore keep-with-next when Word repaginates it.
            # Keep the title and a short actual fact in ONE non-splitting
            # paragraph. Prefer a natural sentence/line boundary; retain every
            # remaining character below, without inventing an ellipsis.
            boundary = re.search(r"[。！？!?\n]|\.(?=\s|$)", value[:160])
            cut = boundary.end() if boundary else min(len(value), 160)
            if not boundary and cut < len(value):
                space = value.rfind(" ", 0, cut)
                if space > cut // 2:
                    cut = space + 1
            lead = f"<strong>{label}：</strong>{_esc(value[:cut])}"
            if value[cut:]:
                detail_lines.append(_esc(value[cut:]))
        else:
            detail_lines.append(f"<strong>{label}：</strong>{_esc(value)}")
    return {"head": _esc(row.get("title")), "sub": _EXPERIENCE_KIND_LABEL.get(row.get("kind"), ""),
            "period": period, "lead": lead, "body": "<br>".join(detail_lines)}


def _cert_view(row: dict[str, Any]) -> dict[str, Any]:
    return {"name": _esc(row.get("name")), "period": _esc(row.get("acquired_date")),
            "desc": _esc(row.get("description"))}


# ---------------------------------------------------------------------------
# Section HTML (shared across templates)
# ---------------------------------------------------------------------------
def _section_title(title: str, theme: dict[str, str]) -> str:
    return (
        f"<p style='font-size:15px;font-weight:700;color:{theme['accent']};"
        f"margin:14px 0 8px;page-break-after:avoid'>{_esc(title)}</p>"
    )


def _render_block(block: dict[str, Any], theme: dict[str, str], *, table_headers: bool = False) -> str:
    btype = block.get("type")
    inner = ""
    if btype == "self_intro":
        inner = f"<div style='font-size:10pt;line-height:145%;color:#333'>{block.get('html') or ''}</div>"
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
            period = f"<span style='color:#777;font-weight:400;font-size:12px'> · {item['period']}</span>" if item.get("period") else ""
            sub = f"<span style='color:#555;font-size:12px'> · {item['sub']}</span>" if item.get("sub") else ""
            body = f"<p style='font-size:10pt;line-height:145%;color:#444;margin:3px 0 8px'>{item['body']}</p>" if item.get("body") else ""
            lead = (
                f"<br><span style='font-size:10pt;font-weight:400;line-height:145%;color:#444'>{item['lead']}</span>"
                if item.get("lead") else ""
            )
            heading = (
                f"<p style='font-size:13.5px;font-weight:700;color:#222;margin:8px 0 3px;page-break-after:avoid;page-break-inside:avoid'>"
                f"{item['head']}{sub}{period}{lead}</p>"
            )
            if table_headers:
                # Word repagination can ignore paragraph keepLines within the
                # sidebar's multi-page row. A small nested row imports as
                # w:cantSplit, keeping the title and first fact together while
                # allowing the unbounded remaining body to flow across pages.
                heading = (
                    "<table width='100%' cellspacing='0' cellpadding='0' border='0' "
                    "style='width:100%;page-break-inside:avoid'><tr style='page-break-inside:avoid'>"
                    f"<td style='padding:0'>{heading}</td></tr></table>"
                )
            cards.append(f"<div style='margin-bottom:10px'>{heading}{body}</div>")
        inner = "".join(cards)
    elif btype == "skill_cert":
        parts = []
        if block.get("skills"):
            chips = " · ".join(
                f"<span style='display:inline-block;background:{theme['soft']};color:{theme['accent']};"
                f"border-radius:4px;padding:2px 9px;margin:0 5px 5px 0;font-size:12px'>{_esc(s)}</span>"
                for s in block.get("skills", []) if str(s or "").strip()
            )
            parts.append(f"<div style='margin-bottom:6px'>{chips}</div>")
        for cert in block.get("certs", []):
            period = f" <span style='color:#888;font-size:12px'>（{cert['period']}）</span>" if cert.get("period") else ""
            desc = f"<span style='color:#666;font-size:12px'> — {cert['desc']}</span>" if cert.get("desc") else ""
            parts.append(f"<div style='font-size:12.5px;margin:2px 0;color:#333'>• <strong>{cert['name']}</strong>{period}{desc}</div>")
        inner = "".join(parts)
    return _section_title(block.get("title", ""), theme) + inner


def _personal_table(model: dict[str, Any]) -> str:
    pairs = model.get("personal_pairs") or []
    if not pairs:
        return ""
    rows = []
    for i in range(0, len(pairs), 2):
        tds = "".join(
            f"<td width='50%' valign='top' style='width:50%;padding:3px 8px 3px 0;font-size:12.5px;color:#333;word-wrap:break-word'>"
            f"<span style='color:#666'>{p['label']}：</span>{p['value']}</td>"
            for p in pairs[i:i + 2]
        )
        rows.append(f"<tr>{tds}</tr>")
    return f"<table width='100%' cellspacing='0' cellpadding='3' border='0' style='width:100%;table-layout:fixed;border-collapse:collapse;margin-top:4px'>{''.join(rows)}</table>"


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
def _doc_shell(body: str) -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>@page{size:A4;margin:18mm}body{margin:0;padding:0;font-family:'Microsoft YaHei','PingFang SC',sans-serif;color:#222;font-size:10pt}"
        "table{border-collapse:collapse}p{orphans:2;widows:2}"
        "@media screen{body{max-width:174mm;margin:0 auto;padding:18mm;background:#fff}}"
        "</style></head>"
        f"<body>{body}</body></html>"
    )


def _render_classic(model: dict[str, Any], theme: dict[str, str]) -> str:
    header = (
        f"<div style='font-size:26px;font-weight:800;color:#1a1a1a'>{model['name']}</div>"
        + (f"<div style='font-size:14px;color:{theme['accent']};font-weight:600;margin-top:2px'>{model['headline']}</div>" if model.get("headline") else "")
        + _personal_table(model)
    )
    avatar = (
        f"<td width='88' valign='top' align='right' style='width:88px;vertical-align:top;text-align:right'>"
        f"<img src='{model['avatar']}' width='78' height='100' style='width:78px;height:100px;object-fit:cover;border-radius:6px'></td>"
        if model.get("avatar") else ""
    )
    head_row = (
        f"<table width='100%' cellspacing='0' cellpadding='0' border='0' style='width:100%'><tr><td valign='top' style='vertical-align:top'>{header}</td>{avatar}</tr></table>"
    )
    body = head_row + "".join(_render_block(b, theme) for b in model["blocks"])
    return _doc_shell(body)


def _render_modern(model: dict[str, Any], theme: dict[str, str]) -> str:
    avatar = (
        f"<img src='{model['avatar']}' width='74' height='96' style='width:74px;height:96px;object-fit:cover;border-radius:8px'>"
        if model.get("avatar") else ""
    )
    banner = (
        f"<table width='100%' cellspacing='0' cellpadding='12' border='0' bgcolor='{theme['accent']}' style='width:100%;background:{theme['accent']};border-radius:10px'><tr>"
        f"<td valign='middle' style='padding:18px 22px;vertical-align:middle'>"
        f"<div style='font-size:25px;font-weight:800;color:#fff'>{model['name']}</div>"
        + (f"<div style='font-size:13.5px;color:#eef;margin-top:3px'>{model['headline']}</div>" if model.get("headline") else "")
        + "</td>"
        + (f"<td width='96' valign='middle' align='right' style='padding:14px 22px 14px 0;text-align:right;width:96px'>{avatar}</td>" if avatar else "")
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
        f"width='90' height='120' style='width:90px;height:120px;object-fit:cover;border-radius:8px'></div>"
        if model.get("avatar") else ""
    )
    contact = "".join(
        f"<p style='font-size:12px;color:#ffffff;margin:6px 0'>{p['label']}<br>{p['value']}</p>"
        for p in model.get("personal_pairs", [])
    )
    side_inner = avatar + (f"<div style='margin-bottom:10px'>{contact}</div>" if contact else "")
    for b in side_blocks:
        side_inner += _render_block_sidebar(b, theme)
    side = (
        f"<td width='28%' valign='top' bgcolor='{theme['accent']}' style='width:28%;vertical-align:top;background:{theme['accent']};color:#fff;padding:12px'>"
        f"<p style='font-size:20px;font-weight:800;color:#ffffff;margin:0 0 4px'>{model['name']}</p>"
        + (f"<div style='font-size:12.5px;color:#eef;margin-bottom:12px'>{model['headline']}</div>" if model.get("headline") else "<div style='height:8px'></div>")
        + side_inner + "</td>"
    )
    gutter = "<td width='4%' bgcolor='#ffffff' style='width:4%'>&nbsp;</td>"
    main = "<td width='68%' valign='top' style='width:68%;vertical-align:top;padding:12px'>" + "".join(_render_block(b, theme, table_headers=True) for b in main_blocks) + "</td>"
    body = f"<table width='100%' cellspacing='0' cellpadding='9' border='0' style='width:100%;table-layout:fixed;border-collapse:collapse'><tr>{side}{gutter}{main}</tr></table>"
    return _doc_shell(body)


def _render_block_sidebar(block: dict[str, Any], theme: dict[str, str]) -> str:
    title = f"<p style='font-size:13px;font-weight:700;color:#ffffff;margin:10px 0 6px;page-break-after:avoid'>{_esc(block.get('title'))}</p>"
    if block.get("type") == "tech_stack":
        rows = "".join(
            f"<div style='font-size:11.5px;color:#eef;margin:2px 0'><strong>{_esc(g.get('group'))}</strong>：{'、'.join(_esc(i) for i in g.get('items', []))}</div>"
            for g in block.get("groups", [])
        )
        return title + rows
    chips = "".join(
        f"<p style='color:#ffffff;margin:3px 0;font-size:11.5px'>{_esc(s)}</p>"
        for s in block.get("skills", []) if str(s or "").strip()
    )
    certs = "".join(
        f"<p style='font-size:11.5px;color:#ffffff;margin:6px 0'>• {c['name']}"
        + (f"<br>{c['period']}" if c.get("period") else "")
        + (f"<br>{c['desc']}" if c.get("desc") else "") + "</p>"
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


class ResumeExportBusy(RuntimeError):
    pass


def export_resume_cached(render_html: str, fmt: str) -> bytes:
    """Content-addressed derivative cache and a kernel-released process limit.

    A killed converter cannot strand a lock. The route has already authorized
    the immutable version, so cache files never become a public download URL.
    """
    if fmt not in {"pdf", "docx"}:
        raise ValueError("仅支持 PDF 或 Word 导出")
    root = Path(os.environ.get("RESUME_EXPORT_CACHE_DIR") or (Path(tempfile.gettempdir()) / "lanshare-resume-exports"))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    renderer_version = os.environ.get("RESUME_RENDERER_VERSION", "resume-html-v5")
    key = hashlib.sha256((renderer_version + ":" + fmt + ":" + render_html).encode()).hexdigest()
    # Date buckets bound both cache lookup and garbage collection. Maintenance
    # never scans thousands of current derivatives to find a few expired ones.
    today = date.today()
    candidates = [root / (today - timedelta(days=age)).isoformat() / (key + "." + fmt) for age in range(8)]
    target = candidates[0]
    for cached in candidates:
        try:
            return cached.read_bytes()
        except FileNotFoundError:
            pass
    with (root / "conversion.lock").open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                # Windows mandatory byte locks can reject this read before
                # locking(), so initialization belongs in the same busy guard.
                if lock.read(1) == b"":
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ResumeExportBusy("文档转换正在处理其他请求，请稍后重试。") from exc
        else:
            import fcntl
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ResumeExportBusy("文档转换正在处理其他请求，请稍后重试。") from exc
        try:
            for cached in candidates:
                try:
                    return cached.read_bytes()
                except FileNotFoundError:
                    pass
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            data = export_resume_bytes(render_html, fmt)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".export-", delete=False) as output:
                    temporary = output.name
                    output.write(data)
                os.replace(temporary, target)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
            return data
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def cleanup_export_cache(*, max_age_days: int = 7, limit: int = 100) -> int:
    """Bounded maintenance of private reconstructible derivatives."""
    import time
    root = Path(os.environ.get("RESUME_EXPORT_CACHE_DIR") or (Path(tempfile.gettempdir()) / "lanshare-resume-exports"))
    if not root.is_dir():
        return 0
    cutoff, removed, inspected = time.time() - max_age_days * 86400, 0, 0
    cutoff_date = (date.today() - timedelta(days=max_age_days)).isoformat()
    buckets = []
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            if index >= 512:
                break
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry.name) and entry.name <= cutoff_date and entry.is_dir(follow_symlinks=False):
                buckets.append(Path(entry.path))
    for bucket in sorted(buckets):
        with os.scandir(bucket) as entries:
            for entry in entries:
                if inspected >= max(1, limit) * 2 or removed >= limit:
                    return removed
                inspected += 1
                path = Path(entry.path)
                if not entry.is_file(follow_symlinks=False) or path.suffix not in {".pdf", ".docx"} or not re.fullmatch(r"[0-9a-f]{64}", path.stem):
                    continue
                try:
                    if entry.stat(follow_symlinks=False).st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except OSError:
                    pass
        try:
            bucket.rmdir()  # Empty bucket only; never recursively delete files.
        except OSError:
            pass
    return removed


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
    try:
        item["optimization_notes"] = json.loads(item.get("optimization_notes_json") or "{}")
    except (TypeError, ValueError):
        item["optimization_notes"] = {}
    try:
        item["import_summary"] = json.loads(item.get("import_summary_json") or "{}")
    except (TypeError, ValueError):
        item["import_summary"] = {}
    try:
        item["source_context"] = json.loads(item.get("source_context_json") or "{}")
    except (TypeError, ValueError):
        item["source_context"] = {}
    return item
