"""Business logic for the standalone poll / vote system (投票活动).

See ``classroom_app/db/schema_polls.py`` for the data model. Key invariants:

* A poll's vote data is shared across every class it is assigned to — a single
  ``polls`` row backs all assigned classes (no per-class forking).
* Three states: ``draft`` (creator-only), ``active`` (open to participants),
  ``closed`` (read-only). A poll past its deadline is treated as closed.
* Eligible voters ("participants") are either every active student in the
  assigned classes (``audience_scope='class'``) or an explicit list
  (``audience_scope='custom'``, used by student-created polls). PM-blacklisted
  peers cannot be added by a student creator.
* Result visibility is gated by ``result_visibility`` (always / after_vote /
  after_close); owners and teachers of an assigned class always see results.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Optional

from fastapi import HTTPException

from ..db.connection import execute_insert_returning_id
from ..db.schema_polls import ensure_poll_schema
from .resource_access_service import ensure_classroom_access, is_super_admin_teacher


POLL_STATUS_DRAFT = "draft"
POLL_STATUS_ACTIVE = "active"
POLL_STATUS_CLOSED = "closed"
POLL_STATUSES = {POLL_STATUS_DRAFT, POLL_STATUS_ACTIVE, POLL_STATUS_CLOSED}

VOTE_TYPE_SINGLE = "single"
VOTE_TYPE_MULTIPLE = "multiple"
VOTE_TYPES = {VOTE_TYPE_SINGLE, VOTE_TYPE_MULTIPLE}

AUDIENCE_CLASS = "class"
AUDIENCE_CUSTOM = "custom"
AUDIENCE_SCOPES = {AUDIENCE_CLASS, AUDIENCE_CUSTOM}

VISIBILITY_ALWAYS = "always"
VISIBILITY_AFTER_VOTE = "after_vote"
VISIBILITY_AFTER_CLOSE = "after_close"
VISIBILITIES = {VISIBILITY_ALWAYS, VISIBILITY_AFTER_VOTE, VISIBILITY_AFTER_CLOSE}

ORIGIN_MANAGEMENT = "management"
ORIGIN_CLASSROOM = "classroom"

MAX_OPTIONS = 12
MIN_OPTIONS = 2
MAX_PARTICIPANTS = 1000
MAX_ASSIGNED_CLASSES = 50


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
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
    return str(user.get("name") or user.get("username") or "成员").strip() or "成员"


def _normalize_deadline(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    # Accept "YYYY-MM-DDTHH:MM" (datetime-local) or full ISO; store normalized.
    normalized = text.replace("Z", "").replace(" ", "T")
    parsed = None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            parsed = datetime.strptime(normalized[: len(fmt) + 2], fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            raise HTTPException(400, "截止时间格式不正确")
    return parsed.replace(second=0, microsecond=0).isoformat(timespec="minutes")


def _deadline_passed(deadline_at: Any) -> bool:
    text = str(deadline_at or "").strip()
    if not text:
        return False
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return False
    return datetime.now() >= parsed


# --------------------------------------------------------------------------- #
# loaders
# --------------------------------------------------------------------------- #
def _load_poll(conn: sqlite3.Connection, poll_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM polls WHERE id = ? LIMIT 1", (int(poll_id),)).fetchone()
    if row is None:
        raise HTTPException(404, "投票活动不存在")
    return dict(row)


def _load_options(conn: sqlite3.Connection, poll_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM poll_options WHERE poll_id = ? ORDER BY sort_order, id",
        (int(poll_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _assigned_class_ids(conn: sqlite3.Connection, poll_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT class_offering_id FROM poll_assignments WHERE poll_id = ? ORDER BY class_offering_id",
        (int(poll_id),),
    ).fetchall()
    return [int(row["class_offering_id"]) for row in rows]


def _assigned_classes(conn: sqlite3.Connection, poll_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT o.id AS id, c.name AS course_name, cl.name AS class_name
        FROM poll_assignments pa
        JOIN class_offerings o ON o.id = pa.class_offering_id
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        WHERE pa.poll_id = ?
        ORDER BY c.name, cl.name
        """,
        (int(poll_id),),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "course_name": str(row["course_name"] or ""),
            "class_name": str(row["class_name"] or ""),
        }
        for row in rows
    ]


def _class_student_ids(conn: sqlite3.Connection, class_offering_ids: list[int]) -> set[int]:
    if not class_offering_ids:
        return set()
    placeholders = ",".join("?" for _ in class_offering_ids)
    rows = conn.execute(
        f"""
        SELECT DISTINCT s.id AS id
        FROM class_offerings o
        JOIN students s ON s.class_id = o.class_id
        WHERE o.id IN ({placeholders})
          AND COALESCE(s.enrollment_status, 'active') = 'active'
        """,
        tuple(class_offering_ids),
    ).fetchall()
    return {int(row["id"]) for row in rows}


