from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Iterable, Optional

from fastapi import HTTPException
from .resource_access_service import ensure_classroom_access as ensure_scoped_classroom_access


ACTIVITY_KIND_POLL = "poll"
ACTIVITY_KIND_QUIZ = "quiz"
ACTIVITY_KIND_QNA = "qna"
ACTIVITY_KINDS = {ACTIVITY_KIND_POLL, ACTIVITY_KIND_QUIZ, ACTIVITY_KIND_QNA}
ACTIVITY_KIND_LABELS = {
    ACTIVITY_KIND_POLL: "课堂投票",
    ACTIVITY_KIND_QUIZ: "随堂测",
    ACTIVITY_KIND_QNA: "匿名提问",
}

ACTIVITY_STATUS_ACTIVE = "active"
ACTIVITY_STATUS_CLOSED = "closed"
ACTIVITY_STATUS_ARCHIVED = "archived"
ACTIVITY_STATUSES = {ACTIVITY_STATUS_ACTIVE, ACTIVITY_STATUS_CLOSED, ACTIVITY_STATUS_ARCHIVED}

RESULT_VISIBILITY_TEACHER_ONLY = "teacher_only"
RESULT_VISIBILITY_AFTER_SUBMIT = "after_submit"
RESULT_VISIBILITY_AFTER_CLOSE = "after_close"
RESULT_VISIBILITY_ALWAYS = "always"
RESULT_VISIBILITIES = {
    RESULT_VISIBILITY_TEACHER_ONLY,
    RESULT_VISIBILITY_AFTER_SUBMIT,
    RESULT_VISIBILITY_AFTER_CLOSE,
    RESULT_VISIBILITY_ALWAYS,
}

SIGNAL_TYPES = {
    "hand": "举手",
    "help": "求助",
    "slow": "跟不上",
    "done": "已完成",
}
SIGNAL_CLEAR = "clear"

