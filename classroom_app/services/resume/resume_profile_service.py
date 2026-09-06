"""Personal info + list-section CRUD for the student resume console.

Sections:

* ``personal``    — singleton row per student (``resume_personal_info``).
* list sections   — ``self_intro`` / ``certificate`` / ``skill`` / ``experience``
                    / ``education`` (one table each, many rows per student).

All writes go through small whitelisted helpers so the router only forwards a
plain payload dict. Validation raises ``ValueError`` (the router maps it to HTTP
400). Mirrors the lightweight engine-aware approach used across the codebase
(``execute_insert_returning_id``).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from ...db.connection import execute_insert_returning_id
from ...db.schema_resume import ensure_resume_schema

# ---------------------------------------------------------------------------
# Section registry — column whitelist + required fields per list section.
# ---------------------------------------------------------------------------
LIST_SECTIONS: dict[str, dict[str, Any]] = {
    "self_intro": {
        "table": "resume_self_intros",
        "fields": ("title", "content_md", "source"),
        "required": ("content_md",),
        "label": "自我介绍",
    },
    "certificate": {
        "table": "resume_certificates",
        "fields": ("name", "acquired_date", "expiry_date", "description"),
        "required": ("name", "acquired_date"),
        "label": "证书",
        "has_attachments": True,
    },
    "skill": {
        "table": "resume_skills",
        "fields": ("name", "level", "acquired_date", "expiry_date", "description"),
        "required": ("name", "acquired_date"),
        "label": "技能",
        "has_attachments": True,
    },
    "experience": {
        "table": "resume_experiences",
        "fields": ("kind", "title", "start_date", "end_date", "role", "content", "contribution", "achievement"),
        "required": ("title", "start_date", "end_date"),
        "label": "经验",
        "has_attachments": True,
    },
    "education": {
        "table": "resume_educations",
        "fields": ("kind", "school", "college", "major", "degree", "start_date", "end_date", "content", "source"),
        "required": ("school", "start_date", "end_date"),
        "label": "学历",
    },
}

PERSONAL_FIELDS = (
    "name", "gender", "birthday", "phone", "email", "qq", "wechat", "address",
    "hometown", "id_card", "expected_position", "expected_industry", "expected_salary",
)
PERSONAL_REQUIRED = ("name", "expected_position")
PERSONAL_CONTACT_FIELDS = ("email", "phone")
EXPERIENCE_KINDS = {
    "internship": "实习", "project": "项目", "course": "课程成果", "competition": "比赛",
    "campus": "社团 / 学生工作", "volunteer": "志愿服务", "part_time": "兼职", "research": "调研 / 科研",
    "employment": "全职工作",
}
EDUCATION_DEGREES = ("高中", "中专", "大专", "本科", "硕士", "博士", "其他")

_FIELD_LIMIT = 2000
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _notify_profile_change(conn, student_id: int) -> None:
    # Invalidate unfinished summaries in the same material transaction. Even
    # a concurrent result that read the old evidence cannot remain usable.
    pending = conn.execute("SELECT id FROM resume_self_intros WHERE student_id = ? AND status = 'generating' AND active_job_id <> ''", (int(student_id),)).fetchall()
    if pending:
        from ..student_career_job_service import supersede_student_career_jobs
        for row in pending:
            conn.execute("UPDATE resume_self_intros SET revision = revision + 1, status = 'failed', active_job_id = '', error_text = '资料已更新，请重新生成摘要。' WHERE id = ? AND student_id = ?", (int(row["id"]), int(student_id)))
            supersede_student_career_jobs(conn, scope_type="resume_intro", scope_id=str(row["id"]), student_id=int(student_id))
    from ..career_lifecycle_service import invalidate_career_profile
    invalidate_career_profile(conn, int(student_id))


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _clean(value: Any, *, limit: int = _FIELD_LIMIT) -> str:
    text = str(value if value is not None else "").strip()
    return text[:limit]


def _normalize_section(section: str) -> str:
    key = str(section or "").strip().replace("-", "_")
    if key not in LIST_SECTIONS:
        raise ValueError(f"未知的资料分区：{section}")
    return key


# ---------------------------------------------------------------------------
# Personal info (singleton)
# ---------------------------------------------------------------------------
def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def get_personal_info(conn, student_id: int) -> dict[str, Any]:
    ensure_resume_schema(conn)
    row = conn.execute(
        "SELECT * FROM resume_personal_info WHERE student_id = ? LIMIT 1",
        (int(student_id),),
    ).fetchone()
    info = _row_to_dict(row)
    if info:
        try:
            info["extra"] = json.loads(info.get("extra_json") or "{}")
        except (TypeError, ValueError):
            info["extra"] = {}
    return info


def _ensure_personal_row(conn, student_id: int) -> dict[str, Any]:
    info = get_personal_info(conn, student_id)
    if info:
        return info
    now = _now()
    conn.execute(
        "INSERT INTO resume_personal_info (student_id, created_at, updated_at) VALUES (?, ?, ?) ON CONFLICT(student_id) DO NOTHING",
        (int(student_id), now, now),
    )
    return get_personal_info(conn, student_id)


def validate_personal_info(payload: dict[str, Any]) -> dict[str, str]:
    cleaned = {field: _clean(payload.get(field), limit=200) for field in PERSONAL_FIELDS}
    missing = [field for field in PERSONAL_REQUIRED if not cleaned.get(field)]
    if missing:
        labels = {
            "name": "姓名", "gender": "性别", "birthday": "生日",
            "email": "邮箱", "expected_position": "期望岗位",
        }
        names = "、".join(labels.get(field, field) for field in missing)
        raise ValueError(f"请填写必填项：{names}")
    if not any(cleaned.get(field) for field in PERSONAL_CONTACT_FIELDS):
        raise ValueError("请至少填写一种联系方式：邮箱或手机号")
    if cleaned.get("email") and not _EMAIL_RE.match(cleaned["email"]):
        raise ValueError("邮箱格式不正确")
    return cleaned


def update_personal_info(conn, student_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_resume_schema(conn)
    cleaned = validate_personal_info(payload)
    current = _ensure_personal_row(conn, student_id)
    from .resume_document_service import require_revision, ResumeConflict
    revision = require_revision(current, payload["revision"]) if "revision" in payload else int(current.get("revision") or 1)
    extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
    assignments = ", ".join(f"{field} = ?" for field in PERSONAL_FIELDS)
    params = [cleaned[field] for field in PERSONAL_FIELDS]
    params.extend([json.dumps(extra, ensure_ascii=False), 1, _now(), int(student_id), revision])
    result = conn.execute(
        f"UPDATE resume_personal_info SET {assignments}, extra_json = ?, seeded = ?, "
        f"revision = revision + 1, updated_at = ? WHERE student_id = ? AND revision = ?",
        params,
    )
    if result.rowcount != 1:
        raise ResumeConflict("个人信息已更新，请保留输入并重新载入。")
    _notify_profile_change(conn, student_id)
    return get_personal_info(conn, student_id)


def merge_personal_info_partial(conn, student_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Fill blank personal fields from an import without overwriting student data."""
    ensure_resume_schema(conn)
    current = _ensure_personal_row(conn, student_id)
    updates: dict[str, str] = {}
    conflicts: list[dict[str, str]] = []
    skipped: list[str] = []
    for field in PERSONAL_FIELDS:
        incoming = _clean(payload.get(field), limit=200)
        if not incoming:
            continue
        if field == "email" and not _EMAIL_RE.match(incoming):
            skipped.append(field)
            continue
        existing = _clean(current.get(field), limit=200)
        if not existing:
            updates[field] = incoming
        elif existing != incoming:
            conflicts.append({"field": field, "existing": existing, "incoming": incoming})
    if updates:
        assignments = ", ".join(f"{field} = ?" for field in updates)
        params = list(updates.values()) + [_now(), int(student_id), int(current.get("revision") or 1)]
        changed = conn.execute(
            f"UPDATE resume_personal_info SET {assignments}, revision = revision + 1, updated_at = ? WHERE student_id = ? AND revision = ?",
            params,
        )
        if changed.rowcount != 1:
            from .resume_document_service import ResumeConflict
            raise ResumeConflict("个人资料在导入期间发生变化，请检查后重新确认。")
        _notify_profile_change(conn, student_id)
    return {
        "updated_fields": list(updates.keys()),
        "conflicts": conflicts,
        "skipped_fields": skipped,
        "info": get_personal_info(conn, student_id),
    }