def _custom_participant_ids(conn: sqlite3.Connection, poll_id: int) -> set[int]:
    rows = conn.execute(
        "SELECT student_id FROM poll_participants WHERE poll_id = ?",
        (int(poll_id),),
    ).fetchall()
    return {int(row["student_id"]) for row in rows}


def _participant_ids(conn: sqlite3.Connection, poll: dict[str, Any]) -> set[int]:
    if str(poll.get("audience_scope")) == AUDIENCE_CUSTOM:
        return _custom_participant_ids(conn, int(poll["id"]))
    return _class_student_ids(conn, _assigned_class_ids(conn, int(poll["id"])))


def _ballot(conn: sqlite3.Connection, poll_id: int, voter_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM poll_ballots WHERE poll_id = ? AND voter_id = ? LIMIT 1",
        (int(poll_id), int(voter_id)),
    ).fetchone()
    return dict(row) if row else None


def _vote_counts(conn: sqlite3.Connection, poll_id: int) -> tuple[int, dict[int, int]]:
    total_row = conn.execute(
        "SELECT COUNT(*) AS total FROM poll_ballots WHERE poll_id = ?",
        (int(poll_id),),
    ).fetchone()
    total_voters = int(total_row["total"] if total_row else 0)
    rows = conn.execute(
        "SELECT option_id, COUNT(*) AS total FROM poll_votes WHERE poll_id = ? GROUP BY option_id",
        (int(poll_id),),
    ).fetchall()
    by_option = {int(row["option_id"]): int(row["total"] or 0) for row in rows}
    return total_voters, by_option


def _my_vote_option_ids(conn: sqlite3.Connection, poll_id: int, voter_id: int) -> set[int]:
    rows = conn.execute(
        "SELECT option_id FROM poll_votes WHERE poll_id = ? AND voter_id = ?",
        (int(poll_id), int(voter_id)),
    ).fetchall()
    return {int(row["option_id"]) for row in rows}


# --------------------------------------------------------------------------- #
# permission / status helpers
# --------------------------------------------------------------------------- #
def _can_manage(conn: sqlite3.Connection, poll: dict[str, Any], user: dict[str, Any]) -> bool:
    role = str(user.get("role") or "").strip().lower()
    if str(poll.get("owner_role")) == role and int(poll.get("owner_user_pk") or 0) == _user_pk(user):
        return True
    if _is_teacher(user) and is_super_admin_teacher(conn, _user_pk(user)):
        return True
    return False


def _is_class_teacher(conn: sqlite3.Connection, poll: dict[str, Any], user: dict[str, Any]) -> bool:
    if not _is_teacher(user):
        return False
    class_ids = _assigned_class_ids(conn, int(poll["id"]))
    if not class_ids:
        return False
    placeholders = ",".join("?" for _ in class_ids)
    row = conn.execute(
        f"SELECT 1 FROM class_offerings WHERE id IN ({placeholders}) AND teacher_id = ? LIMIT 1",
        (*class_ids, _user_pk(user)),
    ).fetchone()
    return row is not None


def _effective_status(poll: dict[str, Any]) -> str:
    status = str(poll.get("status") or POLL_STATUS_DRAFT)
    if status == POLL_STATUS_ACTIVE and _deadline_passed(poll.get("deadline_at")):
        return POLL_STATUS_CLOSED
    return status


def _ensure_poll_view_access(conn: sqlite3.Connection, poll: dict[str, Any], user: dict[str, Any]) -> None:
    if _can_manage(conn, poll, user) or _is_class_teacher(conn, poll, user):
        return
    if _is_student(user) and _user_pk(user) in _participant_ids(conn, poll):
        return
    raise HTTPException(403, "无权查看该投票活动")


def _can_show_results(
    poll: dict[str, Any],
    user: dict[str, Any],
    *,
    has_voted: bool,
    effective_status: str,
    privileged: bool,
) -> bool:
    if privileged:
        return True
    visibility = str(poll.get("result_visibility") or VISIBILITY_AFTER_VOTE)
    if visibility == VISIBILITY_ALWAYS:
        return True
    if visibility == VISIBILITY_AFTER_VOTE:
        return has_voted or effective_status == POLL_STATUS_CLOSED
    if visibility == VISIBILITY_AFTER_CLOSE:
        return effective_status == POLL_STATUS_CLOSED
    return False