MAX_ACTIVE_ACTIVITIES = 6
MAX_OPTIONS = 8
MAX_QUESTIONS_PER_ACTIVITY = 30


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _json_dump(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _json_load(value: Any, fallback: Any = None) -> Any:
    if fallback is None:
        fallback = {}
    if not value:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return fallback


def _normalize_text(value: Any, *, limit: int, field_name: str, required: bool = False) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if required and not text:
        raise HTTPException(400, f"{field_name}不能为空")
    if len(text) > limit:
        raise HTTPException(400, f"{field_name}不能超过 {limit} 个字符")
    return text


def _truncate(value: Any, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def _is_teacher(user: dict[str, Any]) -> bool:
    return str(user.get("role") or "").strip().lower() == "teacher"


def _is_student(user: dict[str, Any]) -> bool:
    return str(user.get("role") or "").strip().lower() == "student"


def _user_pk(user: dict[str, Any]) -> int:
    user_id = _safe_int(user.get("id"))
    if user_id is None:
        raise HTTPException(403, "当前账号无效")
    return user_id


def _actor_name(user: dict[str, Any]) -> str:
    return str(user.get("name") or user.get("username") or "课堂成员").strip()


def ensure_classroom_interaction_access(
    conn: sqlite3.Connection,
    class_offering_id: int,
    user: dict[str, Any],
) -> dict[str, Any]:
    return dict(ensure_scoped_classroom_access(conn, class_offering_id, user))


def _load_activity(conn: sqlite3.Connection, activity_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT *
        FROM classroom_live_activities
        WHERE id = ?
        LIMIT 1
        """,
        (int(activity_id),),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "互动活动不存在")
    return dict(row)


def _ensure_activity_access(
    conn: sqlite3.Connection,
    activity_id: int,
    user: dict[str, Any],
) -> dict[str, Any]:
    activity = _load_activity(conn, int(activity_id))
    ensure_classroom_interaction_access(conn, int(activity["class_offering_id"]), user)
    return activity


def _normalize_activity_kind(value: Any) -> str:
    kind = str(value or "").strip().lower()
    if kind not in ACTIVITY_KINDS:
        raise HTTPException(400, "互动类型不合法")
    return kind


def _normalize_visibility(value: Any, *, kind: str) -> str:
    default = RESULT_VISIBILITY_AFTER_CLOSE if kind == ACTIVITY_KIND_QUIZ else RESULT_VISIBILITY_AFTER_SUBMIT
    visibility = str(value or default).strip().lower()
    if visibility not in RESULT_VISIBILITIES:
        raise HTTPException(400, "结果可见范围不合法")
    return visibility


def _normalize_options(raw_options: Any, *, kind: str) -> list[dict[str, Any]]:
    if kind == ACTIVITY_KIND_QNA:
        return []
    if not isinstance(raw_options, list):
        raise HTTPException(400, "请至少填写两个选项")

    options: list[dict[str, Any]] = []
    for index, item in enumerate(raw_options[:MAX_OPTIONS]):
        if isinstance(item, dict):
            label = _normalize_text(item.get("label"), limit=120, field_name="选项")
            is_correct = _coerce_bool(item.get("is_correct"), default=False)
        else:
            label = _normalize_text(item, limit=120, field_name="选项")
            is_correct = False
        if not label:
            continue
        options.append({
            "option_key": f"o{len(options) + 1}",
            "label": label,
            "is_correct": is_correct,
            "sort_order": index,
        })

    if len(options) < 2:
        raise HTTPException(400, "请至少填写两个有效选项")
    if kind == ACTIVITY_KIND_QUIZ and not any(item["is_correct"] for item in options):
        raise HTTPException(400, "随堂测需要标记一个正确选项")
    return options


def _active_activity_count(conn: sqlite3.Connection, class_offering_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM classroom_live_activities
        WHERE class_offering_id = ?
          AND status = 'active'
        """,
        (int(class_offering_id),),
    ).fetchone()
    return int(row["total"] if row else 0)


def create_activity(
    conn: sqlite3.Connection,
    class_offering_id: int,
    user: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    offering = ensure_classroom_interaction_access(conn, int(class_offering_id), user)
    if not _is_teacher(user):
        raise HTTPException(403, "只有教师可以发起课堂互动")
    if _active_activity_count(conn, int(class_offering_id)) >= MAX_ACTIVE_ACTIVITIES:
        raise HTTPException(400, f"当前活跃互动较多，请先结束部分活动后再发起新的互动")

    kind = _normalize_activity_kind(payload.get("kind"))
    title = _normalize_text(payload.get("title"), limit=80, field_name="标题")
    prompt = _normalize_text(payload.get("prompt"), limit=500, field_name="问题", required=True)
    if not title:
        title = ACTIVITY_KIND_LABELS[kind]
    allow_anonymous = _coerce_bool(payload.get("allow_anonymous"), default=(kind == ACTIVITY_KIND_QNA))
    show_results = _normalize_visibility(payload.get("show_results"), kind=kind)
    options = _normalize_options(payload.get("options"), kind=kind)
    now = _now_iso()

    cursor = conn.execute(
        """
        INSERT INTO classroom_live_activities (
            class_offering_id, kind, title, prompt, status,
            allow_anonymous, show_results, created_by_teacher_id, created_by_name,
            created_at, updated_at, started_at, settings_json
        )
        VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(class_offering_id),
            kind,
            title,
            prompt,
            1 if allow_anonymous else 0,
            show_results,
            _user_pk(user),
            _actor_name(user),
            now,
            now,
            now,
            _json_dump({
                "course_name": offering.get("course_name"),
                "class_name": offering.get("class_name"),
            }),
        ),
    )
    activity_id = int(cursor.lastrowid)
    for option in options:
        conn.execute(
            """
            INSERT INTO classroom_live_options (
                activity_id, option_key, label, is_correct, sort_order, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                activity_id,
                option["option_key"],
                option["label"],
                1 if option["is_correct"] else 0,
                int(option["sort_order"]),
                now,
            ),
        )
    return load_activity_detail(conn, activity_id, user)


def close_activity(
    conn: sqlite3.Connection,
    activity_id: int,
    user: dict[str, Any],
) -> dict[str, Any]:
    activity = _ensure_activity_access(conn, int(activity_id), user)
    if not _is_teacher(user):
        raise HTTPException(403, "只有教师可以结束课堂互动")
    if int(activity["created_by_teacher_id"] or 0) != _user_pk(user):
        offering = ensure_classroom_interaction_access(conn, int(activity["class_offering_id"]), user)
        if int(offering["teacher_id"]) != _user_pk(user):
            raise HTTPException(403, "无权结束该课堂互动")
    if activity["status"] != ACTIVITY_STATUS_ACTIVE:
        return load_activity_detail(conn, int(activity_id), user)

    now = _now_iso()
    conn.execute(
        """
        UPDATE classroom_live_activities
        SET status = 'closed',
            closed_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (now, now, int(activity_id)),
    )
    return load_activity_detail(conn, int(activity_id), user)


def respond_to_activity(
    conn: sqlite3.Connection,
    activity_id: int,
    user: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    activity = _ensure_activity_access(conn, int(activity_id), user)
    if not _is_student(user):
        raise HTTPException(403, "只有学生可以提交互动回应")
    if activity["status"] != ACTIVITY_STATUS_ACTIVE:
        raise HTTPException(400, "该互动已结束")
    if activity["kind"] not in {ACTIVITY_KIND_POLL, ACTIVITY_KIND_QUIZ}:
        raise HTTPException(400, "该互动不支持选项回应")

    option_id = _safe_int(payload.get("option_id"))
    if option_id is None:
        raise HTTPException(400, "请选择一个选项")
    option = conn.execute(
        """
        SELECT id
        FROM classroom_live_options
        WHERE id = ?
          AND activity_id = ?
        LIMIT 1
        """,
        (int(option_id), int(activity_id)),
    ).fetchone()
    if option is None:
        raise HTTPException(400, "选项不存在")

    response_text = _normalize_text(payload.get("response_text"), limit=300, field_name="补充说明")
    is_anonymous = _coerce_bool(payload.get("is_anonymous"), default=False)
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO classroom_live_responses (
            activity_id, student_id, option_id, response_text,
            is_anonymous, created_at, updated_at, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, '{}')
        ON CONFLICT(activity_id, student_id)
        DO UPDATE SET
            option_id = excluded.option_id,
            response_text = excluded.response_text,
            is_anonymous = excluded.is_anonymous,
            updated_at = excluded.updated_at
        """,
        (
            int(activity_id),
            _user_pk(user),
            int(option_id),
            response_text,
            1 if is_anonymous else 0,
            now,
            now,
        ),
    )
    return load_activity_detail(conn, int(activity_id), user)


def submit_question(
    conn: sqlite3.Connection,
    activity_id: int,
    user: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    activity = _ensure_activity_access(conn, int(activity_id), user)
    if not _is_student(user):
        raise HTTPException(403, "只有学生可以提交课堂提问")
    if activity["status"] != ACTIVITY_STATUS_ACTIVE:
        raise HTTPException(400, "该提问入口已结束")
    if activity["kind"] != ACTIVITY_KIND_QNA:
        raise HTTPException(400, "该互动不是提问入口")

    question_text = _normalize_text(payload.get("question_text"), limit=500, field_name="问题", required=True)
    allow_anonymous = bool(activity["allow_anonymous"])
    is_anonymous = _coerce_bool(payload.get("is_anonymous"), default=allow_anonymous) and allow_anonymous
    now = _now_iso()
    cursor = conn.execute(
        """
        INSERT INTO classroom_live_questions (
            activity_id, class_offering_id, student_id, display_name,
            question_text, is_anonymous, status, created_at, updated_at, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, '{}')
        """,
        (
            int(activity_id),
            int(activity["class_offering_id"]),
            _user_pk(user),
            _actor_name(user),
            question_text,
            1 if is_anonymous else 0,
            now,
            now,
        ),
    )
    return load_question(conn, int(cursor.lastrowid), user)


def resolve_question(
    conn: sqlite3.Connection,
    question_id: int,
    user: dict[str, Any],
    *,
    status: str = "addressed",
) -> dict[str, Any]:
    question = _load_question_row(conn, int(question_id))
    ensure_classroom_interaction_access(conn, int(question["class_offering_id"]), user)
    if not _is_teacher(user):
        raise HTTPException(403, "只有教师可以处理课堂提问")
    normalized_status = str(status or "addressed").strip().lower()
    if normalized_status not in {"open", "addressed", "hidden"}:
        raise HTTPException(400, "问题状态不合法")
    now = _now_iso()
    conn.execute(
        """
        UPDATE classroom_live_questions
        SET status = ?,
            addressed_at = CASE WHEN ? = 'addressed' THEN ? ELSE addressed_at END,
            addressed_by_teacher_id = CASE WHEN ? = 'addressed' THEN ? ELSE addressed_by_teacher_id END,
            updated_at = ?
        WHERE id = ?
        """,
        (
            normalized_status,
            normalized_status,
            now,
            normalized_status,
            _user_pk(user),
            now,
            int(question_id),
        ),
    )
    return load_question(conn, int(question_id), user)


def _load_question_row(conn: sqlite3.Connection, question_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT *
        FROM classroom_live_questions
        WHERE id = ?
        LIMIT 1
        """,
        (int(question_id),),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "问题不存在")
    return dict(row)


def set_help_signal(
    conn: sqlite3.Connection,
    class_offering_id: int,
    user: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    ensure_classroom_interaction_access(conn, int(class_offering_id), user)
    if not _is_student(user):
        raise HTTPException(403, "只有学生可以更新举手/求助状态")
    signal_type = str(payload.get("signal_type") or "").strip().lower()
    if signal_type == SIGNAL_CLEAR:
        clear_my_help_signal(conn, int(class_offering_id), user)
        return None
    if signal_type not in SIGNAL_TYPES:
        raise HTTPException(400, "举手/求助状态不合法")

    message = _normalize_text(payload.get("message"), limit=160, field_name="补充说明")
    now = _now_iso()
    student_id = _user_pk(user)
    existing = conn.execute(
        """
        SELECT id
        FROM classroom_live_help_signals
        WHERE class_offering_id = ?
          AND student_id = ?
          AND status = 'active'
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(class_offering_id), student_id),
    ).fetchone()
    if existing:
        signal_id = int(existing["id"])
        conn.execute(
            """
            UPDATE classroom_live_help_signals
            SET signal_type = ?,
                message = ?,
                display_name = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (signal_type, message, _actor_name(user), now, signal_id),
        )
    else:
        cursor = conn.execute(
            """
            INSERT INTO classroom_live_help_signals (
                class_offering_id, student_id, display_name,
                signal_type, status, message, created_at, updated_at, metadata_json
            )
            VALUES (?, ?, ?, ?, 'active', ?, ?, ?, '{}')
            """,
            (int(class_offering_id), student_id, _actor_name(user), signal_type, message, now, now),
        )
        signal_id = int(cursor.lastrowid)
    return load_signal(conn, signal_id, user)


def clear_my_help_signal(conn: sqlite3.Connection, class_offering_id: int, user: dict[str, Any]) -> int:
    ensure_classroom_interaction_access(conn, int(class_offering_id), user)
    if not _is_student(user):
        raise HTTPException(403, "只有学生可以取消自己的举手/求助状态")
    now = _now_iso()
    cursor = conn.execute(
        """
        UPDATE classroom_live_help_signals
        SET status = 'cancelled',
            resolved_at = ?,
            updated_at = ?
        WHERE class_offering_id = ?
          AND student_id = ?
          AND status = 'active'
        """,
        (now, now, int(class_offering_id), _user_pk(user)),
    )
    return int(cursor.rowcount or 0)


def resolve_help_signal(
    conn: sqlite3.Connection,
    signal_id: int,
    user: dict[str, Any],
) -> dict[str, Any]:
    signal = _load_signal_row(conn, int(signal_id))
    ensure_classroom_interaction_access(conn, int(signal["class_offering_id"]), user)
    if not _is_teacher(user):
        raise HTTPException(403, "只有教师可以处理学生举手/求助状态")
    now = _now_iso()
    conn.execute(
        """
        UPDATE classroom_live_help_signals
        SET status = 'resolved',
            resolved_at = ?,
            resolved_by_teacher_id = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (now, _user_pk(user), now, int(signal_id)),
    )
    return load_signal(conn, int(signal_id), user)


def _load_signal_row(conn: sqlite3.Connection, signal_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT *
        FROM classroom_live_help_signals
        WHERE id = ?
        LIMIT 1
        """,
        (int(signal_id),),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "求助状态不存在")
    return dict(row)


def _group_rows(rows: Iterable[Any], key: str) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        grouped.setdefault(int(item[key]), []).append(item)
    return grouped


def _load_options(conn: sqlite3.Connection, activity_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not activity_ids:
        return {}
    placeholders = ",".join("?" for _ in activity_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM classroom_live_options
        WHERE activity_id IN ({placeholders})
        ORDER BY activity_id, sort_order, id
        """,
        tuple(activity_ids),
    ).fetchall()
    return _group_rows(rows, "activity_id")


def _load_response_counts(conn: sqlite3.Connection, activity_ids: list[int]) -> tuple[dict[int, int], dict[int, dict[int, int]]]:
    if not activity_ids:
        return {}, {}
    placeholders = ",".join("?" for _ in activity_ids)
    rows = conn.execute(
        f"""
        SELECT activity_id, option_id, COUNT(*) AS total
        FROM classroom_live_responses
        WHERE activity_id IN ({placeholders})
        GROUP BY activity_id, option_id
        """,
        tuple(activity_ids),
    ).fetchall()
    totals: dict[int, int] = {}
    by_option: dict[int, dict[int, int]] = {}
    for row in rows:
        activity_id = int(row["activity_id"])
        option_id = int(row["option_id"] or 0)
        count = int(row["total"] or 0)
        totals[activity_id] = totals.get(activity_id, 0) + count
        by_option.setdefault(activity_id, {})[option_id] = count
    return totals, by_option


def _load_my_responses(
    conn: sqlite3.Connection,
    activity_ids: list[int],
    user: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    if not activity_ids or not _is_student(user):
        return {}
    placeholders = ",".join("?" for _ in activity_ids)
    rows = conn.execute(
        f"""
        SELECT r.*, o.is_correct, o.label AS option_label
        FROM classroom_live_responses r
        LEFT JOIN classroom_live_options o ON o.id = r.option_id
        WHERE r.activity_id IN ({placeholders})
          AND r.student_id = ?
        """,
        (*activity_ids, _user_pk(user)),
    ).fetchall()
    return {int(row["activity_id"]): dict(row) for row in rows}


def _load_questions(conn: sqlite3.Connection, activity_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not activity_ids:
        return {}
    placeholders = ",".join("?" for _ in activity_ids)
    rows = conn.execute(
        f"""
        SELECT q.*
        FROM classroom_live_questions q
        WHERE q.activity_id IN ({placeholders})
          AND q.status != 'hidden'
        ORDER BY
            q.activity_id,
            CASE q.status WHEN 'open' THEN 0 ELSE 1 END,
            q.created_at DESC,
            q.id DESC
        """,
        tuple(activity_ids),
    ).fetchall()
    grouped = _group_rows(rows, "activity_id")
    return {
        activity_id: items[:MAX_QUESTIONS_PER_ACTIVITY]
        for activity_id, items in grouped.items()
    }


def _load_active_signals(conn: sqlite3.Connection, class_offering_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM classroom_live_help_signals
        WHERE class_offering_id = ?
          AND status = 'active'
        ORDER BY updated_at ASC, id ASC
        """,
        (int(class_offering_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_my_signal(
    conn: sqlite3.Connection,
    class_offering_id: int,
    user: dict[str, Any],
) -> dict[str, Any] | None:
    if not _is_student(user):
        return None
    row = conn.execute(
        """
        SELECT *
        FROM classroom_live_help_signals
        WHERE class_offering_id = ?
          AND student_id = ?
          AND status = 'active'
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(class_offering_id), _user_pk(user)),
    ).fetchone()
    return dict(row) if row else None


def _can_show_results(activity: dict[str, Any], *, is_teacher: bool, has_responded: bool) -> bool:
    if is_teacher:
        return True
    visibility = str(activity.get("show_results") or RESULT_VISIBILITY_AFTER_SUBMIT)
    status = str(activity.get("status") or "")
    if visibility == RESULT_VISIBILITY_ALWAYS:
        return True
    if visibility == RESULT_VISIBILITY_AFTER_SUBMIT:
        return has_responded or status == ACTIVITY_STATUS_CLOSED
    if visibility == RESULT_VISIBILITY_AFTER_CLOSE:
        return status == ACTIVITY_STATUS_CLOSED
    return False


def _serialize_option(
    option: dict[str, Any],
    *,
    activity: dict[str, Any],
    total_responses: int,
    response_count: int,
    can_show_results: bool,
    is_teacher: bool,
    selected_option_id: Optional[int],
) -> dict[str, Any]:
    payload = {
        "id": int(option["id"]),
        "label": str(option["label"] or ""),
        "sort_order": int(option["sort_order"] or 0),
        "selected": selected_option_id == int(option["id"]),
    }
    if is_teacher or (can_show_results and activity["kind"] == ACTIVITY_KIND_QUIZ):
        payload["is_correct"] = bool(option["is_correct"])
    if can_show_results:
        payload["response_count"] = response_count
        payload["response_percent"] = round((response_count / total_responses) * 100, 1) if total_responses else 0
    return payload


def _serialize_response(response: dict[str, Any] | None, activity: dict[str, Any]) -> dict[str, Any] | None:
    if not response:
        return None
    payload = {
        "option_id": int(response["option_id"]) if response.get("option_id") is not None else None,
        "option_label": str(response.get("option_label") or ""),
        "response_text": str(response.get("response_text") or ""),
        "updated_at": str(response.get("updated_at") or ""),
    }
    if activity["kind"] == ACTIVITY_KIND_QUIZ:
        payload["is_correct"] = bool(response.get("is_correct"))
    return payload


def _serialize_question(question: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    is_mine = _is_student(user) and int(question["student_id"]) == _user_pk(user)
    anonymous = bool(question.get("is_anonymous"))
    return {
        "id": int(question["id"]),
        "activity_id": int(question["activity_id"]),
        "question_text": str(question.get("question_text") or ""),
        "display_name": "匿名同学" if anonymous else str(question.get("display_name") or "同学"),
        "is_anonymous": anonymous,
        "is_mine": is_mine,
        "status": str(question.get("status") or "open"),
        "created_at": str(question.get("created_at") or ""),
        "updated_at": str(question.get("updated_at") or ""),
        "can_resolve": _is_teacher(user),
    }


def _serialize_signal(signal: dict[str, Any] | None, user: dict[str, Any]) -> dict[str, Any] | None:
    if not signal:
        return None
    is_mine = _is_student(user) and int(signal["student_id"]) == _user_pk(user)
    signal_type = str(signal.get("signal_type") or "")
    return {
        "id": int(signal["id"]),
        "class_offering_id": int(signal["class_offering_id"]),
        "student_id": int(signal["student_id"]) if _is_teacher(user) or is_mine else None,
        "display_name": str(signal.get("display_name") or "同学") if _is_teacher(user) or is_mine else "同学",
        "signal_type": signal_type,
        "signal_label": SIGNAL_TYPES.get(signal_type, "状态"),
        "message": str(signal.get("message") or ""),
        "status": str(signal.get("status") or "active"),
        "is_mine": is_mine,
        "created_at": str(signal.get("created_at") or ""),
        "updated_at": str(signal.get("updated_at") or ""),
        "can_resolve": _is_teacher(user),
    }


def _serialize_activity(
    activity: dict[str, Any],
    *,
    user: dict[str, Any],
    options: list[dict[str, Any]],
    total_responses: int,
    option_counts: dict[int, int],
    my_response: dict[str, Any] | None,
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    role_is_teacher = _is_teacher(user)
    has_responded = my_response is not None
    can_show_results = _can_show_results(activity, is_teacher=role_is_teacher, has_responded=has_responded)
    selected_option_id = int(my_response["option_id"]) if my_response and my_response.get("option_id") else None
    serialized_options = [
        _serialize_option(
            option,
            activity=activity,
            total_responses=total_responses,
            response_count=int(option_counts.get(int(option["id"]), 0)),
            can_show_results=can_show_results,
            is_teacher=role_is_teacher,
            selected_option_id=selected_option_id,
        )
        for option in options
    ]
    open_question_count = sum(1 for item in questions if item.get("status") == "open")
    return {
        "id": int(activity["id"]),
        "class_offering_id": int(activity["class_offering_id"]),
        "kind": str(activity["kind"]),
        "kind_label": ACTIVITY_KIND_LABELS.get(str(activity["kind"]), "互动"),
        "title": str(activity.get("title") or ACTIVITY_KIND_LABELS.get(str(activity["kind"]), "互动")),
        "prompt": str(activity.get("prompt") or ""),
        "status": str(activity.get("status") or ACTIVITY_STATUS_ACTIVE),
        "allow_anonymous": bool(activity.get("allow_anonymous")),
        "show_results": str(activity.get("show_results") or RESULT_VISIBILITY_AFTER_SUBMIT),
        "created_by_name": str(activity.get("created_by_name") or "教师"),
        "created_at": str(activity.get("created_at") or ""),
        "updated_at": str(activity.get("updated_at") or ""),
        "started_at": str(activity.get("started_at") or ""),
        "closed_at": str(activity.get("closed_at") or ""),
        "response_count": int(total_responses),
        "open_question_count": open_question_count,
        "can_close": role_is_teacher and activity["status"] == ACTIVITY_STATUS_ACTIVE,
        "can_respond": _is_student(user) and activity["status"] == ACTIVITY_STATUS_ACTIVE and activity["kind"] in {ACTIVITY_KIND_POLL, ACTIVITY_KIND_QUIZ},
        "can_ask": _is_student(user) and activity["status"] == ACTIVITY_STATUS_ACTIVE and activity["kind"] == ACTIVITY_KIND_QNA,
        "has_responded": has_responded,
        "can_show_results": can_show_results,
        "my_response": _serialize_response(my_response, activity),
        "options": serialized_options,
        "questions": [_serialize_question(item, user) for item in questions],
        "settings": _json_load(activity.get("settings_json"), {}),
    }


def _load_activity_rows(conn: sqlite3.Connection, class_offering_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM classroom_live_activities
        WHERE class_offering_id = ?
          AND status IN ('active', 'closed')
        ORDER BY
            CASE status WHEN 'active' THEN 0 ELSE 1 END,
            updated_at DESC,
            id DESC
        LIMIT 12
        """,
        (int(class_offering_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def load_activity_detail(conn: sqlite3.Connection, activity_id: int, user: dict[str, Any]) -> dict[str, Any]:
    activity = _ensure_activity_access(conn, int(activity_id), user)
    activity_ids = [int(activity_id)]
    options = _load_options(conn, activity_ids)
    totals, counts = _load_response_counts(conn, activity_ids)
    my_responses = _load_my_responses(conn, activity_ids, user)
    questions = _load_questions(conn, activity_ids)
    return _serialize_activity(
        activity,
        user=user,
        options=options.get(int(activity_id), []),
        total_responses=totals.get(int(activity_id), 0),
        option_counts=counts.get(int(activity_id), {}),
        my_response=my_responses.get(int(activity_id)),
        questions=questions.get(int(activity_id), []),
    )


def load_question(conn: sqlite3.Connection, question_id: int, user: dict[str, Any]) -> dict[str, Any]:
    question = _load_question_row(conn, int(question_id))
    ensure_classroom_interaction_access(conn, int(question["class_offering_id"]), user)
    return _serialize_question(question, user)


def load_signal(conn: sqlite3.Connection, signal_id: int, user: dict[str, Any]) -> dict[str, Any]:
    signal = _load_signal_row(conn, int(signal_id))
    ensure_classroom_interaction_access(conn, int(signal["class_offering_id"]), user)
    return _serialize_signal(signal, user) or {}


def load_interaction_snapshot(
    conn: sqlite3.Connection,
    class_offering_id: int,
    user: dict[str, Any],
) -> dict[str, Any]:
    offering = ensure_classroom_interaction_access(conn, int(class_offering_id), user)
    activities = _load_activity_rows(conn, int(class_offering_id))
    activity_ids = [int(item["id"]) for item in activities]
    options = _load_options(conn, activity_ids)
    totals, counts = _load_response_counts(conn, activity_ids)
    my_responses = _load_my_responses(conn, activity_ids, user)
    questions = _load_questions(conn, activity_ids)
    signals = _load_active_signals(conn, int(class_offering_id))
    my_signal = _load_my_signal(conn, int(class_offering_id), user)

    serialized_activities = [
        _serialize_activity(
            activity,
            user=user,
            options=options.get(int(activity["id"]), []),
            total_responses=totals.get(int(activity["id"]), 0),
            option_counts=counts.get(int(activity["id"]), {}),
            my_response=my_responses.get(int(activity["id"])),
            questions=questions.get(int(activity["id"]), []),
        )
        for activity in activities
    ]
    active_activities = [item for item in serialized_activities if item["status"] == ACTIVITY_STATUS_ACTIVE]
    recent_activities = [item for item in serialized_activities if item["status"] != ACTIVITY_STATUS_ACTIVE]
    unresolved_question_count = sum(item["open_question_count"] for item in serialized_activities)
    signal_counts: dict[str, int] = {}
    for signal in signals:
        signal_type = str(signal.get("signal_type") or "")
        signal_counts[signal_type] = signal_counts.get(signal_type, 0) + 1

    return {
        "classroom": {
            "id": int(offering["id"]),
            "course_name": str(offering.get("course_name") or ""),
            "class_name": str(offering.get("class_name") or ""),
        },
        "role": str(user.get("role") or ""),
        "can_create": _is_teacher(user),
        "summary": {
            "active_activity_count": len(active_activities),
            "recent_activity_count": len(recent_activities),
            "response_count": sum(item["response_count"] for item in serialized_activities),
            "open_question_count": unresolved_question_count,
            "active_signal_count": len(signals),
            "signal_counts": signal_counts,
        },
        "active_activities": active_activities,
        "recent_activities": recent_activities[:6],
        "signals": [_serialize_signal(item, user) for item in signals] if _is_teacher(user) else [],
        "my_signal": _serialize_signal(my_signal, user) if my_signal else None,
        "signal_options": [
            {"key": key, "label": label}
            for key, label in SIGNAL_TYPES.items()
        ],
        "limits": {
            "max_active_activities": MAX_ACTIVE_ACTIVITIES,
            "max_options": MAX_OPTIONS,
            "max_questions_per_activity": MAX_QUESTIONS_PER_ACTIVITY,
        },
    }

