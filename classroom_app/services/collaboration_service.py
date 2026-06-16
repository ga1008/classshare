from __future__ import annotations

import json
import math
import mimetypes
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import quote

from fastapi import HTTPException

from ..db.connection import execute_insert_returning_id
from .blog_service import POST_STATUS_DRAFT, VISIBILITY_CLASS, create_post
from .file_service import resolve_global_file_path
from .message_center_service import create_collaboration_notification
from .resource_access_service import ensure_classroom_access as ensure_scoped_classroom_access


GROUP_STATUS_ACTIVE = "active"
GROUP_STATUS_ARCHIVED = "archived"
GROUP_JOIN_OPEN = "open"
GROUP_JOIN_LOCKED = "locked"
GROUP_JOIN_TEACHER_ASSIGNED = "teacher_assigned"
GROUP_JOIN_POLICIES = {GROUP_JOIN_OPEN, GROUP_JOIN_LOCKED, GROUP_JOIN_TEACHER_ASSIGNED, "invite"}
MAX_GROUP_MEMBERS_LIMIT = 12
DEFAULT_GROUP_MAX_MEMBERS = 6

# --- Random study-group scheme constants -------------------------------------
SCHEME_STATUS_ACTIVE = "active"
SCHEME_STATUS_CLOSED = "closed"
SCHEME_JOIN_POLICY = "scheme_random"  # study_groups.join_policy for scheme groups
MAX_SCHEME_GROUP_COUNT = 60
MIN_SCHEME_MEMBERS = 1
GROUP_PROGRESS_MIN = 0
GROUP_PROGRESS_MAX = 100

# --- Student-initiated invite groups (学生自由发起分组) ----------------------
STUDENT_GROUP_JOIN_POLICY = "invite"
STUDENT_MAX_ACTIVE_GROUPS = 5
INVITE_MAX_PER_HOUR = 10
INVITE_MIN_INTERVAL_SECONDS = 30
INVITE_DECLINE_BLOCK_THRESHOLD = 3
INVITE_STATUS_PENDING = "pending"
INVITE_STATUS_ACCEPTED = "accepted"
INVITE_STATUS_DECLINED = "declined"
INVITE_STATUS_CANCELLED = "cancelled"


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _truncate(value: Any, limit: int = 140) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def _normalize_text(value: Any, *, limit: int, field_name: str, required: bool = False) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if required and not text:
        raise HTTPException(400, f"{field_name}不能为空")
    if len(text) > limit:
        raise HTTPException(400, f"{field_name}不能超过 {limit} 个字符")
    return text


def _normalize_assignment_id(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _normalize_join_policy(value: Any, *, default: str = GROUP_JOIN_OPEN) -> str:
    policy = str(value or default).strip().lower()
    if policy not in GROUP_JOIN_POLICIES:
        raise HTTPException(400, "小组加入策略不合法")
    return policy


def _normalize_max_members(value: Any) -> int:
    parsed = _safe_int(value)
    if parsed is None:
        parsed = DEFAULT_GROUP_MAX_MEMBERS
    return max(2, min(MAX_GROUP_MEMBERS_LIMIT, parsed))


def _normalize_score(value: Any, field_name: str) -> int:
    parsed = _safe_int(value)
    if parsed is None or parsed < 1 or parsed > 5:
        raise HTTPException(400, f"{field_name}必须是 1 到 5 分")
    return parsed


def _is_teacher(user: dict[str, Any]) -> bool:
    return str(user.get("role") or "").lower() == "teacher"


def _is_student(user: dict[str, Any]) -> bool:
    return str(user.get("role") or "").lower() == "student"


def _user_pk(user: dict[str, Any]) -> int:
    user_id = _safe_int(user.get("id"))
    if user_id is None:
        raise HTTPException(403, "当前账号无效")
    return user_id


def _actor_name(user: dict[str, Any]) -> str:
    return str(user.get("name") or user.get("username") or "课堂成员").strip()


def ensure_classroom_access(conn, class_offering_id: int, user: dict[str, Any]) -> dict[str, Any]:
    return dict(ensure_scoped_classroom_access(conn, class_offering_id, user))


def _load_assignment(conn, class_offering_id: int, assignment_id: Any) -> Optional[dict[str, Any]]:
    normalized_id = _normalize_assignment_id(assignment_id)
    if not normalized_id:
        return None
    row = conn.execute(
        """
        SELECT id, title, status, exam_paper_id, due_at, class_offering_id
        FROM assignments
        WHERE id = ? AND class_offering_id = ?
        LIMIT 1
        """,
        (normalized_id, int(class_offering_id)),
    ).fetchone()
    if row is None:
        raise HTTPException(400, "关联任务不存在或不属于当前课堂")
    return dict(row)


def _load_assignment_options(conn, class_offering_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, title, status, exam_paper_id, due_at
        FROM assignments
        WHERE class_offering_id = ?
        ORDER BY
            CASE status WHEN 'published' THEN 0 WHEN 'new' THEN 1 ELSE 2 END,
            created_at DESC,
            id DESC
        """,
        (int(class_offering_id),),
    ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "title": str(row["title"] or "未命名任务"),
            "status": str(row["status"] or ""),
            "is_exam": bool(row["exam_paper_id"]),
            "due_at": str(row["due_at"] or ""),
        }
        for row in rows
    ]


def _load_classroom_students(conn, class_offering_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.id, s.name, s.student_id_number
        FROM class_offerings o
        JOIN students s ON s.class_id = o.class_id
        WHERE o.id = ?
          AND COALESCE(s.enrollment_status, 'active') = 'active'
        ORDER BY s.student_id_number, s.id
        """,
        (int(class_offering_id),),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "name": str(row["name"] or "同学"),
            "student_id_number": str(row["student_id_number"] or ""),
        }
        for row in rows
    ]