# --------------------------------------------------------------------------- #
# blacklist
# --------------------------------------------------------------------------- #
def _student_blockers(conn: sqlite3.Connection, student_id: int) -> set[int]:
    """student ids who have PM-blacklisted the given student (they blocked me)."""
    identity = f"student:{int(student_id)}"
    rows = conn.execute(
        """
        SELECT owner_user_pk
        FROM private_message_blocks
        WHERE blocked_identity = ? AND owner_role = 'student'
        """,
        (identity,),
    ).fetchall()
    return {int(row["owner_user_pk"]) for row in rows if row["owner_user_pk"] is not None}


# --------------------------------------------------------------------------- #
# serialization
# --------------------------------------------------------------------------- #
def _serialize_option(
    option: dict[str, Any],
    *,
    total_voters: int,
    count: int,
    show_results: bool,
    selected: bool,
) -> dict[str, Any]:
    payload = {
        "id": int(option["id"]),
        "label": str(option["label"] or ""),
        "sort_order": int(option["sort_order"] or 0),
        "selected": selected,
    }
    if show_results:
        payload["count"] = count
        payload["percent"] = round((count / total_voters) * 100, 1) if total_voters else 0
    return payload


def serialize_poll(
    conn: sqlite3.Connection,
    poll: dict[str, Any],
    user: dict[str, Any],
    *,
    include_options: bool = True,
) -> dict[str, Any]:
    poll_id = int(poll["id"])
    privileged = _can_manage(conn, poll, user) or _is_class_teacher(conn, poll, user)
    can_manage = _can_manage(conn, poll, user)
    effective_status = _effective_status(poll)
    voter_id = _user_pk(user) if _is_student(user) else 0
    ballot = _ballot(conn, poll_id, voter_id) if _is_student(user) else None
    has_voted = ballot is not None
    total_voters, counts = _vote_counts(conn, poll_id)
    show_results = _can_show_results(
        poll, user, has_voted=has_voted, effective_status=effective_status, privileged=privileged
    )
    my_option_ids = _my_vote_option_ids(conn, poll_id, voter_id) if has_voted else set()

    options: list[dict[str, Any]] = []
    if include_options:
        for option in _load_options(conn, poll_id):
            options.append(
                _serialize_option(
                    option,
                    total_voters=total_voters,
                    count=int(counts.get(int(option["id"]), 0)),
                    show_results=show_results,
                    selected=int(option["id"]) in my_option_ids,
                )
            )

    participant_set = _participant_ids(conn, poll)
    participant_total = len(participant_set)
    is_participant = _is_student(user) and _user_pk(user) in participant_set
    # Expose the explicit participant list to the owner so the edit form can
    # pre-select it (only meaningful for custom-audience student polls).
    custom_participant_ids: list[int] = []
    if can_manage and str(poll.get("audience_scope")) == AUDIENCE_CUSTOM:
        custom_participant_ids = sorted(_custom_participant_ids(conn, poll_id))

    is_mine = str(poll.get("owner_role")) == str(user.get("role") or "").lower() and int(
        poll.get("owner_user_pk") or 0
    ) == (_user_pk(user) if user.get("id") is not None else -1)

    change_count = int(ballot.get("change_count") or 0) if ballot else 0
    allow_change = bool(poll.get("allow_change"))
    max_changes = int(poll.get("max_changes") or 0)
    can_change = allow_change and (max_changes == 0 or change_count < max_changes)
    can_vote = (
        _is_student(user)
        and is_participant
        and effective_status == POLL_STATUS_ACTIVE
        and (not has_voted or can_change)
    )

    return {
        "id": poll_id,
        "title": str(poll.get("title") or "投票"),
        "description": str(poll.get("description") or ""),
        "vote_type": str(poll.get("vote_type") or VOTE_TYPE_SINGLE),
        "status": str(poll.get("status") or POLL_STATUS_DRAFT),
        "effective_status": effective_status,
        "origin": str(poll.get("origin") or ORIGIN_MANAGEMENT),
        "audience_scope": str(poll.get("audience_scope") or AUDIENCE_CLASS),
        "deadline_at": str(poll.get("deadline_at") or ""),
        "deadline_passed": _deadline_passed(poll.get("deadline_at")),
        "allow_change": allow_change,
        "max_changes": max_changes,
        "result_visibility": str(poll.get("result_visibility") or VISIBILITY_AFTER_VOTE),
        "owner_role": str(poll.get("owner_role") or "teacher"),
        "owner_name": str(poll.get("owner_name") or ""),
        "is_mine": is_mine,
        "created_at": str(poll.get("created_at") or ""),
        "updated_at": str(poll.get("updated_at") or ""),
        "closed_at": str(poll.get("closed_at") or ""),
        "options": options,
        "option_count": len(options) if include_options else None,
        "total_voters": total_voters,
        "participant_total": participant_total,
        "is_participant": is_participant,
        "my_participant_ids": custom_participant_ids,
        "has_voted": has_voted,
        "my_option_ids": sorted(my_option_ids),
        "change_count": change_count,
        "show_results": show_results,
        "can_vote": can_vote,
        "can_change": can_change,
        "can_manage": can_manage,
        "assigned_classes": _assigned_classes(conn, poll_id),
    }


