"""课次/首页**多材料**绑定服务。

把"一个课次/首页 = 一份材料"升级为"= 材料列表"。旧的单列字段继续作为列表
首项（主材料）镜像，兼容时间轴/AI/Git 等旧读取方；本服务读写
``class_offering_learning_materials`` 列表表。

约定 ``session_id = 0`` 表示课程首页/课堂级绑定，``> 0`` 表示具体课次。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from fastapi import HTTPException

from ..core import ai_client
from ..db.connection import get_configured_db_engine
from ..db.schema_session_learning_materials import ensure_session_learning_materials_schema
from .material_render_service import resolve_render_target
from .materials_service import (
    build_learning_material_brief,
    ensure_teacher_learning_material_owner,
    is_git_internal_material_path,
    sync_classroom_learning_material_assignments,
)

HOME_SESSION_ID = 0
AI_BLURB_MAX_CHARS = 20
AI_BLURB_GENERATE_LIMIT = 8  # 单次请求最多即时生成的简介数量，避免阻塞过久。


def _now() -> str:
    return datetime.now().isoformat()


def _normalize_session_id(session_id: int | None) -> int:
    try:
        value = int(session_id or 0)
    except (TypeError, ValueError):
        return HOME_SESSION_ID
    return value if value > 0 else HOME_SESSION_ID


def _ensure_offering_owner(conn, class_offering_id: int, teacher_id: int):
    row = conn.execute(
        "SELECT * FROM class_offerings WHERE id = ? AND teacher_id = ? LIMIT 1",
        (class_offering_id, teacher_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "课堂不存在或无权操作")
    return row


def _primary_material_id(conn, class_offering_id: int, session_id: int) -> int:
    if session_id > 0:
        row = conn.execute(
            "SELECT learning_material_id FROM class_offering_sessions WHERE id = ? AND class_offering_id = ? LIMIT 1",
            (session_id, class_offering_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT home_learning_material_id AS learning_material_id FROM class_offerings WHERE id = ? LIMIT 1",
            (class_offering_id,),
        ).fetchone()
    if not row:
        return 0
    try:
        return int(row["learning_material_id"] or 0)
    except (TypeError, ValueError):
        return 0


def _set_primary_material_id(conn, class_offering_id: int, session_id: int, material_id: int | None) -> None:
    value = int(material_id) if material_id else None
    if session_id > 0:
        conn.execute(
            "UPDATE class_offering_sessions SET learning_material_id = ?, updated_at = ? WHERE id = ? AND class_offering_id = ?",
            (value, _now(), session_id, class_offering_id),
        )
    else:
        conn.execute(
            "UPDATE class_offerings SET home_learning_material_id = ? WHERE id = ?",
            (value, class_offering_id),
        )


def _fetch_rows(conn, class_offering_id: int, session_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT lm.id AS row_id, lm.material_id, lm.ai_blurb, lm.ai_blurb_status, lm.sort_order
        FROM class_offering_learning_materials lm
        WHERE lm.class_offering_id = ? AND lm.session_id = ?
        ORDER BY lm.sort_order, lm.id
        """,
        (class_offering_id, session_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _insert_row(conn, class_offering_id: int, session_id: int, material_id: int, teacher_id: int, sort_order: int) -> None:
    now = _now()
    if get_configured_db_engine() == "postgres":
        conn.execute(
            """
            INSERT INTO class_offering_learning_materials
                (class_offering_id, session_id, material_id, sort_order, created_by_teacher_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (class_offering_id, session_id, material_id) DO NOTHING
            """,
            (class_offering_id, session_id, material_id, sort_order, teacher_id, now, now),
        )
    else:
        conn.execute(
            """
            INSERT OR IGNORE INTO class_offering_learning_materials
                (class_offering_id, session_id, material_id, sort_order, created_by_teacher_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (class_offering_id, session_id, material_id, sort_order, teacher_id, now, now),
        )


def _backfill_primary(conn, class_offering_id: int, session_id: int, teacher_id: int) -> None:
    """确保旧单列的主材料始终出现在列表中（懒迁移）。

    不止"列表为空时补一条"：若旧的 AI/Git 自动绑定在列表生成后改写了单列主材料，
    也要把新的主材料补进列表，避免列表漏显当前生效的绑定。
    """
    primary_id = _primary_material_id(conn, class_offering_id, session_id)
    if primary_id <= 0:
        return
    rows = _fetch_rows(conn, class_offering_id, session_id)
    if any(int(row["material_id"]) == primary_id for row in rows):
        return
    min_order = min((int(row["sort_order"]) for row in rows), default=0)
    _insert_row(conn, class_offering_id, session_id, primary_id, teacher_id, sort_order=min_order - 1)


def _material_row(conn, material_id: int):
    return conn.execute(
        "SELECT * FROM course_materials WHERE id = ? LIMIT 1",
        (material_id,),
    ).fetchone()


def build_material_entries(conn, class_offering_id: int, session_id: int, *, teacher_id: int) -> list[dict]:
    """返回该课次/首页绑定的材料列表（含渲染入口与已存简介）。"""
    ensure_session_learning_materials_schema(conn)
    session_id = _normalize_session_id(session_id)
    _backfill_primary(conn, class_offering_id, session_id, teacher_id)

    entries: list[dict] = []
    for row in _fetch_rows(conn, class_offering_id, session_id):
        material = _material_row(conn, int(row["material_id"]))
        if not material:
            continue
        if is_git_internal_material_path(material["material_path"]):
            continue
        render_target = resolve_render_target(conn, material)
        brief = build_learning_material_brief(material, render_target=render_target)
        entries.append(
            {
                "row_id": int(row["row_id"]),
                "material_id": int(brief["id"]),
                "id": int(brief["id"]),
                "name": brief["name"],
                "material_path": brief["material_path"],
                "preview_type": brief["preview_type"],
                "node_type": brief["node_type"],
                "open_url": brief["viewer_url"],
                "viewer_url": brief["viewer_url"],
                "render_url": brief["render_url"],
                "render_kind": brief["render_kind"],
                "is_renderable": brief["is_renderable"],
                "ai_blurb": str(row["ai_blurb"] or "").strip(),
                "ai_blurb_status": str(row["ai_blurb_status"] or "idle"),
            }
        )
    return entries


def add_material(conn, class_offering_id: int, session_id: int, material_id: int, teacher_id: int) -> dict:
    ensure_session_learning_materials_schema(conn)
    _ensure_offering_owner(conn, class_offering_id, teacher_id)
    session_id = _normalize_session_id(session_id)
    if session_id > 0:
        owned = conn.execute(
            "SELECT id FROM class_offering_sessions WHERE id = ? AND class_offering_id = ? LIMIT 1",
            (session_id, class_offering_id),
        ).fetchone()
        if not owned:
            raise HTTPException(404, "课次不存在或无权操作")

    # 校验可绑定（Markdown 文档 / 可渲染 HTML）。
    ensure_teacher_learning_material_owner(conn, material_id, teacher_id)

    _backfill_primary(conn, class_offering_id, session_id, teacher_id)
    existing = _fetch_rows(conn, class_offering_id, session_id)
    if any(int(row["material_id"]) == int(material_id) for row in existing):
        raise HTTPException(409, "该材料已绑定到此处")

    next_order = (max((int(row["sort_order"]) for row in existing), default=-1)) + 1
    _insert_row(conn, class_offering_id, session_id, int(material_id), teacher_id, sort_order=next_order)

    # 同步课堂访问权限，并在主材料为空时镜像为主材料。
    sync_classroom_learning_material_assignments(
        conn,
        class_offering_id=class_offering_id,
        teacher_id=teacher_id,
        material_ids=[int(material_id)],
    )
    if _primary_material_id(conn, class_offering_id, session_id) <= 0:
        _set_primary_material_id(conn, class_offering_id, session_id, int(material_id))

    conn.commit()
    return {"added": True, "material_id": int(material_id), "session_id": session_id}


def remove_material(conn, class_offering_id: int, session_id: int, material_id: int, teacher_id: int) -> dict:
    ensure_session_learning_materials_schema(conn)
    _ensure_offering_owner(conn, class_offering_id, teacher_id)
    session_id = _normalize_session_id(session_id)
    _backfill_primary(conn, class_offering_id, session_id, teacher_id)

    conn.execute(
        "DELETE FROM class_offering_learning_materials WHERE class_offering_id = ? AND session_id = ? AND material_id = ?",
        (class_offering_id, session_id, int(material_id)),
    )

    # 主材料被解绑时，把列表首项提升为新的主材料（或清空）。
    if _primary_material_id(conn, class_offering_id, session_id) == int(material_id):
        remaining = _fetch_rows(conn, class_offering_id, session_id)
        new_primary = int(remaining[0]["material_id"]) if remaining else None
        _set_primary_material_id(conn, class_offering_id, session_id, new_primary)

    conn.commit()
    return {"removed": True, "material_id": int(material_id), "session_id": session_id}


def set_blurb(conn, row_id: int, blurb: str, status: str = "ready") -> None:
    conn.execute(
        "UPDATE class_offering_learning_materials SET ai_blurb = ?, ai_blurb_status = ?, updated_at = ? WHERE id = ?",
        (str(blurb or "")[:AI_BLURB_MAX_CHARS], status, _now(), int(row_id)),
    )


def attach_learning_material_counts(conn, class_offering_id: int, session_items: list[dict], offering_data: dict) -> None:
    """为时间轴页一次性附加每个课次/首页的材料数量（单查询，无写入）。"""
    ensure_session_learning_materials_schema(conn)
    rows = conn.execute(
        "SELECT session_id, material_id FROM class_offering_learning_materials WHERE class_offering_id = ?",
        (class_offering_id,),
    ).fetchall()
    grouped: dict[int, set[int]] = {}
    for row in rows:
        grouped.setdefault(int(row["session_id"]), set()).add(int(row["material_id"]))

    def _count(session_id: int, primary_id: int) -> int:
        ids = set(grouped.get(session_id, set()))
        if primary_id:
            ids.add(primary_id)
        return len(ids)

    for session in session_items:
        try:
            primary = int(session.get("learning_material_id") or 0)
        except (TypeError, ValueError):
            primary = 0
        session["learning_material_count"] = _count(int(session.get("id") or 0), primary)

    try:
        home_primary = int(offering_data.get("home_learning_material_id") or 0)
    except (TypeError, ValueError):
        home_primary = 0
    offering_data["home_learning_material_count"] = _count(HOME_SESSION_ID, home_primary)


def _clean_blurb(text: str) -> str:
    cleaned = str(text or "").strip()
    # 去掉引号/书名号/常见结尾标点，压成一行。
    cleaned = cleaned.replace("\n", " ").replace("\r", " ").strip()
    for ch in ("“", "”", "\"", "'", "「", "」", "《", "》"):
        cleaned = cleaned.replace(ch, "")
    cleaned = cleaned.strip().rstrip("。.!！；;，,")
    return cleaned[:AI_BLURB_MAX_CHARS].strip()


async def generate_material_blurb(*, name: str, type_label: str, material_path: str) -> str:
    """调用快速版 AI 生成一句话介绍（≤20 字）。失败返回空串（调用方降级）。"""
    system_prompt = (
        "你是课程材料速记助手。请为一份课堂学习材料写一句不超过20个汉字的中文简介，"
        "概括它的内容或用途。只输出这一句话，不要引号、不要书名号、不要结尾标点。"
    )
    user_message = (
        f"材料名称：{name}\n"
        f"材料类型：{type_label or '文档'}\n"
        f"材料路径：{material_path or ''}\n"
        "请用一句不超过20字的话介绍它。"
    )
    payload = {
        "system_prompt": system_prompt,
        "messages": [],
        "new_message": user_message,
        "model_capability": "fast",
        "task_type": "fast_text_response",
        "web_search_enabled": False,
        "response_format": "text",
        "task_priority": "default",
        "task_label": "session-material:blurb",
    }
    try:
        response = await ai_client.post("/api/ai/chat", json=payload, timeout=20.0)
        response.raise_for_status()
        data = response.json()
        return _clean_blurb(str(data.get("response_text") or ""))
    except Exception:
        return ""
