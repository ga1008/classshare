"""Shared material attribute mutations; the caller owns the transaction."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException

from ..db.connection import get_configured_db_engine
from .materials_service import ensure_teacher_material_owner, is_git_internal_material_path, normalize_material_path
from .organization_scope_service import load_teacher_org_scope


def subtree_pattern(path: str) -> str:
    return path.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "/%"


def collect_subtree_rows(conn, material_row, include_internal: bool = True):
    rows = conn.execute(
        """
        SELECT *
        FROM course_materials
        WHERE root_id = ?
          AND (material_path = ? OR material_path LIKE ? ESCAPE '!' )
        ORDER BY material_path
        """,
        (material_row["root_id"], material_row["material_path"], subtree_pattern(str(material_row["material_path"]))),
    ).fetchall()
    if include_internal:
        return rows
    return [row for row in rows if not is_git_internal_material_path(row["material_path"])]


def rename_material_subtree(conn, material, new_name: str) -> None:
    normalized_name = normalize_material_path(new_name, fallback_name=str(material["name"] or "material"))
    if "/" in normalized_name or normalized_name in {"", ".git"}:
        raise HTTPException(400, "材料名称不合法")
    parent_id = material["parent_id"]
    if parent_id is None:
        conflict = conn.execute(
            """
            SELECT id
            FROM course_materials
            WHERE parent_id IS NULL
              AND teacher_id = ?
              AND LOWER(name) = LOWER(?)
              AND id != ?
            LIMIT 1
            """,
            (int(material["teacher_id"]), normalized_name, int(material["id"])),
        ).fetchone()
    else:
        conflict = conn.execute(
            """
            SELECT id
            FROM course_materials
            WHERE parent_id = ?
              AND LOWER(name) = LOWER(?)
              AND id != ?
            LIMIT 1
            """,
            (int(parent_id), normalized_name, int(material["id"])),
        ).fetchone()
    if conflict:
        raise HTTPException(409, "同一目录下已有同名材料")

    old_path = str(material["material_path"] or "").strip()
    if not old_path:
        raise HTTPException(400, "材料路径异常，不能重命名")
    prefix = old_path.rsplit("/", 1)[0] if "/" in old_path else ""
    new_path = f"{prefix}/{normalized_name}" if prefix else normalized_name
    subtree = collect_subtree_rows(conn, material)
    now_text = datetime.now().isoformat()
    for row in subtree:
        row_path = str(row["material_path"] or "")
        suffix = row_path[len(old_path):] if row_path.startswith(old_path) else ""
        conn.execute(
            """
            UPDATE course_materials
            SET name = CASE WHEN id = ? THEN ? ELSE name END,
                material_path = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (int(material["id"]), normalized_name, f"{new_path}{suffix}", now_text, int(row["id"])),
        )


def update_material_attributes(conn, *, material_id: int, teacher_id: int, payload: dict[str, Any], expected_updated_at: str | None = None):
    if not isinstance(payload, dict):
        raise HTTPException(400, "请求数据格式错误")
    scope = None
    if "scope_level" in payload:
        scope = str(payload.get("scope_level") or "private").strip().lower()
        if scope not in {"private", "school", "college", "department", "public"}:
            raise HTTPException(400, "Invalid material scope")
    material = ensure_teacher_material_owner(conn, material_id, teacher_id)
    if get_configured_db_engine() == "postgres":
        # All ordinary Web and Agent attribute writes serialize by tree root.
        conn.execute("SELECT id FROM course_materials WHERE id=? FOR UPDATE", (int(material["root_id"] or material_id),)).fetchone()
        conn.execute("SELECT id FROM course_materials WHERE id=? FOR UPDATE", (material_id,)).fetchone()
        material = ensure_teacher_material_owner(conn, material_id, teacher_id)
    if expected_updated_at is not None and expected_updated_at != str(dict(material).get("updated_at") or "legacy"):
        raise HTTPException(409, "材料已被修改，请重新读取属性后再提交。")
    if scope is not None and material["parent_id"] is not None:
        raise HTTPException(400, "开放范围由最外层文件夹统一决定，请在最外层文件夹上设置")
    if "name" in payload:
        rename_material_subtree(conn, material, str(payload.get("name") or ""))
        material = ensure_teacher_material_owner(conn, material_id, teacher_id)
    if scope is not None:
        owner = load_teacher_org_scope(conn, int(material["teacher_id"]))
        now = datetime.now().isoformat()
        conn.execute(
            """UPDATE course_materials SET scope_level=?, owner_role='teacher', owner_user_pk=?,
               school_code=?, school_name=?, college=?, department=?,
               published_at=CASE WHEN ?!='private' THEN COALESCE(published_at,?) ELSE published_at END, updated_at=?
               WHERE root_id=? AND (material_path=? OR material_path LIKE ? ESCAPE '!')""",
            (scope, int(material["teacher_id"]), owner["school_code"], owner["school_name"], owner["college"], owner["department"],
             scope, now, now, int(material["root_id"]), material["material_path"], subtree_pattern(str(material["material_path"]))),
        )
    return ensure_teacher_material_owner(conn, material_id, teacher_id)