# --------------------------------------------------------------------------- #
# option / participant / class normalization for writes
# --------------------------------------------------------------------------- #
def _normalize_options(raw_options: Any) -> list[str]:
    if not isinstance(raw_options, list):
        raise HTTPException(400, "请至少填写两个选项")
    labels: list[str] = []
    for item in raw_options[:MAX_OPTIONS]:
        label = item.get("label") if isinstance(item, dict) else item
        text = _normalize_text(label, limit=160, field_name="选项")
        if text:
            labels.append(text)
    if len(labels) < MIN_OPTIONS:
        raise HTTPException(400, f"请至少填写 {MIN_OPTIONS} 个有效选项")
    return labels


def _write_options(conn: sqlite3.Connection, poll_id: int, labels: list[str]) -> None:
    now = _now_iso()
    for index, label in enumerate(labels):
        conn.execute(
            "INSERT INTO poll_options (poll_id, label, sort_order, created_at) VALUES (?, ?, ?, ?)",
            (int(poll_id), label, index, now),
        )


def _write_assignments(
    conn: sqlite3.Connection,
    poll_id: int,
    class_offering_ids: list[int],
    *,
    assigned_by_role: str,
    assigned_by_user_pk: int,
) -> None:
    now = _now_iso()
    for class_offering_id in class_offering_ids:
        conn.execute(
            """
            INSERT INTO poll_assignments
                (poll_id, class_offering_id, assigned_by_role, assigned_by_user_pk, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(poll_id, class_offering_id) DO NOTHING
            """,
            (int(poll_id), int(class_offering_id), assigned_by_role, int(assigned_by_user_pk), now),
        )


def _write_participants(conn: sqlite3.Connection, poll_id: int, student_ids: list[int]) -> None:
    now = _now_iso()
    for student_id in student_ids:
        conn.execute(
            """
            INSERT INTO poll_participants (poll_id, student_id, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(poll_id, student_id) DO NOTHING
            """,
            (int(poll_id), int(student_id), now),
        )


def _validate_teacher_offerings(conn: sqlite3.Connection, user: dict[str, Any], class_offering_ids: list[int]) -> list[int]:
    normalized = sorted({int(cid) for cid in class_offering_ids if _safe_int(cid) is not None})
    if not normalized:
        return []
    if len(normalized) > MAX_ASSIGNED_CLASSES:
        raise HTTPException(400, "分配的班级数量过多")
    super_admin = is_super_admin_teacher(conn, _user_pk(user))
    placeholders = ",".join("?" for _ in normalized)
    rows = conn.execute(
        f"SELECT id, teacher_id FROM class_offerings WHERE id IN ({placeholders})",
        tuple(normalized),
    ).fetchall()
    found = {int(row["id"]): int(row["teacher_id"]) for row in rows}
    result: list[int] = []
    for cid in normalized:
        if cid not in found:
            raise HTTPException(404, "课堂不存在")
        if found[cid] != _user_pk(user) and not super_admin:
            raise HTTPException(403, "无权分配到该课堂")
        result.append(cid)
    return result


def _validate_custom_participants(
    conn: sqlite3.Connection,
    class_offering_id: int,
    creator_student_id: int,
    student_ids: list[int],
) -> list[int]:
    eligible = _class_student_ids(conn, [int(class_offering_id)])
    blockers = _student_blockers(conn, creator_student_id)
    chosen = sorted({int(sid) for sid in student_ids if _safe_int(sid) is not None})
    result: list[int] = []
    for sid in chosen:
        if sid == creator_student_id:
            continue
        if sid not in eligible:
            continue
        if sid in blockers:
            continue
        result.append(sid)
    # The creator themselves is always a participant of their own poll.
    if creator_student_id in eligible:
        result.append(creator_student_id)
    return sorted(set(result))


# --------------------------------------------------------------------------- #
# create / update / status / delete
# --------------------------------------------------------------------------- #
def _normalize_create_status(value: Any) -> str:
    status = str(value or POLL_STATUS_DRAFT).strip().lower()
    if status == POLL_STATUS_CLOSED:
        raise HTTPException(400, "新建投票不能直接设置为已结束")
    if status not in {POLL_STATUS_DRAFT, POLL_STATUS_ACTIVE}:
        raise HTTPException(400, "投票状态不合法")
    return status


