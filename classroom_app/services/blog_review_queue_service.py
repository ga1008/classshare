"""Blog review desk queue (plan §5.5).

One queue, grouped by where an item came from. An item leaves its tab when a
moderator acts on it: their identity replaces the ``system:*`` / emergency
marker in ``moderated_by`` (or the report / appeal is settled).
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from . import blog_moderation_states as states
from . import blog_persona_service as persona_service
from .blog_moderation_service import SANCTION_ACTIONS, require_moderator

QUEUE_LIMIT = 50
EXCERPT_LENGTH = 220
TABS: dict[str, str] = {
    "resubmitted": "修改待复核",
    "machine": "自动审核",
    "local": "本地规则",
    "reports": "举报",
    "report_limited": "多人举报暂限",
    "emergency": "紧急限制",
    "appeals": "申诉",
}
REPORT_REASON_LABELS = {
    "false_information": "信息不实或已失效",
    "job_scam": "疑似招聘诈骗",
    "spam": "广告或垃圾内容",
    "abuse": "攻击、骚扰或不友善",
    "privacy": "泄露个人隐私",
    "privacy_identity": "暴露了我的身份或个人信息",
    "other": "其他问题",
}
_MACHINE = (states.BY_AI, states.BY_TIMEOUT, states.BY_QUOTA)


def _literal(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


# SQL conditions per state tab (constants only); the alias is "t".
_STATE_TAB_SQL = {
    "machine": f"t.moderation_state IN ('{states.STATE_FLAGGED}', '{states.STATE_REMOVED}') AND t.moderated_by IN ({_literal(_MACHINE)})",
    "resubmitted": f"t.moderation_state = '{states.STATE_PENDING}' AND t.moderated_by = '{states.BY_RESUBMIT}'",
    "local": f"t.moderation_state IN ('{states.STATE_PENDING}', '{states.STATE_FLAGGED}') AND t.moderated_by = '{states.BY_LOCAL_RULES}'",
    "report_limited": f"t.moderation_state = '{states.STATE_LIMITED}' AND t.moderated_by = '{states.BY_REPORTS}'",
    "emergency": f"t.moderation_state = '{states.STATE_LIMITED}' AND t.moderated_by LIKE '{states.EMERGENCY_PREFIX}%'",
}
_LIVE = {"post": "t.status = 'published'", "comment": "t.status = 'active'"}
_TABLES = {"post": "blog_posts", "comment": "blog_comments"}


def queue_counts(conn, user: dict) -> dict[str, int]:
    require_moderator(conn, user)
    counts = {}
    for tab, condition in _STATE_TAB_SQL.items():
        total = 0
        for target_type, table in _TABLES.items():
            row = conn.execute(f"SELECT COUNT(*) AS total FROM {table} t WHERE {_LIVE[target_type]} AND {condition}").fetchone()
            total += int(row["total"] or 0)
        counts[tab] = total
    counts["reports"] = int(conn.execute(
        "SELECT COUNT(*) AS total FROM (SELECT target_type, target_id FROM blog_reports "
        "WHERE status = 'pending' AND target_type IN ('post', 'comment') GROUP BY target_type, target_id) grouped"
    ).fetchone()["total"] or 0)
    counts["appeals"] = int(conn.execute("SELECT COUNT(*) AS total FROM blog_appeals WHERE status = 'pending'").fetchone()["total"] or 0)
    return counts


def list_queue(conn, user: dict, tab: str) -> dict[str, Any]:
    require_moderator(conn, user)
    if tab not in TABS:
        raise ValueError("未知的审核分组")
    if tab in _STATE_TAB_SQL:
        refs = _state_tab_refs(conn, tab)
    elif tab == "reports":
        refs = [(row["target_type"], int(row["target_id"])) for row in conn.execute(
            """
            SELECT target_type, target_id,
                   MAX(CASE WHEN reason_code = 'privacy_identity' THEN 1 ELSE 0 END) AS identity_exposed,
                   COUNT(DISTINCT reporter_identity) AS reporters, MIN(created_at) AS first_at
            FROM blog_reports
            WHERE status = 'pending' AND target_type IN ('post', 'comment')
            GROUP BY target_type, target_id
            ORDER BY identity_exposed DESC, reporters DESC, first_at ASC
            LIMIT ?
            """,
            (QUEUE_LIMIT,),
        ).fetchall()]
    else:
        refs = [("post", int(row["target_id"])) for row in conn.execute(
            "SELECT target_id FROM blog_appeals WHERE status = 'pending' ORDER BY created_at ASC, id ASC LIMIT ?",
            (QUEUE_LIMIT,),
        ).fetchall()]
    items = [item for item in (_build_item(conn, target_type, target_id) for target_type, target_id in refs) if item]
    return {"tab": tab, "label": TABS[tab], "items": items, "counts": queue_counts(conn, user)}


def _state_tab_refs(conn, tab: str) -> list[tuple[str, int]]:
    rows: list[tuple[str, int, str]] = []
    for target_type, table in _TABLES.items():
        for row in conn.execute(
            f"SELECT t.id, t.moderated_at FROM {table} t WHERE {_LIVE[target_type]} AND {_STATE_TAB_SQL[tab]} "
            "ORDER BY t.moderated_at ASC, t.id ASC LIMIT ?",
            (QUEUE_LIMIT,),
        ).fetchall():
            rows.append((target_type, int(row["id"]), str(row["moderated_at"] or "")))
    rows.sort(key=lambda item: item[2])
    return [(target_type, target_id) for target_type, target_id, _ in rows[:QUEUE_LIMIT]]


def _excerpt(markdown: Any) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "[图片]", str(markdown or ""))
    text = re.sub(r"[#>*_`~\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:EXCERPT_LENGTH] + ("…" if len(text) > EXCERPT_LENGTH else "")


def _load(conn, target_type: str, target_id: int) -> Optional[dict]:
    if target_type == "post":
        row = conn.execute("SELECT *, id AS post_id FROM blog_posts WHERE id = ?", (target_id,)).fetchone()
    else:
        row = conn.execute(
            "SELECT c.*, p.title AS post_title, p.author_identity AS post_author_identity, "
            "p.author_display_mode AS post_display_mode FROM blog_comments c JOIN blog_posts p ON p.id = c.post_id "
            "WHERE c.id = ?", (target_id,),
        ).fetchone()
    return dict(row) if row else None


def _author(conn, target_type: str, row: dict) -> dict[str, Any]:
    identity = str(row.get("author_identity") or "")
    mode = str(row.get("author_display_mode") or "real_name")
    snapshot = str((row.get("author_display_snapshot") if target_type == "comment" and mode == "anonymous" else None)
                   or row.get("author_display_name") or "")
    persona = persona_service.resolve(
        actor_identity=identity, actor_role=str(row.get("author_role") or ""), actor_user_pk=row.get("author_user_pk"),
        display_mode=mode, snapshot_name=snapshot, profiles=persona_service.load_profiles(conn, [identity]),
        post_author_identity=str(row.get("post_author_identity") or row.get("author_identity") or ""),
        post_display_mode=str(row.get("post_display_mode") or ""), is_comment=target_type == "comment",
    )
    # The desk never shows who is behind a face: that is the audited reveal.
    return {key: persona[key] for key in ("kind", "display_name", "role_label", "avatar_url", "avatar_kind", "identity")}


def _author_history(conn, identity: str) -> int:
    if not identity:
        return 0
    placeholders = ", ".join("?" for _ in SANCTION_ACTIONS)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS total FROM blog_moderation_logs l
        WHERE l.action IN ({placeholders}) AND (
            (l.target_type = 'post' AND l.target_id IN (SELECT id FROM blog_posts WHERE author_identity = ?))
            OR (l.target_type = 'comment' AND l.target_id IN (SELECT id FROM blog_comments WHERE author_identity = ?))
        )
        """,
        (*SANCTION_ACTIONS, identity, identity),
    ).fetchone()
    return int(row["total"] or 0)


