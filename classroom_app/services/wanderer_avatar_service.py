"""散修 avatars: illustrated SVG → silhouette → runtime geometric fallback.

docs/wanderer-ai-free-agents-2026-10-25.md §11. Resolution order for a
wanderer (``resolve_wanderer_avatar``):

1. an uploaded photo (``students.avatar_file_hash``), the normal profile path;
2. ``wanderer_agents.avatar_file`` → ``static/img/wanderers/<file>`` — the
   persona illustration (seeded wanderers get ``<slug>.svg`` from
   ``seed_wanderers``);
3. ``wanderer_agents.avatar_silhouette`` → ``static/img/wanderers/silhouettes/<key>.svg``
   (天道 picks one with ``pick_silhouette`` when it creates a wanderer);
4. the deterministic geometric SVG rendered from the slug (never fails).

``validate_avatar_svg`` is the shared safety gate for every static avatar
file (no script/image/foreignObject/external refs, has a viewBox, ≤ 12 KB).
"""
from __future__ import annotations

import hashlib
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable, Union

SIZE = 96
CENTER = SIZE / 2
SVG_MIME = "image/svg+xml"
AVATAR_MAX_BYTES = 12 * 1024

AVATAR_DIR = Path(__file__).resolve().parents[2] / "static" / "img" / "wanderers"
SILHOUETTE_DIR = AVATAR_DIR / "silhouettes"
MANIFEST_PATH = AVATAR_DIR / "manifest.json"

SOURCE_UPLOAD = "upload"
SOURCE_ILLUSTRATED = "illustrated"
SOURCE_SILHOUETTE = "silhouette"
SOURCE_GEOMETRIC = "geometric"
SOURCE_LABELS = {
    SOURCE_UPLOAD: "上传头像",
    SOURCE_ILLUSTRATED: "插画",
    SOURCE_SILHOUETTE: "剪影",
    SOURCE_GEOMETRIC: "几何",
}

# Silhouette keys with their admin labels, in display order.
SILHOUETTES: dict[str, str] = {
    "student_m": "男学生",
    "student_f": "女学生",
    "scholar": "学霸（眼镜+书）",
    "athlete": "运动",
    "musician": "音乐",
    "foodie": "美食",
    "gamer": "游戏",
    "artist": "画画/摄影",
    "traveler": "旅行",
    "dog": "小狗",
    "cat": "小猫",
    "sword": "仙剑",
}
SILHOUETTE_KEYS = tuple(SILHOUETTES)

# Priority order: the first rule with a keyword hit wins. Specific pets and
# props come before broad themes so a dog-loving bookworm still gets the dog.
_SILHOUETTE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dog", ("狗", "柯基", "犬", "汪")),
    ("cat", ("猫", "喵")),
    ("sword", ("剑", "武侠", "修仙", "江湖", "武术")),
    ("musician", ("吉他", "音乐", "民谣", "唱", "听歌", "歌手", "歌曲", "粤语歌", "乐器", "钢琴", "冬不拉", "乐队")),
    ("foodie", ("吃", "美食", "烹饪", "小吃", "甜品", "做饭", "厨")),
    ("athlete", ("运动", "球", "跑步", "健身", "体育", "滑雪", "游泳")),
    ("gamer", ("游戏", "电竞", "二次元", "动漫", "cos")),
    ("artist", ("画", "摄影", "设计", "手账", "艺术")),
    ("traveler", ("旅行", "家乡", "漫步", "远方", "地理", "公路")),
    ("scholar", ("笔记", "考研", "书", "读", "哲学", "学习", "编程", "算法", "写作", "知识", "逻辑", "辩论")),
)

# Idioms that contain a keyword without meaning it (夜猫子 = night owl, not a cat lover).
_NOISE_PHRASES = ("夜猫子", "猫腻", "狗血", "走狗", "剑走偏锋")

_AVATAR_FILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\.svg$")
_FORBIDDEN_TAGS = {"script", "image", "foreignobject", "iframe", "object", "embed", "style", "use"}
_LOCAL_URL_RE = re.compile(r"url\(\s*#[A-Za-z0-9_-]+\s*\)")

