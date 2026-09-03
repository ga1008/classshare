"""LessonDoc editor read/validate/save/restore boundary.

The caller supplies a connection and owns commit/rollback. Writes serialize on the
pack row, then compare the actual material file hash. Never hold that lock across
AI/network work. The manifest projection, snapshot, receipt and assets commit with
the document. Content-addressed blobs may be written before a rollback, but their
database references never escape the transaction.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from ...db.schema_lessondoc_editor import ensure_lessondoc_editor_schema
from . import assets, pack_service, render, revisions, validate
from .model import check_budget, ensure_editor_ids, normalization_diagnostics

ABSENT_REVISION = "absent"
WRITE_SOURCES = frozenset({"editor", "restore", "ai_generate", "ai_rewrite", "git", "import", "settings", "source"})


class EditorError(ValueError):
    def __init__(self, code, message, status=400, **details):
        super().__init__(message)
        self.code, self.status, self.details = code, status, details


def owned_pack(conn, pack_id: int, teacher_id: int):
    pack = pack_service.get_pack(conn, pack_id)
    if pack is None or pack.get("status") != "active":
        raise EditorError("PACK_NOT_FOUND", "学习文档包不存在或已归档", 404)
    if int(pack["teacher_id"]) != int(teacher_id):
        raise EditorError("FORBIDDEN", "只能编辑自己的学习文档包", 403)
    root = conn.execute("SELECT id FROM course_materials WHERE id=? AND teacher_id=?", (pack["root_material_id"], teacher_id)).fetchone()
    if root is None:
        raise EditorError("PACK_NOT_FOUND", "学习文档包原始材料已移除", 404)
    return pack


def _lesson_state(conn, pack, lesson_no):
    if lesson_no == 0:
        return None
    row = conn.execute("SELECT * FROM course_doc_pack_lessons WHERE pack_id=? AND lesson_no=?", (pack["id"], lesson_no)).fetchone()
    if row is None:
        raise EditorError("LESSON_NOT_FOUND", "该课次不属于此学习文档包", 404)
    return dict(row)


def lesson_revision(conn, pack, lesson_no):
    row = (pack_service.find_lesson_entry(conn, pack, lesson_no) if lesson_no else
           pack_service._find_child(conn, teacher_id=pack["teacher_id"], parent_id=pack["root_material_id"], name=pack_service.MANIFEST_FILE_NAME))
    return str(row["file_hash"] or "") if row is not None else ABSENT_REVISION


def deck_summary(document):
    for slide in reversed(document.get("slides") or []):
        if slide.get("layout") == "end" and str(slide.get("summary") or "").strip():
            return str(slide["summary"]).strip()[:240]
    return str(document.get("subtitle") or "").strip()[:240] or render.extract_deck_text(document, max_chars=240)


def _read_state(conn, pack, lesson_no, *, allow_corrupt=False):
    state = _lesson_state(conn, pack, lesson_no)
    if lesson_no:
        row = pack_service.find_lesson_entry(conn, pack, lesson_no)
    else:
        row = pack_service._find_child(conn, teacher_id=pack["teacher_id"], parent_id=pack["root_material_id"], name=pack_service.MANIFEST_FILE_NAME)
    revision = str(row["file_hash"] or "") if row is not None else ABSENT_REVISION
    payload = None
    if row is not None:
        text = pack_service._load_file_text(conn, row)
        try:
            payload = render.extract_embedded_json(text or "") if lesson_no else json.loads(text or "")
        except (ValueError, TypeError, RecursionError):
            pass
        if not isinstance(payload, dict):
            if not allow_corrupt:
                raise EditorError("DOCUMENT_CORRUPT", "原文档存在但数据损坏，请从历史版本恢复", 422, revision=revision)
            return dict(document=None, revision=revision, state="corrupt", material_id=int(row["id"]), lesson_state=state)
    elif lesson_no:
        # Missing content for a previously published lesson is not a fresh draft.
        if state["gen_status"] == "ready" and allow_corrupt:
            return dict(document=None, revision=revision, state="absent", material_id=None, lesson_state=state)
        if state["gen_status"] == "ready":
            raise EditorError("DOCUMENT_MISSING", "已发布课次的原文档丢失，请从历史版本恢复", 422, revision=revision)
        manifest = pack_service.read_manifest(conn, pack)
        entry = next((item for item in manifest.get("lessons") or [] if item.get("n") == lesson_no), {})
        payload = {"spec": "lessondoc/2.0", "kind": "lesson", "lesson": lesson_no,
                   "course": (manifest.get("course") or {}).get("name", ""), "title": entry.get("title") or f"第{lesson_no}课",
                   "slides": [{"id": "s1", "layout": "title"}, {"id": "s2", "layout": "content", "empty": True, "blocks": []}]}
    else:
        # Cache is a recovery candidate, never a silently substituted truth.
        if allow_corrupt:
            return dict(document=None, revision=revision, state="absent", material_id=None, lesson_state=state)
        raise EditorError("MANIFEST_MISSING", "课程清单缺失，请恢复历史版本", 422, revision=revision, recovery_available=bool(pack.get("manifest_cache")))
    return dict(document=payload, revision=revision, state="present" if row is not None else "absent", material_id=int(row["id"]) if row is not None else None, lesson_state=state)


def normalize_document(document, lesson_no, *, editor_ids=True):
    try:
        check_budget(document)
        submitted = ensure_editor_ids(document) if editor_ids else document
        clean, warnings = validate.validate_deck(submitted, expected_lesson=lesson_no) if lesson_no else validate.validate_manifest(submitted)
    except ValueError as exc:
        raise EditorError("INVALID_DOCUMENT", str(exc), 422) from exc
    diagnostics = normalization_diagnostics(submitted, clean, warnings)
    return clean, warnings, diagnostics


def load_document(conn, *, pack_id, teacher_id, lesson_no=0):
    ensure_lessondoc_editor_schema(conn)
    pack = owned_pack(conn, pack_id, teacher_id)
    current = _read_state(conn, pack, int(lesson_no))
    try:
        clean, warnings, diagnostics = normalize_document(current["document"], int(lesson_no))
    except EditorError as exc:
        exc.details.setdefault("revision", current["revision"])
        raise
    return dict(pack_id=pack_id, lesson_no=int(lesson_no), document=clean, revision=current["revision"], state=current["state"],
                material_id=current["material_id"], root_material_id=pack["root_material_id"], warnings=warnings, diagnostics=diagnostics,
                assets_outdated=pack.get("assets_fingerprint") != assets.assets_fingerprint())


def _lock_pack(conn, pack_id, teacher_id):
    # UPDATE acquires a row write lock on PostgreSQL and the reserved write lock
    # on SQLite. It also works when the caller already began a short transaction.
    cursor = conn.execute("UPDATE course_doc_packs SET updated_at=updated_at WHERE id=? AND teacher_id=? AND status='active'", (pack_id, teacher_id))
    if not cursor.rowcount:
        owned_pack(conn, pack_id, teacher_id)
        raise EditorError("PACK_UNAVAILABLE", "学习文档包当前无法写入", 409)
    return owned_pack(conn, pack_id, teacher_id)


def _write_projected_home(conn, pack, manifest, source, now):
    current = _read_state(conn, pack, 0)
    clean = validate.validate_manifest(manifest)[0]
    if current["document"] != clean:
        revisions.record_snapshot(conn, pack_id=pack["id"], lesson_no=0, revision=current["revision"],
                                  document=current["document"], source=source, author_id=pack["teacher_id"], now=now)
        pack_service.write_manifest(conn, pack, clean)


def _has_content(document):
    return bool(document) and (bool(document.get("globals")) or
                              any(True for slide in document.get("slides") or [] for _ in validate._iter_blocks(slide)))


def _projection(conn, pack, lesson_no, document, warnings, now, source):
    manifest = _read_state(conn, pack, 0)["document"]
    state = _lesson_state(conn, pack, lesson_no)
    actual_content = _has_content(document)
    excluded = state["gen_status"] == "excluded"
    status = state["gen_status"]
    if actual_content and not excluded:
        status = "ready"
    # Empty editor drafts must not leave a superseded generation task owning them.
    elif status in {"queued", "running", "ready"}:
        status = "pending"
    for entry in manifest.get("lessons") or []:
        if int(entry.get("n") or 0) != lesson_no:
            continue
        entry["title"] = document.get("title") or entry.get("title") or f"第{lesson_no}课"
        entry["status"] = "ready" if status == "ready" else "pending"
        entry["summary"] = deck_summary(document)
        break
    _write_projected_home(conn, pack, manifest, source, now)
    conn.execute("UPDATE course_doc_pack_lessons SET gen_status=?,warnings_json=?,updated_at=? WHERE pack_id=? AND lesson_no=?",
                 (status, json.dumps(warnings, ensure_ascii=False), now, pack["id"], lesson_no))


def _home_projection(conn, pack, submitted, current):
    """Home editor may change presentation, never publish/exclude/rebind lessons."""
    clean = dict(submitted)
    if current and isinstance(current.get("lessons"), list):
        clean["lessons"] = current["lessons"]
    else:
        cache = pack.get("manifest_cache") or {}
        by_n = {entry.get("n"): dict(entry) for entry in submitted.get("lessons") or []}
        by_n.update({entry.get("n"): dict(entry) for entry in cache.get("lessons") or []})
        lessons = []
        for row in conn.execute("SELECT lesson_no,gen_status FROM course_doc_pack_lessons WHERE pack_id=? ORDER BY lesson_no", (pack["id"],)).fetchall():
            entry = by_n.get(row["lesson_no"], {"n": row["lesson_no"], "title": f"第{row['lesson_no']}课"})
            entry["status"] = "ready" if row["gen_status"] == "ready" else "pending"
            lessons.append(entry)
        clean["lessons"] = lessons
    # Course facts may be displayed differently, but their source identity remains
    # on course_doc_packs and the classroom binding tables, outside this JSON.
    return validate.validate_manifest(clean)[0]


def save_document(conn, **kwargs):
    try:
        return _save_document(conn, **kwargs)
    except pack_service.LessonDocWriteConflict as exc:
        raise EditorError("REVISION_CONFLICT", str(exc), 409) from exc


def _save_document(conn, *, pack_id, teacher_id, lesson_no, document, expected_revision,
                  operation_id, source="editor", allow_loss=False, generation_claim=None, force_render=False, expected_material=None):
    ensure_lessondoc_editor_schema(conn)
    if source not in WRITE_SOURCES:
        raise EditorError("INVALID_SOURCE", "无效保存来源")
    if not isinstance(expected_revision, str) or not expected_revision:
        raise EditorError("REVISION_REQUIRED", "保存需要原文档版本号", 428)
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,96}", str(operation_id or "")):
        raise EditorError("INVALID_OPERATION", "操作标识无效")
    lesson_no = int(lesson_no)
    clean, warnings, diagnostics = normalize_document(document, lesson_no, editor_ids=source in {"editor", "restore"})
    if not allow_loss and any(item["destructive"] for item in diagnostics):
        raise EditorError("CONTENT_LOSS", "保存会导致内容丢失，请修正标记项", 422, diagnostics=diagnostics)
    digest = hashlib.sha256(json.dumps([document, expected_revision, source], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    pack = _lock_pack(conn, pack_id, teacher_id)
    if generation_claim is not None:
        claimed = _lesson_state(conn, pack, lesson_no)
        if claimed["gen_status"] != "running" or claimed["updated_at"] != generation_claim:
            raise EditorError("GENERATION_SUPERSEDED", "生成期间课次状态已更新，本次结果未覆盖最新内容", 409)
    receipt = conn.execute("SELECT * FROM lessondoc_save_operations WHERE pack_id=? AND lesson_no=? AND operation_id=?", (pack_id, lesson_no, operation_id)).fetchone()
    current = _read_state(conn, pack, lesson_no, allow_corrupt=source in {"restore", "source"})
    if receipt:
        if receipt["request_digest"] != digest:
            raise EditorError("OPERATION_REUSED", "同一操作标识不能保存不同内容", 409)
        if current["revision"] != receipt["result_revision"]:
            raise EditorError("REPLAY_SUPERSEDED", "这次保存已成功，但文档后来又有更新，请刷新后继续", 409, revision=current["revision"], saved_revision=receipt["result_revision"])
        result = load_document(conn, pack_id=pack_id, teacher_id=teacher_id, lesson_no=lesson_no)
        result.update(unchanged=True, replayed=True, operation_id=operation_id)
        return result
    if current["revision"] != expected_revision:
        raise EditorError("REVISION_CONFLICT", "文档已被其他操作更新，请比较最新版本后再保存", 409, revision=current["revision"])
    if expected_material:
        material_id, file_hash = expected_material
        row = conn.execute("SELECT file_hash FROM course_materials WHERE id=? AND teacher_id=?", (material_id, teacher_id)).fetchone()
        if row is None or row["file_hash"] != file_hash:
            raise EditorError("REVISION_CONFLICT", "文档源码已更新，请重新打开后保存", 409)
    if not lesson_no and source != "settings":
        projected = _home_projection(conn, pack, clean, current["document"])
        if clean.get("lessons") != projected.get("lessons"):
            warnings.append("课次编号、标题和发布状态由课次管理维护，已保留当前值")
        clean = projected
    unchanged = current["state"] == "present" and current["document"] == clean and not force_render
    now = pack_service._now_iso()
    if not unchanged:
        if current["state"] != "absent":
            revisions.record_snapshot(conn, pack_id=pack_id, lesson_no=lesson_no, revision=current["revision"], document=current["document"], source=source, author_id=teacher_id, now=now)
        if lesson_no:
            pack_service.write_lesson_files(conn, pack, lesson_no, clean)
            _projection(conn, pack, lesson_no, clean, warnings, now, source)
        else:
            pack_service.write_manifest(conn, pack, clean)
    if pack.get("assets_fingerprint") != assets.assets_fingerprint():
        pack_service.refresh_pack_assets(conn, pack)
    saved = _read_state(conn, pack, lesson_no)
    conn.execute("INSERT INTO lessondoc_save_operations(pack_id,lesson_no,operation_id,request_digest,result_revision,created_at) VALUES(?,?,?,?,?,?)",
                 (pack_id, lesson_no, operation_id, digest, saved["revision"], now))
    conn.execute("DELETE FROM lessondoc_save_operations WHERE pack_id=? AND id NOT IN (SELECT id FROM lessondoc_save_operations WHERE pack_id=? ORDER BY id DESC LIMIT 128)", (pack_id, pack_id))
    return dict(pack_id=pack_id, lesson_no=lesson_no, document=clean, revision=saved["revision"], state="present", material_id=saved["material_id"],
                root_material_id=pack["root_material_id"], warnings=warnings, diagnostics=diagnostics, unchanged=unchanged, replayed=False, operation_id=operation_id)


def update_settings(conn, *, pack_id, teacher_id, theme=None, stages=None, lesson_no=0, title=None, excluded=None, user_hint=None):
    """Apply legacy manager commands to fresh state under the same short pack lock."""
    ensure_lessondoc_editor_schema(conn)
    pack = _lock_pack(conn, pack_id, teacher_id)
    current = _read_state(conn, pack, 0)
    manifest = current["document"]
    if theme is not None:
        manifest["theme"] = theme
    if stages is not None:
        manifest["stages"] = stages
    if lesson_no:
        state = _lesson_state(conn, pack, lesson_no)
        if excluded is not None and state["gen_status"] in {"queued", "running"}:
            raise EditorError("GENERATION_RUNNING", "课次正在生成中，无法调整排除状态", 409)
        lesson = _read_state(conn, pack, lesson_no)
        status = state["gen_status"]
        if excluded is not None:
            status = "excluded" if excluded else ("ready" if _has_content(lesson["document"]) else "pending")
        pack_service.update_lesson_state(conn, pack_id=pack_id, lesson_no=lesson_no, gen_status=status, user_hint=user_hint)
        for entry in manifest.get("lessons") or []:
            if entry.get("n") == lesson_no:
                if title and title.strip():
                    entry["title"] = title.strip()
                entry["status"] = "ready" if status == "ready" else "pending"
        # Rename both representations; do not create a file just to name a pending lesson.
        if title and title.strip() and lesson["state"] == "present" and lesson["document"].get("title") != title.strip():
            lesson["document"]["title"] = title.strip()
            save_document(conn, pack_id=pack_id, teacher_id=teacher_id, lesson_no=lesson_no, document=lesson["document"],
                          expected_revision=lesson["revision"], operation_id="settings_" + uuid.uuid4().hex, source="settings")
            # The lesson save may have advanced readiness and the home revision.
            current = _read_state(conn, pack, 0)
            manifest = current["document"]
    saved = save_document(conn, pack_id=pack_id, teacher_id=teacher_id, lesson_no=0, document=manifest,
                          expected_revision=current["revision"], operation_id="settings_" + uuid.uuid4().hex, source="settings")
    if theme is not None:
        pack_service.touch_pack(conn, pack_id, theme=saved["document"]["theme"])
    return saved


def list_revisions(conn, *, pack_id, teacher_id, lesson_no=0):
    ensure_lessondoc_editor_schema(conn)
    pack = owned_pack(conn, pack_id, teacher_id)
    _lesson_state(conn, pack, int(lesson_no))
    return revisions.list_snapshots(conn, pack_id=pack_id, lesson_no=int(lesson_no))


def preview_revision(conn, *, pack_id, teacher_id, lesson_no, revision_id):
    ensure_lessondoc_editor_schema(conn)
    pack = owned_pack(conn, pack_id, teacher_id)
    _lesson_state(conn, pack, int(lesson_no))
    snapshot = revisions.get_snapshot(conn, pack_id=pack_id, lesson_no=int(lesson_no), revision_id=revision_id)
    if snapshot is None:
        raise EditorError("REVISION_NOT_FOUND", "历史版本不存在或已超出保留范围", 404)
    if isinstance(snapshot["document"], dict):
        from .media import check_references
        snapshot["diagnostics"] = check_references(conn, pack, int(lesson_no), snapshot["document"])
    return snapshot


def restore_revision(conn, *, pack_id, teacher_id, lesson_no, revision_id, expected_revision, operation_id):
    snapshot = preview_revision(conn, pack_id=pack_id, teacher_id=teacher_id, lesson_no=lesson_no, revision_id=revision_id)
    saved = save_document(conn, pack_id=pack_id, teacher_id=teacher_id, lesson_no=lesson_no, document=snapshot["document"],
                          expected_revision=expected_revision, operation_id=operation_id, source="restore")
    saved["diagnostics"].extend(snapshot.get("diagnostics") or [])
    saved["warnings"].extend(item["message"] for item in snapshot.get("diagnostics") or [])
    return saved