def _ai_verdict(conn, target_type: str, row: dict) -> Optional[dict]:
    receipt = conn.execute(
        "SELECT status, risk, categories_json, evidence, error_message FROM blog_content_reviews "
        "WHERE target_type = ? AND target_id = ? AND content_revision = ? ORDER BY id DESC LIMIT 1",
        (target_type, int(row["id"]), int(row.get("content_revision") or 1)),
    ).fetchone()
    if receipt is None:
        return None
    try:
        categories = json.loads(receipt["categories_json"] or "[]")
    except (TypeError, ValueError):
        categories = []
    return {
        "status": str(receipt["status"] or ""),
        "risk": str(receipt["risk"] or ""),
        "categories": [states.reason_label(code) or code for code in categories if isinstance(code, str)],
        "evidence": str(receipt["evidence"] or ""),
        "failed": str(receipt["status"] or "") in {"failed", "timeout"},
    }


def _reports(conn, target_type: str, target_id: int) -> dict[str, Any]:
    rows = [dict(row) for row in conn.execute(
        "SELECT reason_code, details, reporter_identity, reporter_role, reporter_user_pk, created_at FROM blog_reports "
        "WHERE target_type = ? AND target_id = ? AND status = 'pending' ORDER BY id",
        (target_type, target_id),
    ).fetchall()]
    reasons: dict[str, int] = {}
    for row in rows:
        code = str(row["reason_code"] or "other")
        reasons[code] = reasons.get(code, 0) + 1
    return {
        "count": len({row["reporter_identity"] for row in rows}),
        "reasons": [{"code": code, "label": REPORT_REASON_LABELS.get(code, code), "count": count}
                    for code, count in sorted(reasons.items(), key=lambda pair: -pair[1])],
        "identity_exposed": "privacy_identity" in reasons,
        "details": [str(row["details"])[:300] for row in rows if str(row["details"] or "").strip()][:5],
        # Moderators see reporters by real name (plan §5.4).
        "reporters": _reporter_names(conn, rows)[:10],
    }