def _load_group(conn, group_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT g.*, a.title AS assignment_title, o.teacher_id, o.class_id,
               c.name AS course_name, cl.name AS class_name
        FROM study_groups g
        JOIN class_offerings o ON o.id = g.class_offering_id
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        LEFT JOIN assignments a ON a.id = g.assignment_id
        WHERE g.id = ?
        LIMIT 1
        """,
        (int(group_id),),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "小组不存在")
    return dict(row)


def _ensure_group_access(conn, group_id: int, user: dict[str, Any]) -> dict[str, Any]:
    group = _load_group(conn, group_id)
    ensure_classroom_access(conn, int(group["class_offering_id"]), user)
    return group


def _member_row(conn, group_id: int, student_id: int):
    return conn.execute(
        """
        SELECT *
        FROM study_group_members
        WHERE group_id = ?
          AND student_id = ?
          AND status = 'active'
        LIMIT 1
        """,
        (int(group_id), int(student_id)),
    ).fetchone()


def _is_active_member(conn, group_id: int, student_id: int) -> bool:
    return _member_row(conn, group_id, student_id) is not None


def _can_manage_group(conn, group: dict[str, Any], user: dict[str, Any]) -> bool:
    if _is_teacher(user):
        return int(group["teacher_id"]) == _user_pk(user)
    if not _is_student(user):
        return False
    return int(group.get("leader_student_id") or 0) == _user_pk(user)


def _can_access_group_work(conn, group: dict[str, Any], user: dict[str, Any]) -> bool:
    if _is_teacher(user):
        return int(group["teacher_id"]) == _user_pk(user)
    return _is_student(user) and _is_active_member(conn, int(group["id"]), _user_pk(user))


def _student_conflict_group(
    conn,
    *,
    class_offering_id: int,
    student_id: int,
    assignment_id: Optional[str],
    exclude_group_id: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    params: list[Any] = [int(class_offering_id), int(student_id), assignment_id or ""]
    extra = ""
    if exclude_group_id:
        extra = "AND g.id != ?"
        params.append(int(exclude_group_id))
    row = conn.execute(
        f"""
        SELECT g.id, g.name
        FROM study_group_members m
        JOIN study_groups g ON g.id = m.group_id
        WHERE g.class_offering_id = ?
          AND m.student_id = ?
          AND m.status = 'active'
          AND g.status = 'active'
          AND COALESCE(g.assignment_id, '') = ?
          {extra}
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    return dict(row) if row else None


def _ensure_students_in_class(conn, class_offering_id: int, student_ids: Iterable[int]) -> list[int]:
    normalized_ids = sorted({int(item) for item in student_ids if _safe_int(item) is not None})
    if not normalized_ids:
        return []
    placeholders = ",".join("?" for _ in normalized_ids)
    rows = conn.execute(
        f"""
        SELECT s.id
        FROM class_offerings o
        JOIN students s ON s.class_id = o.class_id
        WHERE o.id = ?
          AND s.id IN ({placeholders})
          AND COALESCE(s.enrollment_status, 'active') = 'active'
        """,
        (int(class_offering_id), *normalized_ids),
    ).fetchall()
    found = {int(row["id"]) for row in rows}
    missing = [student_id for student_id in normalized_ids if student_id not in found]
    if missing:
        raise HTTPException(400, "成员不属于当前课堂或账号不可用")
    return normalized_ids


def _upsert_member(
    conn,
    *,
    group_id: int,
    student_id: int,
    member_role: str = "member",
    added_by_role: str = "",
    added_by_user_pk: Optional[int] = None,
) -> None:
    existing = conn.execute(
        "SELECT id FROM study_group_members WHERE group_id = ? AND student_id = ? LIMIT 1",
        (int(group_id), int(student_id)),
    ).fetchone()
    now = _now_iso()
    if existing:
        conn.execute(
            """
            UPDATE study_group_members
            SET status = 'active',
                member_role = ?,
                left_at = NULL,
                joined_at = COALESCE(joined_at, ?),
                added_by_role = ?,
                added_by_user_pk = ?
            WHERE id = ?
            """,
            (member_role, now, added_by_role, added_by_user_pk, int(existing["id"])),
        )
        return

    conn.execute(
        """
        INSERT INTO study_group_members (
            group_id, student_id, member_role, status, joined_at,
            added_by_role, added_by_user_pk
        )
        VALUES (?, ?, ?, 'active', ?, ?, ?)
        """,
        (int(group_id), int(student_id), member_role, now, added_by_role, added_by_user_pk),
    )


def _sync_leader_role(conn, group_id: int, leader_student_id: Optional[int]) -> None:
    conn.execute(
        "UPDATE study_group_members SET member_role = 'member' WHERE group_id = ?",
        (int(group_id),),
    )
    if leader_student_id is not None:
        conn.execute(
            """
            UPDATE study_group_members
            SET member_role = 'leader', status = 'active', left_at = NULL
            WHERE group_id = ? AND student_id = ?
            """,
            (int(group_id), int(leader_student_id)),
        )


def _link_to_collaboration(class_offering_id: int) -> str:
    return f"/classroom/{int(class_offering_id)}#collaboration-panel"


def _notify(
    conn,
    *,
    recipient_role: str,
    recipient_user_pk: int,
    title: str,
    body: str,
    group: dict[str, Any],
    actor: dict[str, Any],
    ref_id: str,
    allow_duplicates: bool = False,
    metadata: Optional[dict[str, Any]] = None,
) -> int:
    actor_role = str(actor.get("role") or "").lower()
    actor_pk = _safe_int(actor.get("id"))
    return create_collaboration_notification(
        conn,
        recipient_role=recipient_role,
        recipient_user_pk=int(recipient_user_pk),
        title=title,
        body_preview=body,
        link_url=_link_to_collaboration(int(group["class_offering_id"])),
        class_offering_id=int(group["class_offering_id"]),
        ref_id=ref_id,
        actor_role=actor_role,
        actor_user_pk=actor_pk,
        actor_display_name=_actor_name(actor),
        metadata={
            "group_id": group.get("id"),
            "group_name": group.get("name"),
            "assignment_id": group.get("assignment_id"),
            **(metadata or {}),
        },
        allow_duplicates=allow_duplicates,
    )


def _notify_group_members(
    conn,
    *,
    group: dict[str, Any],
    actor: dict[str, Any],
    title: str,
    body: str,
    ref_id: str,
    include_actor: bool = False,
) -> int:
    actor_pk = _safe_int(actor.get("id"))
    rows = conn.execute(
        """
        SELECT student_id
        FROM study_group_members
        WHERE group_id = ? AND status = 'active'
        """,
        (int(group["id"]),),
    ).fetchall()
    count = 0
    for row in rows:
        student_id = int(row["student_id"])
        if not include_actor and actor_pk == student_id and _is_student(actor):
            continue
        count += _notify(
            conn,
            recipient_role="student",
            recipient_user_pk=student_id,
            title=title,
            body=body,
            group=group,
            actor=actor,
            ref_id=f"{ref_id}:student:{student_id}",
        )
    return count


def _notify_teacher(
    conn,
    *,
    group: dict[str, Any],
    actor: dict[str, Any],
    title: str,
    body: str,
    ref_id: str,
    allow_duplicates: bool = False,
) -> int:
    return _notify(
        conn,
        recipient_role="teacher",
        recipient_user_pk=int(group["teacher_id"]),
        title=title,
        body=body,
        group=group,
        actor=actor,
        ref_id=f"{ref_id}:teacher:{group['teacher_id']}",
        allow_duplicates=allow_duplicates,
    )


def create_group(conn, class_offering_id: int, user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    offering = ensure_classroom_access(conn, class_offering_id, user)
    name = _normalize_text(payload.get("name"), limit=60, field_name="小组名称", required=True)
    description = _normalize_text(payload.get("description"), limit=1200, field_name="小组说明")
    assignment = _load_assignment(conn, class_offering_id, payload.get("assignment_id"))
    assignment_id = str(assignment["id"]) if assignment else None
    now = _now_iso()

    if _is_teacher(user):
        join_policy = _normalize_join_policy(payload.get("join_policy"), default=GROUP_JOIN_TEACHER_ASSIGNED)
        max_members = _normalize_max_members(payload.get("max_members"))
        raw_member_ids = payload.get("member_student_ids") or []
        if not isinstance(raw_member_ids, list):
            raise HTTPException(400, "成员列表格式不正确")
        leader_student_id = _safe_int(payload.get("leader_student_id"))
        member_ids = {int(item) for item in _ensure_students_in_class(conn, class_offering_id, raw_member_ids)}
        if leader_student_id is not None:
            _ensure_students_in_class(conn, class_offering_id, [leader_student_id])
            member_ids.add(leader_student_id)
        member_ids = set(sorted(member_ids))
    elif _is_student(user):
        join_policy = GROUP_JOIN_OPEN
        max_members = _normalize_max_members(payload.get("max_members"))
        leader_student_id = _user_pk(user)
        member_ids = {leader_student_id}
    else:
        raise HTTPException(403, "无权创建小组")

    for student_id in member_ids:
        conflict = _student_conflict_group(
            conn,
            class_offering_id=int(class_offering_id),
            student_id=student_id,
            assignment_id=assignment_id,
        )
        if conflict:
            raise HTTPException(400, f"学生已在同一任务的小组中：{conflict['name']}")

    group_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO study_groups (
            class_offering_id, assignment_id, name, description, status, join_policy,
            max_members, leader_student_id, created_by_role, created_by_user_pk,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(class_offering_id),
            assignment_id,
            name,
            description,
            join_policy,
            max_members,
            leader_student_id,
            str(user.get("role") or ""),
            _user_pk(user),
            now,
            now,
        ),
    )
    for student_id in sorted(member_ids):
        _upsert_member(
            conn,
            group_id=group_id,
            student_id=student_id,
            member_role="leader" if leader_student_id == student_id else "member",
            added_by_role=str(user.get("role") or ""),
            added_by_user_pk=_user_pk(user),
        )
    _sync_leader_role(conn, group_id, leader_student_id)

    group = _load_group(conn, group_id)
    if _is_student(user):
        _notify_teacher(
            conn,
            group=group,
            actor=user,
            title=f"学生创建了小组：{name}",
            body=f"{_actor_name(user)} 创建了小组，等待同伴加入或教师调整。",
            ref_id=f"group-created:{group_id}",
        )
    else:
        _notify_group_members(
            conn,
            group=group,
            actor=user,
            title=f"你已加入小组：{name}",
            body="教师已为你分配小组，可以进入协作区查看成员、文件和互评任务。",
            ref_id=f"group-assigned:{group_id}:{now}",
            include_actor=True,
        )
    return group


def update_group(conn, group_id: int, user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    group = _ensure_group_access(conn, group_id, user)
    can_manage = _can_manage_group(conn, group, user)
    if not can_manage:
        raise HTTPException(403, "只有教师或组长可以调整小组信息")

    name = _normalize_text(payload.get("name", group["name"]), limit=60, field_name="小组名称", required=True)
    description = _normalize_text(payload.get("description", group["description"]), limit=1200, field_name="小组说明")
    join_policy = str(group["join_policy"] or GROUP_JOIN_OPEN)
    max_members = int(group["max_members"] or DEFAULT_GROUP_MAX_MEMBERS)
    status = str(group["status"] or GROUP_STATUS_ACTIVE)
    leader_student_id = _safe_int(group.get("leader_student_id"))
    assignment_id = _normalize_assignment_id(group.get("assignment_id"))

    if _is_teacher(user):
        join_policy = _normalize_join_policy(payload.get("join_policy", join_policy), default=join_policy)
        max_members = _normalize_max_members(payload.get("max_members", max_members))
        requested_status = str(payload.get("status", status) or status).strip().lower()
        if requested_status not in {GROUP_STATUS_ACTIVE, GROUP_STATUS_ARCHIVED}:
            raise HTTPException(400, "小组状态不合法")
        status = requested_status
        assignment = _load_assignment(conn, int(group["class_offering_id"]), payload.get("assignment_id", assignment_id))
        assignment_id = str(assignment["id"]) if assignment else None
        if "leader_student_id" in payload:
            leader_student_id = _safe_int(payload.get("leader_student_id"))
            if leader_student_id is not None:
                _ensure_students_in_class(conn, int(group["class_offering_id"]), [leader_student_id])
                if not _is_active_member(conn, group_id, leader_student_id):
                    _upsert_member(
                        conn,
                        group_id=group_id,
                        student_id=leader_student_id,
                        added_by_role=str(user.get("role") or ""),
                        added_by_user_pk=_user_pk(user),
                    )

    archived_at = _now_iso() if status == GROUP_STATUS_ARCHIVED and group.get("status") != GROUP_STATUS_ARCHIVED else group.get("archived_at")
    conn.execute(
        """
        UPDATE study_groups
        SET name = ?,
            description = ?,
            join_policy = ?,
            max_members = ?,
            status = ?,
            leader_student_id = ?,
            assignment_id = ?,
            archived_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            name,
            description,
            join_policy,
            max_members,
            status,
            leader_student_id,
            assignment_id,
            archived_at,
            _now_iso(),
            int(group_id),
        ),
    )
    _sync_leader_role(conn, group_id, leader_student_id)
    return _load_group(conn, group_id)


def join_group(conn, group_id: int, user: dict[str, Any]) -> dict[str, Any]:
    group = _ensure_group_access(conn, group_id, user)
    if not _is_student(user):
        raise HTTPException(403, "只有学生可以加入小组")
    if group.get("status") != GROUP_STATUS_ACTIVE:
        raise HTTPException(400, "小组已归档，不能加入")
    if group.get("join_policy") != GROUP_JOIN_OPEN:
        raise HTTPException(400, "该小组当前不开放自主加入")
    student_id = _user_pk(user)
    if _is_active_member(conn, group_id, student_id):
        return group

    member_count = conn.execute(
        "SELECT COUNT(*) AS count FROM study_group_members WHERE group_id = ? AND status = 'active'",
        (int(group_id),),
    ).fetchone()["count"]
    if int(member_count or 0) >= int(group.get("max_members") or DEFAULT_GROUP_MAX_MEMBERS):
        raise HTTPException(400, "小组人数已满")

    conflict = _student_conflict_group(
        conn,
        class_offering_id=int(group["class_offering_id"]),
        student_id=student_id,
        assignment_id=_normalize_assignment_id(group.get("assignment_id")),
        exclude_group_id=int(group_id),
    )
    if conflict:
        raise HTTPException(400, f"你已经在同一任务的小组中：{conflict['name']}")

    role = "leader" if not group.get("leader_student_id") else "member"
    _upsert_member(
        conn,
        group_id=group_id,
        student_id=student_id,
        member_role=role,
        added_by_role="student",
        added_by_user_pk=student_id,
    )
    if role == "leader":
        conn.execute(
            "UPDATE study_groups SET leader_student_id = ?, updated_at = ? WHERE id = ?",
            (student_id, _now_iso(), int(group_id)),
        )
    else:
        conn.execute("UPDATE study_groups SET updated_at = ? WHERE id = ?", (_now_iso(), int(group_id)))
    group = _load_group(conn, group_id)
    _notify_teacher(
        conn,
        group=group,
        actor=user,
        title=f"{_actor_name(user)} 加入了小组",
        body=f"{_actor_name(user)} 已加入「{group['name']}」。",
        ref_id=f"group-join:{group_id}:{student_id}:{_now_iso()}",
        allow_duplicates=True,
    )
    _notify_group_members(
        conn,
        group=group,
        actor=user,
        title=f"{_actor_name(user)} 加入了小组",
        body=f"新的成员已加入「{group['name']}」，可以一起整理资料和分工。",
        ref_id=f"group-join-member:{group_id}:{student_id}:{_now_iso()}",
    )
    return group


def leave_group(conn, group_id: int, user: dict[str, Any]) -> dict[str, Any]:
    group = _ensure_group_access(conn, group_id, user)
    if not _is_student(user):
        raise HTTPException(403, "只有学生可以退出小组")
    student_id = _user_pk(user)
    if not _is_active_member(conn, group_id, student_id):
        raise HTTPException(400, "你不在该小组中")
    now = _now_iso()
    conn.execute(
        """
        UPDATE study_group_members
        SET status = 'left', left_at = ?, member_role = 'member'
        WHERE group_id = ? AND student_id = ?
        """,
        (now, int(group_id), student_id),
    )
    if int(group.get("leader_student_id") or 0) == student_id:
        next_leader = conn.execute(
            """
            SELECT student_id
            FROM study_group_members
            WHERE group_id = ? AND status = 'active'
            ORDER BY joined_at ASC, id ASC
            LIMIT 1
            """,
            (int(group_id),),
        ).fetchone()
        next_leader_id = int(next_leader["student_id"]) if next_leader else None
        if next_leader_id is None:
            conn.execute(
                """
                UPDATE study_groups
                SET leader_student_id = NULL, status = ?, archived_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (GROUP_STATUS_ARCHIVED, now, now, int(group_id)),
            )
        else:
            conn.execute(
                "UPDATE study_groups SET leader_student_id = ?, updated_at = ? WHERE id = ?",
                (next_leader_id, now, int(group_id)),
            )
            _sync_leader_role(conn, group_id, next_leader_id)
    else:
        conn.execute("UPDATE study_groups SET updated_at = ? WHERE id = ?", (now, int(group_id)))
    group = _load_group(conn, group_id)
    _notify_teacher(
        conn,
        group=group,
        actor=user,
        title=f"{_actor_name(user)} 退出了小组",
        body=f"{_actor_name(user)} 已退出「{group['name']}」。",
        ref_id=f"group-leave:{group_id}:{student_id}:{now}",
        allow_duplicates=True,
    )
    return group


def add_group_member(conn, group_id: int, user: dict[str, Any], student_id: int) -> dict[str, Any]:
    group = _ensure_group_access(conn, group_id, user)
    if not _is_teacher(user):
        raise HTTPException(403, "只有教师可以分配小组成员")
    student_ids = _ensure_students_in_class(conn, int(group["class_offering_id"]), [int(student_id)])
    if not student_ids:
        raise HTTPException(400, "学生不存在")
    conflict = _student_conflict_group(
        conn,
        class_offering_id=int(group["class_offering_id"]),
        student_id=int(student_id),
        assignment_id=_normalize_assignment_id(group.get("assignment_id")),
        exclude_group_id=int(group_id),
    )
    if conflict:
        raise HTTPException(400, f"学生已在同一任务的小组中：{conflict['name']}")
    _upsert_member(
        conn,
        group_id=group_id,
        student_id=int(student_id),
        added_by_role="teacher",
        added_by_user_pk=_user_pk(user),
    )
    if not group.get("leader_student_id"):
        conn.execute(
            "UPDATE study_groups SET leader_student_id = ?, updated_at = ? WHERE id = ?",
            (int(student_id), _now_iso(), int(group_id)),
        )
        _sync_leader_role(conn, group_id, int(student_id))
    else:
        conn.execute("UPDATE study_groups SET updated_at = ? WHERE id = ?", (_now_iso(), int(group_id)))
    group = _load_group(conn, group_id)
    _notify(
        conn,
        recipient_role="student",
        recipient_user_pk=int(student_id),
        title=f"你已加入小组：{group['name']}",
        body="教师已将你加入小组，可以进入协作区查看分工、文件和互评。",
        group=group,
        actor=user,
        ref_id=f"group-member-added:{group_id}:{student_id}:{_now_iso()}",
        allow_duplicates=True,
    )
    return group


def remove_group_member(conn, group_id: int, user: dict[str, Any], student_id: int) -> dict[str, Any]:
    group = _ensure_group_access(conn, group_id, user)
    if not _is_teacher(user):
        raise HTTPException(403, "只有教师可以移出小组成员")
    now = _now_iso()
    conn.execute(
        """
        UPDATE study_group_members
        SET status = 'removed', left_at = ?, member_role = 'member'
        WHERE group_id = ? AND student_id = ?
        """,
        (now, int(group_id), int(student_id)),
    )
    if int(group.get("leader_student_id") or 0) == int(student_id):
        next_leader = conn.execute(
            """
            SELECT student_id
            FROM study_group_members
            WHERE group_id = ? AND status = 'active'
            ORDER BY joined_at ASC, id ASC
            LIMIT 1
            """,
            (int(group_id),),
        ).fetchone()
        next_leader_id = int(next_leader["student_id"]) if next_leader else None
        conn.execute(
            "UPDATE study_groups SET leader_student_id = ?, updated_at = ? WHERE id = ?",
            (next_leader_id, now, int(group_id)),
        )
        _sync_leader_role(conn, group_id, next_leader_id)
    else:
        conn.execute("UPDATE study_groups SET updated_at = ? WHERE id = ?", (now, int(group_id)))
    return _load_group(conn, group_id)


def add_group_file(
    conn,
    group_id: int,
    user: dict[str, Any],
    *,
    file_hash: str,
    original_filename: str,
    mime_type: str,
    file_size: int,
    description: str = "",
) -> dict[str, Any]:
    group = _ensure_group_access(conn, group_id, user)
    if not _can_access_group_work(conn, group, user):
        raise HTTPException(403, "只有小组成员或教师可以上传组内文件")
    description = _normalize_text(description, limit=500, field_name="文件说明")
    filename = Path(str(original_filename or "group-file")).name or "group-file"
    resolved_mime = str(mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream")
    file_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO study_group_files (
            group_id, uploaded_by_role, uploaded_by_user_pk, uploaded_by_name,
            file_hash, original_filename, mime_type, file_size, description, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(group_id),
            str(user.get("role") or ""),
            _user_pk(user),
            _actor_name(user),
            str(file_hash),
            filename,
            resolved_mime,
            int(file_size or 0),
            description,
            _now_iso(),
        ),
    )
    conn.execute("UPDATE study_groups SET updated_at = ? WHERE id = ?", (_now_iso(), int(group_id)))
    file_row = _load_group_file(conn, file_id)
    _notify_group_members(
        conn,
        group=group,
        actor=user,
        title=f"小组文件已更新：{group['name']}",
        body=f"{_actor_name(user)} 上传了「{filename}」。",
        ref_id=f"group-file:{file_row['id']}:{_now_iso()}",
    )
    _notify_teacher(
        conn,
        group=group,
        actor=user,
        title=f"小组文件已更新：{group['name']}",
        body=f"{_actor_name(user)} 上传了「{filename}」。",
        ref_id=f"group-file:{file_row['id']}:{_now_iso()}",
        allow_duplicates=True,
    )
    return _serialize_file(file_row)


def _load_group_file(conn, file_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT f.*, g.class_offering_id, g.name AS group_name, g.teacher_id
        FROM study_group_files f
        JOIN (
            SELECT sg.*, o.teacher_id
            FROM study_groups sg
            JOIN class_offerings o ON o.id = sg.class_offering_id
        ) g ON g.id = f.group_id
        WHERE f.id = ?
        LIMIT 1
        """,
        (int(file_id),),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "文件不存在")
    return dict(row)


def _serialize_file(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "group_id": int(row["group_id"]),
        "name": str(row["original_filename"] or "file"),
        "mime_type": str(row["mime_type"] or "application/octet-stream"),
        "file_size": int(row["file_size"] or 0),
        "description": str(row["description"] or ""),
        "uploaded_by_name": str(row["uploaded_by_name"] or ""),
        "uploaded_by_role": str(row["uploaded_by_role"] or ""),
        "created_at": str(row["created_at"] or ""),
        "download_url": f"/api/collaboration/files/{int(row['id'])}/download",
    }


def resolve_group_file_download(conn, file_id: int, user: dict[str, Any]) -> dict[str, Any]:
    row = _load_group_file(conn, file_id)
    group = _ensure_group_access(conn, int(row["group_id"]), user)
    if not _can_access_group_work(conn, group, user):
        raise HTTPException(403, "无权下载该小组文件")
    path = resolve_global_file_path(str(row["file_hash"]))
    if path is None:
        raise HTTPException(404, "文件已丢失")
    return {
        "path": path,
        "mime_type": str(row["mime_type"] or "application/octet-stream"),
        "filename": str(row["original_filename"] or "group-file"),
    }


def upsert_group_submission(conn, group_id: int, user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    group = _ensure_group_access(conn, group_id, user)
    if not _can_manage_group(conn, group, user):
        raise HTTPException(403, "只有教师或组长可以提交小组成果")
    assignment_id = _normalize_assignment_id(payload.get("assignment_id")) or _normalize_assignment_id(group.get("assignment_id"))
    if assignment_id:
        _load_assignment(conn, int(group["class_offering_id"]), assignment_id)
    title = _normalize_text(payload.get("title") or group["name"], limit=80, field_name="成果标题", required=True)
    summary_md = _normalize_text(payload.get("summary_md") or payload.get("summary"), limit=6000, field_name="成果说明")
    final_file_id = _safe_int(payload.get("final_file_id"))
    if final_file_id is not None:
        file_row = _load_group_file(conn, final_file_id)
        if int(file_row["group_id"]) != int(group_id):
            raise HTTPException(400, "成果文件不属于该小组")
    now = _now_iso()
    if assignment_id:
        existing = conn.execute(
            "SELECT id FROM group_submissions WHERE group_id = ? AND assignment_id = ? LIMIT 1",
            (int(group_id), assignment_id),
        ).fetchone()
    else:
        existing = conn.execute(
            "SELECT id FROM group_submissions WHERE group_id = ? AND assignment_id IS NULL LIMIT 1",
            (int(group_id),),
        ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE group_submissions
            SET submitted_by_role = ?,
                submitted_by_user_pk = ?,
                title = ?,
                summary_md = ?,
                final_file_id = ?,
                status = 'submitted',
                updated_at = ?
            WHERE id = ?
            """,
            (str(user.get("role") or ""), _user_pk(user), title, summary_md, final_file_id, now, int(existing["id"])),
        )
        submission_id = int(existing["id"])
    else:
        submission_id = execute_insert_returning_id(
            conn,
            """
            INSERT INTO group_submissions (
                group_id, assignment_id, submitted_by_role, submitted_by_user_pk,
                title, summary_md, final_file_id, status, submitted_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'submitted', ?, ?)
            """,
            (
                int(group_id),
                assignment_id,
                str(user.get("role") or ""),
                _user_pk(user),
                title,
                summary_md,
                final_file_id,
                now,
                now,
            ),
        )
    conn.execute("UPDATE study_groups SET updated_at = ? WHERE id = ?", (now, int(group_id)))
    group = _load_group(conn, group_id)
    _notify_teacher(
        conn,
        group=group,
        actor=user,
        title=f"小组成果已提交：{group['name']}",
        body=_truncate(summary_md or title, 120),
        ref_id=f"group-submission:{submission_id}:{now}",
        allow_duplicates=True,
    )
    _notify_group_members(
        conn,
        group=group,
        actor=user,
        title=f"小组成果已提交：{group['name']}",
        body="组长提交了小组成果，可以在协作区查看归档内容。",
        ref_id=f"group-submission-member:{submission_id}:{now}",
    )
    return _serialize_submission(_load_group_submission(conn, submission_id))


def _load_group_submission(conn, submission_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT gs.*, a.title AS assignment_title, f.original_filename AS final_file_name
        FROM group_submissions gs
        LEFT JOIN assignments a ON a.id = gs.assignment_id
        LEFT JOIN study_group_files f ON f.id = gs.final_file_id
        WHERE gs.id = ?
        LIMIT 1
        """,
        (int(submission_id),),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "小组成果不存在")
    return dict(row)


def _serialize_submission(row: dict[str, Any]) -> dict[str, Any]:
    blog_post_id = _safe_int(row.get("blog_post_id"))
    return {
        "id": int(row["id"]),
        "group_id": int(row["group_id"]),
        "assignment_id": str(row["assignment_id"] or ""),
        "assignment_title": str(row.get("assignment_title") or ""),
        "title": str(row["title"] or ""),
        "summary_md": str(row["summary_md"] or ""),
        "final_file_id": _safe_int(row.get("final_file_id")),
        "final_file_name": str(row.get("final_file_name") or ""),
        "blog_post_id": blog_post_id,
        "blog_url": f"/blog?post={blog_post_id}" if blog_post_id else "",
        "status": str(row["status"] or ""),
        "submitted_at": str(row["submitted_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def _build_group_submission_blog_content(
    *,
    group: dict[str, Any],
    submission: dict[str, Any],
    members: list[dict[str, Any]],
    files: list[dict[str, Any]],
) -> str:
    member_names = "、".join(str(member.get("name") or "同学") for member in members) or "暂未记录"
    lines = [
        "> 这是一份从课堂小组协作区生成的成果复盘草稿。发布前可以继续补充过程、截图、反思和改进计划。",
        "",
        "## 小组与任务",
        f"- 小组：{group.get('name') or '未命名小组'}",
        f"- 关联任务：{submission.get('assignment_title') or group.get('assignment_title') or '自主学习成果'}",
        f"- 小组成员：{member_names}",
        "",
        "## 成果说明",
        str(submission.get("summary_md") or "请补充本组完成内容、关键思路和最终结论。").strip(),
        "",
        "## 过程证据",
    ]
    final_file_id = _safe_int(submission.get("final_file_id"))
    if final_file_id and submission.get("final_file_name"):
        lines.append(f"- 最终文件：[{submission['final_file_name']}](/api/collaboration/files/{final_file_id}/download)")
    for file_item in files[:6]:
        file_id = _safe_int(file_item.get("id"))
        file_name = str(file_item.get("name") or "组内文件")
        if file_id and file_id != final_file_id:
            lines.append(f"- 组内文件：[{file_name}](/api/collaboration/files/{file_id}/download)")
    if len(lines) and lines[-1] == "## 过程证据":
        lines.append("- 暂未选择最终文件，可以补充实验截图、报告或代码包。")
    lines.extend([
        "",
        "## 复盘",
        "- 做得好的地方：",
        "- 遇到的困难：",
        "- 下一步改进：",
    ])
    return "\n".join(lines)


def create_group_submission_blog_draft(
    conn,
    group_id: int,
    user: dict[str, Any],
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    group = _ensure_group_access(conn, group_id, user)
    if not _can_manage_group(conn, group, user):
        raise HTTPException(403, "只有教师或组长可以把小组成果整理成博客草稿")
    payload = payload or {}
    submission_id = _safe_int(payload.get("submission_id"))
    if submission_id is None:
        latest = conn.execute(
            """
            SELECT id
            FROM group_submissions
            WHERE group_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (int(group_id),),
        ).fetchone()
        if latest is None:
            raise HTTPException(400, "请先保存一份小组成果，再生成博客草稿")
        submission_id = int(latest["id"])
    submission = _load_group_submission(conn, submission_id)
    if int(submission["group_id"]) != int(group_id):
        raise HTTPException(400, "成果记录不属于当前小组")

    existing_post_id = _safe_int(submission.get("blog_post_id"))
    if existing_post_id is not None:
        existing_post = conn.execute(
            "SELECT id, status FROM blog_posts WHERE id = ? LIMIT 1",
            (existing_post_id,),
        ).fetchone()
        if existing_post is not None:
            return {
                "post_id": int(existing_post["id"]),
                "status": str(existing_post["status"] or ""),
                "url": f"/blog?post={int(existing_post['id'])}",
                "reused": True,
                "submission": _serialize_submission(submission),
            }

    maps = _load_group_maps(conn, [int(group_id)])
    members = maps["members"].get(int(group_id), [])
    files = maps["files"].get(int(group_id), [])
    title = _truncate(f"{group.get('name') or '小组'}｜{submission.get('title') or '成果复盘'}", 80)
    content_md = _build_group_submission_blog_content(
        group=group,
        submission=submission,
        members=members,
        files=files,
    )
    try:
        post = create_post(
            conn,
            user,
            title=title,
            content_md=content_md,
            visibility=VISIBILITY_CLASS,
            visible_class_id=int(group["class_id"]),
            tags=["小组协作", "成果复盘"],
            status=POST_STATUS_DRAFT,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    post_id = int(post["id"])
    conn.execute(
        "UPDATE group_submissions SET blog_post_id = ?, updated_at = ? WHERE id = ?",
        (post_id, _now_iso(), int(submission_id)),
    )
    submission = _load_group_submission(conn, submission_id)
    return {
        "post_id": post_id,
        "status": str(post.get("status") or POST_STATUS_DRAFT),
        "url": f"/blog?post={post_id}",
        "reused": False,
        "submission": _serialize_submission(submission),
    }


def submit_peer_review(conn, group_id: int, user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    group = _ensure_group_access(conn, group_id, user)
    if not _is_student(user):
        raise HTTPException(403, "只有学生可以提交同伴互评")
    reviewer_id = _user_pk(user)
    if not _is_active_member(conn, group_id, reviewer_id):
        raise HTTPException(403, "只有小组成员可以互评")
    reviewee_id = _safe_int(payload.get("reviewee_student_id"))
    if reviewee_id is None or reviewee_id == reviewer_id:
        raise HTTPException(400, "请选择需要评价的组员")
    if not _is_active_member(conn, group_id, reviewee_id):
        raise HTTPException(400, "被评价人不在当前小组")
    assignment_id = _normalize_assignment_id(payload.get("assignment_id")) or _normalize_assignment_id(group.get("assignment_id"))
    if assignment_id:
        _load_assignment(conn, int(group["class_offering_id"]), assignment_id)
    responsibility = _normalize_score(payload.get("responsibility_score"), "责任投入")
    collaboration = _normalize_score(payload.get("collaboration_score"), "协作沟通")
    quality = _normalize_score(payload.get("quality_score"), "贡献质量")
    comment = _normalize_text(payload.get("comment"), limit=1200, field_name="评价内容")
    share = 1 if payload.get("share_with_reviewee") else 0
    now = _now_iso()

    if assignment_id:
        existing = conn.execute(
            """
            SELECT id FROM peer_reviews
            WHERE group_id = ? AND assignment_id = ?
              AND reviewer_student_id = ? AND reviewee_student_id = ?
            LIMIT 1
            """,
            (int(group_id), assignment_id, reviewer_id, reviewee_id),
        ).fetchone()
    else:
        existing = conn.execute(
            """
            SELECT id FROM peer_reviews
            WHERE group_id = ? AND assignment_id IS NULL
              AND reviewer_student_id = ? AND reviewee_student_id = ?
            LIMIT 1
            """,
            (int(group_id), reviewer_id, reviewee_id),
        ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE peer_reviews
            SET responsibility_score = ?,
                collaboration_score = ?,
                quality_score = ?,
                comment = ?,
                share_with_reviewee = ?,
                status = 'submitted',
                updated_at = ?
            WHERE id = ?
            """,
            (responsibility, collaboration, quality, comment, share, now, int(existing["id"])),
        )
        review_id = int(existing["id"])
    else:
        review_id = execute_insert_returning_id(
            conn,
            """
            INSERT INTO peer_reviews (
                class_offering_id, group_id, assignment_id, reviewer_student_id, reviewee_student_id,
                responsibility_score, collaboration_score, quality_score, comment,
                share_with_reviewee, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'submitted', ?, ?)
            """,
            (
                int(group["class_offering_id"]),
                int(group_id),
                assignment_id,
                reviewer_id,
                reviewee_id,
                responsibility,
                collaboration,
                quality,
                comment,
                share,
                now,
                now,
            ),
        )
    conn.execute("UPDATE study_groups SET updated_at = ? WHERE id = ?", (now, int(group_id)))
    reviewee = conn.execute("SELECT name FROM students WHERE id = ? LIMIT 1", (reviewee_id,)).fetchone()
    reviewee_name = str(reviewee["name"] or "组员") if reviewee else "组员"
    _notify_teacher(
        conn,
        group=group,
        actor=user,
        title=f"同伴互评已提交：{group['name']}",
        body=f"{_actor_name(user)} 完成了对 {reviewee_name} 的互评。",
        ref_id=f"peer-review:{review_id}:{now}",
        allow_duplicates=True,
    )
    if share:
        _notify(
            conn,
            recipient_role="student",
            recipient_user_pk=reviewee_id,
            title="你收到了一条同伴互评",
            body=_truncate(comment or f"来自小组「{group['name']}」的同伴反馈。", 120),
            group=group,
            actor=user,
            ref_id=f"peer-review-share:{review_id}:{now}",
            allow_duplicates=True,
        )
    return _serialize_review(_load_review(conn, review_id), include_comment=True)


def _load_review(conn, review_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT pr.*,
               reviewer.name AS reviewer_name,
               reviewee.name AS reviewee_name
        FROM peer_reviews pr
        JOIN students reviewer ON reviewer.id = pr.reviewer_student_id
        JOIN students reviewee ON reviewee.id = pr.reviewee_student_id
        WHERE pr.id = ?
        LIMIT 1
        """,
        (int(review_id),),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "互评不存在")
    return dict(row)


def _serialize_review(row: dict[str, Any], *, include_comment: bool = False) -> dict[str, Any]:
    total = int(row["responsibility_score"] or 0) + int(row["collaboration_score"] or 0) + int(row["quality_score"] or 0)
    payload = {
        "id": int(row["id"]),
        "group_id": int(row["group_id"]),
        "assignment_id": str(row["assignment_id"] or ""),
        "reviewer_student_id": int(row["reviewer_student_id"]),
        "reviewer_name": str(row.get("reviewer_name") or ""),
        "reviewee_student_id": int(row["reviewee_student_id"]),
        "reviewee_name": str(row.get("reviewee_name") or ""),
        "responsibility_score": int(row["responsibility_score"] or 0),
        "collaboration_score": int(row["collaboration_score"] or 0),
        "quality_score": int(row["quality_score"] or 0),
        "average_score": round(total / 3, 1) if total else 0,
        "share_with_reviewee": bool(row["share_with_reviewee"]),
        "updated_at": str(row["updated_at"] or row["created_at"] or ""),
    }
    if include_comment:
        payload["comment"] = str(row["comment"] or "")
    return payload


def _load_group_maps(conn, group_ids: list[int]) -> dict[str, Any]:
    if not group_ids:
        return {
            "members": {},
            "files": {},
            "file_counts": {},
            "submissions": {},
            "reviews": {},
        }
    placeholders = ",".join("?" for _ in group_ids)
    params = tuple(group_ids)

    member_rows = conn.execute(
        f"""
        SELECT m.*, s.name AS student_name, s.student_id_number
        FROM study_group_members m
        JOIN students s ON s.id = m.student_id
        WHERE m.group_id IN ({placeholders})
          AND m.status = 'active'
        ORDER BY m.group_id, CASE m.member_role WHEN 'leader' THEN 0 ELSE 1 END, s.student_id_number, s.id
        """,
        params,
    ).fetchall()
    members: dict[int, list[dict[str, Any]]] = {}
    for row in member_rows:
        members.setdefault(int(row["group_id"]), []).append({
            "student_id": int(row["student_id"]),
            "name": str(row["student_name"] or "同学"),
            "student_id_number": str(row["student_id_number"] or ""),
            "member_role": str(row["member_role"] or "member"),
            "joined_at": str(row["joined_at"] or ""),
            "contribution_summary": str(row["contribution_summary"] or ""),
            "contribution_score": row["contribution_score"],
        })

    file_rows = conn.execute(
        f"""
        SELECT *
        FROM study_group_files
        WHERE group_id IN ({placeholders})
        ORDER BY group_id, created_at DESC, id DESC
        """,
        params,
    ).fetchall()
    files: dict[int, list[dict[str, Any]]] = {}
    file_counts: dict[int, int] = {}
    for row in file_rows:
        group_id = int(row["group_id"])
        file_counts[group_id] = file_counts.get(group_id, 0) + 1
        if len(files.setdefault(group_id, [])) < 6:
            files[group_id].append(_serialize_file(dict(row)))

    submission_rows = conn.execute(
        f"""
        SELECT gs.*, a.title AS assignment_title, f.original_filename AS final_file_name
        FROM group_submissions gs
        LEFT JOIN assignments a ON a.id = gs.assignment_id
        LEFT JOIN study_group_files f ON f.id = gs.final_file_id
        WHERE gs.group_id IN ({placeholders})
        ORDER BY gs.group_id, gs.updated_at DESC, gs.id DESC
        """,
        params,
    ).fetchall()
    submissions: dict[int, list[dict[str, Any]]] = {}
    for row in submission_rows:
        submissions.setdefault(int(row["group_id"]), []).append(_serialize_submission(dict(row)))

    review_rows = conn.execute(
        f"""
        SELECT pr.*,
               reviewer.name AS reviewer_name,
               reviewee.name AS reviewee_name
        FROM peer_reviews pr
        JOIN students reviewer ON reviewer.id = pr.reviewer_student_id
        JOIN students reviewee ON reviewee.id = pr.reviewee_student_id
        WHERE pr.group_id IN ({placeholders})
          AND pr.status = 'submitted'
        ORDER BY pr.group_id, pr.updated_at DESC, pr.id DESC
        """,
        params,
    ).fetchall()
    reviews: dict[int, list[dict[str, Any]]] = {}
    for row in review_rows:
        reviews.setdefault(int(row["group_id"]), []).append(_serialize_review(dict(row), include_comment=True))

    return {
        "members": members,
        "files": files,
        "file_counts": file_counts,
        "submissions": submissions,
        "reviews": reviews,
    }


def _build_peer_summary(reviews: list[dict[str, Any]], members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    member_names = {int(item["student_id"]): str(item["name"]) for item in members}
    buckets: dict[int, list[float]] = {}
    for review in reviews:
        buckets.setdefault(int(review["reviewee_student_id"]), []).append(float(review["average_score"] or 0))
    summary = []
    for student_id, name in member_names.items():
        values = [value for value in buckets.get(student_id, []) if value > 0]
        summary.append({
            "student_id": student_id,
            "name": name,
            "review_count": len(values),
            "average_score": round(sum(values) / len(values), 1) if values else 0,
        })
    return summary


def _pending_review_count_for_student(
    *,
    student_id: int,
    groups: list[dict[str, Any]],
    group_members: dict[int, list[dict[str, Any]]],
    group_reviews: dict[int, list[dict[str, Any]]],
) -> int:
    count = 0
    for group in groups:
        group_id = int(group["id"])
        members = group_members.get(group_id, [])
        if student_id not in {int(item["student_id"]) for item in members}:
            continue
        reviewed = {
            int(review["reviewee_student_id"])
            for review in group_reviews.get(group_id, [])
            if int(review["reviewer_student_id"]) == student_id
        }
        for member in members:
            peer_id = int(member["student_id"])
            if peer_id != student_id and peer_id not in reviewed:
                count += 1
    return count


# ============================================================================
# Random study-group scheme system (随机分组方案)
# ============================================================================

def _parse_iso(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text.replace("/", "-")
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    return None


def _normalize_expires_at(value: Any) -> Optional[str]:
    if value is None or str(value).strip() == "":
        return None
    parsed = _parse_iso(value)
    if parsed is None:
        raise HTTPException(400, "时效日期格式不正确")
    if parsed <= datetime.now():
        raise HTTPException(400, "时效日期需要晚于当前时间")
    return parsed.replace(microsecond=0).isoformat()


def _scheme_is_expired(expires_at: Any) -> bool:
    parsed = _parse_iso(expires_at)
    if parsed is None:
        return False
    return datetime.now() > parsed


def _normalize_member_bound(value: Any, *, field_name: str, default: int) -> int:
    parsed = _safe_int(value)
    if parsed is None:
        parsed = default
    if parsed < MIN_SCHEME_MEMBERS:
        raise HTTPException(400, f"{field_name}至少为 {MIN_SCHEME_MEMBERS}")
    return min(MAX_GROUP_MEMBERS_LIMIT, parsed)


def _resolve_group_count(
    class_size: int,
    min_members: int,
    max_members: int,
    requested: Optional[int],
) -> int:
    class_size = max(0, int(class_size))
    if class_size <= 0:
        raise HTTPException(400, "当前班级还没有可分组的学生")
    if min_members > max_members:
        raise HTTPException(400, "每组最小人数不能大于最大人数")
    # Feasible group-count window: enough groups so no group exceeds max, but
    # few enough that each could still reach the min floor.
    min_groups = math.ceil(class_size / max_members)
    max_groups = max(1, class_size // min_members)
    if min_groups > max_groups:
        raise HTTPException(
            400,
            f"按每组 {min_members}-{max_members} 人，{class_size} 名学生无法被合理分组，请调整人数范围",
        )
    if requested is None:
        target = max(1, round(class_size / max(1, (min_members + max_members) / 2)))
        return min(max_groups, max(min_groups, target))
    if requested < min_groups or requested > max_groups:
        raise HTTPException(
            400,
            f"组数需在 {min_groups}-{max_groups} 之间（{class_size} 名学生，每组 {min_members}-{max_members} 人）",
        )
    return requested


def _avatar_url(role: str, user_pk: Any, avatar_hash: Any = "") -> str:
    normalized_pk = _safe_int(user_pk)
    if normalized_pk is None:
        return "/api/profile/avatar"
    revision = quote(str(avatar_hash or "default"), safe="")
    return f"/api/profile/avatar?role={quote(str(role or 'student'), safe='')}&user_id={normalized_pk}&v={revision}"


def _load_member_public_map(conn, class_offering_id: int, student_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    """Public profile info (avatar + classroom cultivation score) per student."""
    ids = sorted({int(item) for item in student_ids if _safe_int(item) is not None})
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT s.id,
               s.avatar_file_hash,
               lps.score AS cultivation_score
        FROM students s
        LEFT JOIN learning_progress_snapshots lps
               ON lps.class_offering_id = ?
              AND lps.student_id = s.id
        WHERE s.id IN ({placeholders})
        """,
        (int(class_offering_id), *ids),
    ).fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        score_value = item.get("cultivation_score")
        result[int(item["id"])] = {
            "avatar_url": _avatar_url("student", item["id"], item.get("avatar_file_hash")),
            "cultivation_score": round(float(score_value), 1) if score_value is not None else None,
        }
    return result


def _load_scheme(conn, scheme_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT gs.*, o.teacher_id, o.class_id
        FROM group_schemes gs
        JOIN class_offerings o ON o.id = gs.class_offering_id
        WHERE gs.id = ?
        LIMIT 1
        """,
        (int(scheme_id),),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "分组方案不存在")
    return dict(row)


def _ensure_scheme_access(conn, scheme_id: int, user: dict[str, Any]) -> dict[str, Any]:
    scheme = _load_scheme(conn, scheme_id)
    ensure_classroom_access(conn, int(scheme["class_offering_id"]), user)
    return scheme


def _scheme_group_rows(conn, scheme_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM study_groups
        WHERE scheme_id = ?
        ORDER BY group_index ASC, id ASC
        """,
        (int(scheme_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _student_scheme_group(conn, scheme_id: int, student_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT g.id, g.name
        FROM study_group_members m
        JOIN study_groups g ON g.id = m.group_id
        WHERE g.scheme_id = ?
          AND m.student_id = ?
          AND m.status = 'active'
        LIMIT 1
        """,
        (int(scheme_id), int(student_id)),
    ).fetchone()
    return dict(row) if row else None


def create_group_scheme(conn, class_offering_id: int, user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    offering = ensure_classroom_access(conn, class_offering_id, user)
    if not _is_teacher(user):
        raise HTTPException(403, "只有教师可以创建分组方案")
    name = _normalize_text(payload.get("name"), limit=60, field_name="方案名称") or "随机分组"
    description = _normalize_text(payload.get("description"), limit=600, field_name="方案说明")
    min_members = _normalize_member_bound(payload.get("min_members"), field_name="每组最小人数", default=2)
    max_members = _normalize_member_bound(payload.get("max_members"), field_name="每组最大人数", default=DEFAULT_GROUP_MAX_MEMBERS)
    requested_count = _safe_int(payload.get("group_count"))
    if requested_count is not None and (requested_count < 1 or requested_count > MAX_SCHEME_GROUP_COUNT):
        raise HTTPException(400, f"组数需在 1-{MAX_SCHEME_GROUP_COUNT} 之间")
    expires_at = _normalize_expires_at(payload.get("expires_at"))

    students = _load_classroom_students(conn, class_offering_id)
    group_count = _resolve_group_count(len(students), min_members, max_members, requested_count)
    now = _now_iso()

    scheme_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO group_schemes (
            class_offering_id, name, description, min_members, max_members,
            group_count, status, expires_at, created_by_teacher_id, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(class_offering_id), name, description, min_members, max_members,
            group_count, SCHEME_STATUS_ACTIVE, expires_at, _user_pk(user), now, now,
        ),
    )
    for index in range(1, group_count + 1):
        execute_insert_returning_id(
            conn,
            """
            INSERT INTO study_groups (
                class_offering_id, assignment_id, name, description, status, join_policy,
                max_members, leader_student_id, scheme_id, group_index, goal_text, progress_percent,
                created_by_role, created_by_user_pk, created_at, updated_at
            )
            VALUES (?, NULL, ?, '', 'active', ?, ?, NULL, ?, ?, '', 0, ?, ?, ?, ?)
            """,
            (
                int(class_offering_id), f"第 {index} 组", SCHEME_JOIN_POLICY, max_members,
                scheme_id, index, str(user.get("role") or ""), _user_pk(user), now, now,
            ),
        )
    return _serialize_scheme(conn, _load_scheme(conn, scheme_id), user)


def random_join_scheme(conn, scheme_id: int, user: dict[str, Any]) -> dict[str, Any]:
    scheme = _ensure_scheme_access(conn, scheme_id, user)
    if not _is_student(user):
        raise HTTPException(403, "只有学生可以参与随机分组")
    if str(scheme.get("status")) != SCHEME_STATUS_ACTIVE:
        raise HTTPException(400, "分组方案已结束")
    if _scheme_is_expired(scheme.get("expires_at")):
        raise HTTPException(400, "分组方案已过期，无法再加入")
    student_id = _user_pk(user)
    _ensure_students_in_class(conn, int(scheme["class_offering_id"]), [student_id])
    existing = _student_scheme_group(conn, scheme_id, student_id)
    if existing:
        raise HTTPException(400, f"你已在本方案的「{existing['name']}」中")

    groups = _scheme_group_rows(conn, scheme_id)
    open_groups = []
    for group in groups:
        if str(group.get("status")) != GROUP_STATUS_ACTIVE:
            continue
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM study_group_members WHERE group_id = ? AND status = 'active'",
            (int(group["id"]),),
        ).fetchone()["c"]
        if int(count or 0) < int(group.get("max_members") or DEFAULT_GROUP_MAX_MEMBERS):
            open_groups.append(group)
    if not open_groups:
        raise HTTPException(400, "所有小组都已满员，请联系老师调整方案")

    chosen = random.choice(open_groups)
    _upsert_member(
        conn,
        group_id=int(chosen["id"]),
        student_id=student_id,
        member_role="member",
        added_by_role="student",
        added_by_user_pk=student_id,
    )
    conn.execute("UPDATE study_groups SET updated_at = ? WHERE id = ?", (_now_iso(), int(chosen["id"])))
    _cleanup_empty_scheme_groups(conn, scheme_id)
    return {
        "scheme": _serialize_scheme(conn, _load_scheme(conn, scheme_id), user),
        "group_id": int(chosen["id"]),
        "group_name": str(chosen["name"]),
    }


def teacher_assign_to_scheme_group(conn, group_id: int, user: dict[str, Any], student_id: int) -> dict[str, Any]:
    """Manually place a student into a scheme group (teacher big-screen drag-drop)."""
    group = _ensure_group_access(conn, group_id, user)
    if not _is_teacher(user):
        raise HTTPException(403, "只有教师可以手动分配小组")
    scheme_id = _safe_int(group.get("scheme_id"))
    if scheme_id is None:
        raise HTTPException(400, "该小组不属于随机分组方案")
    scheme = _load_scheme(conn, scheme_id)
    if _scheme_is_expired(scheme.get("expires_at")):
        raise HTTPException(400, "分组方案已过期，不能再调整")
    student_ids = _ensure_students_in_class(conn, int(group["class_offering_id"]), [int(student_id)])
    if not student_ids:
        raise HTTPException(400, "学生不存在")
    target_student_id = int(student_id)
    existing = _student_scheme_group(conn, scheme_id, target_student_id)
    if existing and int(existing["id"]) != int(group_id):
        raise HTTPException(400, f"该学生已在本方案的「{existing['name']}」中，请先移出")
    member_count = conn.execute(
        "SELECT COUNT(*) AS c FROM study_group_members WHERE group_id = ? AND status = 'active'",
        (int(group_id),),
    ).fetchone()["c"]
    if int(member_count or 0) >= int(group.get("max_members") or DEFAULT_GROUP_MAX_MEMBERS) and not (existing and int(existing["id"]) == int(group_id)):
        raise HTTPException(400, "该小组已满员")
    _upsert_member(
        conn,
        group_id=int(group_id),
        student_id=target_student_id,
        member_role="member",
        added_by_role="teacher",
        added_by_user_pk=_user_pk(user),
    )
    conn.execute("UPDATE study_groups SET updated_at = ? WHERE id = ?", (_now_iso(), int(group_id)))
    _cleanup_empty_scheme_groups(conn, scheme_id)
    return _serialize_scheme(conn, _load_scheme(conn, scheme_id), user)


def set_group_goal_progress(conn, group_id: int, user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    group = _ensure_group_access(conn, group_id, user)
    if not _can_manage_group(conn, group, user):
        raise HTTPException(403, "只有组长或教师可以设置小组目标与进度")
    scheme_id = _safe_int(group.get("scheme_id"))
    if scheme_id is not None:
        scheme = _load_scheme(conn, scheme_id)
        if _scheme_is_expired(scheme.get("expires_at")):
            raise HTTPException(400, "分组方案已过期，不能再修改")
    goal_text = group.get("goal_text") or ""
    if "goal_text" in payload:
        goal_text = _normalize_text(payload.get("goal_text"), limit=600, field_name="小组目标")
    progress = _safe_int(group.get("progress_percent")) or 0
    if "progress_percent" in payload:
        progress = _safe_int(payload.get("progress_percent"))
        if progress is None or progress < GROUP_PROGRESS_MIN or progress > GROUP_PROGRESS_MAX:
            raise HTTPException(400, "进度需在 0-100 之间")
    conn.execute(
        "UPDATE study_groups SET goal_text = ?, progress_percent = ?, updated_at = ? WHERE id = ?",
        (goal_text, int(progress), _now_iso(), int(group_id)),
    )
    if scheme_id is not None:
        return _serialize_scheme(conn, _load_scheme(conn, scheme_id), user)
    return _load_group(conn, group_id)


def nominate_group_leader(conn, group_id: int, user: dict[str, Any], candidate_student_id: int) -> dict[str, Any]:
    group = _ensure_group_access(conn, group_id, user)
    if not _is_student(user):
        raise HTTPException(403, "只有组员可以举荐组长")
    nominator_id = _user_pk(user)
    if not _is_active_member(conn, group_id, nominator_id):
        raise HTTPException(403, "只有本组成员可以举荐组长")
    scheme_id = _safe_int(group.get("scheme_id"))
    if scheme_id is not None:
        scheme = _load_scheme(conn, scheme_id)
        if _scheme_is_expired(scheme.get("expires_at")):
            raise HTTPException(400, "分组方案已过期，不能再举荐")
    if _safe_int(group.get("leader_student_id")):
        raise HTTPException(400, "小组已有组长")
    candidate_id = _safe_int(candidate_student_id)
    if candidate_id is None or not _is_active_member(conn, group_id, candidate_id):
        raise HTTPException(400, "被举荐人不在本组")
    conn.execute(
        "UPDATE study_groups SET leader_student_id = ?, updated_at = ? WHERE id = ?",
        (candidate_id, _now_iso(), int(group_id)),
    )
    _sync_leader_role(conn, group_id, candidate_id)
    if scheme_id is not None:
        return _serialize_scheme(conn, _load_scheme(conn, scheme_id), user)
    return _load_group(conn, group_id)


MAX_GROUP_CHAT_FETCH = 80
MAX_GROUP_CHAT_LENGTH = 800
MAX_GROUP_CHAT_ATTACHMENTS = 10
GROUP_CHAT_ATTACHMENT_MAX_BYTES = 50 * 1024 * 1024
_IMAGE_MIME_PREFIX = "image/"


def _chat_attachment_kind(mime_type: str) -> str:
    return "image" if str(mime_type or "").lower().startswith(_IMAGE_MIME_PREFIX) else "file"


def _chat_attachment_url(group_id: int, attachment_id: int) -> str:
    return f"/api/collaboration/groups/{int(group_id)}/chat/attachments/{int(attachment_id)}"


def _can_use_group_chat(conn, group: dict[str, Any], user: dict[str, Any]) -> bool:
    if _is_teacher(user):
        return int(group["teacher_id"]) == _user_pk(user)
    return _is_student(user) and _is_active_member(conn, int(group["id"]), _user_pk(user))


def _load_chat_attachments(conn, group_id: int, attachment_ids: list[int]) -> list[dict[str, Any]]:
    ids = [int(a) for a in attachment_ids if _safe_int(a) is not None]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM group_chat_attachments WHERE group_id = ? AND id IN ({placeholders})",
        (int(group_id), *ids),
    ).fetchall()
    by_id = {int(row["id"]): dict(row) for row in rows}
    ordered = []
    for attachment_id in ids:
        row = by_id.get(int(attachment_id))
        if row:
            ordered.append(_serialize_chat_attachment(row))
    return ordered


def _serialize_chat_attachment(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": str(row.get("original_filename") or "附件"),
        "mime_type": str(row.get("mime_type") or "application/octet-stream"),
        "file_size": int(row.get("file_size") or 0),
        "kind": str(row.get("kind") or "file"),
        "url": _chat_attachment_url(int(row["group_id"]), int(row["id"])),
    }


def add_group_chat_attachment(
    conn,
    group_id: int,
    user: dict[str, Any],
    *,
    file_hash: str,
    original_filename: str,
    mime_type: str,
    file_size: int,
) -> dict[str, Any]:
    group = _ensure_group_access(conn, group_id, user)
    if not _can_use_group_chat(conn, group, user):
        raise HTTPException(403, "只有本组成员或教师可以上传组内附件")
    from pathlib import Path as _Path
    filename = _Path(str(original_filename or "附件")).name or "附件"
    resolved_mime = str(mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream")
    attachment_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO group_chat_attachments (
            group_id, uploaded_by_role, uploaded_by_user_pk, uploaded_by_name,
            file_hash, original_filename, mime_type, file_size, kind, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(group_id), str(user.get("role") or "student"), _user_pk(user), _actor_name(user),
            str(file_hash), filename, resolved_mime, int(file_size or 0),
            _chat_attachment_kind(resolved_mime), _now_iso(),
        ),
    )
    row = conn.execute(
        "SELECT * FROM group_chat_attachments WHERE id = ? LIMIT 1", (int(attachment_id),)
    ).fetchone()
    return _serialize_chat_attachment(dict(row))


def resolve_group_chat_attachment_download(conn, attachment_id: int, user: dict[str, Any]) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM group_chat_attachments WHERE id = ? LIMIT 1", (int(attachment_id),)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "附件不存在")
    row = dict(row)
    group = _ensure_group_access(conn, int(row["group_id"]), user)
    if not _can_use_group_chat(conn, group, user):
        raise HTTPException(403, "无权下载该组内附件")
    from .file_service import resolve_global_file_path
    path = resolve_global_file_path(str(row["file_hash"]))
    if path is None:
        raise HTTPException(404, "附件已丢失")
    return {
        "path": path,
        "mime_type": str(row.get("mime_type") or "application/octet-stream"),
        "filename": str(row.get("original_filename") or "附件"),
        "kind": str(row.get("kind") or "file"),
    }


def _serialize_chat_message(row: dict[str, Any], *, current_pk: Optional[int], current_role: str) -> dict[str, Any]:
    attachments = []
    raw = row.get("attachments_json")
    if raw:
        try:
            attachments = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            attachments = []
    return {
        "id": int(row["id"]),
        "sender_name": str(row["sender_name"] or "成员"),
        "sender_role": str(row["sender_role"] or "student"),
        "content": str(row["content"] or ""),
        "message_type": str(row.get("message_type") or "text"),
        "recalled": str(row.get("message_type") or "") == "recalled",
        "attachments": attachments if isinstance(attachments, list) else [],
        "created_at": str(row["created_at"] or ""),
        "is_mine": int(row["sender_user_pk"]) == current_pk and str(row["sender_role"] or "") == current_role,
    }


def list_group_chat(conn, group_id: int, user: dict[str, Any], after_id: int = 0) -> dict[str, Any]:
    group = _ensure_group_access(conn, group_id, user)
    if not _can_access_group_work(conn, group, user):
        raise HTTPException(403, "只有本组成员或教师可以查看组内对话")
    after = _safe_int(after_id) or 0
    rows = conn.execute(
        """
        SELECT * FROM group_chat_messages
        WHERE group_id = ? AND id > ?
        ORDER BY id ASC
        LIMIT ?
        """,
        (int(group_id), int(after), MAX_GROUP_CHAT_FETCH),
    ).fetchall()
    current_pk = _safe_int(user.get("id"))
    current_role = str(user.get("role") or "").lower()
    messages = [_serialize_chat_message(dict(row), current_pk=current_pk, current_role=current_role) for row in rows]
    # Recalls of older messages (id <= after_id) won't surface via the
    # incremental fetch, so return ids recalled within the propagation window
    # for the client to reconcile. Cheap, indexed, and only group members poll.
    cutoff = (datetime.now() - timedelta(seconds=_RECALL_PROPAGATION_SECONDS)).replace(microsecond=0).isoformat()
    recall_rows = conn.execute(
        "SELECT id FROM group_chat_messages WHERE group_id = ? AND message_type = 'recalled' AND recalled_at IS NOT NULL AND recalled_at >= ?",
        (int(group_id), cutoff),
    ).fetchall()
    recalls = [int(row["id"]) for row in recall_rows]
    return {"messages": messages, "recalls": recalls, "group_id": int(group_id)}


def post_group_chat(
    conn,
    group_id: int,
    user: dict[str, Any],
    content: Any,
    *,
    attachment_ids: Optional[list[int]] = None,
    message_type: str = "text",
    sticker_emoji_id: Optional[int] = None,
) -> dict[str, Any]:
    group = _ensure_group_access(conn, group_id, user)
    if not _can_use_group_chat(conn, group, user):
        raise HTTPException(403, "只有本组成员或教师可以在组内发言")
    attachments = _load_chat_attachments(conn, int(group_id), attachment_ids or [])
    if sticker_emoji_id is not None:
        from .emoji_service import resolve_custom_emoji_payloads
        payloads = resolve_custom_emoji_payloads(conn, int(group["class_offering_id"]), [int(sticker_emoji_id)], user)
        if not payloads:
            raise HTTPException(400, "表情不存在或不属于你")
        emoji = payloads[0]
        attachments.append({
            "id": 0,
            "kind": "sticker",
            "name": str(emoji.get("name") or "表情"),
            "url": str(emoji.get("image_url") or ""),
            "mime_type": str(emoji.get("mime_type") or "image/png"),
            "file_size": int(emoji.get("file_size") or 0),
        })
        message_type = "sticker"
    text = str(content or "").replace("\r\n", "\n").strip()
    if len(text) > MAX_GROUP_CHAT_LENGTH:
        raise HTTPException(400, f"消息不能超过 {MAX_GROUP_CHAT_LENGTH} 个字符")
    kind = str(message_type or "text").strip().lower()
    if kind not in {"text", "sticker"}:
        kind = "text"
    if not text and not attachments:
        raise HTTPException(400, "消息不能为空")
    now = _now_iso()
    message_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO group_chat_messages (
            group_id, sender_role, sender_user_pk, sender_name, content, message_type, attachments_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(group_id), str(user.get("role") or "student"), _user_pk(user), _actor_name(user),
            text, kind, json.dumps(attachments, ensure_ascii=False), now,
        ),
    )
    return {
        "id": message_id,
        "sender_name": _actor_name(user),
        "sender_role": str(user.get("role") or "student"),
        "content": text,
        "message_type": kind,
        "attachments": attachments,
        "created_at": now,
        "is_mine": True,
    }


def _cleanup_empty_scheme_groups(conn, scheme_id: int) -> int:
    """Once every student in the class is grouped, drop the leftover empty
    groups so the board reflects reality. Returns the number removed."""
    scheme = _load_scheme(conn, scheme_id)
    students = _load_classroom_students(conn, int(scheme["class_offering_id"]))
    total = len(students)
    groups = _scheme_group_rows(conn, scheme_id)
    grouped_ids: set[int] = set()
    counts: dict[int, int] = {}
    for group in groups:
        rows = conn.execute(
            "SELECT student_id FROM study_group_members WHERE group_id = ? AND status = 'active'",
            (int(group["id"]),),
        ).fetchall()
        counts[int(group["id"])] = len(rows)
        grouped_ids.update(int(row["student_id"]) for row in rows)
    if total - len(grouped_ids) > 0:
        return 0  # keep empty slots while students still need a group
    empty_ids = [group_id for group_id, count in counts.items() if count == 0]
    if not empty_ids or len(empty_ids) >= len(groups):
        return 0  # never delete every group (keep at least one)
    placeholders = ",".join("?" for _ in empty_ids)
    conn.execute(f"DELETE FROM study_groups WHERE id IN ({placeholders})", tuple(empty_ids))
    remaining = len(groups) - len(empty_ids)
    conn.execute(
        "UPDATE group_schemes SET group_count = ?, updated_at = ? WHERE id = ?",
        (remaining, _now_iso(), int(scheme_id)),
    )
    return len(empty_ids)


def close_group_scheme(conn, scheme_id: int, user: dict[str, Any]) -> dict[str, Any]:
    scheme = _ensure_scheme_access(conn, scheme_id, user)
    if not _is_teacher(user):
        raise HTTPException(403, "只有教师可以结束分组方案")
    now = _now_iso()
    conn.execute(
        "UPDATE group_schemes SET status = ?, archived_at = ?, updated_at = ? WHERE id = ?",
        (SCHEME_STATUS_CLOSED, now, now, int(scheme_id)),
    )
    return _serialize_scheme(conn, _load_scheme(conn, scheme_id), user)


def assign_scheme_leader(conn, group_id: int, user: dict[str, Any], candidate_student_id: int) -> dict[str, Any]:
    """Teacher directly designates a leader for a leaderless scheme group."""
    group = _ensure_group_access(conn, group_id, user)
    if not _is_teacher(user):
        raise HTTPException(403, "只有教师可以指定组长")
    scheme_id = _safe_int(group.get("scheme_id"))
    if scheme_id is None:
        raise HTTPException(400, "该小组不属于随机分组方案")
    scheme = _load_scheme(conn, scheme_id)
    if _scheme_is_expired(scheme.get("expires_at")):
        raise HTTPException(400, "分组方案已过期，不能再调整")
    if _safe_int(group.get("leader_student_id")):
        raise HTTPException(400, "小组已有组长")
    candidate_id = _safe_int(candidate_student_id)
    if candidate_id is None or not _is_active_member(conn, group_id, candidate_id):
        raise HTTPException(400, "被指定人不在本组")
    conn.execute(
        "UPDATE study_groups SET leader_student_id = ?, updated_at = ? WHERE id = ?",
        (candidate_id, _now_iso(), int(group_id)),
    )
    _sync_leader_role(conn, group_id, candidate_id)
    return _serialize_scheme(conn, _load_scheme(conn, scheme_id), user)


def _pick_auto_leader(conn, class_offering_id: int, member_ids: list[int]) -> Optional[int]:
    """Rank candidates by 修为分数 > 综合评分(各维之和) > 互动维度 > 随机."""
    if not member_ids:
        return None
    try:
        from .learning_progress_service import get_student_learning_state
    except Exception:
        get_student_learning_state = None  # type: ignore
    ranked: list[tuple[float, float, float, float, int]] = []
    for student_id in member_ids:
        score = 0.0
        total = 0.0
        interaction = 0.0
        if get_student_learning_state is not None:
            try:
                state = get_student_learning_state(conn, int(class_offering_id), int(student_id))
                score = float(state.get("score") or 0)
                components = state.get("components") or {}
                if isinstance(components, dict):
                    total = sum(float(value or 0) for value in components.values())
                    interaction = float(components.get("interaction") or 0)
            except Exception:
                pass
        ranked.append((score, total, interaction, random.random(), int(student_id)))
    ranked.sort(reverse=True)
    return ranked[0][4]


def auto_assign_scheme_leaders(conn, scheme_id: int, user: dict[str, Any]) -> dict[str, Any]:
    scheme = _ensure_scheme_access(conn, scheme_id, user)
    if not _is_teacher(user):
        raise HTTPException(403, "只有教师可以一键配置组长")
    if _scheme_is_expired(scheme.get("expires_at")):
        raise HTTPException(400, "分组方案已过期，不能再调整")
    class_offering_id = int(scheme["class_offering_id"])
    groups = _scheme_group_rows(conn, scheme_id)
    assigned = 0
    for group in groups:
        if _safe_int(group.get("leader_student_id")):
            continue
        rows = conn.execute(
            "SELECT student_id FROM study_group_members WHERE group_id = ? AND status = 'active'",
            (int(group["id"]),),
        ).fetchall()
        member_ids = [int(row["student_id"]) for row in rows]
        if not member_ids:
            continue
        leader_id = _pick_auto_leader(conn, class_offering_id, member_ids)
        if leader_id is None:
            continue
        conn.execute(
            "UPDATE study_groups SET leader_student_id = ?, updated_at = ? WHERE id = ?",
            (leader_id, _now_iso(), int(group["id"])),
        )
        _sync_leader_role(conn, int(group["id"]), leader_id)
        assigned += 1
    return {"assigned": assigned, "scheme": _serialize_scheme(conn, _load_scheme(conn, scheme_id), user)}


RECALL_WINDOW_SECONDS = 60
_RECALL_PROPAGATION_SECONDS = 120


def _load_scheme_members(conn, group_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """One batched query for scheme group rosters (name + number + avatar hash).

    Perf: avoids ``_load_group_maps`` which also loads files/submissions/reviews
    that scheme groups never use, and skips the cultivation join (deferred to the
    on-demand member card). Keeps the snapshot light under ~200 concurrent users.
    """
    if not group_ids:
        return {}
    placeholders = ",".join("?" for _ in group_ids)
    rows = conn.execute(
        f"""
        SELECT m.group_id, m.student_id, m.member_role,
               s.name AS student_name, s.student_id_number, s.avatar_file_hash
        FROM study_group_members m
        JOIN students s ON s.id = m.student_id
        WHERE m.group_id IN ({placeholders})
          AND m.status = 'active'
        ORDER BY m.group_id, CASE m.member_role WHEN 'leader' THEN 0 ELSE 1 END, s.student_id_number, s.id
        """,
        tuple(group_ids),
    ).fetchall()
    out: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(int(row["group_id"]), []).append({
            "student_id": int(row["student_id"]),
            "name": str(row["student_name"] or "同学"),
            "student_id_number": str(row["student_id_number"] or ""),
            "member_role": str(row["member_role"] or "member"),
            "avatar_file_hash": row["avatar_file_hash"],
        })
    return out


def load_member_public_card(conn, class_offering_id: int, student_id: int, user: dict[str, Any]) -> dict[str, Any]:
    """On-demand public profile (avatar + classroom 修为值) for the member popover."""
    ensure_classroom_access(conn, class_offering_id, user)
    row = conn.execute(
        "SELECT name, student_id_number FROM students WHERE id = ? LIMIT 1",
        (int(student_id),),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "学生不存在")
    info = _load_member_public_map(conn, int(class_offering_id), [int(student_id)]).get(int(student_id), {})
    return {
        "student_id": int(student_id),
        "name": str(row["name"] or "同学"),
        "student_id_number": str(row["student_id_number"] or ""),
        "avatar_url": info.get("avatar_url", "/api/profile/avatar"),
        "cultivation_score": info.get("cultivation_score"),
    }


def recall_group_chat_message(conn, group_id: int, message_id: int, user: dict[str, Any]) -> dict[str, Any]:
    group = _ensure_group_access(conn, group_id, user)
    if not _can_use_group_chat(conn, group, user):
        raise HTTPException(403, "无权操作该组对话")
    row = conn.execute(
        "SELECT * FROM group_chat_messages WHERE id = ? AND group_id = ? LIMIT 1",
        (int(message_id), int(group_id)),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "消息不存在")
    row = dict(row)
    if int(row["sender_user_pk"]) != _user_pk(user) or str(row["sender_role"] or "") != str(user.get("role") or "").lower():
        raise HTTPException(403, "只能撤回自己发送的消息")
    if str(row.get("message_type")) == "recalled":
        return {"id": int(message_id), "recalled": True}
    created = _parse_iso(row.get("created_at"))
    if created is None or (datetime.now() - created).total_seconds() > RECALL_WINDOW_SECONDS:
        raise HTTPException(400, "超过 1 分钟，无法撤回")
    conn.execute(
        "UPDATE group_chat_messages SET message_type = 'recalled', content = '', attachments_json = '[]', recalled_at = ? WHERE id = ?",
        (_now_iso(), int(message_id)),
    )
    return {"id": int(message_id), "recalled": True}


def _serialize_group_entry(
    row: dict[str, Any],
    members: list[dict[str, Any]],
    *,
    current_student_id: Optional[int],
    is_teacher: bool,
    editable: bool,
) -> dict[str, Any]:
    """Shared card shape for scheme groups AND student-initiated groups, so the
    same front-end group-detail popover + chat work for both."""
    group_id = int(row["id"])
    leader_id = _safe_int(row.get("leader_student_id"))
    member_ids = {int(m["student_id"]) for m in members}
    is_member = current_student_id in member_ids if current_student_id is not None else False
    max_members = int(row.get("max_members") or DEFAULT_GROUP_MAX_MEMBERS)
    serialized_members = [
        {
            "student_id": int(m["student_id"]),
            "name": str(m["name"]),
            "student_id_number": str(m.get("student_id_number") or ""),
            "member_role": str(m.get("member_role") or "member"),
            "is_leader": leader_id is not None and int(m["student_id"]) == leader_id,
            "avatar_url": _avatar_url("student", m["student_id"], m.get("avatar_file_hash")),
        }
        for m in members
    ]
    return {
        "id": group_id,
        "name": str(row["name"]),
        "group_index": int(row.get("group_index") or 0),
        "max_members": max_members,
        "member_count": len(members),
        "is_full": len(members) >= max_members,
        "leader_student_id": leader_id,
        "has_leader": leader_id is not None,
        "goal_text": str(row.get("goal_text") or ""),
        "progress_percent": int(row.get("progress_percent") or 0),
        "my_membership": is_member,
        "members": serialized_members,
        "can_set_goal": bool((is_member and leader_id == current_student_id and editable) or (is_teacher and editable)),
        "can_nominate": bool(is_member and leader_id is None and editable),
        "can_assign_leader": bool(is_teacher and leader_id is None and len(members) > 0 and editable),
    }


def _serialize_scheme(conn, scheme: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    scheme_id = int(scheme["id"])
    class_offering_id = int(scheme["class_offering_id"])
    group_rows = _scheme_group_rows(conn, scheme_id)
    group_ids = [int(row["id"]) for row in group_rows]
    member_map = _load_scheme_members(conn, group_ids)

    is_expired = _scheme_is_expired(scheme.get("expires_at"))
    is_teacher = _is_teacher(user)
    current_student_id = _user_pk(user) if _is_student(user) else None
    expired_or_closed = is_expired or str(scheme.get("status")) != SCHEME_STATUS_ACTIVE

    grouped_student_ids: set[int] = set()
    serialized_groups = []
    for row in group_rows:
        members = member_map.get(int(row["id"]), [])
        grouped_student_ids |= {int(m["student_id"]) for m in members}
        serialized_groups.append(_serialize_group_entry(
            row, members,
            current_student_id=current_student_id,
            is_teacher=is_teacher,
            editable=not expired_or_closed,
        ))

    students = _load_classroom_students(conn, class_offering_id)
    ungrouped = [s for s in students if int(s["id"]) not in grouped_student_ids]
    my_group_id = None
    if current_student_id is not None:
        existing = _student_scheme_group(conn, scheme_id, current_student_id)
        my_group_id = int(existing["id"]) if existing else None
    has_open_group = any(not g["is_full"] for g in serialized_groups)

    return {
        "id": scheme_id,
        "class_offering_id": class_offering_id,
        "name": str(scheme.get("name") or "随机分组"),
        "description": str(scheme.get("description") or ""),
        "min_members": int(scheme.get("min_members") or 0),
        "max_members": int(scheme.get("max_members") or 0),
        "group_count": int(scheme.get("group_count") or len(serialized_groups)),
        "status": str(scheme.get("status") or SCHEME_STATUS_ACTIVE),
        "expires_at": str(scheme.get("expires_at") or ""),
        "is_expired": is_expired,
        "is_active": not expired_or_closed,
        "is_history": expired_or_closed,
        "can_close": bool(is_teacher and not expired_or_closed),
        "leaderless_group_count": sum(1 for g in serialized_groups if not g["has_leader"] and g["member_count"] > 0),
        "created_at": str(scheme.get("created_at") or ""),
        "groups": serialized_groups,
        "grouped_count": len(grouped_student_ids),
        "ungrouped_count": len(ungrouped),
        "total_students": len(students),
        "ungrouped_students": [
            {
                "student_id": int(s["id"]),
                "name": str(s["name"]),
                "student_id_number": str(s.get("student_id_number") or ""),
            }
            for s in ungrouped
        ] if is_teacher else [],
        "my_group_id": my_group_id,
        "can_random_join": bool(
            current_student_id is not None
            and my_group_id is None
            and not expired_or_closed
            and has_open_group
        ),
        "can_manage": is_teacher,
    }


def _load_schemes_for_snapshot(conn, class_offering_id: int, user: dict[str, Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM group_schemes
        WHERE class_offering_id = ?
        ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, created_at DESC, id DESC
        """,
        (int(class_offering_id),),
    ).fetchall()
    schemes = []
    history_count = 0
    for row in rows:
        scheme = dict(row)
        scheme["teacher_id"] = None
        serialized = _serialize_scheme(conn, scheme, user)
        if serialized.get("is_history"):
            history_count += 1
            if history_count > 12:  # cap archived schemes in the hot snapshot path
                continue
        schemes.append(serialized)
    return schemes


# ============================================================================
# Student-initiated invite groups
# ============================================================================

def _student_active_invite_group_count(conn, class_offering_id: int, student_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM study_groups
        WHERE class_offering_id = ? AND created_by_role = 'student' AND created_by_user_pk = ?
          AND join_policy = ? AND status = ?
        """,
        (int(class_offering_id), int(student_id), STUDENT_GROUP_JOIN_POLICY, GROUP_STATUS_ACTIVE),
    ).fetchone()
    return int(row["c"] if row else 0)


def _invite_decline_count(conn, inviter_id: int, invitee_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM group_invitations
        WHERE inviter_student_id = ? AND invitee_student_id = ? AND status = ?
        """,
        (int(inviter_id), int(invitee_id), INVITE_STATUS_DECLINED),
    ).fetchone()
    return int(row["c"] if row else 0)


def _assert_invite_rate(conn, inviter_id: int, *, count: int = 1) -> None:
    """Anti-harassment throttle: >=30s between sends and <=10 invites/hour."""
    now = datetime.now()
    last = conn.execute(
        "SELECT created_at FROM group_invitations WHERE inviter_student_id = ? ORDER BY id DESC LIMIT 1",
        (int(inviter_id),),
    ).fetchone()
    if last is not None:
        last_dt = _parse_iso(last["created_at"])
        if last_dt is not None and (now - last_dt).total_seconds() < INVITE_MIN_INTERVAL_SECONDS:
            wait = INVITE_MIN_INTERVAL_SECONDS - int((now - last_dt).total_seconds())
            raise HTTPException(400, f"操作过于频繁，请 {max(1, wait)} 秒后再发起邀请")
    cutoff = (now - timedelta(hours=1)).replace(microsecond=0).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM group_invitations WHERE inviter_student_id = ? AND created_at >= ?",
        (int(inviter_id), cutoff),
    ).fetchone()
    if int(row["c"] if row else 0) + count > INVITE_MAX_PER_HOUR:
        raise HTTPException(400, f"每小时最多发起 {INVITE_MAX_PER_HOUR} 次邀请，请稍后再试")


def _load_student_group(conn, group_id: int) -> dict[str, Any]:
    group = _load_group(conn, group_id)
    if str(group.get("join_policy")) != STUDENT_GROUP_JOIN_POLICY or _safe_int(group.get("scheme_id")) is not None:
        raise HTTPException(400, "该小组不是学生发起的分组")
    return group


def create_student_group(conn, class_offering_id: int, user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    ensure_classroom_access(conn, class_offering_id, user)
    if not _is_student(user):
        raise HTTPException(403, "只有学生可以自由发起分组")
    student_id = _user_pk(user)
    if _student_active_invite_group_count(conn, class_offering_id, student_id) >= STUDENT_MAX_ACTIVE_GROUPS:
        raise HTTPException(400, f"最多只能同时发起 {STUDENT_MAX_ACTIVE_GROUPS} 个分组")
    name = _normalize_text(payload.get("name"), limit=60, field_name="小组名称", required=True)
    max_members = _normalize_max_members(payload.get("max_members"))
    raw_invitees = payload.get("invitee_student_ids") or []
    if not isinstance(raw_invitees, list):
        raise HTTPException(400, "邀请名单格式不正确")
    now = _now_iso()
    group_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO study_groups (
            class_offering_id, assignment_id, name, description, status, join_policy,
            max_members, leader_student_id, scheme_id, group_index, goal_text, progress_percent,
            created_by_role, created_by_user_pk, created_at, updated_at
        )
        VALUES (?, NULL, ?, '', 'active', ?, ?, ?, NULL, 0, '', 0, 'student', ?, ?, ?)
        """,
        (int(class_offering_id), name, STUDENT_GROUP_JOIN_POLICY, max_members, student_id, student_id, now, now),
    )
    _upsert_member(conn, group_id=group_id, student_id=student_id, member_role="leader", added_by_role="student", added_by_user_pk=student_id)
    if raw_invitees:
        invite_to_group(conn, group_id, user, raw_invitees)
    return _serialize_student_group(conn, _load_group(conn, group_id), user)


def invite_to_group(conn, group_id: int, user: dict[str, Any], invitee_ids: Any) -> dict[str, Any]:
    group = _load_student_group(conn, group_id)
    ensure_classroom_access(conn, int(group["class_offering_id"]), user)
    if not _is_student(user) or int(group.get("leader_student_id") or 0) != _user_pk(user):
        raise HTTPException(403, "只有发起人可以邀请成员")
    inviter_id = _user_pk(user)
    class_offering_id = int(group["class_offering_id"])
    normalized = _ensure_students_in_class(conn, class_offering_id, invitee_ids if isinstance(invitee_ids, list) else [invitee_ids])
    targets = [sid for sid in normalized if sid != inviter_id]
    if not targets:
        raise HTTPException(400, "请选择要邀请的同学")
    # filter out existing members and already-pending invites
    member_ids = {int(m["student_id"]) for m in _load_scheme_members(conn, [group_id]).get(group_id, [])}
    pending_rows = conn.execute(
        f"SELECT invitee_student_id FROM group_invitations WHERE group_id = ? AND status = ?",
        (int(group_id), INVITE_STATUS_PENDING),
    ).fetchall()
    pending_ids = {int(r["invitee_student_id"]) for r in pending_rows}
    blocked = [sid for sid in targets if _invite_decline_count(conn, inviter_id, sid) >= INVITE_DECLINE_BLOCK_THRESHOLD]
    sendable = [sid for sid in targets if sid not in member_ids and sid not in pending_ids and sid not in blocked]
    if not sendable:
        if blocked:
            raise HTTPException(400, "对方已多次拒绝，无法再发送邀请")
        raise HTTPException(400, "所选同学已在组内或已被邀请")
    _assert_invite_rate(conn, inviter_id, count=len(sendable))
    now = _now_iso()
    for invitee_id in sendable:
        execute_insert_returning_id(
            conn,
            """
            INSERT INTO group_invitations (group_id, class_offering_id, inviter_student_id, invitee_student_id, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (int(group_id), class_offering_id, inviter_id, int(invitee_id), INVITE_STATUS_PENDING, now),
        )
        _notify(
            conn,
            recipient_role="student",
            recipient_user_pk=int(invitee_id),
            title=f"{_actor_name(user)} 邀请你加入小组",
            body=f"「{group['name']}」邀请你加入，可在协作区接受或拒绝。",
            group=group,
            actor=user,
            ref_id=f"group-invite:{group_id}:{invitee_id}:{now}",
            allow_duplicates=True,
        )
    return _serialize_student_group(conn, _load_group(conn, group_id), user)


def respond_invitation(conn, invitation_id: int, user: dict[str, Any], *, accept: bool) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM group_invitations WHERE id = ? LIMIT 1", (int(invitation_id),)).fetchone()
    if row is None:
        raise HTTPException(404, "邀请不存在")
    inv = dict(row)
    ensure_classroom_access(conn, int(inv["class_offering_id"]), user)
    if not _is_student(user) or int(inv["invitee_student_id"]) != _user_pk(user):
        raise HTTPException(403, "只能处理发给自己的邀请")
    if str(inv["status"]) != INVITE_STATUS_PENDING:
        raise HTTPException(400, "该邀请已处理")
    group = _load_group(conn, int(inv["group_id"]))
    now = _now_iso()
    inviter_id = int(inv["inviter_student_id"])
    invitee_id = int(inv["invitee_student_id"])
    if accept:
        if str(group.get("status")) != GROUP_STATUS_ACTIVE:
            conn.execute("UPDATE group_invitations SET status = ?, responded_at = ? WHERE id = ?", (INVITE_STATUS_CANCELLED, now, int(invitation_id)))
            raise HTTPException(400, "该小组已解散")
        member_count = conn.execute(
            "SELECT COUNT(*) AS c FROM study_group_members WHERE group_id = ? AND status = 'active'",
            (int(inv["group_id"]),),
        ).fetchone()["c"]
        if int(member_count or 0) >= int(group.get("max_members") or DEFAULT_GROUP_MAX_MEMBERS):
            raise HTTPException(400, "小组人数已满")
        _upsert_member(conn, group_id=int(inv["group_id"]), student_id=invitee_id, member_role="member", added_by_role="student", added_by_user_pk=inviter_id)
        conn.execute("UPDATE group_invitations SET status = ?, responded_at = ? WHERE id = ?", (INVITE_STATUS_ACCEPTED, now, int(invitation_id)))
        conn.execute("UPDATE study_groups SET updated_at = ? WHERE id = ?", (now, int(inv["group_id"])))
        _notify(
            conn, recipient_role="student", recipient_user_pk=inviter_id,
            title=f"{_actor_name(user)} 接受了你的邀请",
            body=f"{_actor_name(user)} 已加入「{group['name']}」。",
            group=group, actor=user, ref_id=f"group-invite-accept:{invitation_id}:{now}", allow_duplicates=True,
        )
    else:
        conn.execute("UPDATE group_invitations SET status = ?, responded_at = ? WHERE id = ?", (INVITE_STATUS_DECLINED, now, int(invitation_id)))
        decline_count = _invite_decline_count(conn, inviter_id, invitee_id)
        extra = ""
        if decline_count >= INVITE_DECLINE_BLOCK_THRESHOLD:
            # permanent invite block is derived from decline_count; also drop the
            # inviter into the invitee's PM blacklist to stop the harassment.
            try:
                from .message_center_service import add_private_message_block
                add_private_message_block(conn, user, contact_identity=f"student:{inviter_id}", class_offering_id=int(inv["class_offering_id"]))
                extra = "，已自动屏蔽对方的邀请与私信"
            except Exception:
                extra = ""
        _notify(
            conn, recipient_role="student", recipient_user_pk=inviter_id,
            title=f"{_actor_name(user)} 拒绝了你的邀请",
            body=f"{_actor_name(user)} 暂时不方便加入「{group['name']}」。" + (f"（对方已多次拒绝，邀请通道关闭）" if decline_count >= INVITE_DECLINE_BLOCK_THRESHOLD else ""),
            group=group, actor=user, ref_id=f"group-invite-decline:{invitation_id}:{now}", allow_duplicates=True,
        )
        return {"snapshot": load_collaboration_snapshot(conn, int(inv["class_offering_id"]), user), "message": "已拒绝邀请" + extra}
    return {"snapshot": load_collaboration_snapshot(conn, int(inv["class_offering_id"]), user), "message": "已加入小组"}


def load_invite_candidates(conn, class_offering_id: int, user: dict[str, Any], group_id: Optional[int] = None) -> list[dict[str, Any]]:
    ensure_classroom_access(conn, class_offering_id, user)
    if not _is_student(user):
        return []
    inviter_id = _user_pk(user)
    students = _load_classroom_students(conn, class_offering_id)
    exclude = {inviter_id}
    if group_id is not None:
        exclude |= {int(m["student_id"]) for m in _load_scheme_members(conn, [int(group_id)]).get(int(group_id), [])}
        pending = conn.execute(
            "SELECT invitee_student_id FROM group_invitations WHERE group_id = ? AND status = ?",
            (int(group_id), INVITE_STATUS_PENDING),
        ).fetchall()
        exclude |= {int(r["invitee_student_id"]) for r in pending}
    candidates = []
    for s in students:
        sid = int(s["id"])
        if sid in exclude:
            continue
        blocked = _invite_decline_count(conn, inviter_id, sid) >= INVITE_DECLINE_BLOCK_THRESHOLD
        candidates.append({
            "student_id": sid,
            "name": str(s["name"]),
            "student_id_number": str(s.get("student_id_number") or ""),
            "blocked": blocked,
        })
    return candidates


def _serialize_student_group(conn, group_row: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    current_student_id = _user_pk(user) if _is_student(user) else None
    members = _load_scheme_members(conn, [int(group_row["id"])]).get(int(group_row["id"]), [])
    entry = _serialize_group_entry(group_row, members, current_student_id=current_student_id, is_teacher=_is_teacher(user), editable=True)
    leader_id = _safe_int(group_row.get("leader_student_id"))
    entry["origin"] = "student"
    entry["status"] = str(group_row.get("status") or GROUP_STATUS_ACTIVE)
    entry["is_history"] = str(group_row.get("status")) != GROUP_STATUS_ACTIVE
    entry["is_owner"] = bool(current_student_id is not None and leader_id == current_student_id)
    entry["can_invite"] = bool(entry["is_owner"] and not entry["is_history"] and not entry["is_full"])
    return entry


def _load_student_groups_for_snapshot(conn, class_offering_id: int, user: dict[str, Any]) -> list[dict[str, Any]]:
    if not _is_student(user):
        return []
    student_id = _user_pk(user)
    rows = conn.execute(
        """
        SELECT g.* FROM study_groups g
        JOIN study_group_members m ON m.group_id = g.id AND m.student_id = ? AND m.status = 'active'
        WHERE g.class_offering_id = ? AND g.join_policy = ? AND g.scheme_id IS NULL
        ORDER BY CASE g.status WHEN 'active' THEN 0 ELSE 1 END, g.updated_at DESC, g.id DESC
        """,
        (int(student_id), int(class_offering_id), STUDENT_GROUP_JOIN_POLICY),
    ).fetchall()
    return [_serialize_student_group(conn, dict(row), user) for row in rows]


def _load_my_invitations(conn, class_offering_id: int, user: dict[str, Any]) -> list[dict[str, Any]]:
    if not _is_student(user):
        return []
    rows = conn.execute(
        """
        SELECT inv.id, inv.group_id, inv.inviter_student_id, inv.created_at,
               g.name AS group_name, g.status AS group_status, g.max_members,
               inviter.name AS inviter_name, inviter.avatar_file_hash AS inviter_avatar
        FROM group_invitations inv
        JOIN study_groups g ON g.id = inv.group_id
        JOIN students inviter ON inviter.id = inv.inviter_student_id
        WHERE inv.class_offering_id = ? AND inv.invitee_student_id = ? AND inv.status = ?
        ORDER BY inv.id DESC
        """,
        (int(class_offering_id), _user_pk(user), INVITE_STATUS_PENDING),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        if str(item.get("group_status")) != GROUP_STATUS_ACTIVE:
            continue
        member_count = conn.execute(
            "SELECT COUNT(*) AS c FROM study_group_members WHERE group_id = ? AND status = 'active'",
            (int(item["group_id"]),),
        ).fetchone()["c"]
        result.append({
            "id": int(item["id"]),
            "group_id": int(item["group_id"]),
            "group_name": str(item["group_name"] or "学习小组"),
            "inviter_name": str(item["inviter_name"] or "同学"),
            "inviter_avatar_url": _avatar_url("student", item["inviter_student_id"], item.get("inviter_avatar")),
            "member_count": int(member_count or 0),
            "max_members": int(item.get("max_members") or DEFAULT_GROUP_MAX_MEMBERS),
            "created_at": str(item.get("created_at") or ""),
        })
    return result


def load_collaboration_snapshot(conn, class_offering_id: int, user: dict[str, Any]) -> dict[str, Any]:
    offering = ensure_classroom_access(conn, class_offering_id, user)
    rows = conn.execute(
        """
        SELECT g.*, a.title AS assignment_title, o.teacher_id, o.class_id
        FROM study_groups g
        JOIN class_offerings o ON o.id = g.class_offering_id
        LEFT JOIN assignments a ON a.id = g.assignment_id
        WHERE g.class_offering_id = ?
          AND g.scheme_id IS NULL
          AND g.join_policy != 'invite'
        ORDER BY
            CASE g.status WHEN 'active' THEN 0 ELSE 1 END,
            g.updated_at DESC,
            g.id DESC
        """,
        (int(class_offering_id),),
    ).fetchall()
    group_rows = [dict(row) for row in rows]
    group_ids = [int(row["id"]) for row in group_rows]
    maps = _load_group_maps(conn, group_ids)
    current_student_id = _user_pk(user) if _is_student(user) else None
    student_groups = [
        row for row in group_rows
        if current_student_id is not None
        and any(int(member["student_id"]) == current_student_id for member in maps["members"].get(int(row["id"]), []))
    ]
    visible_group_rows = group_rows
    if _is_student(user):
        visible_group_rows = []
        for row in group_rows:
            group_id = int(row["id"])
            member_ids = {int(item["student_id"]) for item in maps["members"].get(group_id, [])}
            is_member = current_student_id in member_ids if current_student_id is not None else False
            is_open_group = (
                str(row.get("status") or "") == GROUP_STATUS_ACTIVE
                and str(row.get("join_policy") or "") == GROUP_JOIN_OPEN
            )
            if is_member or is_open_group:
                visible_group_rows.append(row)

    groups = []
    for row in visible_group_rows:
        group_id = int(row["id"])
        members = maps["members"].get(group_id, [])
        member_ids = {int(item["student_id"]) for item in members}
        is_member = current_student_id in member_ids if current_student_id is not None else False
        can_access_work = _is_teacher(user) or is_member
        member_count = len(members)
        can_join = (
            _is_student(user)
            and not is_member
            and row.get("status") == GROUP_STATUS_ACTIVE
            and row.get("join_policy") == GROUP_JOIN_OPEN
            and member_count < int(row.get("max_members") or DEFAULT_GROUP_MAX_MEMBERS)
            and _student_conflict_group(
                conn,
                class_offering_id=int(class_offering_id),
                student_id=int(current_student_id),
                assignment_id=_normalize_assignment_id(row.get("assignment_id")),
                exclude_group_id=group_id,
            ) is None
        )
        reviews = maps["reviews"].get(group_id, [])
        visible_reviews = []
        if _is_teacher(user):
            visible_reviews = reviews
        elif current_student_id is not None and is_member:
            visible_reviews = [
                review for review in reviews
                if int(review["reviewer_student_id"]) == current_student_id
                or (int(review["reviewee_student_id"]) == current_student_id and review.get("share_with_reviewee"))
            ]
        groups.append({
            "id": group_id,
            "name": str(row["name"] or "未命名小组"),
            "description": str(row["description"] or ""),
            "status": str(row["status"] or GROUP_STATUS_ACTIVE),
            "join_policy": str(row["join_policy"] or GROUP_JOIN_OPEN),
            "max_members": int(row["max_members"] or DEFAULT_GROUP_MAX_MEMBERS),
            "leader_student_id": _safe_int(row.get("leader_student_id")),
            "assignment_id": str(row.get("assignment_id") or ""),
            "assignment_title": str(row.get("assignment_title") or ""),
            "member_count": member_count,
            "members": members if can_access_work or row.get("join_policy") == GROUP_JOIN_OPEN else [],
            "files": maps["files"].get(group_id, []) if can_access_work else [],
            "file_count": int(maps["file_counts"].get(group_id, 0)),
            "submissions": maps["submissions"].get(group_id, []) if can_access_work else [],
            "submission_count": len(maps["submissions"].get(group_id, [])),
            "peer_reviews": visible_reviews,
            "peer_summary": _build_peer_summary(reviews, members) if _is_teacher(user) else [],
            "my_membership": is_member,
            "can_join": bool(can_join),
            "can_leave": bool(_is_student(user) and is_member),
            "can_manage": bool(_can_manage_group(conn, row, user)),
            "can_upload": bool(can_access_work and row.get("status") == GROUP_STATUS_ACTIVE),
            "can_submit": bool(_can_manage_group(conn, row, user) and row.get("status") == GROUP_STATUS_ACTIVE),
            "can_review": bool(_is_student(user) and is_member and member_count > 1 and row.get("status") == GROUP_STATUS_ACTIVE),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        })

    pending_review_count = (
        _pending_review_count_for_student(
            student_id=int(current_student_id),
            groups=student_groups,
            group_members=maps["members"],
            group_reviews=maps["reviews"],
        )
        if current_student_id is not None
        else 0
    )
    return {
        "role": str(user.get("role") or ""),
        "classroom": {
            "id": int(offering["id"]),
            "course_name": str(offering["course_name"] or ""),
            "class_name": str(offering["class_name"] or ""),
        },
        "summary": {
            "group_count": len([group for group in groups if group["status"] == GROUP_STATUS_ACTIVE]),
            "my_group_count": len(student_groups) if current_student_id is not None else len(groups),
            "file_count": sum(int(group["file_count"]) for group in groups if _is_teacher(user) or group["my_membership"]),
            "submission_count": sum(int(group["submission_count"]) for group in groups if _is_teacher(user) or group["my_membership"]),
            "pending_peer_review_count": pending_review_count,
        },
        "groups": groups,
        "schemes": _load_schemes_for_snapshot(conn, class_offering_id, user),
        "my_groups": _load_student_groups_for_snapshot(conn, class_offering_id, user),
        "my_invitations": _load_my_invitations(conn, class_offering_id, user),
        "invite_limits": {
            "max_active_groups": STUDENT_MAX_ACTIVE_GROUPS,
            "active_group_count": _student_active_invite_group_count(conn, class_offering_id, _user_pk(user)) if _is_student(user) else 0,
            "max_per_hour": INVITE_MAX_PER_HOUR,
            "min_interval_seconds": INVITE_MIN_INTERVAL_SECONDS,
        },
        "assignments": _load_assignment_options(conn, class_offering_id),
        "students": _load_classroom_students(conn, class_offering_id) if _is_teacher(user) else [],
        "limits": {
            "max_group_members": MAX_GROUP_MEMBERS_LIMIT,
        },
    }
