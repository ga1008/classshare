from __future__ import annotations

import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from fastapi import HTTPException

from .blog_service import POST_STATUS_DRAFT, VISIBILITY_CLASS, create_post
from .file_service import resolve_global_file_path
from .message_center_service import create_collaboration_notification


GROUP_STATUS_ACTIVE = "active"
GROUP_STATUS_ARCHIVED = "archived"
GROUP_JOIN_OPEN = "open"
GROUP_JOIN_LOCKED = "locked"
GROUP_JOIN_TEACHER_ASSIGNED = "teacher_assigned"
GROUP_JOIN_POLICIES = {GROUP_JOIN_OPEN, GROUP_JOIN_LOCKED, GROUP_JOIN_TEACHER_ASSIGNED}
MAX_GROUP_MEMBERS_LIMIT = 12
DEFAULT_GROUP_MAX_MEMBERS = 6


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
    user_id = _user_pk(user)
    offering = conn.execute(
        """
        SELECT o.id, o.class_id, o.teacher_id, c.name AS course_name, cl.name AS class_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        WHERE o.id = ?
        LIMIT 1
        """,
        (int(class_offering_id),),
    ).fetchone()
    if offering is None:
        raise HTTPException(404, "课堂不存在")

    if _is_teacher(user):
        if int(offering["teacher_id"]) != user_id:
            raise HTTPException(403, "无权访问该课堂协作区")
        return dict(offering)

    if _is_student(user):
        row = conn.execute(
            """
            SELECT 1
            FROM students
            WHERE id = ?
              AND class_id = ?
              AND COALESCE(enrollment_status, 'active') = 'active'
            LIMIT 1
            """,
            (user_id, int(offering["class_id"])),
        ).fetchone()
        if row is not None:
            return dict(offering)

    raise HTTPException(403, "无权访问该课堂协作区")


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

    cursor = conn.execute(
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
    group_id = int(cursor.lastrowid)
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
    cursor = conn.execute(
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
    file_row = _load_group_file(conn, int(cursor.lastrowid))
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
        cursor = conn.execute(
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
        submission_id = int(cursor.lastrowid)
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
        cursor = conn.execute(
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
        review_id = int(cursor.lastrowid)
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


def load_collaboration_snapshot(conn, class_offering_id: int, user: dict[str, Any]) -> dict[str, Any]:
    offering = ensure_classroom_access(conn, class_offering_id, user)
    rows = conn.execute(
        """
        SELECT g.*, a.title AS assignment_title, o.teacher_id, o.class_id
        FROM study_groups g
        JOIN class_offerings o ON o.id = g.class_offering_id
        LEFT JOIN assignments a ON a.id = g.assignment_id
        WHERE g.class_offering_id = ?
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
        "assignments": _load_assignment_options(conn, class_offering_id),
        "students": _load_classroom_students(conn, class_offering_id) if _is_teacher(user) else [],
        "limits": {
            "max_group_members": MAX_GROUP_MEMBERS_LIMIT,
        },
    }
