"""Small, versioned preferences owned by the authenticated account."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from typing import Any

from .. import config
from ..db.connection import get_db_connection

logger = logging.getLogger(__name__)
PALETTES = (
    ("indigo", "经典靛蓝"),
    ("sky", "晴空蓝"),
    ("mint", "薄荷绿"),
    ("violet", "鸢尾紫"),
    ("rose", "珊瑚粉"),
)
PALETTE_KEYS = frozenset(key for key, _ in PALETTES)
DEFAULT_PALETTE = "indigo"
_LEARNING_PAGE = re.compile(r"^/(?:dashboard|classroom/[1-9][0-9]*)/?$")


class PreferenceConflict(Exception):
    def __init__(self, current: dict[str, Any]):
        super().__init__("配色已在其他页面或设备更新，请重新选择以保存。")
        self.current = current


def preference_identity(user: dict) -> tuple[str, int]:
    role = str(user.get("role") or "").strip().lower()
    raw_id = user.get("id")
    if role != "student" or isinstance(raw_id, bool):
        raise ValueError("界面配色目前仅面向学生本人开放。")
    try:
        user_pk = int(raw_id)
    except (ValueError, TypeError) as exc:
        raise ValueError("当前学生身份无效。") from exc
    if user_pk <= 0:
        raise ValueError("当前学生身份无效。")
    return role, user_pk


def preference_context_token(user: dict) -> str:
    """A stale tab may not mutate the different account now owning its cookie.

    This is an identity-context check, not an authorization identity supplied by
    the client. Every database key still comes from the authenticated user.
    """
    role, user_pk = preference_identity(user)
    message = f"lanshare.ui-preferences.v1:{role}:{user_pk}".encode()
    return hmac.new(str(config.SECRET_KEY).encode(), message, hashlib.sha256).hexdigest()


def get_ui_preferences(conn: Any, user: dict) -> dict[str, Any]:
    role, user_pk = preference_identity(user)
    row = conn.execute(
        "SELECT palette_key, version, updated_at FROM user_ui_preferences "
        "WHERE user_role = ? AND user_pk = ?",
        (role, user_pk),
    ).fetchone()
    key = str(row["palette_key"]) if row else DEFAULT_PALETTE
    return {
        "palette_key": key if key in PALETTE_KEYS else DEFAULT_PALETTE,
        "version": int(row["version"]) if row else 0,
        "updated_at": str(row["updated_at"]) if row else None,
        "context_token": preference_context_token(user),
    }


def update_ui_preferences(conn: Any, user: dict, *, palette_key: str, version: int) -> dict[str, Any]:
    role, user_pk = preference_identity(user)
    if palette_key not in PALETTE_KEYS:
        raise ValueError("请选择提供的界面配色。")
    if type(version) is not int or version < 0:
        raise ValueError("配色版本无效。")
    if version == 0:
        cursor = conn.execute(
            "INSERT INTO user_ui_preferences (user_role, user_pk, palette_key, version) "
            "VALUES (?, ?, ?, 1) ON CONFLICT (user_role, user_pk) DO NOTHING "
            "RETURNING version",
            (role, user_pk, palette_key),
        )
    else:
        cursor = conn.execute(
            "UPDATE user_ui_preferences SET palette_key = ?, version = version + 1, "
            "updated_at = CURRENT_TIMESTAMP WHERE user_role = ? AND user_pk = ? "
            "AND version = ? RETURNING version",
            (palette_key, role, user_pk, version),
        )
    if cursor.fetchone() is None:
        raise PreferenceConflict(get_ui_preferences(conn, user))
    return get_ui_preferences(conn, user)


def resolve_user_ui_preferences(request: Any, user: dict | None) -> dict[str, Any]:
    """Called by base.html after its route has authenticated the user.

    Other pages/roles perform no lookup. No process or browser cache can leak a
    prior account's color into SSR, and reading a default never creates a row.
    """
    if not user or user.get("role") != "student" or not _LEARNING_PAGE.fullmatch(request.url.path):
        return {"enabled": False}
    cached = getattr(request.state, "user_ui_preferences", None)
    if cached is not None:
        return cached
    try:
        with get_db_connection() as conn:
            preferences = get_ui_preferences(conn, user)
        preferences["available"] = True
    except Exception:
        logger.warning("UI preferences unavailable during learning-page SSR", exc_info=True)
        preferences = {
            "palette_key": DEFAULT_PALETTE,
            "version": 0,
            "updated_at": None,
            "context_token": preference_context_token(user),
            "available": False,
        }
    preferences.update(enabled=True, presets=[{"key": key, "name": name} for key, name in PALETTES])
    request.state.user_ui_preferences = preferences
    return preferences