def _normalize_vote_type(value: Any) -> str:
    vote_type = str(value or VOTE_TYPE_SINGLE).strip().lower()
    if vote_type not in VOTE_TYPES:
        raise HTTPException(400, "投票形式不合法")
    return vote_type


def _normalize_visibility(value: Any) -> str:
    visibility = str(value or VISIBILITY_AFTER_VOTE).strip().lower()
    if visibility not in VISIBILITIES:
        raise HTTPException(400, "统计可见时机不合法")
    return visibility


def _common_poll_fields(payload: dict[str, Any]) -> dict[str, Any]:
    title = _normalize_text(payload.get("title"), limit=120, field_name="标题", required=True)
    description = _normalize_text(payload.get("description"), limit=1000, field_name="说明")
    vote_type = _normalize_vote_type(payload.get("vote_type"))
    deadline_at = _normalize_deadline(payload.get("deadline_at"))
    allow_change = _coerce_bool(payload.get("allow_change"), default=False)
    max_changes = _safe_int(payload.get("max_changes")) or 0
    if max_changes < 0:
        max_changes = 0
    if not allow_change:
        max_changes = 0
    result_visibility = _normalize_visibility(payload.get("result_visibility"))
    return {
        "title": title,
        "description": description,
        "vote_type": vote_type,
        "deadline_at": deadline_at,
        "allow_change": 1 if allow_change else 0,
        "max_changes": max_changes,
        "result_visibility": result_visibility,
    }


def create_poll(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    payload: dict[str, Any],
    *,
    origin: str,
    class_offering_id: Optional[int] = None,
) -> dict[str, Any]:
    ensure_poll_schema(conn)
    fields = _common_poll_fields(payload)
    labels = _normalize_options(payload.get("options"))
    status = _normalize_create_status(payload.get("status"))
    now = _now_iso()

    role = "teacher" if _is_teacher(user) else "student"

    if origin == ORIGIN_CLASSROOM:
        if class_offering_id is None:
            raise HTTPException(400, "缺少课堂信息")
        ensure_classroom_access(conn, int(class_offering_id), user)

    # Determine audience + targets.
    if _is_student(user):
        # Students always create custom-participant polls scoped to one classroom.
        if origin != ORIGIN_CLASSROOM or class_offering_id is None:
            raise HTTPException(400, "学生只能在课堂内创建投票")
        audience_scope = AUDIENCE_CUSTOM
        participants = _validate_custom_participants(
            conn, int(class_offering_id), _user_pk(user), payload.get("participant_ids") or []
        )
        if len([p for p in participants if p != _user_pk(user)]) < 1:
            raise HTTPException(400, "请至少选择一名参与者")
        assigned_classes = [int(class_offering_id)]
    else:
        audience_scope = AUDIENCE_CLASS
        if origin == ORIGIN_CLASSROOM:
            assigned_classes = [int(class_offering_id)]
        else:
            assigned_classes = _validate_teacher_offerings(conn, user, payload.get("class_offering_ids") or [])
        participants = []

    poll_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO polls (
            owner_role, owner_user_pk, owner_name, title, description, vote_type,
            status, origin, origin_class_offering_id, audience_scope, deadline_at,
            allow_change, max_changes, result_visibility, created_at, updated_at, settings_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')
        """,
        (
            role,
            _user_pk(user),
            _actor_name(user),
            fields["title"],
            fields["description"],
            fields["vote_type"],
            status,
            origin,
            int(class_offering_id) if class_offering_id is not None else None,
            audience_scope,
            fields["deadline_at"],
            fields["allow_change"],
            fields["max_changes"],
            fields["result_visibility"],
            now,
            now,
        ),
    )
    _write_options(conn, poll_id, labels)
    _write_assignments(
        conn, poll_id, assigned_classes, assigned_by_role=role, assigned_by_user_pk=_user_pk(user)
    )
    if audience_scope == AUDIENCE_CUSTOM:
        _write_participants(conn, poll_id, participants)
    return load_poll_detail(conn, poll_id, user)


def update_poll(conn: sqlite3.Connection, poll_id: int, user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    ensure_poll_schema(conn)
    poll = _load_poll(conn, int(poll_id))
    if not _can_manage(conn, poll, user):
        raise HTTPException(403, "无权编辑该投票活动")

    fields = _common_poll_fields(payload)
    has_votes = bool(
        conn.execute("SELECT 1 FROM poll_ballots WHERE poll_id = ? LIMIT 1", (int(poll_id),)).fetchone()
    )

    # Options / vote_type can only *change* once votes exist — but editing other
    # fields (title, deadline, etc.) must still work. So only block when the
    # submitted options actually differ from what's stored.
    new_labels: Optional[list[str]] = None
    if "options" in payload and payload.get("options") is not None:
        submitted_labels = _normalize_options(payload.get("options"))
        existing_labels = [str(o["label"] or "") for o in _load_options(conn, int(poll_id))]
        if submitted_labels != existing_labels:
            if has_votes:
                raise HTTPException(400, "已有投票记录，无法修改选项")
            new_labels = submitted_labels
    if has_votes and fields["vote_type"] != str(poll.get("vote_type")):
        raise HTTPException(400, "已有投票记录，无法修改单选/多选")

    now = _now_iso()
    conn.execute(
        """
        UPDATE polls
        SET title = ?, description = ?, vote_type = ?, deadline_at = ?,
            allow_change = ?, max_changes = ?, result_visibility = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            fields["title"],
            fields["description"],
            fields["vote_type"],
            fields["deadline_at"],
            fields["allow_change"],
            fields["max_changes"],
            fields["result_visibility"],
            now,
            int(poll_id),
        ),
    )
    if new_labels is not None:
        conn.execute("DELETE FROM poll_options WHERE poll_id = ?", (int(poll_id),))
        _write_options(conn, poll_id, new_labels)

    # Audience updates (custom participants for student polls; class assignment
    # for teacher management polls) — only the owner, only before votes exist.
    if str(poll.get("audience_scope")) == AUDIENCE_CUSTOM and "participant_ids" in payload:
        class_id = _safe_int(poll.get("origin_class_offering_id"))
        if class_id is not None:
            participants = _validate_custom_participants(
                conn, int(class_id), int(poll["owner_user_pk"]), payload.get("participant_ids") or []
            )
            owner_pk = int(poll["owner_user_pk"])
            if len([p for p in participants if p != owner_pk]) < 1:
                raise HTTPException(400, "请至少选择一名参与者")
            # Only block / rewrite when the participant set actually changes.
            if set(participants) != _custom_participant_ids(conn, int(poll_id)):
                if has_votes:
                    raise HTTPException(400, "已有投票记录，无法修改参与者")
                conn.execute("DELETE FROM poll_participants WHERE poll_id = ?", (int(poll_id),))
                _write_participants(conn, poll_id, participants)
    elif (
        str(poll.get("audience_scope")) == AUDIENCE_CLASS
        and str(poll.get("origin")) == ORIGIN_MANAGEMENT
        and "class_offering_ids" in payload
    ):
        set_poll_assignments(conn, poll_id, user, payload.get("class_offering_ids") or [], _reload=False)

    return load_poll_detail(conn, int(poll_id), user)


