"""Creator studio and author pages (docs/blog-closed-loop-plan-2026-10-12.md §9.4, P6).

An author has up to three *faces*: real name, pen name and anonymous. The
studio shows each face's numbers separately, and everything a reader can see
(author page, follower lists, follow buttons) addresses a face through an
opaque ``ref`` (``blog_persona_service.face_ref``), never through an identity:
following a pen name or opening its page must not reveal who writes it.

``blog_author_refs`` caches ref -> (identity, mode). An unknown ref triggers
one bulk refresh that writes the refs of every account (a few thousand keyed
hashes), at most once a minute per process, so random refs cannot be used to
make the server rescan all accounts on every request. Rows are upserted per
(identity, mode), which also replaces refs after a SECRET_KEY rotation.

Trust boundary: refs are as unlinkable as SECRET_KEY is secret — the same key
that signs sessions, whose leak would already allow forging any login.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from . import blog_author_stats as stats
from . import blog_moderation_states as states
from . import blog_persona_service as persona_service
from . import blog_safety_service as safety

REF_PATTERN = re.compile(r"^[0-9a-f]{20}$")
FOLLOWABLE_MODES = (persona_service.MODE_REAL, persona_service.MODE_PEN)
MAX_BIO_LENGTH = 60
COMMENTS_PER_PAGE = 20
UNREPLIED_WINDOW_DAYS = 14
FOLLOW_TYPE_BY_MODE = stats.FOLLOW_TYPE_BY_MODE
MODE_BY_FOLLOW_TYPE = {value: key for key, value in FOLLOW_TYPE_BY_MODE.items()}
FACE_LABELS = {"real_name": "实名", "nickname": "笔名", "anonymous": "匿名"}
REF_REFRESH_INTERVAL_SECONDS = 60
_LAST_REF_REFRESH = {"at": 0.0}


def _identity(user: dict) -> tuple[int, str, str]:
    from .blog_service import _ensure_identity

    user_pk, role, identity = _ensure_identity(user)
    if role not in {"student", "teacher"}:
        raise PermissionError("当前账号没有创作中心")
    return user_pk, role, identity


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --- refs ---------------------------------------------------------------------------

def resolve_ref(conn, ref: Any) -> Optional[tuple[str, str]]:
    """(identity, mode) of an author face, or None for an unknown/invalid ref."""
    value = str(ref or "").strip().lower()
    if not REF_PATTERN.fullmatch(value):
        return None
    lookup = "SELECT identity, display_mode FROM blog_author_refs WHERE ref = ?"
    row = conn.execute(lookup, (value,)).fetchone()
    if row is None and _refresh_refs(conn):
        row = conn.execute(lookup, (value,)).fetchone()
    return (str(row["identity"]), str(row["display_mode"])) if row is not None else None


def _refresh_refs(conn) -> bool:
    """Write every account's face refs; throttled. Returns whether it ran."""
    now = time.monotonic()
    if now - _LAST_REF_REFRESH["at"] < REF_REFRESH_INTERVAL_SECONDS:
        return False
    _LAST_REF_REFRESH["at"] = now
    stamp = _now()
    for role, table, modes in (("student", "students", FOLLOWABLE_MODES),
                               # Teachers never write under a pen name (plan §3.4).
                               ("teacher", "teachers", (persona_service.MODE_REAL,))):
        for account in conn.execute(f"SELECT id FROM {table}").fetchall():
            identity = f"{role}:{int(account['id'])}"
            for mode in modes:
                conn.execute(
                    "INSERT INTO blog_author_refs (ref, identity, display_mode, created_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT (identity, display_mode) DO UPDATE SET ref = excluded.ref",
                    (persona_service.face_ref(identity, mode), identity, mode, stamp),
                )
    return True


def pen_face_available(conn, identity: str) -> bool:
    """A pen name exists only while a student has one set."""
    account = _account(conn, identity)
    return bool(account and account["role"] == "student" and str(account.get("nickname") or "").strip())


