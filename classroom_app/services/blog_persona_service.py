"""Author persona: the one place that decides how a blog author appears.

docs/blog-closed-loop-plan-2026-10-12.md §3. Every serializer (posts,
comments, discovery, notifications) asks this module instead of reading
``author_display_mode`` itself, so a person can never look different — or leak
more — in one view than in another.

Kinds
- real       teacher or student under their real name: identity, class tag,
             cultivation badge and uploaded avatar are public.
- pen        student pen name (stored mode ``nickname``): only the pen name and
             its initial. No identity, class, badge or uploaded photo (the same
             photo would visually link the pen name to the real person).
- anon       anonymous commenter: "匿名同学·<code>", a code stored per post at
             write time so it is stable everywhere and unique within the post.
- op_anon    the author of an anonymous post, inside that post: "楼主".
- assistant  the housekeeper / AI news desk.
"""
from __future__ import annotations

import random
from datetime import datetime
from typing import Any, Iterable, Optional

ROLE_LABELS = {"teacher": "教师", "student": "学生", "assistant": "AI 助手"}

MODE_REAL = "real_name"
MODE_PEN = "nickname"
MODE_ANONYMOUS = "anonymous"

KIND_REAL = "real"
KIND_PEN = "pen"
KIND_ANON = "anon"
KIND_OP_ANON = "op_anon"
KIND_ASSISTANT = "assistant"

OP_ANON_NAME = "楼主"
ANON_PREFIX = "匿名同学·"
# Neutral nouns (no people, ranks, body parts or loaded words). Codes only need
# to be unique within one post; when every word is taken a digit is appended.
ANON_CODE_WORDS = (
    "青柚", "远山", "白鹭", "松针", "晨雾", "海盐", "竹影", "银杏", "云杉", "落樱",
    "晚风", "星河", "石榴", "麦穗", "清泉", "薄荷", "橘灯", "雪松", "芦苇", "枫叶",
    "柳梢", "桂花", "梧桐", "鲸歌", "珊瑚", "琥珀", "黛瓦", "青瓷", "纸鸢", "风铃",
    "灯塔", "山茶", "苔藓", "云朵", "雨巷", "溪石", "茉莉", "樱桃", "莲蓬", "稻香",
    "秋月", "春芽", "夏蝉", "冬阳", "雪花", "露珠", "虹桥", "霞光", "潮汐", "沙丘",
    "竹笛", "陶罐", "书签", "墨砚", "画舫", "木舟", "篝火", "蒲公英", "向日葵", "满天星",
    "绿萝", "仙人掌", "含羞草", "紫藤", "百合", "海棠", "芍药", "睡莲", "蔷薇", "玉兰",
    "柠檬", "蓝莓", "椰子", "芒果", "菠萝", "葡萄", "荔枝", "柿子", "山楂", "核桃",
    "松鼠", "海豚", "白鸽", "企鹅", "考拉", "熊猫", "狐狸", "鹿角", "云雀", "燕尾",
    "萤火", "蜻蜓", "蝴蝶", "飞鱼", "海螺", "贝壳", "鹅卵石", "雪豹", "猫头鹰", "长颈鹿",
    "北斗", "极光", "流星", "月光", "晨星", "银河", "彗尾", "日晷", "罗盘", "风车",
    "邮筒", "信笺", "折扇", "竹篮", "茶盏", "棋盘", "花窗", "石桥", "古井", "长亭",
    "青苔", "梅枝", "雁阵", "渔火", "炊烟", "麦田", "草垛", "果园",
)


RESERVED_PEN_NAMES = frozenset({"楼主", "管家", "作者", "系统", "管理员", "老师", "ai助手", "ai 助手"})