def set_poll_status(conn: sqlite3.Connection, poll_id: int, user: dict[str, Any], status: Any) -> dict[str, Any]:
    ensure_poll_schema(conn)
    poll = _load_poll(conn, int(poll_id))
    if not _can_manage(conn, poll, user):
        raise HTTPException(403, "无权变更该投票状态")
    new_status = str(status or "").strip().lower()
    if new_status not in POLL_STATUSES:
        raise HTTPException(400, "投票状态不合法")
    if new_status == POLL_STATUS_ACTIVE:
        if not _assigned_class_ids(conn, int(poll_id)):
            raise HTTPException(400, "请先分配到至少一个班级再开始投票")
        if len(_load_options(conn, int(poll_id))) < MIN_OPTIONS:
            raise HTTPException(400, "选项不足，无法开始投票")
        if _deadline_passed(poll.get("deadline_at")):
            raise HTTPException(400, "截止时间已过，请先调整或清空截止时间再开始")
    now = _now_iso()
    closed_at = now if new_status == POLL_STATUS_CLOSED else None
    conn.execute(
        "UPDATE polls SET status = ?, closed_at = ?, updated_at = ? WHERE id = ?",
        (new_status, closed_at, now, int(poll_id)),
    )
    return load_poll_detail(conn, int(poll_id), user)


def delete_poll(conn: sqlite3.Connection, poll_id: int, user: dict[str, Any]) -> int:
    ensure_poll_schema(conn)
    poll = _load_poll(conn, int(poll_id))
    if not _can_manage(conn, poll, user):
        raise HTTPException(403, "无权删除该投票活动")
    for table in ("poll_votes", "poll_ballots", "poll_participants", "poll_assignments", "poll_options"):
        conn.execute(f"DELETE FROM {table} WHERE poll_id = ?", (int(poll_id),))
    conn.execute("DELETE FROM polls WHERE id = ?", (int(poll_id),))
    return int(poll_id)


