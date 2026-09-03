"""Adapter for the existing material source editor; JSON remains the LessonDoc truth."""

import json
import re
import uuid

from . import editor_service as editor, pack_service, render, spec


def context(conn, material):
    registration = pack_service.registered_material_context(conn, material)
    if not registration:
        return None
    relative = registration["relative_path"]
    if relative in {"main.html", "course.json"}:
        number = 0
    else:
        match = re.fullmatch(r"lesson_(\d+)/lesson_\1\.html", relative)
        number = int(match.group(1)) if match else None
    registration["lesson_no"] = number
    registration["revision"] = editor.lesson_revision(conn, registration["pack"], number) if number is not None else str(material["file_hash"] or "")
    return registration


def save(conn, *, material, teacher_id, content, revision, source_revision, operation_id):
    registration = context(conn, material)
    if not registration:
        return None
    pack = registration["pack"]
    if not revision:
        raise editor.EditorError("REVISION_REQUIRED", "请重新打开源码后保存，以检查学习文档版本", 428)
    number = registration["lesson_no"]
    if number is None:
        pack = editor._lock_pack(conn, pack["id"], teacher_id)
        current = conn.execute("SELECT * FROM course_materials WHERE id=?", (material["id"],)).fetchone()
        if current is None or current["file_hash"] != revision:
            raise editor.EditorError("REVISION_CONFLICT", "素材已更新，请重新打开源码", 409)
        return dict(raw_asset=True, pack_id=pack["id"], engine_asset=registration["relative_path"] in {f"assets/{name}" for name in spec.ASSET_FILES})
    if not source_revision:
        raise editor.EditorError("REVISION_REQUIRED", "源码版本不完整，请重新打开后保存", 428)
    if len(content.encode("utf-8")) > 3 * 1024 * 1024:
        raise editor.EditorError("DOCUMENT_TOO_LARGE", "学习文档源码不能超过 3 MiB", 413)
    try:
        document = json.loads(content) if registration["relative_path"] == "course.json" else render.extract_embedded_json(content)
    except (ValueError, RecursionError) as exc:
        raise editor.EditorError("INVALID_DOCUMENT", "学习文档源码缺少有效 JSON 数据", 422) from exc
    saved = editor.save_document(conn, pack_id=pack["id"], teacher_id=teacher_id, lesson_no=number, document=document,
                                 expected_revision=revision, operation_id=operation_id or "source_" + uuid.uuid4().hex, source="source",
                                 expected_material=(material["id"], source_revision))
    if registration["relative_path"] != "course.json":
        saved["warnings"].append("LessonDoc 以 JSON 数据为准，HTML 壳使用平台模板生成")
    return saved