AvatarBody = Union[bytes, Path]


# ---------------------------------------------------------------------------
# geometric fallback (unchanged look; §10.3)
# ---------------------------------------------------------------------------

def _digest(slug: str) -> bytes:
    return hashlib.sha256(f"wanderer-avatar|{slug}".encode("utf-8")).digest()


def _polygon(radius: float, sides: int, rotate: float) -> str:
    points = []
    for index in range(sides):
        angle = math.radians(rotate + 360.0 * index / sides)
        points.append(f"{CENTER + radius * math.cos(angle):.2f},{CENTER + radius * math.sin(angle):.2f}")
    return " ".join(points)


def _orbit(count: int, radius: float, dot: float, rotate: float, color: str) -> str:
    dots = []
    for index in range(count):
        angle = math.radians(rotate + 360.0 * index / count)
        x = CENTER + radius * math.cos(angle)
        y = CENTER + radius * math.sin(angle)
        dots.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{dot:.2f}" fill="{color}"/>')
    return "".join(dots)


def render_wanderer_avatar_svg(slug: str) -> str:
    """The deterministic geometric SVG avatar for ``slug`` (same slug, same bytes)."""
    data = _digest(slug)
    hue_a = data[0] * 360 // 256
    hue_b = (hue_a + 60 + data[1] % 120) % 360
    sides = 3 + data[2] % 6
    rotate = data[3] * 360 / 256
    dots = 3 + data[4] % 7
    inner_sides = 3 + data[5] % 4
    ring = 26 + data[6] % 8
    bg_a = f"hsl({hue_a}, 62%, 46%)"
    bg_b = f"hsl({hue_b}, 58%, 30%)"
    shape = f"hsl({hue_b}, 80%, 82%)"
    accent = f"hsl({hue_a}, 90%, 88%)"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" width="{SIZE}" height="{SIZE}" '
        f'role="img" aria-label="散修头像">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{bg_a}"/><stop offset="1" stop-color="{bg_b}"/></linearGradient></defs>'
        f'<rect width="{SIZE}" height="{SIZE}" fill="url(#g)"/>'
        f'<circle cx="{CENTER}" cy="{CENTER}" r="{ring}" fill="none" stroke="{accent}" stroke-opacity=".35" stroke-width="1.5"/>'
        f'<polygon points="{_polygon(22, sides, rotate)}" fill="{shape}" fill-opacity=".9"/>'
        f'<polygon points="{_polygon(10, inner_sides, rotate + 180)}" fill="{bg_b}" fill-opacity=".85"/>'
        f'{_orbit(dots, ring, 2.6, rotate / 2, accent)}'
        "</svg>\n"
    )


# ---------------------------------------------------------------------------
# silhouette choice
# ---------------------------------------------------------------------------

def _texts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable) and not isinstance(value, (bytes, dict)):
        return [str(item) for item in value if item]
    return []


def pick_silhouette(seed_or_wanderer: dict[str, Any] | None) -> str:
    """A silhouette key for a seed or hydrated wanderer (deterministic keyword match).

    Looks at interests, archetype and temperament; falls back to
    ``student_f`` / ``student_m`` by gender.
    """
    source = seed_or_wanderer or {}
    haystack = " ".join(
        _texts(source.get("interests")) + _texts(source.get("archetype")) + _texts(source.get("temperament"))
    ).lower()
    for phrase in _NOISE_PHRASES:
        haystack = haystack.replace(phrase, " ")
    for key, keywords in _SILHOUETTE_RULES:
        if any(word in haystack for word in keywords):
            return key
    return "student_f" if str(source.get("gender") or "").strip() == "女" else "student_m"


def normalize_silhouette(value: Any) -> str:
    """'' (cleared) or a known key; anything else is a ValueError."""
    key = str(value or "").strip()
    if key and key not in SILHOUETTES:
        raise ValueError("未知的剪影头像")
    return key