def set_poll_assignments(
    conn: sqlite3.Connection,
    poll_id: int,
    user: dict[str, Any],
    class_offering_ids: list[int],
    *,
    _reload: bool = True,
) -> dict[str, Any]:
    ensure_poll_schema(conn)
    poll = _load_poll(conn, int(poll_id))
    if not _can_manage(conn, poll, user):
        raise HTTPException(403, "无权分配该投票活动")
    if str(poll.get("origin")) != ORIGIN_MANAGEMENT or str(poll.get("audience_scope")) != AUDIENCE_CLASS:
        raise HTTPException(400, "该投票活动不支持跨班级分配")
    valid = _validate_teacher_offerings(conn, user, class_offering_ids)
    conn.execute("DELETE FROM poll_assignments WHERE poll_id = ?", (int(poll_id),))
    role = "teacher" if _is_teacher(user) else "student"
    _write_assignments(conn, poll_id, valid, assigned_by_role=role, assigned_by_user_pk=_user_pk(user))
    conn.execute("UPDATE polls SET updated_at = ? WHERE id = ?", (_now_iso(), int(poll_id)))
    if _reload:
        return load_poll_detail(conn, int(poll_id), user)
    return {}


# --------------------------------------------------------------------------- #
# voting
# --------------------------------------------------------------------------- #
def vote(conn: sqlite3.Connection, poll_id: int, user: dict[str, Any], option_ids: Any) -> dict[str, Any]:
    ensure_poll_schema(conn)
    poll = _load_poll(conn, int(poll_id))
    if not _is_student(user):
        raise HTTPException(403, "只有学生可以参与投票")
    voter_id = _user_pk(user)
    if voter_id not in _participant_ids(conn, poll):
        raise HTTPException(403, "你不在该投票活动的参与名单内")
    if _effective_status(poll) != POLL_STATUS_ACTIVE:
        raise HTTPException(400, "该投票已结束或尚未开始")

    chosen = [int(oid) for oid in (option_ids or []) if _safe_int(oid) is not None]
    chosen = sorted(set(chosen))
    if not chosen:
        raise HTTPException(400, "请选择至少一个选项")
    if str(poll.get("vote_type")) == VOTE_TYPE_SINGLE and len(chosen) != 1:
        raise HTTPException(400, "该投票为单选，请只选择一个选项")

    valid_option_ids = {int(opt["id"]) for opt in _load_options(conn, int(poll_id))}
    if any(oid not in valid_option_ids for oid in chosen):
        raise HTTPException(400, "选项不存在")

    now = _now_iso()
    ballot = _ballot(conn, int(poll_id), voter_id)
    if ballot is None:
        execute_insert_returning_id(
            conn,
            "INSERT INTO poll_ballots (poll_id, voter_id, change_count, created_at, updated_at) VALUES (?, ?, 0, ?, ?)",
            (int(poll_id), voter_id, now, now),
        )
    else:
        allow_change = bool(poll.get("allow_change"))
        max_changes = int(poll.get("max_changes") or 0)
        change_count = int(ballot.get("change_count") or 0)
        if not allow_change:
            raise HTTPException(400, "该投票不允许修改")
        if max_changes and change_count >= max_changes:
            raise HTTPException(400, "已达到可修改次数上限")
        conn.execute(
            "UPDATE poll_ballots SET change_count = change_count + 1, updated_at = ? WHERE poll_id = ? AND voter_id = ?",
            (now, int(poll_id), voter_id),
        )
        conn.execute(
            "DELETE FROM poll_votes WHERE poll_id = ? AND voter_id = ?",
            (int(poll_id), voter_id),
        )
    for option_id in chosen:
        conn.execute(
            "INSERT INTO poll_votes (poll_id, voter_id, option_id, created_at) VALUES (?, ?, ?, ?)",
            (int(poll_id), voter_id, int(option_id), now),
        )
    return load_poll_detail(conn, int(poll_id), user)


# --------------------------------------------------------------------------- #
# read APIs
# --------------------------------------------------------------------------- #
def load_poll_detail(conn: sqlite3.Connection, poll_id: int, user: dict[str, Any]) -> dict[str, Any]:
    ensure_poll_schema(conn)
    poll = _load_poll(conn, int(poll_id))
    _ensure_poll_view_access(conn, poll, user)
    return serialize_poll(conn, poll, user, include_options=True)


