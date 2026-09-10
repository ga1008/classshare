"""Authorized compatibility reads for the Agent bridge.

SQL supplied by the runtime is never executed. Old, exact query examples map
to server-owned templates; new callers address those templates by name. This
keeps migration reads available while the full business tool catalog evolves.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ..config import AGENT_TASK_WORKSPACE_ROOT
from ..db.connection import get_configured_db_engine
from .agent_actor_service import AgentActor
from .agent_bridge_service import EXAMPLE_QUERIES, read_platform_file, run_readonly_query, unified_search
from .resource_access_service import ensure_classroom_access, teacher_can_read_assignment


QUERY_NAMES = (
    "my_classrooms", "classroom_assignments", "assignment_missing_students",
    "gongwen_search", "classroom_sessions", "my_materials", "my_schedule", "low_scores",
)
_QUERIES = dict(zip(QUERY_NAMES, EXAMPLE_QUERIES, strict=True))
_PARAMS = {
    "my_classrooms": set(),
    "classroom_assignments": {"class_offering_id"},
    "assignment_missing_students": {"class_offering_id", "assignment_id"},
    "gongwen_search": {"keyword", "pattern"},
    "classroom_sessions": {"class_offering_id"},
    "my_materials": set(),
    "my_schedule": {"start_at"},
    "low_scores": {"threshold"},
}


def query_catalog(actor: AgentActor) -> list[dict[str, Any]]:
    if actor.role != "teacher":
        return []
    return [
        {"name": name, "purpose": item["purpose"], "parameters": sorted(_PARAMS[name])}
        for name, item in _QUERIES.items()
    ]


def _canonical_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", str(sql or "")).strip().rstrip(";").strip().casefold()


def _positive_int(params: dict[str, Any], key: str) -> int:
    value = params.get(key)
    try:
        if isinstance(value, bool):
            raise ValueError
        result = int(value)
        if result <= 0 or str(result) != str(value).strip():
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"{key} 必须是正整数。") from None


def run_scoped_query(
    conn, actor: AgentActor, *, query: str = "", sql: str = "",
    params: dict[str, Any] | None = None, limit: int = 200,
) -> dict[str, Any]:
    if actor.role != "teacher":
        raise HTTPException(status_code=403, detail="该兼容查询仅用于教师业务，请使用当前身份的业务工具。")
    name = str(query or "").strip()
    if sql:
        matched = next((key for key, item in _QUERIES.items() if _canonical_sql(sql) == _canonical_sql(item["sql"])), None)
        if not matched or (name and name != matched):
            raise HTTPException(status_code=422, detail={
                "code": "agent_query_template_required",
                "message": "任意 SQL 已停用。请从 /api/agent-bridge/schema 选择授权查询名称及参数。",
            })
        name = matched
    if name not in _QUERIES:
        raise HTTPException(status_code=422, detail="未知授权查询，请先读取查询目录。")
    given = dict(params or {})
    # A former example included teacher_id. Keep that compatibility field but
    # never let it select the actor, including for a platform administrator.
    if "teacher_id" in given and str(given["teacher_id"]) != str(actor.id):
        raise HTTPException(status_code=403, detail="不能通过查询参数切换执行身份。")
    unknown = set(given) - _PARAMS[name] - {"teacher_id"}
    if unknown:
        raise HTTPException(status_code=422, detail="查询包含未声明的参数。")
    values = {key: value for key, value in given.items() if key in _PARAMS[name]}
    values["teacher_id"] = actor.id
    if "class_offering_id" in _PARAMS[name]:
        offering_id = _positive_int(values, "class_offering_id")
        ensure_classroom_access(conn, offering_id, actor.as_user())
        values["class_offering_id"] = offering_id
    if name == "assignment_missing_students":
        assignment_id = _positive_int(values, "assignment_id")
        assignment = conn.execute("SELECT * FROM assignments WHERE id = ? LIMIT 1", (assignment_id,)).fetchone()
        if (not assignment or not teacher_can_read_assignment(conn, actor.id, assignment)
                or int(assignment["class_offering_id"] or 0) != values["class_offering_id"]):
            raise HTTPException(status_code=403, detail="作业与已授权课堂不匹配。")
        values["assignment_id"] = assignment_id
    if name == "low_scores":
        try:
            threshold = float(values.get("threshold", 60))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="threshold 必须为有效分数。") from None
        if not 0 <= threshold <= 1000:
            raise HTTPException(status_code=422, detail="threshold 超出有效范围。")
        values["threshold"] = threshold
    if name == "my_schedule":
        start_at = str(values.get("start_at") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ][0-9:+.Z-]{5,32})?", start_at):
            raise HTTPException(status_code=422, detail="start_at 必须为 ISO 日期或时间。")
        values["start_at"] = start_at
    if get_configured_db_engine() == "postgres":
        # Scope is the request transaction, never a global database setting.
        conn.execute("SET LOCAL statement_timeout = '5s'")
    if name == "gongwen_search":
        keyword = str(values.get("keyword") or values.get("pattern") or "").strip("% ")
        if not keyword or len(keyword) > 120:
            raise HTTPException(status_code=422, detail="keyword 长度必须为1至120字符。")
        results = unified_search(conn, teacher_id=actor.id, scope="gongwen", keyword=keyword, limit=min(limit, 20))
        return {"query": name, "rows": results, "row_count": len(results), "truncated": len(results) >= min(limit, 20)}
    # Execute the trusted template, NOT the string passed by the runtime.
    result = run_readonly_query(conn, _QUERIES[name]["sql"], limit, params=values)
    return {"query": name, **result}


_PLATFORM_FILE_SELECTORS = ('material_id', 'submission_file_id', 'collaboration_file_id', 'course_file_id')


def _platform_file(conn, actor, kind, file_id):
    """Resolve only a stored file id, using its normal download authorization."""
    from .file_service import resolve_global_file_path
    user = actor.as_user()
    if kind == 'material_id':
        from .materials_service import ensure_user_material_access
        row = dict(ensure_user_material_access(conn, file_id, user))
        if row.get('node_type') != 'file':
            raise HTTPException(404, '材料文件不存在。')
        resolved = resolve_global_file_path(str(row.get('file_hash') or ''))
        filename, url = str(row.get('name') or ''), f'/materials/view/{file_id}'
    elif kind == 'submission_file_id':
        from .submission_preview_service import ensure_submission_file_access, _resolve_file_path
        row = dict(ensure_submission_file_access(conn, file_id, user))
        # The path comes only from the authorized DB row, never caller input.
        resolved = _resolve_file_path(str(row.get('stored_path') or ''))
        filename, url = str(row.get('original_filename') or ''), f'/submissions/download/{file_id}'
    elif kind == 'collaboration_file_id':
        from .collaboration_service import resolve_group_file_download, _load_group_file
        download = resolve_group_file_download(conn, file_id, user)
        row = _load_group_file(conn, file_id)
        resolved = Path(download['path'])
        filename, url = str(download['filename']), f'/api/collaboration/files/{file_id}/download'
    elif kind == 'course_file_id':
        from .resource_access_service import ensure_scoped_resource_access
        from .download_policy import ensure_download_allowed
        found = conn.execute('''SELECT cf.*, c.created_by_teacher_id FROM course_files cf
            JOIN courses c ON c.id=cf.course_id WHERE cf.id=?''', (file_id,)).fetchone()
        if found is None:
            raise HTTPException(404, '课程文件不存在。')
        row = dict(found)
        # This is exactly files._ensure_course_file_access's shared policy.
        ensure_scoped_resource_access(conn, row, user)
        ensure_download_allowed(row.get('file_size'), resource_label='共享文件')
        resolved = resolve_global_file_path(str(row.get('file_hash') or ''))
        filename, url = str(row.get('file_name') or ''), f'/download/course_file/{file_id}'
    else:
        raise HTTPException(422, '未知平台文件类型。')
    if resolved is None:
        raise HTTPException(404, '文件不存在。')
    source_hash = str(row.get('file_hash') or '').lower()
    if not re.fullmatch('[0-9a-f]{64}', source_hash):
        source_hash = ''  # Legacy submission rows may have no content hash.
    # Bind ownership, permission scope and source pointer without exposing any
    # stored path or internal DB field in the public result.
    binding = hashlib.sha256(json.dumps([kind, file_id, row, str(Path(resolved).absolute())],
        ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    return {'path': Path(resolved), 'filename': filename, 'url': url, 'binding': binding, 'hash': source_hash}


def assert_scoped_file_current(conn, actor, result, **selectors):
    selected = [(key, value) for key, value in selectors.items() if key in _PLATFORM_FILE_SELECTORS and value is not None]
    if not selected:
        return
    if len(selected) != 1:
        raise HTTPException(422, '每次只能指定一类平台文件。')
    kind, file_id = selected[0]
    current = _platform_file(conn, actor, kind, file_id)
    if current['binding'] != result.get('_source_binding') or (current['hash'] and current['hash'] != result.get('sha256')):
        raise HTTPException(409, '文件或其归属已更新，请重新读取当前版本。')
    result.pop('_source_binding', None)


def read_scoped_file(
    conn, actor: AgentActor, task_id: int, *, path: str = "",
    material_id: int | None = None, revision: str | None = None,
    submission_file_id: int | None = None, collaboration_file_id: int | None = None,
    course_file_id: int | None = None,
) -> dict[str, Any]:
    if not isinstance(path, str):
        raise HTTPException(422, '任务路径必须是文本。')
    selected = [(key, value) for key, value in (
        ('material_id', material_id), ('submission_file_id', submission_file_id),
        ('collaboration_file_id', collaboration_file_id), ('course_file_id', course_file_id)) if value is not None]
    if len(selected) + bool(path.strip()) != 1:
        raise HTTPException(422, '请且只能指定一个平台文件标识或本任务相对路径。')
    if selected:
        kind, file_id = selected[0]
        if type(file_id) is not int or not 1 <= file_id <= 2**63 - 1:
            raise HTTPException(422, '文件标识必须是正整数。')
        source = _platform_file(conn, actor, kind, file_id)
        if revision is not None and source['hash'] and revision != source['hash']:
            raise HTTPException(409, '文件已更新，请重新获取当前版本。')
        result = read_platform_file(str(source['path']), filename=source['filename'])
        if (source['hash'] and result['sha256'] != source['hash']) or (revision is not None and result['sha256'] != revision):
            raise HTTPException(409, '文件内容与预期版本不一致，请重新读取或核对存储。')
        result.pop("path", None)
        return {**result, kind: file_id, 'revision': result['sha256'], 'url': source['url'], '_source_binding': source['binding']}
    raw = str(path or "").strip()
    if not raw:
        raise HTTPException(status_code=422, detail="请提供 material_id 或本任务内的相对文件路径。")
    root = (AGENT_TASK_WORKSPACE_ROOT / "tasks" / str(int(task_id))).absolute()
    requested = Path(raw)
    resolved = (requested if requested.is_absolute() else root / requested).absolute()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=403, detail="只允许读取本任务文件；平台材料请使用 material_id。") from None
    if any(part.startswith(".") for part in relative.parts) or relative.name.casefold() in {
        "bridge.md", "config.toml", "credentials.json", "docker.env",
    }:
        raise HTTPException(status_code=403, detail="运行配置或凭据文件不作为任务内容提供。")
    result = read_platform_file(str(resolved))
    if revision is not None and revision != result['sha256']:
        raise HTTPException(409, '任务文件已更新，请重新获取当前版本。')
    result["path"] = relative.as_posix()
    result['revision'] = result['sha256']
    return result