def _account(conn, identity: str) -> Optional[dict[str, Any]]:
    role, _, raw_pk = identity.partition(":")
    table = {"student": "students", "teacher": "teachers"}.get(role)
    if table is None or not raw_pk.isdigit():
        return None
    row = conn.execute(f"SELECT id, name, nickname, description FROM {table} WHERE id = ?", (int(raw_pk),)).fetchone()
    return {**dict(row), "role": role} if row else None


# --- bios -----------------------------------------------------------------------------

def get_bios(conn, identity: str) -> dict[str, str]:
    row = conn.execute("SELECT real_bio, pen_bio FROM blog_author_profiles WHERE identity = ?", (identity,)).fetchone()
    if row is None:
        return {"real_bio": "", "pen_bio": ""}
    return {"real_bio": str(row["real_bio"] or ""), "pen_bio": str(row["pen_bio"] or "")}


def _clean_bio(conn, value: Any) -> str:
    text = " ".join(str(value or "").split())[:MAX_BIO_LENGTH]
    if text and safety.enforce_local_rules(conn, text).flagged:
        raise ValueError("简介里有不适合公开的内容（如联系方式），请修改后再保存")
    return text


def set_bios(conn, user: dict, *, real_bio: Any = None, pen_bio: Any = None) -> dict[str, str]:
    _pk, role, identity = _identity(user)
    current = get_bios(conn, identity)
    updated = {
        "real_bio": _clean_bio(conn, real_bio) if real_bio is not None else current["real_bio"],
        # Only students write under a pen name (plan §3.4).
        "pen_bio": _clean_bio(conn, pen_bio) if pen_bio is not None and role == "student" else current["pen_bio"],
    }
    conn.execute(
        """
        INSERT INTO blog_author_profiles (identity, real_bio, pen_bio, updated_at) VALUES (?, ?, ?, ?)
        ON CONFLICT (identity) DO UPDATE SET real_bio = excluded.real_bio, pen_bio = excluded.pen_bio,
            updated_at = excluded.updated_at
        """,
        (identity, updated["real_bio"], updated["pen_bio"], _now()),
    )
    return updated


# --- overview -------------------------------------------------------------------------

def _todo(conn, identity: str) -> dict[str, int]:
    sanctioned = conn.execute(
        "SELECT COUNT(*) AS total FROM blog_posts WHERE author_identity = ? AND status = 'published' "
        "AND moderation_state IN (?, ?, ?)",
        (identity, states.STATE_LIMITED, states.STATE_REMOVED, states.STATE_APPEAL_PENDING),
    ).fetchone()
    reviewing = conn.execute(
        "SELECT COUNT(*) AS total FROM blog_posts WHERE author_identity = ? AND status = 'published' "
        "AND moderation_state = ?",
        (identity, states.STATE_PENDING),
    ).fetchone()
    since = (datetime.now() - timedelta(days=UNREPLIED_WINDOW_DAYS)).isoformat(timespec="seconds")
    unreplied = conn.execute(
        f"""
        SELECT COUNT(*) AS total FROM blog_comments c JOIN blog_posts p ON p.id = c.post_id
        WHERE p.author_identity = ? AND p.status = 'published' AND c.author_identity <> ?
          AND c.status = 'active' AND {states.public_state_sql('c')} AND c.created_at >= ?
          AND NOT EXISTS (
              SELECT 1 FROM blog_comments r
              WHERE r.root_comment_id = COALESCE(c.root_comment_id, c.id)
                AND r.author_identity = ? AND r.created_at > c.created_at AND r.status = 'active'
          )
        """,
        (identity, identity, since, identity),
    ).fetchone()
    return {
        "sanctioned": int(sanctioned["total"] or 0),
        "reviewing": int(reviewing["total"] or 0),
        "unreplied_comments": int(unreplied["total"] or 0),
    }


