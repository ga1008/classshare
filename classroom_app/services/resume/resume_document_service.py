"""CRUD for assembled résumés (``resumes`` table).

A résumé is created in status ``rendering`` (the list page shows a placeholder
card that polls), then the background render job (``resume_generation_service``)
fills ``render_html`` / ``tech_stack_json`` and flips status to ``ready`` (or
``failed`` with ``error_text``). Editing re-enters ``rendering``.
"""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from typing import Any

from ...db.connection import execute_insert_returning_id
from ...db.schema_resume import ensure_resume_schema
from . import resume_render_service as render


class ResumeNotFound(ValueError, LookupError):
    """Owner-scoped absence, compatible with older service callers."""


class ResumeConflict(ValueError):
    """The caller is editing an obsolete version; never discard their input."""


def require_revision(current: dict[str, Any], expected: Any) -> int:
    actual = int(current.get("revision") or 1)
    try:
        valid = expected is not None and int(expected) == actual
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ResumeConflict("资料已在其他窗口更新，请重新载入或另存副本；当前输入仍可保留。")
    return actual


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _normalize_layout(layout: Any) -> dict[str, Any]:
    if not isinstance(layout, dict):
        return {}
    safe: dict[str, Any] = {}
    fields = layout.get("personal_fields")
    if isinstance(fields, list):
        safe["personal_fields"] = [str(f) for f in fields if str(f) in render._PERSONAL_LABELS][:20]
    blocks_in = layout.get("blocks") if isinstance(layout.get("blocks"), list) else []
    blocks: list[dict[str, Any]] = []
    for spec in blocks_in[:30]:
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