def set_personal_avatar(conn, student_id: int, file_hash: str, mime_type: str, *, expected_revision: Any = None) -> int:
    ensure_resume_schema(conn)
    current = _ensure_personal_row(conn, student_id)
    from .resume_document_service import require_revision, ResumeConflict
    revision = require_revision(current, expected_revision) if expected_revision is not None else int(current["revision"])
    locked = conn.execute("UPDATE resume_personal_info SET student_id = student_id WHERE student_id = ? AND revision = ?", (int(student_id), revision))
    if locked.rowcount != 1:
        raise ResumeConflict("个人资料已更新，请保留输入并重新载入。")
    from ..file_service import lock_global_file_references
    lock_global_file_references(conn, [file_hash])
    conn.execute(
        "UPDATE resume_personal_info SET avatar_file_hash = ?, avatar_mime_type = ?, "
        "revision = revision + 1, updated_at = ? WHERE student_id = ? AND revision = ?",
        (_clean(file_hash, limit=128), _clean(mime_type, limit=64), _now(), int(student_id), revision),
    )
    return revision + 1


def seed_personal_info_from_platform(conn, student_id: int, user: dict[str, Any] | None = None) -> dict[str, Any]:
    """First-visit pre-fill from the platform profile (idempotent: only if unseeded)."""
    ensure_resume_schema(conn)
    info = _ensure_personal_row(conn, student_id)
    if int(info.get("seeded") or 0) == 1 or _clean(info.get("name")):
        return get_personal_info(conn, student_id)
    try:
        from ..profile_service import get_user_profile

        profile = get_user_profile(conn, {"id": student_id, "role": "student"})
    except Exception:
        profile = {}
    user = user or {}
    mapped = {
        "name": profile.get("name") or profile.get("nickname") or user.get("name") or "",
        "gender": profile.get("gender") or "",
        "email": profile.get("email") or user.get("email") or "",
        "phone": profile.get("phone") or "",
        "qq": profile.get("qq") or "",
        "wechat": profile.get("wechat") or "",
    }
    avatar_hash = str(profile.get("avatar_file_hash") or "")
    from ..file_service import lock_global_file_references, resolve_global_file_path
    # Match normal avatar changes: business row before sorted blob locks.
    conn.execute("UPDATE resume_personal_info SET student_id = student_id WHERE student_id = ?", (int(student_id),))
    if avatar_hash and resolve_global_file_path(avatar_hash):
        lock_global_file_references(conn, [avatar_hash])
    else:
        avatar_hash = ""
    assignments = ", ".join(f"{field} = ?" for field in mapped)
    params = [_clean(value, limit=200) for value in mapped.values()]
    params.extend([avatar_hash, profile.get("avatar_mime_type") or "", _now(), int(student_id)])
    conn.execute(
        f"UPDATE resume_personal_info SET {assignments}, avatar_file_hash = ?, "
        f"avatar_mime_type = ?, seeded = 1, revision = revision + 1, updated_at = ? WHERE student_id = ? AND seeded = 0",
        params,
    )
    return get_personal_info(conn, student_id)