def overview(conn, user: dict, *, mode: Any = None) -> dict[str, Any]:
    _pk, role, identity = _identity(user)
    modes = stats.author_modes(conn, identity)
    selected = str(mode or "") if str(mode or "") in modes else modes[0]
    pen_live = pen_face_available(conn, identity)

    def page_ref(face_mode: str) -> str:
        if face_mode == persona_service.MODE_REAL or (face_mode == persona_service.MODE_PEN and pen_live):
            return persona_service.face_ref(identity, face_mode)
        return ""  # anonymous, or a pen name that was cleared: no page

    faces = [{"mode": face_mode, "label": FACE_LABELS[face_mode], "ref": page_ref(face_mode)} for face_mode in modes]
    return {
        "faces": faces,
        "stats": stats.overview(conn, identity, selected),
        "todo": _todo(conn, identity),
        "bios": get_bios(conn, identity),
        "can_use_pen_name": role == "student",
    }


# --- interactions ------------------------------------------------------------------------

def _comment_card(conn, user: dict, row: dict) -> Optional[dict[str, Any]]:
    from .blog_comment_service import get_comment_view

    try:
        view = get_comment_view(conn, user, int(row["id"]))
    except (ValueError, PermissionError):
        return None
    return {**view, "post_title": str(row.get("post_title") or "")}


def _page_info(total_row, page: int, offset: int) -> dict[str, Any]:
    total = int(total_row["total"] or 0)
    return {"total": total, "page": page, "has_more": offset + COMMENTS_PER_PAGE < total}


def received_comments(conn, user: dict, *, page: int = 1) -> dict[str, Any]:
    """Comments by others on my posts, and replies by others to my comments."""
    _pk, _role, identity = _identity(user)
    page = max(1, int(page))
    offset = (page - 1) * COMMENTS_PER_PAGE
    where = f"""
        c.author_identity <> ? AND c.status = 'active' AND {states.public_state_sql('c')} AND p.status = 'published'
        AND (p.author_identity = ? OR c.reply_to_comment_id IN (
            SELECT mine.id FROM blog_comments mine WHERE mine.author_identity = ?))
    """
    params = (identity, identity, identity)
    total = conn.execute(
        f"SELECT COUNT(*) AS total FROM blog_comments c JOIN blog_posts p ON p.id = c.post_id WHERE {where}", params,
    ).fetchone()
    rows = conn.execute(
        f"""
        SELECT c.id, p.title AS post_title FROM blog_comments c JOIN blog_posts p ON p.id = c.post_id
        WHERE {where} ORDER BY c.created_at DESC, c.id DESC LIMIT ? OFFSET ?
        """,
        (*params, COMMENTS_PER_PAGE, offset),
    ).fetchall()
    items = [card for card in (_comment_card(conn, user, dict(row)) for row in rows) if card]
    return {"items": items, **_page_info(total, page, offset)}


