"""Material whiteboard service (材料白板).

Teachers draw on a free-form whiteboard on top of a course material; boards are
persisted here as JSON element lists with an optimistic-lock ``version``.
Ownership is (``user["role"]``, ``user["id"]``) and every entry point re-checks
material access via ``ensure_user_material_access`` so a board can never be
read or written through a material the caller cannot open.

Only roles listed in ``WHITEBOARD_ALLOWED_ROLES`` may use whiteboards; opening
the feature to students is a one-line change here.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from ..db.schema_material_whiteboards import ensure_material_whiteboard_schema
from .materials_service import ensure_user_material_access

WHITEBOARD_ALLOWED_ROLES = {"teacher"}

BOARD_NAME_MAX_LENGTH = 60
BOARD_KEY_MAX_LENGTH = 80
MAX_ELEMENTS = 20000
MAX_ELEMENTS_BYTES = 2 * 1024 * 1024
ALLOWED_ELEMENT_TYPES = {"stroke", "shape", "text", "eraser"}
VIEWPORT_SCALE_MIN = 0.35
VIEWPORT_SCALE_MAX = 2.6
DEFAULT_SCHEMA_VERSION = 2

_META_COLUMNS = (
    "board_key, name, viewport_json, element_count, schema_version, version, "
    "visibility, created_at, updated_at"
)
_FULL_COLUMNS = _META_COLUMNS + ", elements_json"


class WhiteboardValidationError(ValueError):
    """Payload failed validation (router maps to 400)."""


class WhiteboardTooLarge(WhiteboardValidationError):
    """Serialized elements exceed the size budget (router maps to 413)."""


class WhiteboardNotFound(LookupError):
    """No live board for this owner/material/key (router maps to 404)."""


class WhiteboardConflict(Exception):
    """Optimistic-lock mismatch; carries the current server board (409)."""

    def __init__(self, board: dict[str, Any]):
        super().__init__("whiteboard version conflict")
        self.board = board


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _owner(user: dict) -> tuple[str, int]:
    role = str(user.get("role") or "").strip().lower()
    if role not in WHITEBOARD_ALLOWED_ROLES:
        raise HTTPException(403, "当前角色不能使用材料白板")
    try:
        user_pk = int(user["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(403, "当前角色不能使用材料白板") from exc
    return role, user_pk


def _prepare(conn, user: dict, material_id: int) -> tuple[str, int]:
    """Common preamble: ensure schema, gate role, verify material access."""
    ensure_material_whiteboard_schema(conn)
    owner = _owner(user)
    ensure_user_material_access(conn, int(material_id), user)
    return owner


def _normalize_board_key(board_key: Any) -> str:
    key = str(board_key or "").strip()
    if not key or len(key) > BOARD_KEY_MAX_LENGTH:
        raise WhiteboardValidationError("白板标识无效")
    return key


def _normalize_name(name: Any) -> str:
    if name is None:
        return ""
    if not isinstance(name, str):
        raise WhiteboardValidationError("白板名称必须是文本")
    cleaned = name.strip()
    if len(cleaned) > BOARD_NAME_MAX_LENGTH:
        raise WhiteboardValidationError(f"白板名称不能超过 {BOARD_NAME_MAX_LENGTH} 个字符")
    return cleaned


def _assert_finite(value: Any, path: str) -> None:
    """Reject NaN/inf anywhere inside a value (nested lists/dicts included)."""
    if isinstance(value, bool):
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise WhiteboardValidationError(f"数值无效：{path}")
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_finite(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_finite(item, f"{path}[{index}]")


def _normalize_elements(elements: Any) -> tuple[list, str]:
    if not isinstance(elements, list):
        raise WhiteboardValidationError("elements 必须是数组")
    if len(elements) > MAX_ELEMENTS:
        raise WhiteboardValidationError(f"白板元素不能超过 {MAX_ELEMENTS} 个")
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            raise WhiteboardValidationError(f"第 {index + 1} 个元素不是对象")
        element_type = element.get("type")
        if element_type not in ALLOWED_ELEMENT_TYPES:
            raise WhiteboardValidationError(f"第 {index + 1} 个元素类型不支持：{element_type!r}")
        _assert_finite(element, f"elements[{index}]")
    serialized = json.dumps(elements, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(serialized.encode("utf-8")) > MAX_ELEMENTS_BYTES:
        raise WhiteboardTooLarge("白板内容超过 2MB 上限")
    return elements, serialized


def _normalize_viewport(viewport: Any) -> tuple[dict, str]:
    if viewport is None:
        viewport = {}
    if not isinstance(viewport, dict):
        raise WhiteboardValidationError("viewport 必须是对象")
    _assert_finite(viewport, "viewport")
    cleaned = dict(viewport)
    scale = cleaned.get("scale")
    if scale is not None:
        if isinstance(scale, bool) or not isinstance(scale, (int, float)):
            raise WhiteboardValidationError("viewport.scale 必须是数字")
        cleaned["scale"] = min(VIEWPORT_SCALE_MAX, max(VIEWPORT_SCALE_MIN, float(scale)))
    serialized = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return cleaned, serialized


def _normalize_schema_version(value: Any) -> int:
    if value is None:
        return DEFAULT_SCHEMA_VERSION
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WhiteboardValidationError("schema_version 无效")
    return value


def _parse_base_version(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise WhiteboardValidationError("base_version 无效") from exc


def _loads(text: Any, fallback: Any) -> Any:
    try:
        return json.loads(text) if text else fallback
    except (TypeError, ValueError):
        return fallback


def _row_to_board(row: Any, *, include_elements: bool) -> dict[str, Any]:
    board = {
        "board_key": row["board_key"],
        "name": row["name"] or "",
        "viewport": _loads(row["viewport_json"], {}),
        "element_count": int(row["element_count"] or 0),
        "schema_version": int(row["schema_version"] or DEFAULT_SCHEMA_VERSION),
        "version": int(row["version"] or 1),
        "visibility": row["visibility"] or "private",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if include_elements:
        board["elements"] = _loads(row["elements_json"], [])
    return board


def _fetch_row(conn, owner: tuple[str, int], material_id: int, board_key: str, *, full: bool):
    columns = _FULL_COLUMNS if full else _META_COLUMNS
    return conn.execute(
        f"""
        SELECT {columns}
        FROM material_whiteboards
        WHERE owner_role = ? AND owner_user_pk = ? AND material_id = ? AND board_key = ?
          AND deleted_at IS NULL
        """,
        (owner[0], owner[1], int(material_id), board_key),
    ).fetchone()


def list_boards(conn, user: dict, material_id: int) -> list[dict[str, Any]]:
    """Board metadata (no elements) for this owner + material, newest first."""
    owner = _prepare(conn, user, material_id)
    rows = conn.execute(
        f"""
        SELECT {_META_COLUMNS}
        FROM material_whiteboards
        WHERE owner_role = ? AND owner_user_pk = ? AND material_id = ? AND deleted_at IS NULL
        ORDER BY updated_at DESC, id DESC
        """,
        (owner[0], owner[1], int(material_id)),
    ).fetchall()
    return [_row_to_board(row, include_elements=False) for row in rows]


def get_board(conn, user: dict, material_id: int, board_key: str) -> dict[str, Any]:
    owner = _prepare(conn, user, material_id)
    key = _normalize_board_key(board_key)
    row = _fetch_row(conn, owner, material_id, key, full=True)
    if not row:
        raise WhiteboardNotFound("白板不存在")
    return _row_to_board(row, include_elements=True)


def _update_existing(conn, owner, material_id, key, fields: dict[str, Any], now: str) -> None:
    conn.execute(
        """
        UPDATE material_whiteboards
        SET name = ?, viewport_json = ?, elements_json = ?, element_count = ?,
            schema_version = ?, version = version + 1, updated_at = ?
        WHERE owner_role = ? AND owner_user_pk = ? AND material_id = ? AND board_key = ?
          AND deleted_at IS NULL
        """,
        (
            fields["name"], fields["viewport_json"], fields["elements_json"],
            fields["element_count"], fields["schema_version"], now,
            owner[0], owner[1], int(material_id), key,
        ),
    )


def _insert_new(conn, owner, material_id, key, fields: dict[str, Any], now: str) -> None:
    # A soft-deleted row with the same key may still occupy the UNIQUE slot;
    # revive it in place so the client-generated key stays usable.
    conn.execute(
        """
        UPDATE material_whiteboards
        SET deleted_at = NULL, name = ?, viewport_json = ?, elements_json = ?,
            element_count = ?, schema_version = ?, version = 1,
            visibility = 'private', share_token = NULL, created_at = ?, updated_at = ?
        WHERE owner_role = ? AND owner_user_pk = ? AND material_id = ? AND board_key = ?
          AND deleted_at IS NOT NULL
        """,
        (
            fields["name"], fields["viewport_json"], fields["elements_json"],
            fields["element_count"], fields["schema_version"], now, now,
            owner[0], owner[1], int(material_id), key,
        ),
    )
    if _fetch_row(conn, owner, material_id, key, full=False):
        return
    try:
        _insert_row(conn, owner, material_id, key, fields, now)
    except Exception as exc:  # 并发写同一 key：唯一约束冲突 → 交给乐观锁按 409 处理
        if "unique" not in str(exc).lower() and "duplicate" not in str(exc).lower():
            raise
        existing = _fetch_row(conn, owner, material_id, key, full=True)
        if existing is None:
            raise
        raise WhiteboardConflict(_row_to_board(existing, include_elements=True)) from exc


def _insert_row(conn, owner, material_id, key, fields: dict[str, Any], now: str) -> None:
    conn.execute(
        """
        INSERT INTO material_whiteboards (
            owner_role, owner_user_pk, material_id, board_key, name,
            viewport_json, elements_json, element_count, schema_version,
            version, visibility, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'private', ?, ?)
        """,
        (
            owner[0], owner[1], int(material_id), key, fields["name"],
            fields["viewport_json"], fields["elements_json"], fields["element_count"],
            fields["schema_version"], now, now,
        ),
    )


def upsert_board(
    conn,
    user: dict,
    material_id: int,
    board_key: str,
    payload: dict[str, Any],
    base_version: Any,
) -> dict[str, Any]:
    """Full-content save with optimistic locking.

    ``base_version`` is the version the client last loaded (``None`` for a
    brand-new board). If the row exists and the versions differ the save is
    refused with ``WhiteboardConflict`` carrying the server copy.
    """
    owner = _prepare(conn, user, material_id)
    key = _normalize_board_key(board_key)
    if not isinstance(payload, dict):
        raise WhiteboardValidationError("请求体必须是对象")

    name = _normalize_name(payload.get("name"))
    _viewport, viewport_json = _normalize_viewport(payload.get("viewport"))
    elements, elements_json = _normalize_elements(payload.get("elements"))
    fields = {
        "name": name,
        "viewport_json": viewport_json,
        "elements_json": elements_json,
        # 与前端一致：只统计墨迹元素，橡皮笔画不计入「笔数」。
        "element_count": sum(1 for item in elements if item.get("type") != "eraser"),
        "schema_version": _normalize_schema_version(payload.get("schema_version")),
    }
    expected_version = _parse_base_version(base_version)

    existing = _fetch_row(conn, owner, material_id, key, full=True)
    now = _now_iso()
    if existing:
        if expected_version != int(existing["version"] or 1):
            raise WhiteboardConflict(_row_to_board(existing, include_elements=True))
        _update_existing(conn, owner, material_id, key, fields, now)
    else:
        _insert_new(conn, owner, material_id, key, fields, now)
    conn.commit()
    row = _fetch_row(conn, owner, material_id, key, full=True)
    return _row_to_board(row, include_elements=True)


def rename_board(conn, user: dict, material_id: int, board_key: str, name: Any) -> dict[str, Any]:
    owner = _prepare(conn, user, material_id)
    key = _normalize_board_key(board_key)
    cleaned = _normalize_name(name)
    if not _fetch_row(conn, owner, material_id, key, full=False):
        raise WhiteboardNotFound("白板不存在")
    conn.execute(
        """
        UPDATE material_whiteboards
        SET name = ?, updated_at = ?
        WHERE owner_role = ? AND owner_user_pk = ? AND material_id = ? AND board_key = ?
          AND deleted_at IS NULL
        """,
        (cleaned, _now_iso(), owner[0], owner[1], int(material_id), key),
    )
    conn.commit()
    row = _fetch_row(conn, owner, material_id, key, full=False)
    return _row_to_board(row, include_elements=False)


def delete_board(conn, user: dict, material_id: int, board_key: str) -> dict[str, Any]:
    """Soft delete: stamps ``deleted_at`` so the board disappears from lists."""
    owner = _prepare(conn, user, material_id)
    key = _normalize_board_key(board_key)
    if not _fetch_row(conn, owner, material_id, key, full=False):
        raise WhiteboardNotFound("白板不存在")
    now = _now_iso()
    conn.execute(
        """
        UPDATE material_whiteboards
        SET deleted_at = ?, updated_at = ?
        WHERE owner_role = ? AND owner_user_pk = ? AND material_id = ? AND board_key = ?
          AND deleted_at IS NULL
        """,
        (now, now, owner[0], owner[1], int(material_id), key),
    )
    conn.commit()
    return {"board_key": key, "deleted_at": now}