def pen_name_problem(conn, name: Any, *, self_role: str, self_pk: Any) -> str:
    """Why a pen name may not be used ("" when it may).

    A pen name is shown verbatim as a public face, so it must not pass for
    "楼主", the housekeeper, an anonymous code or a real person on the platform.
    """
    normalized = str(name or "").strip()
    if not normalized:
        return ""
    if normalized.lower() in RESERVED_PEN_NAMES or normalized.startswith("匿名") or "·" in normalized:
        return "笔名不能使用“楼主”“管家”“匿名…”等平台保留称呼"
    self_pk_int = int(self_pk) if str(self_pk or "").isdigit() else -1
    for role, table in (("student", "students"), ("teacher", "teachers")):
        exclude = self_pk_int if role == str(self_role or "").strip().lower() else -1
        clash = conn.execute(
            f"SELECT 1 FROM {table} WHERE name = ? AND id <> ? LIMIT 1", (normalized, exclude)
        ).fetchone()
        if clash is not None:
            return "笔名不能与平台上其他人的真实姓名相同"
    return ""


def normalize_mode(role: Any, mode: Any) -> str:
    """Only students choose a face; everyone else is always real-named."""
    if str(role or "").strip().lower() != "student":
        return MODE_REAL
    normalized = str(mode or MODE_REAL).strip().lower()
    return normalized if normalized in {MODE_REAL, MODE_PEN, MODE_ANONYMOUS} else MODE_REAL


def is_public_identity_mode(role: Any, mode: Any) -> bool:
    """Whether content in this mode may be linked to the author's identity."""
    return normalize_mode(role, mode) == MODE_REAL


def load_profiles(conn, identities: Iterable[str]) -> dict[str, dict[str, str]]:
    """Live name / pen name / avatar for ``role:id`` identities (one query per role)."""
    by_role: dict[str, set[int]] = {"teacher": set(), "student": set()}
    for identity in identities:
        role, _, raw_pk = str(identity or "").partition(":")
        if role in by_role and raw_pk.isdigit():
            by_role[role].add(int(raw_pk))
    profiles: dict[str, dict[str, str]] = {}
    for role, table in (("teacher", "teachers"), ("student", "students")):
        ids = sorted(by_role[role])
        if not ids:
            continue
        placeholders = ", ".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id, name, nickname, avatar_file_hash FROM {table} WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        for row in rows:
            profiles[f"{role}:{int(row['id'])}"] = {
                "name": str(row["name"] or ""),
                "nickname": str(row["nickname"] or ""),
                "avatar_hash": str(row["avatar_file_hash"] or ""),
            }
    return profiles


def avatar_url_for_hash(avatar_hash: str) -> str:
    """Content-addressed avatar URL: carries no user id (see /api/blog/avatar)."""
    avatar_hash = str(avatar_hash or "").strip().lower()
    return f"/api/blog/avatar/{avatar_hash}" if avatar_hash else ""


def _persona(**fields: Any) -> dict[str, Any]:
    persona = {
        "kind": KIND_REAL,
        "identity": "",
        "role": "",
        "role_label": "",
        "user_pk": None,
        "display_name": "",
        "display_mode": MODE_REAL,
        "is_anonymous": False,
        "is_op": False,
        "avatar_url": "",
        "avatar_kind": "person",
        "cultivation_badge": None,
        "can_follow": False,
        "can_message": False,
    }
    persona.update(fields)
    return persona