def _career_position_options_from_state(state: dict[str, Any], *, limit: int = 14) -> list[dict[str, Any]]:
    """Extract expected-position choices from the career recommendation network."""
    if not isinstance(state, dict) or not state.get("ok"):
        return []
    network = state.get("network") if isinstance(state.get("network"), dict) else {}
    nodes = network.get("nodes") if isinstance(network.get("nodes"), list) else []
    top_paths = (
        (state.get("personalized") or {}).get("top_paths")
        if isinstance(state.get("personalized"), dict)
        else []
    )
    top_paths = top_paths if isinstance(top_paths, list) else []
    by_tag = {
        str(node.get("tag") or ""): node
        for node in nodes
        if isinstance(node, dict) and str(node.get("tag") or "").strip()
    }

    candidates: list[dict[str, Any]] = []

    def add(label: Any, *, tag: Any = "", node: dict[str, Any] | None = None,
            reason: Any = "", order: int = 999, featured: bool = False) -> None:
        name = _clean(label, limit=80)
        if not name:
            return
        node = node or {}
        tag_text = _clean(tag or node.get("tag"), limit=24)
        try:
            rec = max(1, min(5, int(round(float(node.get("rec") or 0)))))
        except (TypeError, ValueError):
            rec = 0
        try:
            glow = float(node.get("glow") or 0)
        except (TypeError, ValueError):
            glow = 0.0
        candidates.append({
            "value": name,
            "label": name,
            "tag": tag_text,
            "meta": f"推荐度 {rec}/5" if rec else "职业推荐",
            "hint": _clean(reason or node.get("tip") or node.get("reason") or "", limit=120),
            # Personalized paths are a ranked list authored for this student.
            # Keep that exact order before adding broader network alternatives;
            # several network nodes may intentionally share one career tag.
            "_sort": (
                0 if featured else 1,
                order if featured else 0 if node.get("highlighted") else 1,
                0 if featured else -glow,
                0 if featured else -rec,
                order,
                name,
            ),
        })

    for index, path in enumerate(top_paths):
        if not isinstance(path, dict):
            continue
        tag = str(path.get("tag") or "")
        node = by_tag.get(tag, {})
        add(
            path.get("name") or node.get("name"),
            tag=tag,
            node=node,
            reason=path.get("why"),
            order=index,
            featured=True,
        )

    for index, node in enumerate(nodes, start=len(candidates)):
        if isinstance(node, dict):
            add(node.get("name"), tag=node.get("tag"), node=node, order=index + 100)

    seen: set[str] = set()
    options: list[dict[str, Any]] = []
    for option in sorted(candidates, key=lambda item: item["_sort"]):
        key = option["value"].casefold()
        if key in seen:
            continue
        seen.add(key)
        option.pop("_sort", None)
        options.append(option)
        if len(options) >= limit:
            break
    return options


