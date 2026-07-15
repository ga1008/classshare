from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from . import blog_service
from .blog_notifications import notify_opportunity_deadline


OPPORTUNITY_STATES = {"saved", "preparing", "applied", "interview", "offer", "closed"}
OPPORTUNITY_TYPES = {
    "campus_recruitment",
    "internship",
    "public_institution",
    "civil_service",
    "grassroots_program",
    "career_fair",
    "policy",
    "other",
}
ACTIVE_STATUSES = {"active", "expiring"}
SOURCE_LEVELS = {"A", "B", "C"}
REGION_ALIASES = {
    "nanning": ("南宁",),
    "guangxi": ("广西", "南宁", "柳州", "桂林", "北海", "玉林", "梧州", "钦州", "贵港", "百色"),
    "prd": ("珠三角", "粤港澳大湾区", "广州", "深圳", "珠海", "佛山", "东莞", "中山", "惠州", "肇庆", "江门"),
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _json_list(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = re.split(r"[,，、;；/|\n]+", text)
    elif isinstance(value, (list, tuple, set)):
        parsed = value
    else:
        parsed = []
    result: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        normalized = re.sub(r"\s+", " ", str(item or "")).strip()[:80]
        if not normalized or normalized.casefold() in seen:
            continue
        seen.add(normalized.casefold())
        result.append(normalized)
        if len(result) >= 20:
            break
    return result


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_url(value: Any) -> str:
    raw = str(value or "").strip()[:2000]
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return raw


def _domain(value: Any) -> str:
    host = (urlparse(_safe_url(value)).hostname or "").lower().strip(".")
    return host[4:] if host.startswith("www.") else host


def infer_source_level(source_url: Any, requested: Any = "") -> str:
    domain = _domain(source_url)
    if (
        domain.endswith(".gov.cn")
        or domain.endswith(".edu.cn")
        or domain in {"ncss.cn", "mohrss.gov.cn", "chrm.mohrss.gov.cn", "gxrc.com"}
    ):
        return "A"
    normalized = str(requested or "").strip().upper()
    return normalized if normalized in SOURCE_LEVELS else "C"


def _normalize_datetime(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw or raw.lower() in {"null", "none", "未知", "以官方页面为准"}:
        return None
    match = re.search(r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})", raw)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), 23, 59, 59).isoformat()
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _safe_confidence(value: Any) -> float:
    try:
        return min(max(float(value or 0), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0


def upsert_opportunity_for_post(
    conn,
    post_id: int,
    payload: dict[str, Any] | None,
    *,
    source_url: str = "",
    source_name: str = "",
    published_at: str = "",
) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    normalized_source_url = _safe_url(source_url or data.get("source_url"))
    application_url = _safe_url(data.get("application_url")) or normalized_source_url
    deadline_at = _normalize_datetime(data.get("deadline_at"))
    expires_at = _normalize_datetime(data.get("expires_at")) or deadline_at
    opportunity_type = str(data.get("opportunity_type") or "campus_recruitment").strip().lower()
    if opportunity_type not in OPPORTUNITY_TYPES:
        opportunity_type = "other"
    now = _now_iso()
    values = (
        int(post_id),
        str(data.get("employer_name") or "").strip()[:200],
        opportunity_type,
        str(data.get("positions_text") or data.get("positions") or "").strip()[:1000],
        _json_dumps(_json_list(data.get("regions"))),
        str(data.get("city") or "").strip()[:80],
        _json_dumps(_json_list(data.get("target_groups"))),
        str(data.get("education_text") or "").strip()[:200],
        _json_dumps(_json_list(data.get("majors"))),
        str(data.get("headcount_text") or "").strip()[:100],
        str(data.get("compensation_text") or "").strip()[:300],
        str(data.get("application_method") or "").strip()[:1000],
        application_url,
        normalized_source_url,
        _domain(normalized_source_url),
        str(source_name or data.get("source_name") or "").strip()[:160],
        infer_source_level(normalized_source_url, data.get("source_level")),
        _normalize_datetime(published_at or data.get("published_at")),
        deadline_at,
        now,
        expires_at,
        "active",
        _safe_confidence(data.get("extraction_confidence")),
        str(data.get("verification_notes") or "").strip()[:1000],
        now,
        now,
    )
    conn.execute(
        """
        INSERT INTO blog_opportunities (
            post_id, employer_name, opportunity_type, positions_text, regions_json, city,
            target_groups_json, education_text, majors_json, headcount_text, compensation_text,
            application_method, application_url, source_url, source_domain, source_name,
            source_level, published_at, deadline_at, last_verified_at, expires_at, status,
            extraction_confidence, verification_notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(post_id) DO UPDATE SET
            employer_name = excluded.employer_name,
            opportunity_type = excluded.opportunity_type,
            positions_text = excluded.positions_text,
            regions_json = excluded.regions_json,
            city = excluded.city,
            target_groups_json = excluded.target_groups_json,
            education_text = excluded.education_text,
            majors_json = excluded.majors_json,
            headcount_text = excluded.headcount_text,
            compensation_text = excluded.compensation_text,
            application_method = excluded.application_method,
            application_url = excluded.application_url,
            source_url = excluded.source_url,
            source_domain = excluded.source_domain,
            source_name = excluded.source_name,
            source_level = excluded.source_level,
            published_at = excluded.published_at,
            deadline_at = excluded.deadline_at,
            last_verified_at = excluded.last_verified_at,
            expires_at = excluded.expires_at,
            status = excluded.status,
            extraction_confidence = excluded.extraction_confidence,
            verification_notes = excluded.verification_notes,
            updated_at = excluded.updated_at
        """,
        values,
    )
    return get_opportunity_for_post(conn, post_id) or {}


def refresh_opportunity_statuses(conn, *, now: datetime | None = None) -> int:
    check_time = (now or datetime.now()).isoformat(timespec="seconds")
    cursor = conn.execute(
        """
        UPDATE blog_opportunities
        SET status = 'expired', updated_at = ?
        WHERE status IN ('active', 'expiring')
          AND COALESCE(NULLIF(deadline_at, ''), NULLIF(expires_at, '')) IS NOT NULL
          AND COALESCE(NULLIF(deadline_at, ''), NULLIF(expires_at, '')) < ?
        """,
        (check_time, check_time),
    )
    warning_cutoff = ((now or datetime.now()) + timedelta(days=3)).isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE blog_opportunities
        SET status = 'expiring', updated_at = ?
        WHERE status = 'active'
          AND deadline_at IS NOT NULL AND deadline_at != ''
          AND deadline_at >= ? AND deadline_at <= ?
        """,
        (check_time, check_time, warning_cutoff),
    )
    return int(cursor.rowcount or 0)


def backfill_career_opportunities(conn, *, limit: int = 100) -> int:
    """Give legacy career posts a safe minimal record without inventing fields."""
    rows = conn.execute(
        """
        SELECT p.id, p.title, p.created_at
        FROM blog_posts p
        WHERE p.section_key = 'career'
          AND NOT EXISTS (SELECT 1 FROM blog_opportunities o WHERE o.post_id = p.id)
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT ?
        """,
        (max(1, min(int(limit or 100), 500)),),
    ).fetchall()
    for row in rows:
        source = conn.execute(
            """
            SELECT source_name, canonical_url, url, published_at
            FROM blog_news_crawler_items
            WHERE post_id = ?
            ORDER BY selected DESC, id ASC
            LIMIT 1
            """,
            (int(row["id"]),),
        ).fetchone()
        source_data = dict(source) if source else {}
        upsert_opportunity_for_post(
            conn,
            int(row["id"]),
            {
                "positions_text": str(row["title"] or ""),
                "verification_notes": "历史就业文章已纳入机会列表，具体要求和截止时间请核验原公告。",
                "extraction_confidence": 0,
            },
            source_url=str(source_data.get("canonical_url") or source_data.get("url") or ""),
            source_name=str(source_data.get("source_name") or ""),
            published_at=str(source_data.get("published_at") or row["created_at"] or ""),
        )
    return len(rows)


def notify_due_opportunity_deadlines(conn, *, now: datetime | None = None) -> int:
    check_time = now or datetime.now()
    now_iso = check_time.isoformat(timespec="seconds")
    cutoff_iso = (check_time + timedelta(days=3)).isoformat(timespec="seconds")
    rows = conn.execute(
        """
        SELECT o.*, p.title AS post_title,
               s.id AS state_id, s.user_identity, s.user_role, s.user_pk, s.state
        FROM blog_opportunity_user_states s
        JOIN blog_opportunities o ON o.id = s.opportunity_id
        JOIN blog_posts p ON p.id = o.post_id
        WHERE s.state IN ('saved', 'preparing', 'applied')
          AND (s.deadline_reminder_sent_at IS NULL OR s.deadline_reminder_sent_at = '')
          AND o.status IN ('active', 'expiring')
          AND o.deadline_at IS NOT NULL AND o.deadline_at != ''
          AND o.deadline_at >= ? AND o.deadline_at <= ?
        ORDER BY o.deadline_at ASC, s.id ASC
        """,
        (now_iso, cutoff_iso),
    ).fetchall()
    sent = 0
    for row in rows:
        data = dict(row)
        if not notify_opportunity_deadline(conn, data, data):
            continue
        conn.execute(
            "UPDATE blog_opportunity_user_states SET deadline_reminder_sent_at = ?, updated_at = ? WHERE id = ?",
            (now_iso, now_iso, int(data["state_id"])),
        )
        sent += 1
    return sent


def _deserialize_opportunity(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key in ("regions", "target_groups", "majors"):
        raw = data.pop(f"{key}_json", "[]")
        try:
            parsed = json.loads(str(raw or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = []
        data[key] = parsed if isinstance(parsed, list) else []
    data["id"] = int(data.get("id") or 0)
    data["post_id"] = int(data.get("post_id") or 0)
    data["extraction_confidence"] = float(data.get("extraction_confidence") or 0)
    data["source_level_label"] = {"A": "官方/公共服务来源", "B": "用人单位官方来源", "C": "转载线索"}.get(
        str(data.get("source_level") or "C"), "转载线索"
    )
    data["is_official"] = str(data.get("source_level") or "C") in {"A", "B"}
    deadline = _normalize_datetime(data.get("deadline_at"))
    data["deadline_at"] = deadline
    if deadline:
        try:
            data["deadline_days"] = (datetime.fromisoformat(deadline).date() - datetime.now().date()).days
        except ValueError:
            data["deadline_days"] = None
    else:
        data["deadline_days"] = None
    data["user_state"] = str(data.get("user_state") or "") or None
    data["user_notes"] = str(data.get("user_notes") or "")
    data["reminder_at"] = str(data.get("reminder_at") or "") or None
    return data


def get_opportunity_for_post(conn, post_id: int, *, user_identity: str = "") -> dict[str, Any] | None:
    params: list[Any] = [str(user_identity or ""), str(user_identity or ""), str(user_identity or ""), int(post_id)]
    row = conn.execute(
        """
        SELECT o.*,
               (SELECT state FROM blog_opportunity_user_states s
                WHERE s.opportunity_id = o.id AND s.user_identity = ? LIMIT 1) AS user_state,
               (SELECT notes FROM blog_opportunity_user_states s
                WHERE s.opportunity_id = o.id AND s.user_identity = ? LIMIT 1) AS user_notes,
               (SELECT reminder_at FROM blog_opportunity_user_states s
                WHERE s.opportunity_id = o.id AND s.user_identity = ? LIMIT 1) AS reminder_at
        FROM blog_opportunities o
        WHERE o.post_id = ?
        LIMIT 1
        """,
        params,
    ).fetchone()
    return _deserialize_opportunity(row) if row else None


def list_opportunities(
    conn,
    user: dict,
    *,
    page: int = 1,
    limit: int = 20,
    region: str = "",
    opportunity_type: str = "",
    deadline_days: int | None = None,
    query: str = "",
    user_state: str = "",
    sort: str = "latest",
) -> dict[str, Any]:
    backfill_career_opportunities(conn)
    refresh_opportunity_statuses(conn)
    user_pk, _role, identity = blog_service._ensure_identity(user)
    visibility_sql, visibility_params = blog_service._build_post_visibility_sql(
        user,
        viewer_identity=identity,
        viewer_user_pk=user_pk,
        table_alias="p",
    )
    conditions = [f"({visibility_sql})", "p.status = ?", "p.section_key = 'career'", "o.status IN ('active', 'expiring')"]
    params: list[Any] = [*visibility_params, blog_service.POST_STATUS_PUBLISHED]
    if opportunity_type and opportunity_type in OPPORTUNITY_TYPES:
        conditions.append("o.opportunity_type = ?")
        params.append(opportunity_type)
    if region in REGION_ALIASES:
        region_terms = REGION_ALIASES[region]
        conditions.append("(" + " OR ".join("(o.city LIKE ? OR o.regions_json LIKE ?)" for _ in region_terms) + ")")
        for term in region_terms:
            params.extend((f"%{term}%", f"%{term}%"))
    if deadline_days is not None and 0 < deadline_days <= 90:
        deadline_limit = (datetime.now() + timedelta(days=deadline_days)).isoformat(timespec="seconds")
        conditions.append("o.deadline_at IS NOT NULL AND o.deadline_at != '' AND o.deadline_at <= ?")
        params.append(deadline_limit)
    normalized_query = str(query or "").strip()
    if normalized_query:
        like_query = f"%{normalized_query}%"
        conditions.append("(p.title LIKE ? OR o.employer_name LIKE ? OR o.positions_text LIKE ? OR o.majors_json LIKE ?)")
        params.extend((like_query, like_query, like_query, like_query))
    if user_state in OPPORTUNITY_STATES:
        conditions.append(
            "EXISTS (SELECT 1 FROM blog_opportunity_user_states us "
            "WHERE us.opportunity_id = o.id AND us.user_identity = ? AND us.state = ?)"
        )
        params.extend((identity, user_state))

    where_sql = " AND ".join(conditions)
    if sort == "hot":
        order_sql = f"{blog_service._trending_order_expression('p')} DESC, o.deadline_at ASC, p.id DESC"
    elif sort == "featured":
        order_sql = "p.is_featured DESC, o.deadline_at ASC, p.created_at DESC, p.id DESC"
    else:
        order_sql = (
            "CASE WHEN o.status = 'expiring' THEN 0 ELSE 1 END, "
            "CASE WHEN o.deadline_at IS NULL OR o.deadline_at = '' THEN 1 ELSE 0 END, "
            "o.deadline_at ASC, p.created_at DESC, p.id DESC"
        )
    offset = max(page - 1, 0) * limit
    total = int(conn.execute(
        f"SELECT COUNT(*) AS total FROM blog_opportunities o JOIN blog_posts p ON p.id = o.post_id WHERE {where_sql}",
        params,
    ).fetchone()["total"])
    rows = conn.execute(
        f"""
        SELECT p.*,
               LENGTH(p.content_md) AS content_length,
               EXISTS(SELECT 1 FROM blog_likes bl WHERE bl.target_type = 'post' AND bl.target_id = p.id AND bl.user_identity = ?) AS is_liked,
               EXISTS(SELECT 1 FROM blog_bookmarks bb WHERE bb.post_id = p.id AND bb.user_identity = ?) AS is_bookmarked,
               o.id AS opportunity_id
        FROM blog_opportunities o
        JOIN blog_posts p ON p.id = o.post_id
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """,
        [identity, identity, *params, limit, offset],
    ).fetchall()
    row_items = [dict(row) for row in rows]
    badge_map = blog_service._build_author_cultivation_badge_map(conn, row_items)
    posts = []
    for row in row_items:
        post = blog_service._serialize_post_summary(row, viewer_identity=identity, cultivation_badge_map=badge_map)
        post["opportunity"] = get_opportunity_for_post(conn, int(row["id"]), user_identity=identity)
        posts.append(post)
    return {
        "posts": posts,
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": offset + limit < total,
    }


def set_opportunity_user_state(
    conn,
    user: dict,
    opportunity_id: int,
    *,
    state: str,
    reminder_at: Any = None,
    notes: Any = "",
) -> dict[str, Any]:
    user_pk, role, identity = blog_service._ensure_identity(user)
    opportunity = conn.execute("SELECT id, post_id FROM blog_opportunities WHERE id = ?", (opportunity_id,)).fetchone()
    if opportunity is None:
        raise ValueError("就业机会不存在")
    normalized_state = str(state or "").strip().lower()
    if normalized_state in {"", "none", "remove"}:
        conn.execute(
            "DELETE FROM blog_opportunity_user_states WHERE opportunity_id = ? AND user_identity = ?",
            (opportunity_id, identity),
        )
        return {"opportunity_id": opportunity_id, "state": None}
    if normalized_state not in OPPORTUNITY_STATES:
        raise ValueError("求职进度状态不正确")
    normalized_reminder = _normalize_datetime(reminder_at)
    normalized_notes = str(notes or "").strip()[:2000]
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO blog_opportunity_user_states (
            opportunity_id, user_identity, user_role, user_pk, state, reminder_at, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(opportunity_id, user_identity) DO UPDATE SET
            state = excluded.state,
            reminder_at = excluded.reminder_at,
            notes = excluded.notes,
            updated_at = excluded.updated_at
        """,
        (opportunity_id, identity, role, user_pk, normalized_state, normalized_reminder, normalized_notes, now, now),
    )
    return {
        "opportunity_id": opportunity_id,
        "state": normalized_state,
        "reminder_at": normalized_reminder,
        "notes": normalized_notes,
    }