def resolve(
    *,
    actor_identity: str,
    actor_role: str,
    actor_user_pk: Any,
    display_mode: str,
    snapshot_name: str,
    profiles: dict[str, dict[str, str]],
    badge: Optional[dict] = None,
    post_author_identity: str = "",
    post_display_mode: str = "",
    is_comment: bool = False,
) -> dict[str, Any]:
    role = str(actor_role or "").strip().lower()
    identity = str(actor_identity or "")
    is_author = bool(identity) and identity == str(post_author_identity or "")
    mode = normalize_mode(role, display_mode)
    post_mode = str(post_display_mode or MODE_REAL)
    # The "作者" mark joins a comment to the post's byline, so it is only set
    # when both wear the same face: a pen-name comment on one's own real-name
    # post must not reveal who the pen name is (and vice versa).
    is_op = is_author and (not is_comment or mode == post_mode)

    if role == "assistant":
        return _persona(kind=KIND_ASSISTANT, role="assistant", role_label=ROLE_LABELS["assistant"],
                        display_name=snapshot_name or "管家", avatar_kind="assistant", is_op=is_op)

    # Inside an anonymous post its author is always "楼主", whatever they chose.
    if is_comment and is_author and post_mode == MODE_ANONYMOUS:
        return _persona(kind=KIND_OP_ANON, display_name=OP_ANON_NAME, display_mode=MODE_ANONYMOUS,
                        is_anonymous=True, is_op=True, avatar_kind="anon")

    if mode == MODE_ANONYMOUS:
        name = "匿名同学"
        if is_comment and snapshot_name:
            name = snapshot_name if snapshot_name.startswith(ANON_PREFIX) else f"{ANON_PREFIX}{snapshot_name}"
        # Only "楼主" (above) may mark an anonymous voice as the author.
        return _persona(kind=KIND_ANON, display_name=name, display_mode=MODE_ANONYMOUS,
                        is_anonymous=True, is_op=False, avatar_kind="anon")

    profile = profiles.get(identity) or {}
    if mode == MODE_PEN:
        # Live pen name; the snapshot covers a pen name cleared later.
        name = profile.get("nickname") or snapshot_name or "笔名作者"
        return _persona(kind=KIND_PEN, role=role, role_label=ROLE_LABELS.get(role, ""),
                        display_name=name, display_mode=MODE_PEN, is_op=is_op)

    user_pk = str(actor_user_pk if actor_user_pk is not None else "")
    return _persona(
        kind=KIND_REAL,
        identity=identity,
        role=role,
        role_label=ROLE_LABELS.get(role, ""),
        user_pk=int(user_pk) if user_pk.isdigit() else None,
        display_name=profile.get("name") or snapshot_name or "未命名用户",
        is_op=is_op,
        avatar_url=avatar_url_for_hash(profile.get("avatar_hash", "")),
        cultivation_badge=badge if role == "student" else None,
        can_follow=role in {"teacher", "student"},
        can_message=role in {"teacher", "student"},
    )


ANON_CODE_CLAIM_ATTEMPTS = 8


def _is_integrity_error(exc: BaseException) -> bool:
    # sqlite3 and psycopg integrity errors, matched by class name (no driver import).
    return any(cls.__name__ in {"IntegrityError", "UniqueViolation"} for cls in type(exc).__mro__)


def _pick_code(taken: set[str], chooser: random.Random) -> str:
    available = [word for word in ANON_CODE_WORDS if word not in taken]
    if available:
        return chooser.choice(available)
    suffix = 2
    while True:
        candidate = f"{chooser.choice(ANON_CODE_WORDS)}{suffix}"
        if candidate not in taken:
            return candidate
        suffix += 1


def assign_anonymous_code(conn, post_id: int, identity: str, *, rng: Optional[random.Random] = None) -> str:
    """Claim (or reuse) this person's code in this post; the caller stores it.

    ``blog_anonymous_codes`` is the source of truth. A claim that loses a race
    (same person twice, or two people drawing the same word) is undone with a
    savepoint, so the caller's transaction stays usable on both engines.
    """
    chooser = rng or random.SystemRandom()
    for _attempt in range(ANON_CODE_CLAIM_ATTEMPTS):
        rows = conn.execute(
            "SELECT author_identity, code FROM blog_anonymous_codes WHERE post_id = ?", (post_id,)
        ).fetchall()
        mine = next((str(row["code"]) for row in rows if str(row["author_identity"]) == identity), "")
        if mine:
            return mine
        code = _pick_code({str(row["code"]) for row in rows}, chooser)
        conn.execute("SAVEPOINT blog_anon_code")
        try:
            conn.execute(
                "INSERT INTO blog_anonymous_codes (post_id, author_identity, code, created_at)"
                " VALUES (?, ?, ?, ?)",
                (post_id, identity, code, datetime.now().isoformat()),
            )
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT blog_anon_code")
            conn.execute("RELEASE SAVEPOINT blog_anon_code")
            if not _is_integrity_error(exc):
                raise
            continue
        conn.execute("RELEASE SAVEPOINT blog_anon_code")
        return code
    raise RuntimeError("匿名代号分配繁忙，请稍后重试")
