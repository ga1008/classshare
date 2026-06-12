from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from ..db.connection import execute_insert_returning_id
from .classroom_interaction_service import ensure_classroom_interaction_access


PEER_HELP_ACTION_TYPE = "peer_help"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _json_dumps(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _role(user: dict[str, Any]) -> str:
    return str(user.get("role") or "").strip().lower()


def _user_pk(user: dict[str, Any]) -> int:
    user_id = _safe_int(user.get("id"))
    if user_id <= 0:
        raise HTTPException(403, "当前账号无效")
    return user_id


def _display_name(user: dict[str, Any]) -> str:
    return str(user.get("name") or user.get("username") or "课堂成员").strip() or "课堂成员"


def _load_chat_message(conn, class_offering_id: int, message_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id, class_offering_id, user_id, user_name, user_role, message,
               quote_message_id, logged_at, timestamp
        FROM chat_logs
        WHERE class_offering_id = ?
          AND id = ?
        LIMIT 1
        """,
        (int(class_offering_id), int(message_id)),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "消息不存在")
    return dict(row)


def _load_quoted_message(conn, class_offering_id: int, quote_message_id: Any) -> dict[str, Any] | None:
    quote_id = _safe_int(quote_message_id)
    if quote_id <= 0:
        return None
    row = conn.execute(
        """
        SELECT id, user_id, user_name, user_role, message
        FROM chat_logs
        WHERE class_offering_id = ?
          AND id = ?
        LIMIT 1
        """,
        (int(class_offering_id), quote_id),
    ).fetchone()
    return dict(row) if row else None


def _event_payload_contains(conn, class_offering_id: int, value: str) -> bool:
    if not value:
        return False
    row = conn.execute(
        """
        SELECT id
        FROM classroom_behavior_events
        WHERE class_offering_id = ?
          AND action_type = ?
          AND payload_json LIKE ?
        LIMIT 1
        """,
        (int(class_offering_id), PEER_HELP_ACTION_TYPE, f"%{value}%"),
    ).fetchone()
    return row is not None


def _count_peer_help_events(conn, class_offering_id: int, helper_student_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM classroom_behavior_events
        WHERE class_offering_id = ?
          AND user_pk = ?
          AND user_role = 'student'
          AND action_type = ?
        """,
        (int(class_offering_id), int(helper_student_id), PEER_HELP_ACTION_TYPE),
    ).fetchone()
    return _safe_int(row["total"] if row else 0)


def _touch_helper_behavior_state(conn, class_offering_id: int, helper_student_id: int, now_text: str) -> None:
    conn.execute(
        """
        INSERT INTO classroom_behavior_states (
            class_offering_id, user_pk, user_role, total_activity_count,
            last_event_at, last_page_key, created_at, updated_at
        )
        VALUES (?, ?, 'student', 1, ?, 'peer_help', ?, ?)
        ON CONFLICT(class_offering_id, user_pk, user_role)
        DO UPDATE SET
            total_activity_count = total_activity_count + 1,
            last_event_at = excluded.last_event_at,
            last_page_key = 'peer_help',
            updated_at = excluded.updated_at
        """,
        (int(class_offering_id), int(helper_student_id), now_text, now_text, now_text),
    )


def _mark_learning_progress_dirty(conn, class_offering_id: int, helper_student_id: int, source_ref: str) -> None:
    try:
        from .learning_progress_service import mark_student_learning_progress_dirty

        mark_student_learning_progress_dirty(
            conn,
            int(class_offering_id),
            int(helper_student_id),
            source_ref=source_ref,
        )
    except Exception:
        pass


def mark_chat_message_useful(
    conn,
    class_offering_id: int,
    message_id: int,
    marker_user: dict[str, Any],
) -> dict[str, Any]:
    ensure_classroom_interaction_access(conn, int(class_offering_id), marker_user)

    marker_role = _role(marker_user)
    marker_id = _user_pk(marker_user)
    if marker_role not in {"teacher", "student"}:
        raise HTTPException(403, "当前账号不能标记课堂消息")

    message = _load_chat_message(conn, int(class_offering_id), int(message_id))
    helper_role = str(message.get("user_role") or "").strip().lower()
    helper_student_id = _safe_int(message.get("user_id"))
    if helper_role != "student" or helper_student_id <= 0:
        raise HTTPException(400, "只有学生回复可以计入助人修为")
    if marker_role == "student" and helper_student_id == marker_id:
        raise HTTPException(400, "不能给自己的消息标记有用")

    quoted_message = _load_quoted_message(conn, int(class_offering_id), message.get("quote_message_id"))
    if marker_role == "student":
        quoted_by_marker = (
            quoted_message is not None
            and str(quoted_message.get("user_role") or "").strip().lower() == "student"
            and _safe_int(quoted_message.get("user_id")) == marker_id
        )
        if not quoted_by_marker:
            raise HTTPException(403, "只有被回复的提问者可以标记同学回答有用")

    today = datetime.now().date().isoformat()
    message_dedupe_key = f"peer_help:message:{int(class_offering_id)}:{int(message_id)}:{marker_role}:{marker_id}"
    if _event_payload_contains(conn, int(class_offering_id), message_dedupe_key):
        return {
            "counted": False,
            "reason": "duplicate_message",
            "helper_student_id": helper_student_id,
            "peer_help_count": _count_peer_help_events(conn, int(class_offering_id), helper_student_id),
        }

    pair_dedupe_key = ""
    if marker_role == "student":
        pair_dedupe_key = f"peer_help:pair-day:{int(class_offering_id)}:{marker_id}:{helper_student_id}:{today}"
        if _event_payload_contains(conn, int(class_offering_id), pair_dedupe_key):
            return {
                "counted": False,
                "reason": "pair_daily_limit",
                "helper_student_id": helper_student_id,
                "peer_help_count": _count_peer_help_events(conn, int(class_offering_id), helper_student_id),
            }

    now_text = datetime.now().replace(microsecond=0).isoformat()
    payload = {
        "chat_message_id": int(message_id),
        "message_dedupe_key": message_dedupe_key,
        "pair_dedupe_key": pair_dedupe_key,
        "marked_by_role": marker_role,
        "marked_by_user_pk": marker_id,
        "marked_by_name": _display_name(marker_user),
        "helper_student_id": helper_student_id,
        "helper_name": str(message.get("user_name") or ""),
        "quoted_message_id": _safe_int(message.get("quote_message_id")) or None,
        "quoted_by_marker": bool(marker_role == "student"),
    }
    event_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO classroom_behavior_events (
            class_offering_id, user_pk, user_role, display_name,
            action_type, summary_text, payload_json, created_at
        )
        VALUES (?, ?, 'student', ?, ?, ?, ?, ?)
        """,
        (
            int(class_offering_id),
            helper_student_id,
            str(message.get("user_name") or "课堂成员"),
            PEER_HELP_ACTION_TYPE,
            "同伴帮助被标记为有用",
            _json_dumps(payload),
            now_text,
        ),
    )
    _touch_helper_behavior_state(conn, int(class_offering_id), helper_student_id, now_text)
    _mark_learning_progress_dirty(
        conn,
        int(class_offering_id),
        helper_student_id,
        source_ref=f"peer_help:{int(message_id)}",
    )
    return {
        "counted": True,
        "event_id": int(event_id),
        "helper_student_id": helper_student_id,
        "helper_name": str(message.get("user_name") or ""),
        "peer_help_count": _count_peer_help_events(conn, int(class_offering_id), helper_student_id),
    }