def build_expected_position_options(conn, student_id: int, *, limit: int = 14) -> list[dict[str, Any]]:
    """Best-effort bridge from career recommendations to the resume form."""
    try:
        from ..career_path_service import build_state

        state = build_state(conn, int(student_id))
    except Exception:
        return []
    return _career_position_options_from_state(state, limit=limit)


# ---------------------------------------------------------------------------
# List sections — generic CRUD
# ---------------------------------------------------------------------------
def _validate_list_payload(section: str, payload: dict[str, Any]) -> dict[str, str]:
    spec = LIST_SECTIONS[section]
    cleaned: dict[str, str] = {}
    for field in spec["fields"]:
        cleaned[field] = _clean(payload.get(field))
    missing = [field for field in spec["required"] if not cleaned.get(field)]
    if missing:
        labels = {
            "name": "名称",
            "school": "学校 / 机构名称",
            "title": "名称",
            "start_date": "开始时间",
            "end_date": "结束时间",
            "acquired_date": "获得时间",
            "content_md": "自我介绍内容",
        }
        raise ValueError(f"{spec['label']}缺少必填信息：{'、'.join(labels.get(field, field) for field in missing)}")
    for field in ("start_date", "end_date", "acquired_date", "expiry_date"):
        value = cleaned.get(field)
        if not value or field == "end_date" and value.casefold() in {"至今", "present", "current"}:
            continue
        formats = {4: "%Y", 7: "%Y-%m", 10: "%Y-%m-%d"}
        try:
            pattern = formats[len(value)]
            if not re.fullmatch(r"\d{4}(?:-\d{2}){0,2}", value):
                raise ValueError("invalid date")
            datetime.strptime(value, pattern)
        except (ValueError, KeyError) as exc:
            raise ValueError("日期格式无效，请填写真实的年份或年月") from exc
    if section in {"experience", "education"}:
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and start > end:
            raise ValueError("开始时间不能晚于结束时间")
    if section == "experience" and cleaned.get("kind") and cleaned["kind"] not in EXPERIENCE_KINDS:
        raise ValueError("经历类型不正确，请选择已支持的类型")
    if section == "education" and cleaned.get("degree") and cleaned["degree"] not in EDUCATION_DEGREES:
        raise ValueError("学历层次不正确，不确定时可以留空")
    return cleaned


def list_section(conn, student_id: int, section: str, *, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    section = _normalize_section(section)
    ensure_resume_schema(conn)
    table = LIST_SECTIONS[section]["table"]
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE student_id = ? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        (int(student_id), max(1, min(1000, int(limit))), max(0, int(offset))),
    ).fetchall()
    return [dict(row) for row in rows]


def get_section_item(conn, student_id: int, section: str, item_id: int) -> dict[str, Any]:
    section = _normalize_section(section)
    ensure_resume_schema(conn)
    table = LIST_SECTIONS[section]["table"]
    row = conn.execute(
        f"SELECT * FROM {table} WHERE id = ? AND student_id = ? LIMIT 1",
        (int(item_id), int(student_id)),
    ).fetchone()
    if not row:
        raise ValueError("未找到该记录")
    return dict(row)


