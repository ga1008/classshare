"""Small, versioned preferences owned by the authenticated account."""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Mapping
from typing import Any

from .. import config
from ..db.connection import get_db_connection

logger = logging.getLogger(__name__)
PALETTES = (
    ("teal", "清润青绿"),
    ("indigo", "经典靛蓝"),
    ("sky", "晴空蓝"),
    ("mint", "薄荷绿"),
    ("violet", "鸢尾紫"),
    ("rose", "珊瑚粉"),
)
PALETTE_KEYS = frozenset(key for key, _ in PALETTES)
DEFAULT_PALETTE = "indigo"
MAX_PREFERENCE_VERSION = 2147483646
PREFERENCE_VALUES = {
    "palette_key": PALETTE_KEYS,
    "appearance": frozenset({"light", "dark", "auto"}),
    "glass": frozenset({"tinted", "off"}),
}


class PreferenceConflict(Exception):
    def __init__(self, current: dict[str, Any]):
        super().__init__("界面偏好已在其他页面或设备更新，请重新选择以保存。")
        self.current = current


def preference_identity(user: dict) -> tuple[str, int]:
    role = str(user.get("role") or "").strip().lower()
    raw_id = user.get("id")
    if role not in {"student", "teacher"} or isinstance(raw_id, bool) or not isinstance(raw_id, (int, str)):
        raise ValueError("当前用户身份无效。")
    try:
        user_pk = int(raw_id)
    except (ValueError, TypeError) as exc:
        raise ValueError("当前用户身份无效。") from exc
    if user_pk <= 0:
        raise ValueError("当前用户身份无效。")
    return role, user_pk


def preference_context_token(user: dict) -> str:
    """A stale tab may not mutate the different account now owning its cookie.

    This is an identity-context check, not an authorization identity supplied by
    the client. Every database key still comes from the authenticated user.
    """
    role, user_pk = preference_identity(user)
    message = f"lanshare.ui-preferences.v1:{role}:{user_pk}".encode()
    return hmac.new(str(config.SECRET_KEY).encode(), message, hashlib.sha256).hexdigest()


def default_palette_for_role(role: str) -> str:
    return "teal" if role == "teacher" else DEFAULT_PALETTE


def _resolved_preferences(user: dict, row: Any = None) -> dict[str, Any]:
    role, _ = preference_identity(user)
    defaults = {"palette_key": default_palette_for_role(role), "appearance": "auto", "glass": "tinted"}
    return {
        **{field: row[field] if row and row[field] in allowed else defaults[field]
           for field, allowed in PREFERENCE_VALUES.items()},
        "version": int(row["version"]) if row else 0,
        "updated_at": str(row["updated_at"]) if row else None,
        "context_token": preference_context_token(user),
    }


def get_ui_preferences(conn: Any, user: dict) -> dict[str, Any]:
    role, user_pk = preference_identity(user)
    row = conn.execute(
        "SELECT palette_key, appearance, glass, version, updated_at FROM user_ui_preferences "
        "WHERE user_role = ? AND user_pk = ?",
        (role, user_pk),
    ).fetchone()
    return _resolved_preferences(user, row)


def validate_preference_changes(changes: Mapping[str, Any]) -> dict[str, str]:
    """One whitelist serves HTTP validation and direct service callers."""
    if not isinstance(changes, Mapping) or not changes or changes.keys() - PREFERENCE_VALUES.keys():
        raise ValueError("请仅提交需要修改的界面偏好。")
    for field, value in changes.items():
        if not isinstance(value, str) or value not in PREFERENCE_VALUES[field]:
            raise ValueError(f"界面偏好 {field} 的取值无效。")
    return {field: changes[field] for field in PREFERENCE_VALUES if field in changes}


def update_ui_preferences(conn: Any, user: dict, *, changes: Mapping[str, Any], version: int) -> dict[str, Any]:
    role, user_pk = preference_identity(user)
    changes = validate_preference_changes(changes)
    if type(version) is not int or not 0 <= version <= MAX_PREFERENCE_VERSION:
        raise ValueError("界面偏好版本无效。")
    if version == 0:
        cursor = conn.execute(
            "INSERT INTO user_ui_preferences (user_role, user_pk, palette_key, appearance, glass, version) "
            "VALUES (?, ?, ?, ?, ?, 1) ON CONFLICT (user_role, user_pk) DO NOTHING "
            "RETURNING version",
            (role, user_pk, changes.get("palette_key", default_palette_for_role(role)),
             changes.get("appearance"), changes.get("glass")),
        )
    else:
        assignments = ", ".join(f"{field} = ?" for field in changes)
        cursor = conn.execute(
            f"UPDATE user_ui_preferences SET {assignments}, version = version + 1, "
            "updated_at = CURRENT_TIMESTAMP WHERE user_role = ? AND user_pk = ? "
            "AND version = ? RETURNING version",
            (*changes.values(), role, user_pk, version),
        )
    if cursor.fetchone() is None:
        raise PreferenceConflict(get_ui_preferences(conn, user))
    return get_ui_preferences(conn, user)


def resolve_user_ui_preferences(request: Any, user: dict | None) -> dict[str, Any]:
    """Resolve for any page after its route has authenticated the user.

    Only a request-local identity cache is used; defaults never create a row.
    """
    if not user:
        return {"enabled": False}
    try:
        identity = preference_identity(user)
    except ValueError:
        return {"enabled": False}
    cache = getattr(request.state, "user_ui_preferences_by_identity", None)
    if cache is None:
        cache = request.state.user_ui_preferences_by_identity = {}
    if identity in cache:
        return cache[identity]
    try:
        with get_db_connection() as conn:
            preferences = get_ui_preferences(conn, user)
        preferences["available"] = True
    except Exception:
        logger.warning("UI preferences unavailable during page SSR", exc_info=True)
        preferences = {**_resolved_preferences(user), "available": False}
    preferences.update(enabled=True, presets=[{"key": key, "name": name} for key, name in PALETTES])
    cache[identity] = preferences
    return preferences