def sent_comments(conn, user: dict, *, page: int = 1) -> dict[str, Any]:
    """My comments, including handled ones (with the reason), as I published them."""
    _pk, _role, identity = _identity(user)
    page = max(1, int(page))
    offset = (page - 1) * COMMENTS_PER_PAGE
    total = conn.execute(
        "SELECT COUNT(*) AS total FROM blog_comments WHERE author_identity = ? AND status = 'active'", (identity,),
    ).fetchone()
    rows = conn.execute(
        """
        SELECT c.id, c.post_id, c.content_md, c.created_at, c.edited_at, c.author_display_mode,
               c.author_display_name, c.moderation_state, c.moderation_reason_code, c.moderation_note,
               c.like_count, p.title AS post_title, p.status AS post_status
        FROM blog_comments c JOIN blog_posts p ON p.id = c.post_id
        WHERE c.author_identity = ? AND c.status = 'active'
        ORDER BY c.created_at DESC, c.id DESC LIMIT ? OFFSET ?
        """,
        (identity, COMMENTS_PER_PAGE, offset),
    ).fetchall()
    items = []
    for row in rows:
        state = str(row["moderation_state"] or states.STATE_NONE)
        sanctioned = state in states.SANCTIONED_STATES
        items.append({
            "id": int(row["id"]),
            "post_id": int(row["post_id"]),
            "post_title": str(row["post_title"] or ""),
            "post_available": str(row["post_status"] or "") == "published",
            "content_md": str(row["content_md"] or ""),
            "created_at": str(row["created_at"] or ""),
            "edited": bool(row["edited_at"]),
            "like_count": int(row["like_count"] or 0),
            # "你以 匿名同学·青柚 发表": the face others saw, so the author can tell.
            "face": {"mode": str(row["author_display_mode"] or persona_service.MODE_REAL),
                     "name": str(row["author_display_name"] or "")},
            "moderation": {
                "state": state,
                # "flagged" is a queue marker the author is not told about (plan §5.1).
                "label": "" if state == states.STATE_FLAGGED else states.STATE_LABELS.get(state, ""),
                "reason_label": states.reason_label(str(row["moderation_reason_code"] or "")) if sanctioned else "",
                "note": str(row["moderation_note"] or "") if sanctioned else "",
            },
        })
    return {"items": items, **_page_info(total, page, offset)}


# --- followers and following -----------------------------------------------------------------

def followers(conn, user: dict) -> dict[str, Any]:
    """Who follows my real name and my pen name (followers are real-named, D10)."""
    _pk, role, identity = _identity(user)
    mine = {
        str(row["target_key"])
        for row in conn.execute(
            "SELECT target_key FROM blog_follows WHERE user_identity = ? AND target_type = 'author'", (identity,),
        ).fetchall()
    }
    groups: dict[str, list[dict[str, Any]]] = {}
    totals: dict[str, int] = {}
    for mode in FOLLOWABLE_MODES if role == "student" else (persona_service.MODE_REAL,):
        totals[mode] = stats.follower_count(conn, identity, mode) or 0
        rows = conn.execute(
            "SELECT user_identity, created_at FROM blog_follows WHERE target_type = ? AND target_key = ? "
            "ORDER BY created_at DESC LIMIT 500",
            (FOLLOW_TYPE_BY_MODE[mode], identity),
        ).fetchall()
        people = persona_service.load_profiles(conn, [str(row["user_identity"]) for row in rows])
        groups[mode] = []
        for row in rows:
            follower = str(row["user_identity"])
            profile = people.get(follower) or {}
            groups[mode].append({
                "name": profile.get("name") or "已注销用户",
                "avatar_url": persona_service.avatar_url_for_hash(profile.get("avatar_hash", "")),
                "ref": persona_service.face_ref(follower, persona_service.MODE_REAL),
                "followed_at": str(row["created_at"] or ""),
                "follows_back": follower in mine,
            })
    return {
        "real": groups.get(persona_service.MODE_REAL, []),
        "pen": groups.get(persona_service.MODE_PEN, []),
        # The lists stop at 500; the headings show the true totals.
        "real_total": totals.get(persona_service.MODE_REAL, 0),
        "pen_total": totals.get(persona_service.MODE_PEN, 0),
    }


