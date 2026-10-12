"""Blog moderation by people (plan §4, §5.4–§5.6).

Moderator rights (D7), limit / remove / restore / confirm actions, reports
that sanction content, appeals (posts only, D6), identity reveal and the audit
log. The AI review (``blog_safety_review``) shares ``log_action`` and
``notify_author_of_outcome`` so every outcome reads the same to the author.

Every action writes ``blog_moderation_logs``; nothing here commits — the
router owns the transaction.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Optional

from . import blog_moderation_states as states
from .blog_notifications import notify_blog_moderation
from .resource_access_service import is_super_admin_teacher

TARGET_TYPES = ("post", "comment")
SANCTION_ACTIONS = ("limit", "remove", "emergency_limit", "ai_remove", "report_limit")
ACTION_STATE = {
    "limit": states.STATE_LIMITED, "emergency_limit": states.STATE_LIMITED, "report_limit": states.STATE_LIMITED,
    "remove": states.STATE_REMOVED, "ai_remove": states.STATE_REMOVED,
}
REPORT_REASON_TO_CODE = {
    "false_information": "misinformation", "job_scam": "illegal", "privacy_identity": "privacy",
    "spam": "spam", "abuse": "abuse", "privacy": "privacy", "other": "other",
}
REVEAL_REASONS = {"report": "处理举报", "safety": "安全风险", "discipline": "学业纪律", "other": "其他"}
# States a moderator can lift back to "passed" (not already-public content).
RESTORABLE_STATES = (states.STATE_PENDING, states.STATE_FLAGGED, *states.SANCTIONED_STATES)
# "Confirm" keeps a settled decision and takes it off the desk; a pending or
# appealed item still needs a real decision, so it cannot be confirmed.
CONFIRMABLE_STATES = (states.STATE_FLAGGED, states.STATE_LIMITED, states.STATE_REMOVED)
MAX_APPEAL_LENGTH = 300
MAX_NOTE_LENGTH = 300
AUDIT_PAGE_SIZE = 50


class ModerationError(ValueError):
    """A moderation request that cannot apply to the item's current state."""


def _now() -> str:
    return datetime.now().isoformat()


def report_auto_limit_threshold() -> int:
    try:
        return max(int(os.getenv("BLOG_REPORT_AUTO_LIMIT", "3")), 1)
    except ValueError:
        return 3


def _identity(user: dict) -> tuple[str, int]:
    role = str(user.get("role") or "").strip().lower()
    try:
        user_pk = int(user.get("id"))
    except (TypeError, ValueError):
        raise PermissionError("无效的用户")
    return f"{role}:{user_pk}", user_pk


def is_super_admin(conn, user: dict) -> bool:
    return str(user.get("role") or "").lower() == "teacher" and is_super_admin_teacher(conn, user.get("id"))


def is_blog_moderator(conn, user: dict) -> bool:
    """D7: super admins and teachers granted blog moderation."""
    if str(user.get("role") or "").lower() != "teacher":
        return False
    if is_super_admin_teacher(conn, user.get("id")):
        return True
    row = conn.execute(
        "SELECT 1 FROM blog_moderators m JOIN teachers t ON t.id = m.teacher_id "
        "WHERE m.teacher_id = ? AND m.revoked_at IS NULL AND COALESCE(t.is_active, 1) = 1 LIMIT 1",
        (int(user.get("id") or 0),),
    ).fetchone()
    return row is not None


def require_moderator(conn, user: dict) -> None:
    if not is_blog_moderator(conn, user):
        raise PermissionError("只有博客管理员可以执行此操作")


def require_super_admin(conn, user: dict) -> None:
    if not is_super_admin(conn, user):
        raise PermissionError("只有超级管理员可以执行此操作")


# --- moderators -------------------------------------------------------------