def list_resumes(conn, student_id: int, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    ensure_resume_schema(conn)
    rows = conn.execute(
        "SELECT id, title, target_position, template_key, optimized_summary_md, optimization_notes_json, source_context_json, "
        "source_filename, source_mime_type, source_file_size, import_summary_json, "
        "status, error_text, revision, render_revision, active_job_id, created_at, updated_at "
        "FROM resumes WHERE student_id = ? AND archived = 0 ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        (int(student_id), max(1, min(100, int(limit))), max(0, int(offset))),
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
        try:
            item["source_context"] = json.loads(item.get("source_context_json") or "{}")
        except (TypeError, ValueError):
            item["source_context"] = {}
        item.pop("optimization_notes_json", None)
        item.pop("import_summary_json", None)
        item.pop("source_context_json", None)
        items.append(item)
    return items



def list_resume_states(conn, student_id: int, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute("SELECT id,status,revision,render_revision,active_job_id,error_text,updated_at FROM resumes WHERE student_id = ? AND archived = 0 ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (int(student_id), max(1, min(100, int(limit))), max(0, int(offset))))]

def get_resume(conn, student_id: int, resume_id: int, *, include_archived: bool = False) -> dict[str, Any]:
    ensure_resume_schema(conn)
    row = conn.execute(
        "SELECT * FROM resumes WHERE id = ? AND student_id = ?" + ("" if include_archived else " AND archived = 0") + " LIMIT 1",
        (int(resume_id), int(student_id)),
    ).fetchone()
    if not row:
        raise ResumeNotFound("未找到该简历")
    return render.parse_resume_row(dict(row))


def _normalize_source_context(value: Any) -> dict[str, Any]:
    """Keep only provenance needed to reconnect a resume to career/job flows."""
    if not isinstance(value, dict):
        return {}
    safe: dict[str, Any] = {}
    for key in ("source", "career_tag", "direction_id", "target_position", "job_id", "job_target_id", "recommendation_revision"):
        raw = value.get(key)
        if isinstance(raw, (str, int)) and str(raw).strip():
            safe[key] = str(raw).strip()[:120]
    return safe


def create_resume(conn, student_id: int, *, title: str, template_key: str,
                  layout: Any, target_position: str = "", source_context: Any = None,
                  draft: bool = False, content_overrides: Any = None, client_id: str | None = None,
                  optimized_summary_md: Any = "", tech_stack: Any = None) -> int:
    ensure_resume_schema(conn)
    now = _now()
    layout_json = json.dumps(_normalize_layout(layout), ensure_ascii=False)
    source_context_json = json.dumps(_normalize_source_context(source_context), ensure_ascii=False)
    resume_id = int(
        execute_insert_returning_id(
            conn,
            "INSERT INTO resumes (student_id, title, target_position, template_key, layout_json, "
            "source_context_json, status, created_at, updated_at, client_id) "
            "VALUES (?, ?, ?, ?, ?, ?, 'rendering', ?, ?, ?)",
            (
                int(student_id), str(title or "我的简历")[:120], str(target_position or "")[:120],
                str(template_key or "classic")[:40], layout_json, source_context_json, now, now, client_id,
            ),
        )
    )
    if draft:
        conn.execute("UPDATE resumes SET status = 'draft' WHERE id = ?", (resume_id,))
    if optimized_summary_md or tech_stack:
        from .resume_ai_service import _coerce_tech_groups
        conn.execute("UPDATE resumes SET optimized_summary_md = ?, tech_stack_json = ? WHERE id = ? AND student_id = ?", (str(optimized_summary_md or "")[:2000], _json(_coerce_tech_groups(tech_stack)), resume_id, int(student_id)))
    capture_version(conn, student_id, resume_id, content_overrides=content_overrides)
    return resume_id


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
    from ..file_service import lock_global_file_references
    lock_global_file_references(conn, [file_hash])
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
                  layout: Any, target_position: str = "", source_context: Any = None,
                  expected_revision: Any = None, draft: bool = False,
                  content_overrides: Any = None, refresh_materials: bool = False,
                  optimized_summary_md: Any = None, tech_stack: Any = None) -> int:
    current = get_resume(conn, student_id, resume_id)  # ownership
    revision = require_revision(current, expected_revision) if expected_revision is not None else int(current.get("revision") or 1)
    layout_json = json.dumps(_normalize_layout(layout), ensure_ascii=False)
    if source_context is None:
        source_context = current.get("source_context") or {}
    source_context_json = json.dumps(_normalize_source_context(source_context), ensure_ascii=False)
    from .resume_ai_service import _coerce_tech_groups
    summary = (current.get("optimized_summary_md") or "") if optimized_summary_md is None else str(optimized_summary_md or "")[:2000]
    groups = (current.get("tech_stack") or []) if tech_stack is None else _coerce_tech_groups(tech_stack)
    result = conn.execute(
        "UPDATE resumes SET title = ?, target_position = ?, template_key = ?, layout_json = ?, source_context_json = ?, "
        "optimized_summary_md = ?, tech_stack_json = ?, status = ?, "
        "active_job_id = '', revision = revision + 1, error_text = '', updated_at = ? WHERE id = ? AND student_id = ? AND revision = ? AND archived = 0",
        (
            str(title or "我的简历")[:120], str(target_position or "")[:120],
            str(template_key or "classic")[:40], layout_json, source_context_json, summary, _json(groups),
            "draft" if draft else "rendering", _now(), int(resume_id), int(student_id), revision,
        ),
    )
    if result.rowcount != 1:
        raise ResumeConflict("简历已更新，请保留输入并重新载入。")
    capture_version(conn, student_id, resume_id, content_overrides=content_overrides, refresh_materials=refresh_materials)
    return revision + 1


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
        "UPDATE resumes SET archived = 1, revision = revision + 1, active_job_id = '', status = 'archived' WHERE id = ? AND student_id = ?",
        (int(resume_id), int(student_id)),
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _selected_bundle(conn, student_id: int, layout: dict[str, Any], overrides: Any = None, fallback_bundle: Any = None) -> dict[str, Any]:
    bundle = render.profile.collect_profile_bundle(conn, student_id)
    ids: dict[str, set[int]] = {key: set() for key in render.profile.LIST_SECTIONS}
    for block in layout.get("blocks") or []:
        kind = block.get("type")
        if kind in {"self_intro", "education", "experience"}:
            ids[kind].update(int(value) for value in block.get("ids") or [])
        elif kind == "skill_cert":
            ids["skill"].update(int(value) for value in block.get("skill_ids") or [])
            ids["certificate"].update(int(value) for value in block.get("cert_ids") or [])
    selected = {"personal": dict((fallback_bundle or {}).get("personal") or bundle.get("personal") or {})}
    for section in ids:
        # Preserve exactly what the editor displayed. New selections come from
        # the current material bank; previously selected facts stay frozen.
        prior = {int(item["id"]): item for item in (fallback_bundle or {}).get(section) or []}
        available = {int(item["id"]): item for item in bundle.get(section) or []}
        available.update(prior)
        selected[section] = [dict(available[item_id]) for item_id in sorted(ids[section]) if item_id in available]
    # Changes here belong only to this document; never mutate the material bank.
    for override in (overrides if isinstance(overrides, list) else [])[:100]:
        if not isinstance(override, dict):
            continue
        section = str(override.get("section") or "")
        fields = override.get("fields") if isinstance(override.get("fields"), dict) else {}
        if section == "personal":
            for field in render.profile.PERSONAL_FIELDS:
                if field in fields:
                    selected["personal"][field] = str(fields[field] or "")[:200]
            continue
        spec = render.profile.LIST_SECTIONS.get(section)
        if not spec:
            continue
        fields = override.get("fields") if isinstance(override.get("fields"), dict) else {}
        for item in selected.get(section) or []:
            if str(item["id"]) == str(override.get("id")):
                for field in spec["fields"]:
                    if field in fields and field != "source":
                        item[field] = str(fields[field] or "")[:8000 if field == "content_md" else 2000]
    return selected


def capture_version(conn, student_id: int, resume_id: int, *, content_overrides: Any = None, refresh_materials: bool = False) -> dict[str, Any]:
    """Freeze facts at save time. The HTML is a derivative of this exact input."""
    resume = get_resume(conn, student_id, resume_id, include_archived=True)
    context = resume.get("source_context") or {}
    prior = conn.execute("SELECT snapshot_json FROM resume_versions WHERE resume_id = ? AND student_id = ? ORDER BY revision DESC LIMIT 1",
                         (int(resume_id), int(student_id))).fetchone()
    prior_snapshot = json.loads(prior["snapshot_json"]) if prior else {}
    job_id = context.get("job_target_id") or context.get("job_id")
    job_snapshot: dict[str, Any] = {}
    if job_id and str((prior_snapshot.get("job_target") or {}).get("id") or "") == str(job_id):
        job_snapshot = prior_snapshot["job_target"]
    elif job_id:
        from . import resume_job_target_service as targets
        try:
            job_snapshot = targets.get_job_target(conn, student_id, int(job_id))
        except (LookupError, TypeError, ValueError):
            # Legacy deleted targets cannot block the history migration. New
            # associations are validated by the command route before saving.
            job_snapshot = prior_snapshot.get("job_target") or {}
    snapshot = {
        "title": resume["title"], "target_position": resume["target_position"],
        "template_key": resume["template_key"], "layout": resume.get("layout") or {},
        "source_context": context, "job_target": job_snapshot,
        "bundle": _selected_bundle(conn, student_id, resume.get("layout") or {}, content_overrides, None if refresh_materials else prior_snapshot.get("bundle")),
        "content_overrides": content_overrides if isinstance(content_overrides, list) else [],
        "tech_stack": resume.get("tech_stack") or [],
        "optimized_summary_md": resume.get("optimized_summary_md") or "",
        "optimization_notes": resume.get("optimization_notes") or {},
        "source_file_hash": resume.get("source_file_hash") or "",
    }
    if not prior and resume.get("status") == "ready":
        snapshot["legacy_materials_reconstructed"] = True
        snapshot["warnings"] = ["旧简历原始素材未冻结，保留原已生成预览；可编辑素材由现有资料重建，请核对。"]
    from ..file_service import lock_global_file_references, resolve_global_file_path
    personal = snapshot["bundle"].get("personal") or {}
    avatar_hash = str(personal.get("avatar_file_hash") or "")
    if avatar_hash and not resolve_global_file_path(avatar_hash):
        personal["avatar_file_hash"] = ""
        snapshot.setdefault("warnings", []).append("原头像文件不可用，请重新上传头像。")
        avatar_hash = ""
    if snapshot["source_file_hash"] and not resolve_global_file_path(snapshot["source_file_hash"]):
        snapshot["source_file_hash"] = ""
        snapshot.setdefault("warnings", []).append("导入原件文件不可用，已保留识别后的文字内容。")
    hashes = [value for value in (avatar_hash, snapshot["source_file_hash"]) if value]
    if hashes:
        lock_global_file_references(conn, sorted(set(hashes)))
    text = _json(snapshot)
    revision = int(resume.get("revision") or 1)
    # A legacy ready document keeps its actual delivered HTML when backfilled.
    html = str(resume.get("render_html") or "") if int(resume.get("render_revision") or 0) in (0, revision) and resume.get("status") == "ready" else ""
    conn.execute(
        "INSERT INTO resume_versions (student_id,resume_id,revision,snapshot_json,content_hash,render_html,status,created_at) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (resume_id,revision) DO NOTHING",
        (int(student_id), int(resume_id), revision, text, hashlib.sha256(text.encode()).hexdigest(), html,
         "ready" if html else "draft", _now()),
    )
    if html and not int(resume.get("render_revision") or 0):
        conn.execute("UPDATE resumes SET render_revision = ? WHERE id = ? AND student_id = ?", (revision, int(resume_id), int(student_id)))
    return get_version(conn, student_id, resume_id, revision)


def get_version(conn, student_id: int, resume_id: int, revision: int | None = None) -> dict[str, Any]:
    ensure_resume_schema(conn)
    resume = get_resume(conn, student_id, resume_id, include_archived=True)
    requested = int(revision if revision is not None else resume.get("revision") or 1)
    if requested < 1:
        raise ValueError("版本号必须是正整数")
    row = conn.execute("SELECT * FROM resume_versions WHERE student_id = ? AND resume_id = ? AND revision = ?",
                       (int(student_id), int(resume_id), requested)).fetchone()
    if not row:
        raise LookupError("该简历版本不存在")
    result = dict(row)
    result["snapshot"] = json.loads(result.pop("snapshot_json") or "{}")
    return result


def list_versions(conn, student_id: int, resume_id: int) -> list[dict[str, Any]]:
    get_resume(conn, student_id, resume_id, include_archived=True)
    return [dict(row) for row in conn.execute(
        "SELECT revision, content_hash, status, created_at FROM resume_versions WHERE resume_id = ? AND student_id = ? ORDER BY revision DESC LIMIT 100",
        (int(resume_id), int(student_id))).fetchall()]


def snapshot_resume(version: dict[str, Any]) -> dict[str, Any]:
    snapshot = dict(version["snapshot"])
    snapshot["content_snapshot"] = snapshot.pop("bundle", {})
    return snapshot


def save_version_render(conn, student_id: int, resume_id: int, revision: int, html: str) -> bool:
    """Idempotent derivative write; never mark a later draft as rendered."""
    cursor = conn.execute(
        "UPDATE resume_versions SET render_html = ?, status = 'ready' WHERE student_id = ? AND resume_id = ? AND revision = ? AND render_html = ''",
        (html, int(student_id), int(resume_id), int(revision)))
    conn.execute(
        "UPDATE resumes SET render_html = ?, render_revision = ?, status = 'ready', active_job_id = '', error_text = '' "
        "WHERE id = ? AND student_id = ? AND revision = ? AND archived = 0",
        (html, int(revision), int(resume_id), int(student_id), int(revision)))
    return cursor.rowcount == 1


def create_candidate(conn, student_id: int, resume_id: int, base_revision: int, kind: str, payload: dict[str, Any], *, job_id: str) -> int:
    now = _now()
    conn.execute(
        "INSERT INTO resume_candidates (student_id,resume_id,base_revision,kind,payload_json,job_id,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(resume_id,job_id,kind) DO NOTHING",
        (int(student_id), int(resume_id), int(base_revision), kind, _json(payload), str(job_id), now, now))
    row = conn.execute("SELECT id FROM resume_candidates WHERE resume_id = ? AND student_id = ? AND job_id = ? AND kind = ?",
                       (int(resume_id), int(student_id), str(job_id), kind)).fetchone()
    return int(row["id"])


def get_candidate(conn, student_id: int, resume_id: int, candidate_id: int) -> dict[str, Any]:
    get_resume(conn, student_id, resume_id)
    row = conn.execute("SELECT * FROM resume_candidates WHERE id = ? AND resume_id = ? AND student_id = ?",
                       (int(candidate_id), int(resume_id), int(student_id))).fetchone()
    if not row:
        raise LookupError("候选结果不存在或无权访问")
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json") or "{}")
    return result


def list_candidates(conn, student_id: int, resume_id: int) -> list[dict[str, Any]]:
    get_resume(conn, student_id, resume_id)
    rows = conn.execute("SELECT * FROM resume_candidates WHERE resume_id = ? AND student_id = ? ORDER BY id DESC LIMIT 20", (int(resume_id), int(student_id))).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        results.append(item)
    return results


def accept_optimization(conn, student_id: int, resume_id: int, candidate_id: int, expected_revision: Any) -> int:
    resume = get_resume(conn, student_id, resume_id)
    revision = require_revision(resume, expected_revision)
    candidate = get_candidate(conn, student_id, resume_id, candidate_id)
    if candidate["kind"] != "optimization" or candidate["status"] != "pending" or int(candidate["base_revision"]) != revision:
        raise ResumeConflict("候选结果已失效，请基于当前简历重新优化。")
    previous = get_version(conn, student_id, resume_id, revision)
    snapshot = previous["snapshot"]
    from .resume_ai_service import _coerce_tech_groups, _coerce_notes
    result = candidate["payload"]
    snapshot["optimized_summary_md"] = str(result.get("summary_md") or "")[:2000]
    snapshot["tech_stack"] = _coerce_tech_groups(result.get("tech_stack"))
    snapshot["optimization_notes"] = {"items": _coerce_notes(result.get("notes"))}
    cursor = conn.execute(
        "UPDATE resumes SET revision = revision + 1, status = 'rendering', active_job_id = '', optimized_summary_md = ?, "
        "tech_stack_json = ?, optimization_notes_json = ?, updated_at = ? WHERE id = ? AND student_id = ? AND revision = ? AND archived = 0",
        (snapshot["optimized_summary_md"], _json(snapshot["tech_stack"]), _json(snapshot["optimization_notes"]), _now(), int(resume_id), int(student_id), revision))
    if cursor.rowcount != 1:
        raise ResumeConflict("简历已更新，请重新载入。")
    text = _json(snapshot)
    conn.execute("INSERT INTO resume_versions (student_id,resume_id,revision,snapshot_json,content_hash,status,created_at) VALUES (?,?,?,?,?,'draft',?)",
                 (int(student_id), int(resume_id), revision + 1, text, hashlib.sha256(text.encode()).hexdigest(), _now()))
    conn.execute("UPDATE resume_candidates SET status = 'accepted', updated_at = ? WHERE id = ? AND student_id = ?", (_now(), int(candidate_id), int(student_id)))
    return revision + 1


def restore_version(conn, student_id: int, resume_id: int, revision: int, expected_revision: Any) -> int:
    current = get_resume(conn, student_id, resume_id)
    old_revision = require_revision(current, expected_revision)
    previous = get_version(conn, student_id, resume_id, revision)
    snapshot = previous["snapshot"]
    cursor = conn.execute(
        "UPDATE resumes SET revision = revision + 1, title = ?, target_position = ?, template_key = ?, layout_json = ?, "
        "source_context_json = ?, optimized_summary_md = ?, tech_stack_json = ?, optimization_notes_json = ?, "
        "status = 'draft', active_job_id = '', updated_at = ? WHERE id = ? AND student_id = ? AND revision = ? AND archived = 0",
        (snapshot.get("title", "我的简历"), snapshot.get("target_position", ""), snapshot.get("template_key", "classic"),
         _json(snapshot.get("layout") or {}), _json(snapshot.get("source_context") or {}), snapshot.get("optimized_summary_md", ""),
         _json(snapshot.get("tech_stack") or []), _json(snapshot.get("optimization_notes") or {}), _now(), int(resume_id), int(student_id), old_revision))
    if cursor.rowcount != 1:
        raise ResumeConflict("简历已更新，请重新载入。")
    conn.execute("INSERT INTO resume_versions (student_id,resume_id,revision,snapshot_json,content_hash,render_html,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (int(student_id), int(resume_id), old_revision + 1, _json(snapshot), previous["content_hash"], previous["render_html"], previous["status"], _now()))
    if previous["render_html"]:
        conn.execute("UPDATE resumes SET render_html = ?, render_revision = ?, status = 'ready' WHERE id = ? AND student_id = ?",
                     (previous["render_html"], old_revision + 1, int(resume_id), int(student_id)))
    return old_revision + 1


def backfill_resume_versions(conn, *, limit: int = 100) -> int:
    """Bounded maintenance migration; avoid material writes in polling GETs."""
    ensure_resume_schema(conn)
    rows = conn.execute("SELECT r.id,r.student_id FROM resumes r LEFT JOIN resume_versions v ON v.resume_id=r.id AND v.revision=r.revision "
                        "WHERE v.id IS NULL AND r.archived=0 AND r.status NOT IN ('parsing') ORDER BY r.id LIMIT ?", (int(limit),)).fetchall()
    count = 0
    for row in rows:
        capture_version(conn, int(row["student_id"]), int(row["id"]))
        count += 1
    return count