def following(conn, user: dict) -> list[dict[str, Any]]:
    """What I follow, with names; author faces are addressed by ref only."""
    from .blog_section_service import list_blog_sections

    _pk, _role, identity = _identity(user)
    rows = conn.execute(
        "SELECT target_type, target_key, created_at FROM blog_follows WHERE user_identity = ? "
        "ORDER BY created_at DESC, id DESC",
        (identity,),
    ).fetchall()
    people = persona_service.load_profiles(
        conn, [str(row["target_key"]) for row in rows if str(row["target_type"]) in MODE_BY_FOLLOW_TYPE])
    sections = {item["section_key"]: item["name"] for item in list_blog_sections(conn)}
    items = []
    for row in rows:
        target_type, key = str(row["target_type"]), str(row["target_key"])
        item: dict[str, Any] = {"target_type": target_type, "followed_at": str(row["created_at"] or "")}
        if target_type in MODE_BY_FOLLOW_TYPE:
            mode = MODE_BY_FOLLOW_TYPE[target_type]
            profile = people.get(key) or {}
            name = profile.get("name") if mode == persona_service.MODE_REAL else profile.get("nickname")
            item.update(target_key=persona_service.face_ref(key, mode), name=name or "已不可用的作者",
                        face=FACE_LABELS[mode])
        elif target_type == "section":
            item.update(target_key=key, name=sections.get(key, key), face="板块")
        else:
            item.update(target_key=key, name=f"#{key}", face="标签")
        items.append(item)
    return items


# --- author page --------------------------------------------------------------------------------

def author_page(conn, viewer: dict, ref: Any, *, page: int = 1) -> dict[str, Any]:
    from .blog_service import _ensure_identity, list_posts

    face = resolve_ref(conn, ref)
    account = _account(conn, face[0]) if face else None
    if face is None or account is None:
        raise ValueError("作者不存在")
    identity, mode = face
    if mode == persona_service.MODE_PEN and not pen_face_available(conn, identity):
        raise ValueError("作者不存在")  # A cleared pen name has no public page any more.
    _vpk, _vrole, viewer_identity = _ensure_identity(viewer)
    persona = persona_service.resolve(
        actor_identity=identity, actor_role=account["role"], actor_user_pk=account["id"], display_mode=mode,
        snapshot_name=str(account.get("name") or ""), profiles=persona_service.load_profiles(conn, [identity]),
    )
    bios = get_bios(conn, identity)
    if mode == persona_service.MODE_PEN:
        bio = bios["pen_bio"]
    else:
        teacher_intro = str(account.get("description") or "")[:MAX_BIO_LENGTH] if account["role"] == "teacher" else ""
        bio = bios["real_bio"] or teacher_intro
    posts = list_posts(conn, viewer, author_identity=identity, author_display_mode=mode, page=max(1, int(page)))
    following_row = conn.execute(
        "SELECT 1 FROM blog_follows WHERE user_identity = ? AND target_type = ? AND target_key = ? LIMIT 1",
        (viewer_identity, FOLLOW_TYPE_BY_MODE[mode], identity),
    ).fetchone()
    return {
        "author": persona,
        "ref": persona_service.face_ref(identity, mode),
        "mode": mode,
        "follow_type": FOLLOW_TYPE_BY_MODE[mode],
        "face": FACE_LABELS[mode],
        "bio": bio,
        "followers": stats.follower_count(conn, identity, mode) or 0,
        "is_following": following_row is not None,
        "is_self": viewer_identity == identity,
        **posts,
    }


def creator_summary(conn, user: dict) -> dict[str, Any]:
    """The "我的创作" card in the personal center (all faces together; only the
    owner sees it, so nothing links a pen name to anyone else)."""
    _pk, role, identity = _identity(user)
    posts = conn.execute(
        "SELECT SUM(CASE WHEN status = 'published' THEN 1 ELSE 0 END) AS published, "
        "SUM(CASE WHEN status = 'draft' THEN 1 ELSE 0 END) AS drafts "
        "FROM blog_posts WHERE author_identity = ?",
        (identity,),
    ).fetchone()
    followers = conn.execute(
        "SELECT COUNT(*) AS total FROM blog_follows WHERE target_type IN ('author', 'pen') AND target_key = ?",
        (identity,),
    ).fetchone()
    todo = _todo(conn, identity)
    return {
        "published": int(posts["published"] or 0),
        "drafts": int(posts["drafts"] or 0),
        "followers": int(followers["total"] or 0),
        "needs_attention": todo["sanctioned"] + todo["unreplied_comments"],
        "href": "/blog?view=studio",
    }