def list_moderators(conn, user: dict) -> list[dict[str, Any]]:
    require_super_admin(conn, user)
    rows = conn.execute(
        "SELECT m.teacher_id, m.granted_by, m.created_at, t.name FROM blog_moderators m "
        "JOIN teachers t ON t.id = m.teacher_id WHERE m.revoked_at IS NULL ORDER BY m.created_at, m.id"
    ).fetchall()
    return [dict(row) for row in rows]


def set_moderator(conn, user: dict, teacher_id: Any, *, granted: bool) -> dict[str, Any]:
    require_super_admin(conn, user)
    try:
        teacher_pk = int(teacher_id)
    except (TypeError, ValueError):
        raise ModerationError("教师不存在")
    if conn.execute("SELECT 1 FROM teachers WHERE id = ? LIMIT 1", (teacher_pk,)).fetchone() is None:
        raise ModerationError("教师不存在")
    actor, _ = _identity(user)
    now = _now()
    if granted:
        conn.execute(
            "UPDATE blog_moderators SET granted_by = ?, created_at = ?, revoked_by = '', revoked_at = NULL "
            "WHERE teacher_id = ? AND revoked_at IS NOT NULL",
            (actor, now, teacher_pk),
        )
        conn.execute("SAVEPOINT blog_grant_moderator")
        try:
            conn.execute(
                "INSERT INTO blog_moderators (teacher_id, granted_by, created_at) "
                "SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM blog_moderators WHERE teacher_id = ?)",
                (teacher_pk, actor, now, teacher_pk),
            )
        except Exception as exc:  # noqa: BLE001 - a concurrent grant already won
            conn.execute("ROLLBACK TO SAVEPOINT blog_grant_moderator")
            if not any(cls.__name__ in {"IntegrityError", "UniqueViolation"} for cls in type(exc).__mro__):
                raise
        conn.execute("RELEASE SAVEPOINT blog_grant_moderator")
    else:
        # The row stays as the grant's record (moderation logs are per post).
        conn.execute(
            "UPDATE blog_moderators SET revoked_by = ?, revoked_at = ? WHERE teacher_id = ? AND revoked_at IS NULL",
            (actor, now, teacher_pk),
        )
    return {"teacher_id": teacher_pk, "granted": bool(granted)}


# --- targets, logs, notifications ---------------------------------------------

def _table(target_type: str) -> str:
    if target_type not in TARGET_TYPES:
        raise ModerationError("处理对象不正确")
    return "blog_posts" if target_type == "post" else "blog_comments"


def load_target(conn, target_type: str, target_id: Any) -> dict[str, Any]:
    _table(target_type)
    try:
        normalized_id = int(target_id)
    except (TypeError, ValueError):
        raise ModerationError("内容不存在")
    if target_type == "post":
        row = conn.execute("SELECT *, id AS post_id FROM blog_posts WHERE id = ?", (normalized_id,)).fetchone()
    else:
        row = conn.execute(
            "SELECT c.*, p.title AS post_title, p.author_identity AS post_author_identity, "
            "p.author_display_mode AS post_display_mode FROM blog_comments c "
            "JOIN blog_posts p ON p.id = c.post_id WHERE c.id = ?",
            (normalized_id,),
        ).fetchone()
    if row is None:
        raise ModerationError("内容不存在")
    return dict(row)


def _persona_kind(target: dict) -> str:
    mode = str(target.get("author_display_mode") or "real_name")
    return {"nickname": "pen", "anonymous": "anon"}.get(mode, "real")


