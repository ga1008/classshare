from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException, UploadFile

from ..config import GLOBAL_FILES_DIR
from ..core import ai_client
from .materials_git_service import refresh_root_git_metadata
from .materials_service import (
    attach_learning_material_briefs,
    get_learning_material_brief_map,
    infer_material_profile,
    make_unique_material_name,
    normalize_material_path,
    sync_classroom_learning_material_assignments,
)


TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_FAILED = "failed"

ACTIVE_TASK_STATUSES = {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}
FINAL_TASK_STATUSES = {TASK_STATUS_COMPLETED, TASK_STATUS_FAILED}

TASK_STATUS_LABELS = {
    TASK_STATUS_QUEUED: "排队中",
    TASK_STATUS_RUNNING: "助教在思考",
    TASK_STATUS_COMPLETED: "已生成",
    TASK_STATUS_FAILED: "生成失败",
}

TASK_TRIGGER_LABELS = {
    "guided": "按要求生成",
    "auto": "一键生成",
}

TEXT_SAMPLE_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".log",
    ".html",
    ".htm",
    ".xml",
}
EXTRACTABLE_SAMPLE_EXTENSIONS = {".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".pdf"}

MAX_EXAMPLE_FILE_COUNT = 5
MAX_EXAMPLE_FILE_BYTES = 8 * 1024 * 1024
MAX_EXAMPLE_TEXT_CHARS = 6000
MAX_REFERENCE_TEXT_CHARS_PER_FILE = 4800
MAX_REFERENCE_TOTAL_CHARS = 32000
MAX_AI_FILE_TEXT_ITEMS = 16
MAX_AUTO_REFERENCE_DOCS = 4
MAX_GUIDED_REFERENCE_DOCS = 8
MAX_STRUCTURE_REFERENCE_DOCS = 6
MAX_GENERATED_NODE_COUNT = 8
MAX_GENERATED_FILE_CHARS = 48000
MAX_REQUIREMENT_TEXT_CHARS = 4000
MAX_DOCUMENT_TYPE_CHARS = 80
STALE_TASK_MINUTES = 90


def _now_iso() -> str:
    return datetime.now().isoformat()


def _safe_text(value: Any, *, max_length: int = 0) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if max_length > 0 and len(text) > max_length:
        return text[:max_length].rstrip()
    return text


def _truncate_text(value: str, *, limit: int) -> tuple[str, bool]:
    text = _safe_text(value)
    if len(text) <= limit:
        return text, False
    if limit < 80:
        return text[:limit].rstrip(), True
    head = text[: int(limit * 0.72)].rstrip()
    tail = text[-int(limit * 0.18):].lstrip()
    return f"{head}\n\n[内容已截断]\n\n{tail}".strip(), True


def _load_json_payload(raw_value: Any) -> dict[str, Any]:
    if not raw_value:
        return {}
    try:
        parsed = json.loads(str(raw_value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def expire_stale_generation_tasks(conn, *, stale_minutes: int = STALE_TASK_MINUTES) -> int:
    cutoff = (datetime.now() - timedelta(minutes=max(1, stale_minutes))).isoformat()
    now = _now_iso()
    cursor = conn.execute(
        """
        UPDATE session_material_generation_tasks
        SET status = ?,
            error_message = CASE
                WHEN TRIM(COALESCE(error_message, '')) = '' THEN ?
                ELSE error_message
            END,
            completed_at = COALESCE(completed_at, ?),
            updated_at = ?
        WHERE status IN (?, ?)
          AND updated_at < ?
        """,
        (
            TASK_STATUS_FAILED,
            "生成任务长时间未完成，系统已自动结束，请重新发起。",
            now,
            now,
            TASK_STATUS_QUEUED,
            TASK_STATUS_RUNNING,
            cutoff,
        ),
    )
    return int(cursor.rowcount or 0)


def serialize_generation_task(
    row,
    *,
    generated_material: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = dict(row)
    status = str(item.get("status") or TASK_STATUS_QUEUED).strip().lower()
    trigger_mode = _safe_text(item.get("trigger_mode")).lower() or "guided"
    material = generated_material
    generated_material_id = int(item.get("generated_material_id") or 0) or None

    if material:
        generated_material_id = int(material.get("id") or 0) or generated_material_id

    return {
        "id": int(item.get("id") or 0),
        "class_offering_id": int(item.get("class_offering_id") or 0),
        "session_id": int(item.get("session_id") or 0),
        "teacher_id": int(item.get("teacher_id") or 0),
        "trigger_mode": trigger_mode,
        "trigger_label": TASK_TRIGGER_LABELS.get(trigger_mode, "AI生成"),
        "status": status,
        "status_label": TASK_STATUS_LABELS.get(status, "处理中"),
        "is_active": status in ACTIVE_TASK_STATUSES,
        "is_terminal": status in FINAL_TASK_STATUSES,
        "document_type": _safe_text(item.get("document_type"), max_length=MAX_DOCUMENT_TYPE_CHARS),
        "requirement_text": _safe_text(item.get("requirement_text"), max_length=MAX_REQUIREMENT_TEXT_CHARS),
        "error_message": _safe_text(item.get("error_message"), max_length=280),
        "created_at": _safe_text(item.get("created_at")),
        "started_at": _safe_text(item.get("started_at")),
        "completed_at": _safe_text(item.get("completed_at")),
        "updated_at": _safe_text(item.get("updated_at")),
        "generated_material_id": generated_material_id,
        "generated_material_path": _safe_text(
            (material or {}).get("material_path") or item.get("generated_material_path"),
        ),
        "generated_material_viewer_url": _safe_text((material or {}).get("viewer_url")),
        "generated_material": material,
    }


def _load_task_rows(conn, session_ids: list[int], *, teacher_id: int | None = None) -> dict[int, dict]:
    if not session_ids:
        return {}

    placeholders = ",".join("?" for _ in session_ids)
    params: list[object] = list(session_ids)
    teacher_sql = ""
    if teacher_id is not None:
        teacher_sql = " AND t.teacher_id = ?"
        params.append(int(teacher_id))

    rows = conn.execute(
        f"""
        SELECT t.*
        FROM session_material_generation_tasks t
        JOIN (
            SELECT session_id, MAX(id) AS latest_id
            FROM session_material_generation_tasks
            WHERE session_id IN ({placeholders})
            GROUP BY session_id
        ) latest ON latest.latest_id = t.id
        WHERE 1 = 1 {teacher_sql}
        ORDER BY t.id DESC
        """,
        params,
    ).fetchall()

    generated_material_map = get_learning_material_brief_map(
        conn,
        (row["generated_material_id"] for row in rows),
        teacher_id=teacher_id,
        markdown_only=True,
    )

    return {
        int(row["session_id"]): serialize_generation_task(
            row,
            generated_material=generated_material_map.get(int(row["generated_material_id"] or 0)),
        )
        for row in rows
    }


def attach_generation_tasks(
    conn,
    items: list[dict[str, Any]],
    *,
    teacher_id: int | None = None,
) -> list[dict[str, Any]]:
    expire_stale_generation_tasks(conn)
    session_ids = []
    for item in items:
        try:
            session_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            session_id = 0
        if session_id > 0:
            session_ids.append(session_id)

    task_map = _load_task_rows(conn, session_ids, teacher_id=teacher_id)
    for item in items:
        try:
            session_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            session_id = 0
        task = task_map.get(session_id)
        item["material_generation_task"] = task
        item["material_generation_status"] = task["status"] if task else "idle"
        item["has_material_generation_in_progress"] = bool(task and task["is_active"])
    return items


def get_latest_generation_task(
    conn,
    *,
    session_id: int,
    teacher_id: int | None = None,
) -> dict[str, Any] | None:
    expire_stale_generation_tasks(conn)
    params: list[object] = [int(session_id)]
    teacher_sql = ""
    if teacher_id is not None:
        teacher_sql = " AND teacher_id = ?"
        params.append(int(teacher_id))
    row = conn.execute(
        f"""
        SELECT *
        FROM session_material_generation_tasks
        WHERE session_id = ? {teacher_sql}
        ORDER BY id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if not row:
        return None
    material_map = get_learning_material_brief_map(
        conn,
        [row["generated_material_id"]],
        teacher_id=teacher_id,
        markdown_only=True,
    )
    return serialize_generation_task(
        row,
        generated_material=material_map.get(int(row["generated_material_id"] or 0)),
    )


def normalize_document_type(value: Any, *, session_title: str = "", session_content: str = "") -> str:
    normalized = " ".join(str(value or "").split()).strip()
    if normalized:
        return normalized[:MAX_DOCUMENT_TYPE_CHARS]

    context = f"{session_title}\n{session_content}".strip()
    if "实验" in context:
        return "实验指导"
    if "复习" in context or "总结" in context:
        return "复习提纲"
    if "案例" in context:
        return "案例讲义"
    return "课堂学习文档"


def normalize_requirement_text(value: Any) -> str:
    return _safe_text(value, max_length=MAX_REQUIREMENT_TEXT_CHARS)


async def extract_example_documents(files: list[UploadFile] | None) -> list[dict[str, Any]]:
    normalized_files = [file for file in (files or []) if file and _safe_text(file.filename)]
    if len(normalized_files) > MAX_EXAMPLE_FILE_COUNT:
        raise HTTPException(400, f"示例文档最多上传 {MAX_EXAMPLE_FILE_COUNT} 个。")

    extracted: list[dict[str, Any]] = []
    for file in normalized_files:
        file_name = _safe_text(file.filename)
        ext = Path(file_name).suffix.lower()
        payload = await file.read()
        if len(payload) > MAX_EXAMPLE_FILE_BYTES:
            raise HTTPException(400, f"示例文档 {file_name} 超过 {MAX_EXAMPLE_FILE_BYTES // (1024 * 1024)}MB 限制。")

        text = ""
        truncated = False
        if ext in TEXT_SAMPLE_EXTENSIONS:
            for encoding in ("utf-8", "gbk", "gb2312", "latin-1"):
                try:
                    text = payload.decode(encoding)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if not text:
                text = payload.decode("utf-8", errors="replace")
        elif ext in EXTRACTABLE_SAMPLE_EXTENSIONS:
            try:
                from ai_assistant_doc_extract import extract_document_text
            except Exception as exc:  # pragma: no cover
                raise HTTPException(500, f"示例文档解析组件不可用: {exc}") from exc

            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as temp_file:
                temp_file.write(payload)
                temp_path = temp_file.name
            try:
                result = extract_document_text(Path(temp_path), ext)
                text = str(result.text or "")
            finally:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
        else:
            raise HTTPException(400, f"示例文档 {file_name} 格式暂不支持，请上传 Markdown、文本、Office 或 PDF 文件。")

        text = _safe_text(text)
        if not text:
            raise HTTPException(400, f"示例文档 {file_name} 没有可提取的文本内容。")

        text, truncated = _truncate_text(text, limit=MAX_EXAMPLE_TEXT_CHARS)
        extracted.append(
            {
                "name": file_name,
                "ext": ext,
                "size": len(payload),
                "content": text,
                "truncated": truncated,
            }
        )

    return extracted


def create_generation_task(
    conn,
    *,
    class_offering_id: int,
    session_id: int,
    teacher_id: int,
    trigger_mode: str,
    document_type: str,
    requirement_text: str,
    example_documents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    expire_stale_generation_tasks(conn)
    existing_active = conn.execute(
        """
        SELECT *
        FROM session_material_generation_tasks
        WHERE session_id = ?
          AND teacher_id = ?
          AND status IN (?, ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(session_id), int(teacher_id), TASK_STATUS_QUEUED, TASK_STATUS_RUNNING),
    ).fetchone()
    if existing_active:
        task = serialize_generation_task(existing_active)
        task["already_running"] = True
        return task

    now = _now_iso()
    payload = {
        "example_documents": list(example_documents or []),
    }
    cursor = conn.execute(
        """
        INSERT INTO session_material_generation_tasks (
            class_offering_id,
            session_id,
            teacher_id,
            trigger_mode,
            status,
            document_type,
            requirement_text,
            request_payload_json,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(class_offering_id),
            int(session_id),
            int(teacher_id),
            _safe_text(trigger_mode, max_length=24).lower() or "guided",
            TASK_STATUS_QUEUED,
            document_type,
            requirement_text,
            json.dumps(payload, ensure_ascii=False),
            now,
        ),
    )
    row = conn.execute(
        "SELECT * FROM session_material_generation_tasks WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    task = serialize_generation_task(row)
    task["already_running"] = False
    return task


def _load_material_text(conn, material_id: int) -> str:
    row = conn.execute(
        """
        SELECT id, name, material_path, file_hash, preview_type, node_type
        FROM course_materials
        WHERE id = ?
        LIMIT 1
        """,
        (int(material_id),),
    ).fetchone()
    if not row:
        return ""
    if str(row["node_type"] or "") != "file" or str(row["preview_type"] or "") != "markdown" or not row["file_hash"]:
        return ""

    file_path = Path(GLOBAL_FILES_DIR) / str(row["file_hash"])
    if not file_path.exists():
        return ""

    raw_bytes = file_path.read_bytes()
    for encoding in ("utf-8", "gbk", "gb2312", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def _common_path_prefix(paths: list[str]) -> str:
    if not paths:
        return ""
    split_paths = [[segment for segment in path.split("/") if segment] for path in paths if path]
    if not split_paths:
        return ""
    prefix: list[str] = []
    min_length = min(len(segments) for segments in split_paths)
    for index in range(min_length):
        current = split_paths[0][index]
        if all(segments[index] == current for segments in split_paths[1:]):
            prefix.append(current)
        else:
            break
    return "/".join(prefix)


def _collapse_repeated_leading_segments(path: str) -> str:
    normalized = normalize_material_path(path)
    segments = [segment for segment in normalized.split("/") if segment]
    if len(segments) < 4:
        return normalized

    for prefix_size in range(len(segments) // 2, 1, -1):
        prefix = segments[:prefix_size]
        if segments[prefix_size: prefix_size * 2] == prefix:
            return "/".join(prefix + segments[prefix_size * 2:])
    return normalized


def _select_reference_docs(previous_docs: list[dict[str, Any]], *, trigger_mode: str) -> list[dict[str, Any]]:
    ordered_docs = sorted(
        (dict(item) for item in previous_docs if item),
        key=lambda item: int(item.get("order_index") or 0),
    )
    if not ordered_docs:
        return []

    normalized_mode = _safe_text(trigger_mode).lower()
    if normalized_mode == "auto":
        return ordered_docs[-MAX_AUTO_REFERENCE_DOCS:]
    if normalized_mode == "guided":
        return ordered_docs[-MAX_GUIDED_REFERENCE_DOCS:]
    return ordered_docs


def _load_material_row_by_path(conn, *, teacher_id: int, material_path: str):
    return conn.execute(
        """
        SELECT id, material_path, name, root_id, node_type
        FROM course_materials
        WHERE teacher_id = ?
          AND material_path = ?
        LIMIT 1
        """,
        (int(teacher_id), material_path),
    ).fetchone()


def _resolve_structure_parent_row(conn, *, teacher_id: int, paths: list[str]):
    common_prefix = _common_path_prefix(paths)
    if not common_prefix:
        return None

    row = _load_material_row_by_path(conn, teacher_id=teacher_id, material_path=common_prefix)
    if row and str(row["node_type"] or "") != "folder" and "/" in common_prefix:
        fallback_prefix = common_prefix.rsplit("/", 1)[0]
        row = _load_material_row_by_path(conn, teacher_id=teacher_id, material_path=fallback_prefix)

    if (
        row
        and str(row["node_type"] or "") == "folder"
        and len(paths) == 1
        and paths[0].lower().endswith("/readme.md")
        and "/" in str(row["material_path"] or "")
    ):
        parent_prefix = str(row["material_path"]).rsplit("/", 1)[0]
        parent_row = _load_material_row_by_path(conn, teacher_id=teacher_id, material_path=parent_prefix)
        if parent_row and str(parent_row["node_type"] or "") == "folder":
            row = parent_row

    if not row or str(row["node_type"] or "") != "folder":
        return None
    return row


def _build_structure_candidates(conn, *, teacher_id: int, previous_docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = [
        {
            "key": "teacher_root",
            "label": "教师材料库根目录",
            "parent_id": None,
            "parent_path": "",
            "sample_paths": [],
        }
    ]

    all_paths = [
        _collapse_repeated_leading_segments(_safe_text(item.get("material_path")))
        for item in previous_docs
        if _safe_text(item.get("material_path"))
    ]
    if not all_paths:
        return candidates

    candidate_specs = [
        ("recent_parent", "沿用最近课时目录结构", all_paths[-MAX_STRUCTURE_REFERENCE_DOCS:], all_paths[-4:]),
        ("common_parent", "沿用历史公共目录", all_paths, all_paths[-4:]),
    ]
    seen_parent_ids: set[int] = set()
    for key, label, candidate_paths, sample_paths in candidate_specs:
        row = _resolve_structure_parent_row(conn, teacher_id=int(teacher_id), paths=candidate_paths)
        if not row:
            continue
        parent_id = int(row["id"])
        if parent_id in seen_parent_ids:
            continue
        seen_parent_ids.add(parent_id)
        candidates.append(
            {
                "key": key,
                "label": f"{label}：{row['material_path']}",
                "parent_id": parent_id,
                "parent_path": _safe_text(row["material_path"]),
                "sample_paths": sample_paths,
            }
        )
    return candidates


def _coerce_path_relative_to_parent(path: Any, *, parent_path: str) -> str:
    normalized = _collapse_repeated_leading_segments(_safe_text(path))
    if not normalized:
        return ""

    normalized_parent = _collapse_repeated_leading_segments(parent_path)
    if not normalized_parent:
        return normalized

    prefixes = [normalized_parent]
    parent_leaf = normalized_parent.rsplit("/", 1)[-1]
    if parent_leaf and parent_leaf not in prefixes:
        prefixes.append(parent_leaf)

    for prefix in prefixes:
        if normalized == prefix:
            return ""
        if normalized.startswith(f"{prefix}/"):
            return normalized[len(prefix) + 1:]
    return normalized


def _rebase_generation_result_to_parent(
    result_payload: dict[str, Any],
    *,
    base_parent_path: str,
) -> dict[str, Any]:
    payload = dict(result_payload or {})
    payload["bind_path"] = _coerce_path_relative_to_parent(
        payload.get("bind_path"),
        parent_path=base_parent_path,
    )

    rebased_nodes: list[dict[str, Any]] = []
    raw_nodes = payload.get("nodes")
    if isinstance(raw_nodes, list):
        for raw_node in raw_nodes:
            if not isinstance(raw_node, dict):
                continue
            next_node = dict(raw_node)
            next_path = _coerce_path_relative_to_parent(
                raw_node.get("path"),
                parent_path=base_parent_path,
            )
            if not next_path:
                continue
            next_node["path"] = next_path
            rebased_nodes.append(next_node)
    payload["nodes"] = rebased_nodes
    return payload


def _build_reference_file_texts(
    *,
    previous_docs: list[dict[str, Any]],
    example_documents: list[dict[str, Any]],
) -> list[dict[str, str]]:
    file_texts: list[dict[str, str]] = []
    total_chars = 0

    for item in previous_docs:
        excerpt, _truncated = _truncate_text(
            _safe_text(item.get("content")),
            limit=MAX_REFERENCE_TEXT_CHARS_PER_FILE,
        )
        if not excerpt:
            continue
        total_chars += len(excerpt)
        if total_chars > MAX_REFERENCE_TOTAL_CHARS:
            break
        file_texts.append(
            {
                "name": f"历史课时-{int(item.get('order_index') or 0):02d}-{_safe_text(item.get('title')) or '未命名'}.md",
                "content": (
                    f"原课时：第 {int(item.get('order_index') or 0)} 次课\n"
                    f"原路径：{_safe_text(item.get('material_path'))}\n\n"
                    f"{excerpt}"
                ),
            }
        )

    for item in example_documents:
        if len(file_texts) >= MAX_AI_FILE_TEXT_ITEMS:
            break
        file_texts.append(
            {
                "name": _safe_text(item.get("name")) or "示例文档",
                "content": _safe_text(item.get("content")),
            }
        )

    return file_texts[:MAX_AI_FILE_TEXT_ITEMS]


def _build_generation_user_message(
    *,
    classroom: dict[str, Any],
    session: dict[str, Any],
    task: dict[str, Any],
    previous_docs: list[dict[str, Any]],
    structure_candidates: list[dict[str, Any]],
) -> str:
    teacher_requirement = _safe_text(task.get("requirement_text"))
    document_type = _safe_text(task.get("document_type")) or "课堂学习文档"
    trigger_label = TASK_TRIGGER_LABELS.get(_safe_text(task.get("trigger_mode")).lower(), "AI生成")

    previous_doc_lines = []
    for item in previous_docs:
        previous_doc_lines.append(
            f"- 第 {int(item.get('order_index') or 0)} 次课《{_safe_text(item.get('title'))}》"
            f" -> {_safe_text(item.get('material_path'))}"
        )

    candidate_lines = []
    for candidate in structure_candidates:
        suffix = f"（示例：{'；'.join(candidate['sample_paths'])}）" if candidate["sample_paths"] else ""
        candidate_lines.append(f"- {candidate['key']}: {candidate['label']}{suffix}")

    message_parts = [
        f"请为当前课堂生成“{document_type}”，并保持与已有材料的风格、结构和命名逻辑尽量一致。",
        "",
        "【当前操作】",
        f"- 触发方式：{trigger_label}",
        (
            f"- 历史参考范围：本次只提供最近 {len(previous_docs)} 份已绑定文档，避免把过多旧材料一次性塞给你。"
            if previous_docs and _safe_text(task.get("trigger_mode")).lower() == "auto"
            else "- 历史参考范围：会优先参考最近课时，并结合教师要求与示例文档。"
        ),
        "- 生成完成后，系统会自动把生成出的 Markdown 文档绑定到当前课时。",
        "",
        "【课堂信息】",
        f"- 课程：{_safe_text(classroom.get('course_name'))}",
        f"- 班级：{_safe_text(classroom.get('class_name'))}",
        f"- 教师：{_safe_text(classroom.get('teacher_name'))}",
        f"- 学期：{_safe_text(classroom.get('semester_name') or classroom.get('semester')) or '未设置'}",
        f"- 排课：{_safe_text(classroom.get('schedule_info')) or '未设置'}",
        "",
        "【当前课时】",
        f"- 课时序号：第 {int(session.get('order_index') or 0)} 次课",
        f"- 标题：{_safe_text(session.get('title'))}",
        f"- 日期：{_safe_text(session.get('session_date'))}",
        f"- 节数：{int(session.get('section_count') or 0) or 1}",
        f"- 内容要求：\n{_safe_text(session.get('content')) or '未填写课时内容'}",
        "",
        "【可用的历史文档】",
        *(previous_doc_lines or ["- 当前课时之前暂无已绑定文档，请主要依据课程信息和课时内容生成。"]),
        "",
        "【可选保存位置】",
        *candidate_lines,
    ]

    if teacher_requirement:
        message_parts.extend(
            [
                "",
                "【教师补充要求】",
                teacher_requirement,
            ]
        )

    message_parts.extend(
        [
            "",
            "历史文档和示例文档（如果有）会作为附加文件一并提供给你参考。",
            "请只返回 JSON，不要输出解释。",
        ]
    )
    return "\n".join(message_parts)


async def _call_generation_ai(
    *,
    user_message: str,
    file_texts: list[dict[str, str]],
) -> dict[str, Any]:
    system_prompt = (
        "你是一名高校课程材料架构师，负责为具体课时生成可直接落库的 Markdown 学习文档。\n"
        "你必须严格返回 JSON 对象，不要输出 Markdown 代码块，也不要输出额外解释。\n"
        "输出 JSON 结构如下：\n"
        "{\n"
        '  "target_parent_key": "从候选目录 key 中选择一个",\n'
        '  "bind_path": "相对路径，指向本次课要绑定的 Markdown 文件",\n'
        '  "summary": "一句话说明生成策略",\n'
        '  "nodes": [\n'
        '    {"path": "相对路径", "type": "folder"},\n'
        '    {"path": "相对路径/xxx.md", "type": "markdown", "content": "Markdown 正文", "bind": true}\n'
        "  ]\n"
        "}\n\n"
        "规则：\n"
        "1. 所有 path 都必须相对于 target_parent_key 对应的父目录，不能出现绝对路径、盘符或 ..。\n"
        "2. 可以创建文件夹，但最终至少要有 1 个 Markdown 文件。\n"
        "3. 若历史文档呈现“每课一个文件夹 + readme.md”模式，请优先沿用；若历史文档是同级 Markdown 文件，请优先沿用。\n"
        "4. 只生成本次课真正需要的最小结构，不要创建无意义的空目录。\n"
        "5. bind_path 必须命中某个 Markdown 文件；若 nodes 中只有一个 Markdown 文件，也要显式返回 bind_path。\n"
        "6. Markdown 内容要结构清晰，适合教师课堂使用与学生课后复习，默认使用中文。\n"
        "7. 不能覆盖历史文档，不要复用已有课时的文件路径。"
    )
    payload = {
        "system_prompt": system_prompt,
        "messages": [],
        "new_message": user_message,
        "base64_urls": [],
        "file_texts": file_texts,
        "model_capability": "thinking",
        "response_format": "json",
        "task_priority": "background",
        "task_label": "session_material_generate",
    }
    try:
        response = await ai_client.post("/api/ai/chat", json=payload, timeout=240.0)
        response.raise_for_status()
        data = response.json()
        parsed = data.get("response_json")
        if not isinstance(parsed, dict):
            raise HTTPException(500, "AI 未返回有效的 JSON 结果。")
        return parsed
    except httpx.ConnectError:
        raise HTTPException(503, "AI 助教服务未运行，请先启动 ai_assistant.py。")
    except httpx.TimeoutException:
        raise HTTPException(504, "AI 生成超时，请稍后重试。")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"AI 服务错误: {exc.response.text}")


def _normalize_generated_nodes(result_payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    raw_nodes = result_payload.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise HTTPException(500, "AI 未返回可执行的材料节点。")

    bind_path = _safe_text(result_payload.get("bind_path"))
    normalized_nodes: list[dict[str, Any]] = []

    for raw_node in raw_nodes[:MAX_GENERATED_NODE_COUNT]:
        if not isinstance(raw_node, dict):
            continue
        node_type = _safe_text(raw_node.get("type")).lower()
        raw_path = _safe_text(raw_node.get("path"))
        if not raw_path:
            continue
        normalized_path = normalize_material_path(raw_path)

        if node_type in {"folder", "dir", "directory"}:
            normalized_nodes.append({"type": "folder", "path": normalized_path})
            continue

        if node_type in {"markdown", "file", "markdown_file", "md"}:
            if not normalized_path.lower().endswith((".md", ".markdown")):
                normalized_path = f"{normalized_path}.md"
            content = _safe_text(raw_node.get("content"))
            if not content:
                continue
            if len(content) > MAX_GENERATED_FILE_CHARS:
                content = content[:MAX_GENERATED_FILE_CHARS].rstrip()
            normalized_nodes.append(
                {
                    "type": "file",
                    "path": normalized_path,
                    "content": content,
                    "bind": bool(raw_node.get("bind")) or normalized_path == bind_path,
                }
            )

    file_nodes = [node for node in normalized_nodes if node["type"] == "file"]
    if not file_nodes:
        raise HTTPException(500, "AI 结果中没有可绑定的 Markdown 文件。")

    if bind_path:
        bind_path = normalize_material_path(bind_path)
        if not bind_path.lower().endswith((".md", ".markdown")):
            bind_path = f"{bind_path}.md"
        for node in file_nodes:
            node["bind"] = node["path"] == bind_path or bool(node["bind"])

    if sum(1 for node in file_nodes if node.get("bind")) == 0:
        file_nodes[0]["bind"] = True

    selected_bind_seen = False
    for node in file_nodes:
        if node.get("bind") and not selected_bind_seen:
            selected_bind_seen = True
        else:
            node["bind"] = False

    normalized_nodes.sort(key=lambda item: (0 if item["type"] == "folder" else 1, item["path"].count("/"), item["path"]))
    selected_bind_path = next(node["path"] for node in file_nodes if node.get("bind"))
    return selected_bind_path, normalized_nodes


def _get_child_row(conn, *, teacher_id: int, parent_id: int | None, name: str):
    if parent_id is None:
        return conn.execute(
            """
            SELECT *
            FROM course_materials
            WHERE teacher_id = ?
              AND parent_id IS NULL
              AND name = ?
            LIMIT 1
            """,
            (int(teacher_id), name),
        ).fetchone()
    return conn.execute(
        """
        SELECT *
        FROM course_materials
        WHERE teacher_id = ?
          AND parent_id = ?
          AND name = ?
        LIMIT 1
        """,
        (int(teacher_id), int(parent_id), name),
    ).fetchone()


def _store_markdown_bytes(content: str) -> tuple[str, int]:
    payload_bytes = content.encode("utf-8")
    file_hash = hashlib.sha256(payload_bytes).hexdigest()
    GLOBAL_FILES_DIR.mkdir(parents=True, exist_ok=True)
    target_path = Path(GLOBAL_FILES_DIR) / file_hash
    if not target_path.exists():
        target_path.write_bytes(payload_bytes)
    return file_hash, len(payload_bytes)


def _create_folder_row(
    conn,
    *,
    teacher_id: int,
    parent_id: int | None,
    root_id: int | None,
    material_path: str,
    name: str,
    now: str,
) -> dict[str, Any]:
    cursor = conn.execute(
        """
        INSERT INTO course_materials (
            teacher_id,
            parent_id,
            root_id,
            material_path,
            name,
            node_type,
            mime_type,
            preview_type,
            ai_capability,
            file_ext,
            file_hash,
            file_size,
            ai_parse_status,
            ai_optimize_status,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'folder', 'inode/directory', 'folder', 'none', '', NULL, 0, 'idle', 'idle', ?, ?)
        """,
        (teacher_id, parent_id, root_id, material_path, name, now, now),
    )
    folder_id = int(cursor.lastrowid)
    actual_root_id = int(root_id or 0) or folder_id
    if not root_id:
        conn.execute(
            "UPDATE course_materials SET root_id = ? WHERE id = ?",
            (actual_root_id, folder_id),
        )
    row = conn.execute(
        "SELECT * FROM course_materials WHERE id = ? LIMIT 1",
        (folder_id,),
    ).fetchone()
    row_dict = dict(row)
    row_dict["root_id"] = actual_root_id
    return row_dict


def _create_file_row(
    conn,
    *,
    teacher_id: int,
    parent_id: int | None,
    root_id: int | None,
    material_path: str,
    name: str,
    content: str,
    now: str,
) -> dict[str, Any]:
    file_hash, file_size = _store_markdown_bytes(content)
    file_profile = infer_material_profile(name, "text/markdown")
    cursor = conn.execute(
        """
        INSERT INTO course_materials (
            teacher_id,
            parent_id,
            root_id,
            material_path,
            name,
            node_type,
            mime_type,
            preview_type,
            ai_capability,
            file_ext,
            file_hash,
            file_size,
            ai_parse_status,
            ai_optimize_status,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'file', ?, ?, ?, ?, ?, ?, 'idle', 'idle', ?, ?)
        """,
        (
            teacher_id,
            parent_id,
            root_id,
            material_path,
            name,
            file_profile["mime_type"],
            file_profile["preview_type"],
            file_profile["ai_capability"],
            file_profile["file_ext"],
            file_hash,
            file_size,
            now,
            now,
        ),
    )
    file_id = int(cursor.lastrowid)
    actual_root_id = int(root_id or 0) or file_id
    if not root_id:
        conn.execute(
            "UPDATE course_materials SET root_id = ? WHERE id = ?",
            (actual_root_id, file_id),
        )
    row = conn.execute(
        "SELECT * FROM course_materials WHERE id = ? LIMIT 1",
        (file_id,),
    ).fetchone()
    row_dict = dict(row)
    row_dict["root_id"] = actual_root_id
    return row_dict


def _material_path_join(base_path: str, name: str) -> str:
    return normalize_material_path(f"{base_path}/{name}" if base_path else name)


def persist_generated_materials(
    conn,
    *,
    teacher_id: int,
    class_offering_id: int,
    session_id: int,
    base_parent_id: int | None,
    nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    base_parent = None
    base_path = ""
    base_root_id = None
    if base_parent_id:
        base_parent = conn.execute(
            "SELECT * FROM course_materials WHERE id = ? AND teacher_id = ? LIMIT 1",
            (int(base_parent_id), int(teacher_id)),
        ).fetchone()
        if not base_parent or str(base_parent["node_type"] or "") != "folder":
            raise HTTPException(400, "指定的材料父目录不存在或不可写。")
        base_path = _safe_text(base_parent["material_path"])
        base_root_id = int(base_parent["root_id"] or 0) or None

    now = _now_iso()
    affected_root_ids: set[int] = set()
    folder_cache: dict[str, dict[str, Any]] = {}
    folder_aliases: dict[tuple[str, str], str] = {}
    bound_material_row: dict[str, Any] | None = None

    if base_parent:
        folder_cache[base_path] = dict(base_parent)
        affected_root_ids.add(int(base_parent["root_id"] or 0))

    for node in nodes:
        relative_path = normalize_material_path(node["path"])
        segments = [segment for segment in relative_path.split("/") if segment]
        if not segments:
            continue

        parent_id = int(base_parent["id"]) if base_parent else None
        parent_path = base_path
        current_root_id = base_root_id

        folder_segments = segments if node["type"] == "folder" else segments[:-1]
        for desired_segment in folder_segments:
            alias_key = (parent_path, desired_segment)
            actual_segment = folder_aliases.get(alias_key, desired_segment)
            existing = _get_child_row(
                conn,
                teacher_id=teacher_id,
                parent_id=parent_id,
                name=actual_segment,
            )
            if existing and str(existing["node_type"] or "") != "folder":
                actual_segment = make_unique_material_name(conn, teacher_id, parent_id, desired_segment)
                folder_aliases[alias_key] = actual_segment
                existing = None

            folder_path = _material_path_join(parent_path, actual_segment)
            if folder_path in folder_cache:
                folder_row = folder_cache[folder_path]
            elif existing:
                folder_row = dict(existing)
            else:
                folder_row = _create_folder_row(
                    conn,
                    teacher_id=teacher_id,
                    parent_id=parent_id,
                    root_id=current_root_id,
                    material_path=folder_path,
                    name=actual_segment,
                    now=now,
                )
            folder_cache[folder_path] = folder_row
            parent_id = int(folder_row["id"])
            parent_path = folder_path
            current_root_id = int(folder_row["root_id"] or 0) or current_root_id
            affected_root_ids.add(int(folder_row["root_id"] or 0))

        if node["type"] == "folder":
            continue

        desired_file_name = segments[-1]
        existing_file = _get_child_row(
            conn,
            teacher_id=teacher_id,
            parent_id=parent_id,
            name=desired_file_name,
        )
        if existing_file:
            actual_file_name = make_unique_material_name(conn, teacher_id, parent_id, desired_file_name)
        else:
            actual_file_name = desired_file_name

        file_path = _material_path_join(parent_path, actual_file_name)
        file_row = _create_file_row(
            conn,
            teacher_id=teacher_id,
            parent_id=parent_id,
            root_id=current_root_id,
            material_path=file_path,
            name=actual_file_name,
            content=node["content"],
            now=now,
        )
        affected_root_ids.add(int(file_row["root_id"] or 0))
        if node.get("bind"):
            bound_material_row = file_row

    if not bound_material_row:
        raise HTTPException(500, "系统未找到可绑定到课时的生成文档。")

    conn.execute(
        """
        UPDATE class_offering_sessions
        SET learning_material_id = ?,
            updated_at = ?
        WHERE id = ? AND class_offering_id = ?
        """,
        (int(bound_material_row["id"]), now, int(session_id), int(class_offering_id)),
    )
    sync_classroom_learning_material_assignments(
        conn,
        class_offering_id=int(class_offering_id),
        teacher_id=int(teacher_id),
        material_ids=[int(bound_material_row["id"])],
    )

    for root_id in sorted(root_id for root_id in affected_root_ids if root_id):
        refresh_root_git_metadata(conn, int(root_id))

    brief_map = get_learning_material_brief_map(
        conn,
        [bound_material_row["id"]],
        teacher_id=teacher_id,
        markdown_only=True,
    )
    return brief_map.get(int(bound_material_row["id"])) or {
        "id": int(bound_material_row["id"]),
        "name": _safe_text(bound_material_row.get("name")),
        "material_path": _safe_text(bound_material_row.get("material_path")),
        "viewer_url": f"/materials/view/{int(bound_material_row['id'])}",
    }


def get_teacher_session_with_material_state(
    conn,
    *,
    class_offering_id: int,
    session_id: int,
    teacher_id: int,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT s.id,
               s.class_offering_id,
               s.course_lesson_id,
               s.order_index,
               s.title,
               s.content,
               s.section_count,
               s.slot_section_count,
               s.session_date,
               s.weekday,
               s.week_index,
               s.learning_material_id
        FROM class_offering_sessions s
        JOIN class_offerings o ON o.id = s.class_offering_id
        WHERE s.id = ?
          AND s.class_offering_id = ?
          AND o.teacher_id = ?
        LIMIT 1
        """,
        (int(session_id), int(class_offering_id), int(teacher_id)),
    ).fetchone()
    if not row:
        return None
    items = attach_learning_material_briefs(
        conn,
        [dict(row)],
        teacher_id=int(teacher_id),
        markdown_only=True,
    )
    attach_generation_tasks(conn, items, teacher_id=int(teacher_id))
    return items[0]


async def run_generation_task(task_id: int) -> None:
    task_id = int(task_id)
    try:
        from ..database import get_db_connection

        with get_db_connection() as conn:
            task_row = conn.execute(
                """
                SELECT t.*,
                       s.order_index,
                       s.title AS session_title,
                       s.content AS session_content,
                       s.section_count,
                       s.session_date,
                       o.teacher_id,
                       o.schedule_info,
                       c.name AS course_name,
                       c.description AS course_description,
                       cl.name AS class_name,
                       cl.description AS class_description,
                       sem.name AS semester_name,
                       teacher.name AS teacher_name
                FROM session_material_generation_tasks t
                JOIN class_offering_sessions s ON s.id = t.session_id
                JOIN class_offerings o ON o.id = t.class_offering_id
                JOIN courses c ON c.id = o.course_id
                JOIN classes cl ON cl.id = o.class_id
                JOIN teachers teacher ON teacher.id = o.teacher_id
                LEFT JOIN academic_semesters sem ON sem.id = o.semester_id
                WHERE t.id = ?
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            if not task_row:
                return
            if str(task_row["status"] or "").lower() not in ACTIVE_TASK_STATUSES:
                return

            now = _now_iso()
            conn.execute(
                """
                UPDATE session_material_generation_tasks
                SET status = ?,
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (TASK_STATUS_RUNNING, now, now, task_id),
            )
            conn.commit()

            request_payload = _load_json_payload(task_row["request_payload_json"])
            example_documents = list(request_payload.get("example_documents") or [])

            previous_session_rows = conn.execute(
                """
                SELECT id, order_index, title, session_date, learning_material_id
                FROM class_offering_sessions
                WHERE class_offering_id = ?
                  AND order_index < ?
                  AND learning_material_id IS NOT NULL
                ORDER BY order_index
                """,
                (int(task_row["class_offering_id"]), int(task_row["order_index"] or 0)),
            ).fetchall()
            previous_docs = attach_learning_material_briefs(
                conn,
                [dict(row) for row in previous_session_rows],
                teacher_id=int(task_row["teacher_id"]),
                markdown_only=True,
            )

            normalized_previous_docs: list[dict[str, Any]] = []
            for item in previous_docs:
                if not item.get("learning_material_id"):
                    continue
                content = _load_material_text(conn, int(item["learning_material_id"]))
                if not _safe_text(content):
                    continue
                normalized_previous_docs.append(
                    {
                        "session_id": int(item["id"]),
                        "order_index": int(item["order_index"] or 0),
                        "title": _safe_text(item.get("title")),
                        "session_date": _safe_text(item.get("session_date")),
                        "material_id": int(item["learning_material_id"]),
                        "material_path": _safe_text(item.get("learning_material_path")),
                        "content": content,
                    }
                )

            classroom_context = {
                "course_name": _safe_text(task_row["course_name"]),
                "course_description": _safe_text(task_row["course_description"], max_length=1200),
                "class_name": _safe_text(task_row["class_name"]),
                "class_description": _safe_text(task_row["class_description"], max_length=1200),
                "teacher_name": _safe_text(task_row["teacher_name"]),
                "semester_name": _safe_text(task_row["semester_name"]),
                "schedule_info": _safe_text(task_row["schedule_info"]),
            }
            session_context = {
                "id": int(task_row["session_id"]),
                "order_index": int(task_row["order_index"] or 0),
                "title": _safe_text(task_row["session_title"]),
                "content": _safe_text(task_row["session_content"], max_length=4000),
                "section_count": int(task_row["section_count"] or 0) or 1,
                "session_date": _safe_text(task_row["session_date"]),
            }
            task_context = {
                "id": int(task_row["id"]),
                "trigger_mode": _safe_text(task_row["trigger_mode"]).lower() or "guided",
                "document_type": normalize_document_type(
                    task_row["document_type"],
                    session_title=session_context["title"],
                    session_content=session_context["content"],
                ),
                "requirement_text": normalize_requirement_text(task_row["requirement_text"]),
            }
            reference_docs = _select_reference_docs(
                normalized_previous_docs,
                trigger_mode=task_context["trigger_mode"],
            )
            structure_candidates = _build_structure_candidates(
                conn,
                teacher_id=int(task_row["teacher_id"]),
                previous_docs=normalized_previous_docs,
            )

        file_texts = _build_reference_file_texts(
            previous_docs=reference_docs,
            example_documents=example_documents,
        )
        user_message = _build_generation_user_message(
            classroom=classroom_context,
            session=session_context,
            task=task_context,
            previous_docs=reference_docs,
            structure_candidates=structure_candidates,
        )
        ai_result = await _call_generation_ai(user_message=user_message, file_texts=file_texts)
        target_parent_key = _safe_text(ai_result.get("target_parent_key")).lower() or "teacher_root"
        fallback_parent = next(
            (candidate for candidate in structure_candidates if candidate["key"] != "teacher_root"),
            structure_candidates[0],
        )
        base_parent = next(
            (candidate for candidate in structure_candidates if candidate["key"] == target_parent_key),
            fallback_parent,
        )
        ai_result = _rebase_generation_result_to_parent(
            ai_result,
            base_parent_path=_safe_text(base_parent.get("parent_path")),
        )
        bind_path, nodes = _normalize_generated_nodes(ai_result)

        with get_db_connection() as conn:
            generated_material = persist_generated_materials(
                conn,
                teacher_id=int(task_row["teacher_id"]),
                class_offering_id=int(task_row["class_offering_id"]),
                session_id=int(task_row["session_id"]),
                base_parent_id=base_parent["parent_id"],
                nodes=nodes,
            )

            result_payload = {
                "summary": _safe_text(ai_result.get("summary"), max_length=300),
                "target_parent_key": base_parent["key"],
                "bind_path": bind_path,
                "created_bind_material_path": generated_material["material_path"],
                "node_count": len(nodes),
            }
            now = _now_iso()
            conn.execute(
                """
                UPDATE session_material_generation_tasks
                SET status = ?,
                    generated_material_id = ?,
                    generated_material_path = ?,
                    result_payload_json = ?,
                    error_message = '',
                    completed_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    TASK_STATUS_COMPLETED,
                    int(generated_material["id"]),
                    generated_material["material_path"],
                    json.dumps(result_payload, ensure_ascii=False),
                    now,
                    now,
                    task_id,
                ),
            )
            conn.commit()
    except Exception as exc:
        error_message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        try:
            from ..database import get_db_connection

            with get_db_connection() as conn:
                now = _now_iso()
                conn.execute(
                    """
                    UPDATE session_material_generation_tasks
                    SET status = ?,
                        error_message = ?,
                        completed_at = COALESCE(completed_at, ?),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        TASK_STATUS_FAILED,
                        _safe_text(error_message, max_length=280) or "生成失败",
                        now,
                        now,
                        task_id,
                    ),
                )
                conn.commit()
        except Exception as inner_exc:  # pragma: no cover
            print(f"[SESSION_MATERIAL_AI] failed to persist error state: {inner_exc}")
        print(f"[SESSION_MATERIAL_AI] generation failed for task {task_id}: {error_message}")
