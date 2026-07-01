"""CRUD for assembled résumés (``resumes`` table).

A résumé is created in status ``rendering`` (the list page shows a placeholder
card that polls), then the background render job (``resume_generation_service``)
fills ``render_html`` / ``tech_stack_json`` and flips status to ``ready`` (or
``failed`` with ``error_text``). Editing re-enters ``rendering``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ...db.connection import execute_insert_returning_id
from ...db.schema_resume import ensure_resume_schema
from . import resume_render_service as render


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _normalize_layout(layout: Any) -> dict[str, Any]:
    if not isinstance(layout, dict):
        return {}
    safe: dict[str, Any] = {}
    fields = layout.get("personal_fields")
    if isinstance(fields, list):
        safe["personal_fields"] = [str(f) for f in fields][:20]
    blocks_in = layout.get("blocks") if isinstance(layout.get("blocks"), list) else []
    blocks: list[dict[str, Any]] = []
    for spec in blocks_in:
        if not isinstance(spec, dict):
            continue
        btype = str(spec.get("type") or "").strip()
        if btype not in ("self_intro", "tech_stack", "education", "experience", "skill_cert"):
            continue
        entry: dict[str, Any] = {"type": btype}
        for key in ("ids", "skill_ids", "cert_ids"):
            if isinstance(spec.get(key), list):
                entry[key] = [int(x) for x in spec[key] if str(x).strip().lstrip("-").isdigit()][:50]
        blocks.append(entry)
    safe["blocks"] = blocks
    return safe


def normalize_layout(layout: Any) -> dict[str, Any]:
    """Public wrapper used by request validation without creating a document."""
    return _normalize_layout(layout)


def list_resumes(conn, student_id: int) -> list[dict[str, Any]]:
    ensure_resume_schema(conn)
    rows = conn.execute(
        "SELECT id, title, target_position, template_key, optimized_summary_md, optimization_notes_json, "
        "source_filename, source_mime_type, source_file_size, import_summary_json, "
        "status, error_text, created_at, updated_at "
        "FROM resumes WHERE student_id = ? ORDER BY created_at DESC, id DESC",
        (int(student_id),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["optimization_notes"] = json.loads(item.get("optimization_notes_json") or "{}")
        except (TypeError, ValueError):
            item["optimization_notes"] = {}
        try:
            item["import_summary"] = json.loads(item.get("import_summary_json") or "{}")
        except (TypeError, ValueError):
            item["import_summary"] = {}
        item.pop("optimization_notes_json", None)
        item.pop("import_summary_json", None)
        items.append(item)
    return items


def get_resume(conn, student_id: int, resume_id: int) -> dict[str, Any]:
    ensure_resume_schema(conn)
    row = conn.execute(
        "SELECT * FROM resumes WHERE id = ? AND student_id = ? LIMIT 1",
        (int(resume_id), int(student_id)),
    ).fetchone()
    if not row:
        raise ValueError("未找到该简历")
    return render.parse_resume_row(dict(row))


def create_resume(conn, student_id: int, *, title: str, template_key: str,
                  layout: Any, target_position: str = "") -> int:
    ensure_resume_schema(conn)
    now = _now()
    layout_json = json.dumps(_normalize_layout(layout), ensure_ascii=False)
    return int(
        execute_insert_returning_id(
            conn,
            "INSERT INTO resumes (student_id, title, target_position, template_key, layout_json, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'rendering', ?, ?)",
            (
                int(student_id), str(title or "我的简历")[:120], str(target_position or "")[:120],
                str(template_key or "classic")[:40], layout_json, now, now,
            ),
        )
    )


def create_import_resume(
    conn,
    student_id: int,
    *,
    filename: str,
    file_hash: str,
    mime_type: str = "",
    file_size: int = 0,
) -> int:
    ensure_resume_schema(conn)
    now = _now()
    title = f"导入解析：{str(filename or '简历文件')[:90]}"
    summary = {
        "source": "import",
        "source_filename": str(filename or "")[:240],
        "message": "正在解析简历文件",
    }
    return int(
        execute_insert_returning_id(
            conn,
            "INSERT INTO resumes (student_id, title, template_key, layout_json, status, "
            "source_file_hash, source_filename, source_mime_type, source_file_size, "
            "import_summary_json, created_at, updated_at) "
            "VALUES (?, ?, 'classic', '{}', 'parsing', ?, ?, ?, ?, ?, ?, ?)",
            (
                int(student_id),
                title[:120],
                str(file_hash or "")[:128],
                str(filename or "")[:240],
                str(mime_type or "")[:100],
                int(file_size or 0),
                json.dumps(summary, ensure_ascii=False),
                now,
                now,
            ),
        )
    )


def update_resume(conn, student_id: int, resume_id: int, *, title: str, template_key: str,
                  layout: Any, target_position: str = "") -> None:
    get_resume(conn, student_id, resume_id)  # ownership
    layout_json = json.dumps(_normalize_layout(layout), ensure_ascii=False)
    conn.execute(
        "UPDATE resumes SET title = ?, target_position = ?, template_key = ?, layout_json = ?, "
        "optimized_summary_md = '', optimization_notes_json = '{}', status = 'rendering', "
        "error_text = '', updated_at = ? WHERE id = ? AND student_id = ?",
        (
            str(title or "我的简历")[:120], str(target_position or "")[:120],
            str(template_key or "classic")[:40], layout_json, _now(), int(resume_id), int(student_id),
        ),
    )


def save_import_result(
    conn,
    resume_id: int,
    *,
    title: str,
    target_position: str,
    template_key: str,
    layout: Any,
    render_html: str,
    tech_stack: list[Any],
    import_summary: Any,
    status: str = "ready",
    error_text: str = "",
) -> None:
    ensure_resume_schema(conn)
    layout_json = json.dumps(_normalize_layout(layout), ensure_ascii=False)
    conn.execute(
        "UPDATE resumes SET title = ?, target_position = ?, template_key = ?, layout_json = ?, "
        "render_html = ?, tech_stack_json = ?, import_summary_json = ?, status = ?, error_text = ?, "
        "updated_at = ? WHERE id = ?",
        (
            str(title or "导入简历")[:120],
            str(target_position or "")[:120],
            str(template_key or "classic")[:40],
            layout_json,
            str(render_html or ""),
            json.dumps(tech_stack or [], ensure_ascii=False),
            json.dumps(import_summary or {}, ensure_ascii=False),
            status,
            str(error_text or "")[:600],
            _now(),
            int(resume_id),
        ),
    )


def save_import_summary(conn, resume_id: int, import_summary: Any) -> None:
    ensure_resume_schema(conn)
    conn.execute(
        "UPDATE resumes SET import_summary_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(import_summary or {}, ensure_ascii=False), _now(), int(resume_id)),
    )


def save_render(conn, resume_id: int, *, render_html: str, tech_stack: list[Any],
                status: str = "ready", error_text: str = "") -> None:
    ensure_resume_schema(conn)
    conn.execute(
        "UPDATE resumes SET render_html = ?, tech_stack_json = ?, status = ?, error_text = ?, "
        "updated_at = ? WHERE id = ?",
        (str(render_html or ""), json.dumps(tech_stack or [], ensure_ascii=False),
         status, str(error_text or "")[:600], _now(), int(resume_id)),
    )


def save_optimization(conn, resume_id: int, *, target_position: str, optimized_summary_md: str,
                      optimization_notes: Any, render_html: str, tech_stack: list[Any],
                      status: str = "ready", error_text: str = "") -> None:
    ensure_resume_schema(conn)
    conn.execute(
        "UPDATE resumes SET target_position = ?, optimized_summary_md = ?, optimization_notes_json = ?, "
        "render_html = ?, tech_stack_json = ?, status = ?, error_text = ?, updated_at = ? WHERE id = ?",
        (
            str(target_position or "")[:120],
            str(optimized_summary_md or "")[:2000],
            json.dumps(optimization_notes or {}, ensure_ascii=False),
            str(render_html or ""),
            json.dumps(tech_stack or [], ensure_ascii=False),
            status,
            str(error_text or "")[:600],
            _now(),
            int(resume_id),
        ),
    )


def set_status(conn, resume_id: int, status: str, error_text: str = "") -> None:
    ensure_resume_schema(conn)
    conn.execute(
        "UPDATE resumes SET status = ?, error_text = ?, updated_at = ? WHERE id = ?",
        (status, str(error_text or "")[:600], _now(), int(resume_id)),
    )


def delete_resume(conn, student_id: int, resume_id: int) -> None:
    ensure_resume_schema(conn)
    conn.execute(
        "DELETE FROM resumes WHERE id = ? AND student_id = ?",
        (int(resume_id), int(student_id)),
    )