def _log(conn, *, actor: str, actor_role: str, actor_pk: Optional[int], target_type: str, target_id: int,
         post_id: Optional[int], action: str, reason_code: str = "", note: str = "",
         persona_kind: str = "", extra: Optional[dict] = None) -> int:
    from ..db.connection import execute_insert_returning_id

    return execute_insert_returning_id(
        conn,
        """
        INSERT INTO blog_moderation_logs (
            post_id, moderator_identity, moderator_role, moderator_user_pk, action, reason,
            created_at, target_type, target_id, reason_code, note, target_persona_kind, extra_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        # Legacy NOT NULL column: automatic (system:*) actions record 0.
        (post_id, actor, actor_role, actor_pk if actor_pk is not None else 0, action, note, _now(), target_type, int(target_id),
         reason_code, note, persona_kind, json.dumps(extra or {}, ensure_ascii=False)),
    )


def log_action(conn, *, actor: Any, target_type: str, target: dict, action: str,
               reason_code: str = "", note: str = "", extra: Optional[dict] = None) -> int:
    """``actor`` is a user dict, or a ``system:*`` string for automatic actions."""
    if isinstance(actor, dict):
        identity, actor_pk = _identity(actor)
        actor_role = str(actor.get("role") or "")
    else:
        identity, actor_pk, actor_role = str(actor), None, "system"
    return _log(conn, actor=identity, actor_role=actor_role, actor_pk=actor_pk, target_type=target_type,
                target_id=int(target["id"]), post_id=int(target.get("post_id") or target["id"]),
                action=action, reason_code=reason_code, note=str(note or "")[:MAX_NOTE_LENGTH],
                persona_kind=_persona_kind(target), extra=extra)


_OUTCOME_TITLES = {
    "removed": "{noun}已被移除",
    "limited": "{noun}暂时仅你可见",
    "report_limited": "{noun}收到多次举报，暂时仅你可见",
    "restored": "{noun}已恢复公开",
    "passed": "你的帖子已通过内容检查",
    "appeal_accepted": "申诉已通过，{noun}已恢复公开",
    "appeal_rejected": "申诉未通过",
}


def notify_author_of_outcome(conn, *, target_type: str, target: dict, outcome: str,
                             reason_code: str = "", note: str = "") -> bool:
    noun = "你的帖子" if target_type == "post" else "你的评论"
    parts = []
    if reason_code:
        parts.append(f"原因：{states.reason_label(reason_code) or '其他'}。")
    if note:
        parts.append(str(note).rstrip("。") + "。")
    if outcome == "report_limited":
        parts.append("管理员将尽快处理。")
    if target_type == "post" and outcome in {"removed", "limited", "report_limited"}:
        parts.append("修改后会重新检查；如有异议可在帖子页申诉。")
    if outcome == "passed":
        parts.append("现在大家都能看到了。")
    return notify_blog_moderation(
        conn, recipient_role=target.get("author_role"), recipient_pk=target.get("author_user_pk"),
        post_id=int(target.get("post_id") or target["id"]), title=_OUTCOME_TITLES[outcome].format(noun=noun),
        body="".join(parts), comment_id=int(target["id"]) if target_type == "comment" else None,
    )


# --- reports ---------------------------------------------------------------

def _pending_reports(conn, target_type: str, target_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM blog_reports WHERE target_type = ? AND target_id = ? AND status = 'pending' ORDER BY id",
        (target_type, int(target_id)),
    ).fetchall()
    return [dict(row) for row in rows]


def settle_reports(conn, user_identity: str, target_type: str, target: dict, *, actioned: bool, note: str = "") -> int:
    """Close every pending report on the item and tell each reporter the result."""
    reports = _pending_reports(conn, target_type, int(target["id"]))
    status = "resolved" if actioned else "dismissed"
    for report in reports:
        # The table keeps one row per (reporter, status): an older settled
        # report from the same person is replaced by this one.
        conn.execute(
            "DELETE FROM blog_reports WHERE target_type = ? AND target_id = ? AND reporter_identity = ? AND status = ?",
            (target_type, int(target["id"]), report["reporter_identity"], status),
        )
        conn.execute(
            "UPDATE blog_reports SET status = ?, resolved_by_identity = ?, resolution_notes = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, user_identity, str(note or "")[:2000], int(report["id"])),
        )
        notify_blog_moderation(
            conn, recipient_role=report["reporter_role"], recipient_pk=report["reporter_user_pk"],
            post_id=int(target.get("post_id") or target["id"]),
            title="你的反馈已处理",
            body="经核查，相关内容已处理。感谢你的反馈。" if actioned else "经核查，暂未发现违规。感谢你的反馈。",
            comment_id=int(target["id"]) if target_type == "comment" else None,
        )
    return len(reports)


def apply_report_threshold(conn, target_type: str, target_id: int) -> bool:
    """≥N different reporters on public content → limited for now (plan §5.4)."""
    if target_type not in TARGET_TYPES:
        return False
    reports = _pending_reports(conn, target_type, target_id)
    if len({report["reporter_identity"] for report in reports}) < report_auto_limit_threshold():
        return False
    target = load_target(conn, target_type, target_id)
    if not states.is_public_state(target.get("moderation_state")):
        return False
    reasons = [REPORT_REASON_TO_CODE.get(str(report["reason_code"]), "other") for report in reports]
    reason_code = max(sorted(set(reasons)), key=reasons.count)
    if not _write_state(conn, target_type, target, states.STATE_LIMITED, states.BY_REPORTS, reason_code, ""):
        return False
    log_action(conn, actor=states.BY_REPORTS, target_type=target_type, target=target,
               action="report_limit", reason_code=reason_code, note=f"{len(reports)} 人举报")
    notify_author_of_outcome(conn, target_type=target_type, target=target, outcome="report_limited",
                             reason_code=reason_code)
    return True


# --- actions ---------------------------------------------------------------

def _write_state(conn, target_type: str, target: dict, state: str, by: str, reason_code: str, note: str) -> bool:
    """Compare-and-set on the state the caller read, so two moderators never both win."""
    expected = str(target.get("moderation_state") or states.STATE_NONE)
    cursor = conn.execute(
        f"UPDATE {_table(target_type)} SET moderation_state = ?, moderated_by = ?, moderated_at = ?, "
        "moderation_reason_code = ?, moderation_note = ? WHERE id = ? AND COALESCE(moderation_state, 'none') = ?",
        (state, by, _now(), reason_code, str(note or "")[:MAX_NOTE_LENGTH], int(target["id"]), expected),
    )
    return int(cursor.rowcount or 0) == 1


def _require_live(target_type: str, target: dict) -> None:
    live = "published" if target_type == "post" else "active"
    if str(target.get("status") or "") != live:
        raise ModerationError("只能处理已发布的内容")


def _open_appeal(conn, target_type: str, target_id: int) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM blog_appeals WHERE target_type = ? AND target_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
        (target_type, int(target_id)),
    ).fetchone()
    return dict(row) if row else None


def _close_appeal(conn, appeal: Optional[dict], actor: str, decision: str, note: str) -> None:
    if appeal:
        conn.execute(
            "UPDATE blog_appeals SET status = 'decided', decided_by = ?, decision = ?, decision_note = ?, decided_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (actor, decision, str(note or "")[:MAX_NOTE_LENGTH], _now(), int(appeal["id"])),
        )


def _plan_action(action: str, *, moderator: bool, actor: str, current: str, code: str, text: str,
                 target: dict) -> tuple[str, str, str, str, str]:
    """(new state, moderated_by, log action, reason code, outcome) for a person's action."""
    if action in {"limit", "remove"}:
        if not code:
            raise ModerationError("请选择处理原因")
        if action == "remove" and not moderator:
            raise PermissionError("只有博客管理员可以移除内容")
        if not moderator and not states.is_public_state(current):
            # An emergency limit only stops public content; anything already
            # under review, sanctioned or appealed belongs to a moderator.
            raise PermissionError("这条内容已在处理中，请联系博客管理员")
        if current == ACTION_STATE[action]:
            raise ModerationError("内容已经是这个状态")
        by = actor if moderator else f"{states.EMERGENCY_PREFIX}{actor}"
        log_name = action if moderator else "emergency_limit"
        return ACTION_STATE[action], by, log_name, code, "removed" if action == "remove" else "limited"
    if not moderator:
        raise PermissionError("只有博客管理员可以执行此操作")
    if action == "restore":
        if not text:
            raise ModerationError("恢复内容请填写一句说明")
        if current not in RESTORABLE_STATES:
            raise ModerationError("内容当前已公开")
        return states.STATE_PASSED, actor, "restore", "", "restored"
    if action == "confirm":
        if current not in CONFIRMABLE_STATES:
            raise ModerationError("这条内容需要明确处理（恢复、限制或移除），不能只确认")
        return current, actor, "confirm", code or str(target.get("moderation_reason_code") or ""), ""
    raise ModerationError("不支持的处理动作")


def moderate(conn, user: dict, target_type: str, target_id: Any, *, action: str,
             reason_code: Any = "", note: Any = "") -> dict[str, Any]:
    """limit / remove / restore / confirm by a person.

    Any teacher may *limit* published content (emergency, queued for a
    moderator); everything else needs a blog moderator (D7). ``confirm`` keeps
    the current state and takes it off the review desk.
    """
    if str(user.get("role") or "").lower() != "teacher":
        raise PermissionError("只有教师可以处理博客内容")
    actor, _ = _identity(user)
    moderator = is_blog_moderator(conn, user)
    action = str(action or "").strip().lower()
    code = states.normalize_reason_code(reason_code)
    text = str(note or "").strip()[:MAX_NOTE_LENGTH]
    target = load_target(conn, target_type, target_id)
    _require_live(target_type, target)
    current = str(target.get("moderation_state") or states.STATE_NONE)
    state, by, log_name, code, outcome = _plan_action(
        action, moderator=moderator, actor=actor, current=current, code=code, text=text, target=target,
    )
    stored_note = text or (str(target.get("moderation_note") or "") if action == "confirm" else "")
    if not _write_state(conn, target_type, target, state, by, code, stored_note):
        raise ModerationError("内容状态刚刚发生了变化，请刷新后再处理")
    log_action(conn, actor=user, target_type=target_type, target=target, action=log_name, reason_code=code, note=text)
    appeal = _open_appeal(conn, target_type, int(target["id"]))
    _close_appeal(conn, appeal, actor, "accepted" if state == states.STATE_PASSED else "rejected", text)
    settle_reports(conn, actor, target_type, target, actioned=state in states.SANCTIONED_STATES, note=text)
    if target_type == "comment" and current == states.STATE_PENDING and states.is_public_state(state):
        from .blog_service import notify_released_comment

        notify_released_comment(conn, int(target["id"]))
    if appeal:
        outcome = "appeal_accepted" if state == states.STATE_PASSED else "appeal_rejected"
    if outcome and (state != current or appeal):
        notify_author_of_outcome(conn, target_type=target_type, target=target, outcome=outcome,
                                 reason_code=code if state in states.SANCTIONED_STATES else "", note=text)
    return {"target_type": target_type, "target_id": int(target["id"]), "moderation_state": state,
            "emergency": log_name == "emergency_limit"}


def resolve_reports(conn, user: dict, target_type: str, target_id: Any, *, decision: str,
                    reason_code: Any = "", note: Any = "") -> dict[str, Any]:
    """Review-desk handling of the reports on one item: dismiss, limit or remove."""
    require_moderator(conn, user)
    decision = str(decision or "").strip().lower()
    if decision in {"limit", "remove"}:
        return moderate(conn, user, target_type, target_id, action=decision, reason_code=reason_code, note=note)
    if decision != "dismiss":
        raise ModerationError("不支持的处理方式")
    actor, _ = _identity(user)
    target = load_target(conn, target_type, target_id)
    text = str(note or "").strip()[:MAX_NOTE_LENGTH]
    restored = False
    if str(target.get("moderated_by") or "") == states.BY_REPORTS and target.get("moderation_state") == states.STATE_LIMITED:
        # The automatic report limit is lifted together with the reports.
        restored = _write_state(conn, target_type, target, states.STATE_PASSED, actor, "", text)
        if restored:
            notify_author_of_outcome(conn, target_type=target_type, target=target, outcome="restored", note=text)
    log_action(conn, actor=user, target_type=target_type, target=target, action="dismiss_reports", note=text)
    settled = settle_reports(conn, actor, target_type, target, actioned=False, note=text)
    return {"target_type": target_type, "target_id": int(target["id"]), "dismissed": settled, "restored": restored}


# --- appeals ---------------------------------------------------------------

def _latest_sanction_log(conn, target_type: str, target_id: int) -> Optional[dict]:
    placeholders = ", ".join("?" for _ in SANCTION_ACTIONS)
    row = conn.execute(
        f"SELECT * FROM blog_moderation_logs WHERE target_type = ? AND COALESCE(target_id, post_id) = ? "
        f"AND action IN ({placeholders}) ORDER BY id DESC LIMIT 1",
        (target_type, int(target_id), *SANCTION_ACTIONS),
    ).fetchone()
    return dict(row) if row else None


def appeal_status(conn, target: dict) -> dict[str, Any]:
    """What the author's banner shows about appealing this post."""
    if _open_appeal(conn, "post", int(target["id"])):
        return {"can_appeal": False, "pending": True, "used": True}
    if str(target.get("moderation_state") or "") not in {states.STATE_LIMITED, states.STATE_REMOVED}:
        return {"can_appeal": False, "pending": False, "used": False}
    log = _latest_sanction_log(conn, "post", int(target["id"]))
    used = bool(log) and conn.execute(
        "SELECT 1 FROM blog_appeals WHERE target_type = 'post' AND target_id = ? AND moderation_log_id = ? LIMIT 1",
        (int(target["id"]), int(log["id"])),
    ).fetchone() is not None
    return {"can_appeal": bool(log) and not used, "pending": False, "used": used}


def create_appeal(conn, user: dict, target_id: Any, *, reason: Any) -> dict[str, Any]:
    """Posts only (D6); one appeal per sanction; the author's own post."""
    from ..db.connection import execute_insert_returning_id

    actor, _ = _identity(user)
    target = load_target(conn, "post", target_id)
    if str(target.get("author_identity") or "") != actor:
        raise PermissionError("只能对自己的帖子申诉")
    text = str(reason or "").strip()
    if not text:
        raise ModerationError("请填写申诉理由")
    if len(text) > MAX_APPEAL_LENGTH:
        raise ModerationError(f"申诉理由不超过 {MAX_APPEAL_LENGTH} 字")
    if not appeal_status(conn, target)["can_appeal"]:
        raise ModerationError("这次处理已经申诉过，或当前状态无需申诉")
    log = _latest_sanction_log(conn, "post", int(target["id"]))
    if not _write_state(conn, "post", target, states.STATE_APPEAL_PENDING, str(target.get("moderated_by") or ""),
                        str(target.get("moderation_reason_code") or ""), str(target.get("moderation_note") or "")):
        raise ModerationError("帖子状态刚刚发生了变化，请刷新后再试")
    appeal_id = execute_insert_returning_id(
        conn,
        "INSERT INTO blog_appeals (target_type, target_id, moderation_log_id, author_identity, reason, status, created_at) "
        "VALUES ('post', ?, ?, ?, ?, 'pending', ?)",
        (int(target["id"]), int(log["id"]), actor, text, _now()),
    )
    log_action(conn, actor=user, target_type="post", target=target, action="appeal", note=text[:120])
    return {"id": appeal_id, "target_id": int(target["id"]), "status": "pending"}


def decide_appeal(conn, user: dict, appeal_id: Any, *, decision: str, note: Any = "") -> dict[str, Any]:
    require_moderator(conn, user)
    actor, _ = _identity(user)
    row = conn.execute("SELECT * FROM blog_appeals WHERE id = ? AND status = 'pending'", (int(appeal_id),)).fetchone()
    if row is None:
        raise ModerationError("申诉不存在或已处理")
    appeal = dict(row)
    decision = str(decision or "").strip().lower()
    if decision not in {"accept", "reject"}:
        raise ModerationError("请选择通过或驳回")
    text = str(note or "").strip()[:MAX_NOTE_LENGTH]
    if decision == "reject" and not text:
        raise ModerationError("驳回申诉请填写说明")
    target = load_target(conn, appeal["target_type"], appeal["target_id"])
    if target.get("moderation_state") != states.STATE_APPEAL_PENDING:
        raise ModerationError("帖子已不在申诉中，请刷新后再处理")
    log = conn.execute("SELECT action FROM blog_moderation_logs WHERE id = ?", (int(appeal["moderation_log_id"]),)).fetchone()
    prior = ACTION_STATE.get(str(log["action"]) if log else "limit", states.STATE_LIMITED)
    state = states.STATE_PASSED if decision == "accept" else prior
    code = "" if decision == "accept" else str(target.get("moderation_reason_code") or "")
    if not _write_state(conn, appeal["target_type"], target, state, actor, code, text):
        raise ModerationError("帖子状态刚刚发生了变化，请刷新后再处理")
    _close_appeal(conn, appeal, actor, "accepted" if decision == "accept" else "rejected", text)
    log_action(conn, actor=user, target_type=appeal["target_type"], target=target,
               action="appeal_accept" if decision == "accept" else "appeal_reject", note=text)
    if decision == "accept":
        settle_reports(conn, actor, appeal["target_type"], target, actioned=False, note=text)
    notify_author_of_outcome(conn, target_type=appeal["target_type"], target=target,
                             outcome="appeal_accepted" if decision == "accept" else "appeal_rejected", note=text)
    return {"id": int(appeal["id"]), "decision": decision, "moderation_state": state}


def withdraw_open_appeal_on_edit(conn, post_id: int, actor: str) -> None:
    """Editing a sanctioned post sends it back to review; an open appeal ends with it."""
    _close_appeal(conn, _open_appeal(conn, "post", post_id), actor, "withdrawn", "作者修改后重新提交检查")


# --- identity reveal and audit ----------------------------------------------

def reveal_identity(conn, user: dict, target_type: str, target_id: Any, *, reason_kind: Any, note: Any = "") -> dict[str, Any]:
    """Who is behind a pen-name / anonymous face; always audited (D8: the author is not told)."""
    require_moderator(conn, user)
    kind = str(reason_kind or "").strip().lower()
    if kind not in REVEAL_REASONS:
        raise ModerationError("请选择查看原因")
    text = str(note or "").strip()[:MAX_NOTE_LENGTH]
    if kind == "other" and not text:
        raise ModerationError("请说明查看原因")
    target = load_target(conn, target_type, target_id)
    # Reveal applies to content others could see or report, never to drafts.
    _require_live(target_type, target)
    role, _, raw_pk = str(target.get("author_identity") or "").partition(":")
    if role not in {"student", "teacher"} or not raw_pk.isdigit():
        raise ModerationError("该内容没有可查看的作者")
    if role == "student":
        row = conn.execute(
            "SELECT s.name, s.student_id_number, COALESCE(c.name, '') AS class_name FROM students s "
            "LEFT JOIN classes c ON c.id = s.class_id WHERE s.id = ?", (int(raw_pk),),
        ).fetchone()
    else:
        row = conn.execute("SELECT name, '' AS student_id_number, '' AS class_name FROM teachers WHERE id = ?",
                           (int(raw_pk),)).fetchone()
    if row is None:
        raise ModerationError("作者账号已不存在")
    log_action(conn, actor=user, target_type=target_type, target=target, action="identity_reveal",
               note=text, extra={"reason_kind": kind})
    return {"role": role, "name": str(row["name"] or ""), "class_name": str(row["class_name"] or ""),
            "student_number": str(row["student_id_number"] or "")}


def list_audit_logs(conn, user: dict, *, action: Any = "", actor: Any = "", target_type: Any = "",
                    page: int = 1) -> dict[str, Any]:
    require_super_admin(conn, user)
    conditions, params = ["1 = 1"], []
    for column, value in (("action", action), ("moderator_identity", actor), ("target_type", target_type)):
        if str(value or "").strip():
            conditions.append(f"{column} = ?")
            params.append(str(value).strip())
    where = " AND ".join(conditions)
    page = max(int(page or 1), 1)
    total = int(conn.execute(f"SELECT COUNT(*) AS total FROM blog_moderation_logs WHERE {where}", params).fetchone()["total"])
    rows = conn.execute(
        f"SELECT * FROM blog_moderation_logs WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        (*params, AUDIT_PAGE_SIZE, (page - 1) * AUDIT_PAGE_SIZE),
    ).fetchall()
    names = actor_names(conn, [str(row["moderator_identity"] or "") for row in rows])
    items = []
    for row in rows:
        item = dict(row)
        item["actor_name"] = names.get(str(item.get("moderator_identity") or ""), "")
        item["reason_label"] = states.reason_label(item.get("reason_code"))
        items.append(item)
    return {"items": items, "total": total, "page": page, "has_more": page * AUDIT_PAGE_SIZE < total}


def actor_names(conn, identities: list[str]) -> dict[str, str]:
    system = {states.BY_AI: "AI 审核", states.BY_LOCAL_RULES: "本地规则", states.BY_TIMEOUT: "超时兜底",
              states.BY_QUOTA: "额度兜底", states.BY_REPORTS: "多人举报"}
    names = {identity: system[identity] for identity in identities if identity in system}
    # A student actor is the content's author (an appeal): never named here, so
    # the audit list cannot bypass the audited reveal of a pen-name/anonymous face.
    names.update({identity: "作者" for identity in identities if identity.startswith("student:")})
    teacher_ids = sorted({int(i.split(":", 1)[1]) for i in identities
                          if i.startswith("teacher:") and i.split(":", 1)[1].isdigit()})
    if teacher_ids:
        placeholders = ", ".join("?" for _ in teacher_ids)
        for row in conn.execute(f"SELECT id, name FROM teachers WHERE id IN ({placeholders})", teacher_ids).fetchall():
            names[f"teacher:{int(row['id'])}"] = str(row["name"] or "")
    return names


def search_teachers(conn, user: dict, query: Any) -> list[dict[str, Any]]:
    """Super admins pick teachers to grant blog moderation (by name or email)."""
    require_super_admin(conn, user)
    text = str(query or "").strip()
    if len(text) < 1:
        return []
    from .blog_service import _escape_like_pattern

    pattern = f"%{_escape_like_pattern(text)}%"
    rows = conn.execute(
        "SELECT t.id, t.name, t.email, EXISTS(SELECT 1 FROM blog_moderators m WHERE m.teacher_id = t.id "
        "AND m.revoked_at IS NULL) AS granted "
        "FROM teachers t WHERE COALESCE(t.is_active, 1) = 1 "
        "AND (LOWER(t.name) LIKE LOWER(?) ESCAPE '\\' OR LOWER(COALESCE(t.email, '')) LIKE LOWER(?) ESCAPE '\\') "
        "ORDER BY t.name LIMIT 20",
        (pattern, pattern),
    ).fetchall()
    return [{"id": int(row["id"]), "name": str(row["name"] or ""), "email": str(row["email"] or ""),
             "granted": bool(row["granted"])} for row in rows]