# ---------------------------------------------------------------------------
# static files
# ---------------------------------------------------------------------------

def validate_avatar_svg(data: bytes | str) -> list[str]:
    """Problems with an avatar SVG ([] = safe to serve)."""
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    problems: list[str] = []
    if len(raw) > AVATAR_MAX_BYTES:
        problems.append(f"too large: {len(raw)} bytes")
    if re.search(rb"<!(DOCTYPE|ENTITY)", raw, re.IGNORECASE):
        problems.append("DOCTYPE/ENTITY not allowed")
        return problems
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return [*problems, f"invalid XML: {exc}"]
    if root.tag.rsplit("}", 1)[-1] != "svg":
        problems.append("root is not <svg>")
    if not str(root.get("viewBox") or "").strip():
        problems.append("missing viewBox")
    for element in root.iter():
        tag = str(element.tag).rsplit("}", 1)[-1].lower()
        if tag in _FORBIDDEN_TAGS:
            problems.append(f"forbidden element <{tag}>")
        for name, value in element.attrib.items():
            attr = name.rsplit("}", 1)[-1].lower()
            text = str(value)
            if attr.startswith("on"):
                problems.append(f"event attribute {attr}")
            if attr == "href" and not text.startswith("#"):
                problems.append(f"external href {text[:40]}")
            if "url(" in _LOCAL_URL_RE.sub("", text):
                problems.append(f"non-local url() in {attr}")
            if "javascript:" in text.lower():
                problems.append("javascript: URL")
    return problems


def _safe_child(directory: Path, name: str) -> Path | None:
    if not _AVATAR_FILE_RE.match(name or ""):
        return None
    path = (directory / name).resolve()
    if path.parent != directory.resolve() or not path.is_file():
        return None
    return path


def illustrated_avatar_path(avatar_file: Any) -> Path | None:
    return _safe_child(AVATAR_DIR, str(avatar_file or "").strip())


def silhouette_path(key: Any) -> Path | None:
    name = str(key or "").strip()
    return _safe_child(SILHOUETTE_DIR, f"{name}.svg") if name in SILHOUETTES else None


def default_avatar_file(slug: Any) -> str:
    """'<slug>.svg' when the persona illustration exists, else ''."""
    name = f"{str(slug or '').strip()}.svg"
    return name if illustrated_avatar_path(name) is not None else ""


def _upload_path(wanderer: dict[str, Any]) -> Path | None:
    file_hash = str(wanderer.get("avatar_file_hash") or "").strip()
    if not file_hash:
        return None
    from .emoji_service import get_custom_emoji_path

    try:
        path = Path(get_custom_emoji_path(file_hash))
    except Exception:  # missing upload: fall through to the persona avatar
        return None
    return path if path.is_file() else None


def avatar_source(wanderer: dict[str, Any]) -> str:
    """Which link of the chain serves this wanderer now (see module docstring)."""
    if _upload_path(wanderer) is not None:
        return SOURCE_UPLOAD
    if illustrated_avatar_path(wanderer.get("avatar_file")) is not None:
        return SOURCE_ILLUSTRATED
    if silhouette_path(wanderer.get("avatar_silhouette")) is not None:
        return SOURCE_SILHOUETTE
    return SOURCE_GEOMETRIC


def resolve_wanderer_avatar(wanderer: dict[str, Any]) -> tuple[AvatarBody, str]:
    """(path or in-memory bytes, mime type) for a hydrated wanderer."""
    upload = _upload_path(wanderer)
    if upload is not None:
        return upload, str(wanderer.get("avatar_mime_type") or "application/octet-stream")
    illustrated = illustrated_avatar_path(wanderer.get("avatar_file"))
    if illustrated is not None:
        return illustrated, SVG_MIME
    silhouette = silhouette_path(wanderer.get("avatar_silhouette"))
    if silhouette is not None:
        return silhouette, SVG_MIME
    return render_wanderer_avatar_svg(str(wanderer.get("slug") or "")).encode("utf-8"), SVG_MIME