def load_classroom_snapshot(conn: sqlite3.Connection, class_offering_id: int, user: dict[str, Any]) -> dict[str, Any]:
    ensure_poll_schema(conn)
    offering = dict(ensure_classroom_access(conn, int(class_offering_id), user))

    rows = conn.execute(
        """
        SELECT DISTINCT p.*
        FROM polls p
        JOIN poll_assignments pa ON pa.poll_id = p.id
        WHERE pa.class_offering_id = ?
        ORDER BY
            CASE p.status WHEN 'active' THEN 0 WHEN 'closed' THEN 1 ELSE 2 END,
            p.updated_at DESC, p.id DESC
        """,
        (int(class_offering_id),),
    ).fetchall()

    cards: list[dict[str, Any]] = []
    for row in rows:
        poll = dict(row)
        status = str(poll.get("status"))
        is_owner = str(poll.get("owner_role")) == str(user.get("role") or "").lower() and int(
            poll.get("owner_user_pk") or 0
        ) == _user_pk(user)
        # Visibility: drafts only to creator. Non-draft polls visible to
        # participants (and to the class teacher / owner).
        if status == POLL_STATUS_DRAFT and not is_owner:
            continue
        if not is_owner:
            privileged = _can_manage(conn, poll, user) or _is_class_teacher(conn, poll, user)
            if not privileged:
                if not (_is_student(user) and _user_pk(user) in _participant_ids(conn, poll)):
                    continue
        cards.append(serialize_poll(conn, poll, user, include_options=True))

    active_cards = [c for c in cards if c["effective_status"] == POLL_STATUS_ACTIVE]
    mine_cards = [c for c in cards if c["is_mine"]]
    return {
        "classroom": {
            "id": int(offering["id"]),
            "course_name": str(offering.get("course_name") or ""),
            "class_name": str(offering.get("class_name") or ""),
        },
        "role": str(user.get("role") or ""),
        "can_create": True,
        "polls": cards,
        "summary": {
            "total": len(cards),
            "active": len(active_cards),
            "mine": len(mine_cards),
        },
    }


def load_management_list(conn: sqlite3.Connection, user: dict[str, Any]) -> dict[str, Any]:
    ensure_poll_schema(conn)
    if not _is_teacher(user):
        raise HTTPException(403, "只有教师可以管理投票活动")
    rows = conn.execute(
        """
        SELECT * FROM polls
        WHERE owner_role = 'teacher' AND owner_user_pk = ? AND origin = 'management'
        ORDER BY
            CASE status WHEN 'active' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END,
            updated_at DESC, id DESC
        """,
        (_user_pk(user),),
    ).fetchall()
    polls = [serialize_poll(conn, dict(row), user, include_options=True) for row in rows]
    return {
        "role": "teacher",
        "polls": polls,
        "summary": {
            "total": len(polls),
            "active": sum(1 for p in polls if p["effective_status"] == POLL_STATUS_ACTIVE),
            "draft": sum(1 for p in polls if p["status"] == POLL_STATUS_DRAFT),
            "closed": sum(1 for p in polls if p["effective_status"] == POLL_STATUS_CLOSED),
        },
    }


def list_teacher_offerings(conn: sqlite3.Connection, user: dict[str, Any]) -> list[dict[str, Any]]:
    ensure_poll_schema(conn)
    if not _is_teacher(user):
        raise HTTPException(403, "只有教师可以分配投票活动")
    rows = conn.execute(
        """
        SELECT o.id AS id, c.name AS course_name, cl.name AS class_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        WHERE o.teacher_id = ?
        ORDER BY c.name, cl.name
        """,
        (_user_pk(user),),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "course_name": str(row["course_name"] or ""),
            "class_name": str(row["class_name"] or ""),
        }
        for row in rows
    ]


def list_class_candidates(conn: sqlite3.Connection, class_offering_id: int, user: dict[str, Any]) -> list[dict[str, Any]]:
    """Class members a student can add to a poll (PM-blacklisters excluded)."""
    ensure_poll_schema(conn)
    ensure_classroom_access(conn, int(class_offering_id), user)
    if not _is_student(user):
        creator_id = -1
        blockers: set[int] = set()
    else:
        creator_id = _user_pk(user)
        blockers = _student_blockers(conn, creator_id)
    rows = conn.execute(
        """
        SELECT s.id AS id, s.name AS name, s.student_id_number AS student_id_number
        FROM class_offerings o
        JOIN students s ON s.class_id = o.class_id
        WHERE o.id = ?
          AND COALESCE(s.enrollment_status, 'active') = 'active'
        ORDER BY s.student_id_number, s.id
        """,
        (int(class_offering_id),),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        sid = int(row["id"])
        if sid == creator_id:
            continue
        candidates.append(
            {
                "id": sid,
                "name": str(row["name"] or "同学"),
                "student_id_number": str(row["student_id_number"] or ""),
                "blocked": sid in blockers,
            }
        )
    return candidates
