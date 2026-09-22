"""Small, versioned preferences owned by the authenticated account."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from collections.abc import Mapping
from datetime import date
from functools import lru_cache
from pathlib import Path
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

# The page backdrop reuses the login library; the account never supplies files.
BACKDROP_LIBRARY_BASE = "/static/img/life_tips/"
BACKDROP_MANIFEST = Path(__file__).resolve().parents[2] / "static" / "img" / "life_tips" / "manifest.json"
BACKDROP_CATEGORIES = (
    ("academic-rules", "学业规则"),
    ("thesis", "论文写作"),
    ("teaching", "教学相长"),
    ("career", "职业路径"),
    ("research", "职称科研"),
    ("postgrad", "考研"),
    ("life", "人生大实话"),
    ("wellbeing", "身心权益"),
    ("internship", "实习"),
    ("industry", "行业城市"),
    ("civil-service", "考公考编"),
    ("graduation", "毕业条件"),
    ("scholarship", "奖学金"),
    ("interview", "简历面试"),
    ("contract", "合同五险"),
)
BACKDROP_CATEGORY_LABELS = {f"scene-{key}": name for key, name in BACKDROP_CATEGORIES}
BACKDROP_MODES = frozenset({"off", "scene", *BACKDROP_CATEGORY_LABELS})
DEFAULT_BACKDROP = "scene"
DEFAULT_BACKDROP_COLOR = "#ffffff"
_BACKDROP_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.(?:webp|jpg|jpeg|png)")


class ConstrainedFormat:
    """A value space too large to enumerate is still an explicit whitelist.

    Membership is the only way a value reaches storage or a style, so the same
    ``value in PREFERENCE_VALUES[field]`` check guards reads, writes and HTTP.
    """

    __slots__ = ("_pattern",)

    def __init__(self, pattern: str):
        self._pattern = re.compile(pattern)

    def __contains__(self, value: Any) -> bool:
        return isinstance(value, str) and self._pattern.fullmatch(value) is not None


# Lowercase six-digit hex only: no names, functions, variables or whitespace.
HEX_COLOR = ConstrainedFormat(r"#[0-9a-f]{6}")

PREFERENCE_VALUES = {
    "palette_key": PALETTE_KEYS,
    "appearance": frozenset({"light", "dark", "auto"}),
    "glass": frozenset({"tinted", "off"}),
    "backdrop": BACKDROP_MODES,
    "backdrop_color": HEX_COLOR,
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
    defaults = {"palette_key": default_palette_for_role(role), "appearance": "auto", "glass": "tinted",
                "backdrop": DEFAULT_BACKDROP, "backdrop_color": DEFAULT_BACKDROP_COLOR}
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
        "SELECT palette_key, appearance, glass, backdrop, backdrop_color, version, updated_at "
        "FROM user_ui_preferences "
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
            "INSERT INTO user_ui_preferences (user_role, user_pk, palette_key, appearance, glass, "
            "backdrop, backdrop_color, version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1) ON CONFLICT (user_role, user_pk) DO NOTHING "
            "RETURNING version",
            (role, user_pk, changes.get("palette_key", default_palette_for_role(role)),
             changes.get("appearance"), changes.get("glass"),
             changes.get("backdrop"), changes.get("backdrop_color")),
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


@lru_cache(maxsize=1)
def backdrop_library() -> tuple[tuple[str, frozenset[str]], ...]:
    """Read the shipped login library once; a bad manifest degrades to no image."""
    try:
        data = json.loads(BACKDROP_MANIFEST.read_text(encoding="utf-8"))
        entries = data["images"]
    except Exception:
        logger.warning("Page backdrop library unavailable", exc_info=True)
        return ()
    images = []
    for entry in entries if isinstance(entries, list) else []:
        file = entry.get("file") if isinstance(entry, Mapping) else None
        # A file name is a library key, never a caller-supplied path fragment.
        if not isinstance(file, str) or not _BACKDROP_FILE.fullmatch(file):
            continue
        raw = entry.get("categories")
        images.append((file, frozenset(name for name in (raw if isinstance(raw, list) else []) if isinstance(name, str))))
    return tuple(sorted(images))


def _stable_index(seed: str, size: int) -> int:
    """FNV-1a keeps the server pick and the client preview on the same image."""
    digest = 2166136261
    for byte in seed.encode("utf-8"):
        digest = ((digest ^ byte) * 16777619) & 0xFFFFFFFF
    return digest % size


def backdrop_image_for(mode: str, seed: str) -> str | None:
    if mode not in BACKDROP_MODES or mode == "off":
        return None
    label = BACKDROP_CATEGORY_LABELS.get(mode)
    library = backdrop_library()
    pool = [file for file, categories in library if label is None or label in categories]
    if not pool:
        pool = [file for file, _ in library]
    if not pool:
        return None
    return BACKDROP_LIBRARY_BASE + pool[_stable_index(f"{seed}|{mode}", len(pool))]


def backdrop_seed(role: str, user_pk: int, today: date | None = None) -> str:
    """Stable within one account-day so navigation does not reshuffle the page."""
    message = f"lanshare.ui-backdrop.v1:{role}:{user_pk}:{(today or date.today()).isoformat()}"
    return hashlib.sha256(message.encode()).hexdigest()[:16]


def resolve_backdrop(preferences: Mapping[str, Any], *, seed: str) -> dict[str, Any]:
    mode = preferences.get("backdrop") if preferences.get("backdrop") in BACKDROP_MODES else DEFAULT_BACKDROP
    color = preferences.get("backdrop_color") if preferences.get("backdrop_color") in HEX_COLOR else DEFAULT_BACKDROP_COLOR
    image = backdrop_image_for(mode, seed)
    return {
        "mode": mode,
        "color": color,
        "seed": seed,
        "image": image,
        # Only whitelisted library file names reach this value, so the url()
        # needs no quoting and the template stays a pure custom-property write.
        "image_css": f"url({image})" if image else "none",
        "manifest": BACKDROP_LIBRARY_BASE + "manifest.json",
        "base": BACKDROP_LIBRARY_BASE,
        "categories": [{"key": f"scene-{key}", "name": name} for key, name in BACKDROP_CATEGORIES],
    }


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
    preferences.update(enabled=True, presets=[{"key": key, "name": name} for key, name in PALETTES],
                       backdrop_scene=resolve_backdrop(preferences, seed=backdrop_seed(*identity)))
    cache[identity] = preferences
    return preferences