def _reporter_names(conn, rows: list[dict]) -> list[str]:
    names: list[str] = []
    for row in rows:
        table = {"student": "students", "teacher": "teachers"}.get(str(row.get("reporter_role") or ""))
        if not table or row.get("reporter_user_pk") is None:
            continue
        found = conn.execute(f"SELECT name FROM {table} WHERE id = ?", (int(row["reporter_user_pk"]),)).fetchone()
        name = str(found["name"] or "") if found else ""
        if name and name not in names:
            names.append(name)
    return names


def _appeal(conn, target_id: int) -> Optional[dict]:
    row = conn.execute(
        "SELECT id, reason, created_at FROM blog_appeals WHERE target_type = 'post' AND target_id = ? AND status = 'pending' "
        "ORDER BY id DESC LIMIT 1", (target_id,),
    ).fetchone()
    return dict(row) if row else None


def _source_label(moderated_by: str) -> str:
    if moderated_by.startswith(states.EMERGENCY_PREFIX):
        return "教师紧急限制"
    return {states.BY_AI: "AI 审核", states.BY_TIMEOUT: "审核超时", states.BY_QUOTA: "AI 额度用尽",
            states.BY_LOCAL_RULES: "本地规则", states.BY_REPORTS: "多人举报",
            states.BY_RESUBMIT: "作者修改后重新提交"}.get(moderated_by, "")


def _build_item(conn, target_type: str, target_id: int) -> Optional[dict[str, Any]]:
    row = _load(conn, target_type, target_id)
    if row is None:
        return None
    state = str(row.get("moderation_state") or states.STATE_NONE)
    post_id = int(row.get("post_id") or row["id"])
    author = _author(conn, target_type, row)
    return {
        "target_type": target_type,
        "target_id": int(row["id"]),
        "post_id": post_id,
        "title": str((row.get("title") if target_type == "post" else row.get("post_title")) or ""),
        "excerpt": _excerpt(row.get("content_md")),
        "state": state,
        "state_label": states.STATE_LABELS.get(state, state),
        "reason_label": states.reason_label(row.get("moderation_reason_code")),
        "note": str(row.get("moderation_note") or ""),
        "source_label": _source_label(str(row.get("moderated_by") or "")),
        "moderated_at": str(row.get("moderated_at") or ""),
        "created_at": str(row.get("created_at") or ""),
        "link_url": f"/blog?post={post_id}",
        "author": {key: value for key, value in author.items() if key != "identity"},
        "can_reveal": author["kind"] in {"pen", "anon", "op_anon"},
        "author_history": _author_history(conn, str(row.get("author_identity") or "")),
        "ai": _ai_verdict(conn, target_type, row),
        "reports": _reports(conn, target_type, int(row["id"])),
        "appeal": _appeal(conn, int(row["id"])) if target_type == "post" else None,
        "is_live": str(row.get("status") or "") in {"published", "active"},
    }