def create_section_item(conn, student_id: int, section: str, payload: dict[str, Any]) -> int:
    section = _normalize_section(section)
    ensure_resume_schema(conn)
    cleaned = _validate_list_payload(section, payload)
    table = LIST_SECTIONS[section]["table"]
    columns = list(cleaned.keys()) + ["student_id", "created_at", "updated_at"]
    now = _now()
    values = list(cleaned.values()) + [int(student_id), now, now]
    placeholders = ", ".join("?" for _ in columns)
    new_id = execute_insert_returning_id(
        conn,
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    _notify_profile_change(conn, student_id)
    return int(new_id)


def update_section_item(conn, student_id: int, section: str, item_id: int, payload: dict[str, Any]) -> None:
    section = _normalize_section(section)
    ensure_resume_schema(conn)
    current = get_section_item(conn, student_id, section, item_id)  # ownership + existence
    from .resume_document_service import require_revision, ResumeConflict
    revision = require_revision(current, payload["revision"]) if "revision" in payload else int(current.get("revision") or 1)
    cleaned = _validate_list_payload(section, payload)
    table = LIST_SECTIONS[section]["table"]
    assignments = ", ".join(f"{field} = ?" for field in cleaned)
    params = list(cleaned.values()) + [_now(), int(item_id), int(student_id), revision]
    finished_intro = ", status = 'ready', active_job_id = '', error_text = ''" if section == "self_intro" else ""
    result = conn.execute(
        f"UPDATE {table} SET {assignments}, revision = revision + 1, updated_at = ?{finished_intro} WHERE id = ? AND student_id = ? AND revision = ?",
        params,
    )
    if result.rowcount != 1:
        raise ResumeConflict("素材已更新，请保留输入并重新载入。")
    if section == "self_intro" and current.get("active_job_id"):
        from ..student_career_job_service import supersede_student_career_jobs
        supersede_student_career_jobs(conn, scope_type="resume_intro", scope_id=str(item_id), student_id=student_id)
    _notify_profile_change(conn, student_id)


def delete_section_item(conn, student_id: int, section: str, item_id: int) -> None:
    section = _normalize_section(section)
    ensure_resume_schema(conn)
    table = LIST_SECTIONS[section]["table"]
    result = conn.execute(
        f"DELETE FROM {table} WHERE id = ? AND student_id = ?",
        (int(item_id), int(student_id)),
    )
    if result.rowcount:
        _notify_profile_change(conn, student_id)


def create_education_auto(conn, student_id: int, *, school: str, college: str = "",
                          major: str = "", start_date: str = "", end_date: str = "",
                          content: str = "", kind: str = "university") -> int:
    """Insert an AI-seeded education row (source='ai_auto')."""
    return create_section_item(
        conn,
        student_id,
        "education",
        {
            "kind": kind, "school": school, "college": college, "major": major,
            "start_date": start_date, "end_date": end_date, "content": content,
            "source": "ai_auto",
        },
    )


def has_any_education(conn, student_id: int) -> bool:
    ensure_resume_schema(conn)
    row = conn.execute(
        "SELECT 1 FROM resume_educations WHERE student_id = ? LIMIT 1",
        (int(student_id),),
    ).fetchone()
    return row is not None


def collect_profile_bundle(conn, student_id: int) -> dict[str, Any]:
    """Everything filled so far — used by the builder palette + AI prompts."""
    bundle: dict[str, Any] = {"personal": get_personal_info(conn, student_id)}
    for section in LIST_SECTIONS:
        bundle[section] = list_section(conn, student_id, section)
    return bundle


# ---------------------------------------------------------------------------
# Self-intro placeholder lifecycle (for AI generation)
# ---------------------------------------------------------------------------
def create_self_intro_placeholder(conn, student_id: int, *, title: str = "AI 生成中…") -> int:
    ensure_resume_schema(conn)
    now = _now()
    return int(
        execute_insert_returning_id(
            conn,
            "INSERT INTO resume_self_intros (student_id, title, content_md, source, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'ai_generated', 'generating', ?, ?)",
            (int(student_id), _clean(title, limit=120), "", now, now),
        )
    )


def finish_self_intro(conn, intro_id: int, *, content_md: str, title: str = "", status: str = "ready",
                      error_text: str = "") -> None:
    ensure_resume_schema(conn)
    sets = ["content_md = ?", "status = ?", "error_text = ?", "updated_at = ?"]
    params: list[Any] = [_clean(content_md, limit=8000), status, _clean(error_text, limit=600), _now()]
    if title:
        sets.append("title = ?")
        params.append(_clean(title, limit=120))
    params.append(int(intro_id))
    conn.execute(
        f"UPDATE resume_self_intros SET {', '.join(sets)} WHERE id = ?",
        params,
    )
