from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlsplit

from .message_center_service import (
    MESSAGE_CATEGORY_PRIVATE,
    build_user_identity,
)
from .learning_progress_service import build_student_global_cultivation_profile
from .student_auth_service import build_student_security_summary

PROFILE_SECTIONS = ("overview", "settings", "security", "notifications", "private")

EDITABLE_PROFILE_FIELDS = (
    "nickname",
    "email",
    "phone",
    "wechat",
    "qq",
    "homepage_url",
    "description",
)

FIELD_LIMITS = {
    "nickname": 40,
    "email": 120,
    "phone": 32,
    "wechat": 64,
    "qq": 32,
    "homepage_url": 240,
    "description": 600,
}

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_profile_section(section: Any) -> str:
    normalized = str(section or "overview").strip().lower()
    return normalized if normalized in PROFILE_SECTIONS else "overview"


def _now_iso() -> str:
    return datetime.now().isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _query_scalar(conn, sql: str, params: tuple[Any, ...] = (), default: int | float = 0):
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return default
    value = row[0]
    return default if value is None else value


def _normalize_text(value: Any, *, limit: int, multiline: bool = False) -> str:
    normalized = str(value or "").replace("\x00", "").strip()
    if multiline:
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    else:
        normalized = re.sub(r"\s+", " ", normalized)
    return normalized[:limit]


def _normalize_email(value: Any, *, required: bool = False) -> str:
    normalized = _normalize_text(value, limit=FIELD_LIMITS["email"]).lower()
    if required and not normalized:
        raise ValueError("邮箱不能为空。")
    if normalized and not EMAIL_PATTERN.match(normalized):
        raise ValueError("请输入有效的邮箱地址。")
    return normalized


def _normalize_homepage_url(value: Any) -> str:
    normalized = _normalize_text(value, limit=FIELD_LIMITS["homepage_url"])
    if not normalized:
        return ""
    if "://" not in normalized:
        normalized = f"https://{normalized}"
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("个人主页仅支持 http 或 https 链接。")
    return normalized


def _normalize_profile_payload(payload: dict[str, Any], *, role: str) -> dict[str, str]:
    normalized = {
        "nickname": _normalize_text(payload.get("nickname"), limit=FIELD_LIMITS["nickname"]),
        "email": _normalize_email(payload.get("email"), required=role == "teacher"),
        "phone": _normalize_text(payload.get("phone"), limit=FIELD_LIMITS["phone"]),
        "wechat": _normalize_text(payload.get("wechat"), limit=FIELD_LIMITS["wechat"]),
        "qq": _normalize_text(payload.get("qq"), limit=FIELD_LIMITS["qq"]),
        "homepage_url": _normalize_homepage_url(payload.get("homepage_url")),
        "description": _normalize_text(payload.get("description"), limit=FIELD_LIMITS["description"], multiline=True),
    }
    return normalized


def _profile_avatar_url(profile: dict[str, Any]) -> str:
    revision = str(profile.get("avatar_updated_at") or profile.get("avatar_file_hash") or "default")
    return f"/api/profile/avatar?v={quote(revision, safe='')}"


def _serialize_profile(row, *, role: str) -> dict[str, Any]:
    item = dict(row)
    profile = {
        "id": _safe_int(item.get("id")),
        "role": role,
        "role_label": "教师" if role == "teacher" else "学生",
        "name": str(item.get("name") or ""),
        "student_id_number": str(item.get("student_id_number") or ""),
        "class_name": str(item.get("class_name") or ""),
        "classmate_count": _safe_int(item.get("classmate_count")),
        "email": str(item.get("email") or ""),
        "phone": str(item.get("phone") or ""),
        "wechat": str(item.get("wechat") or ""),
        "qq": str(item.get("qq") or ""),
        "homepage_url": str(item.get("homepage_url") or ""),
        "nickname": str(item.get("nickname") or ""),
        "description": str(item.get("description") or "") if role == "teacher" else "",
        "profile_info": str(item.get("profile_info") or ""),
        "created_at": str(item.get("created_at") or ""),
        "password_updated_at": str(item.get("password_updated_at") or ""),
        "avatar_file_hash": str(item.get("avatar_file_hash") or ""),
        "avatar_mime_type": str(item.get("avatar_mime_type") or ""),
        "avatar_updated_at": str(item.get("avatar_updated_at") or ""),
        "today_mood": str(item.get("today_mood") or ""),
        "today_mood_updated_at": str(item.get("today_mood_updated_at") or ""),
    }
    profile["display_role"] = profile["nickname"] or profile["role_label"]
    profile["avatar_url"] = _profile_avatar_url(profile)
    profile["completion"] = _calculate_profile_completion(profile)
    return profile


def _calculate_profile_completion(profile: dict[str, Any]) -> dict[str, Any]:
    weighted_fields = [
        "email",
        "phone",
        "wechat",
        "qq",
        "homepage_url",
        "avatar_file_hash",
        "today_mood",
    ]
    if profile.get("role") == "teacher":
        weighted_fields.append("description")
    completed = [field for field in weighted_fields if str(profile.get(field) or "").strip()]
    percent = round(len(completed) / len(weighted_fields) * 100) if weighted_fields else 0
    return {
        "percent": percent,
        "completed": len(completed),
        "total": len(weighted_fields),
    }


def get_user_profile(conn, user: dict) -> dict[str, Any]:
    role = str(user.get("role") or "").strip().lower()
    user_id = _safe_int(user.get("id"))
    if role == "teacher":
        row = conn.execute(
            """
            SELECT id, name, email, phone, wechat, qq, homepage_url, password_updated_at, profile_info,
                   nickname, description, avatar_file_hash, avatar_mime_type,
                   avatar_updated_at, today_mood, today_mood_updated_at, created_at
            FROM teachers
            WHERE id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT s.id, s.student_id_number, s.name, s.gender, s.email, s.phone,
                   s.wechat, s.qq, s.homepage_url, s.profile_info, s.nickname,
                   s.description, s.password_updated_at, s.avatar_file_hash,
                   s.avatar_mime_type, s.avatar_updated_at, s.today_mood,
                   s.today_mood_updated_at, s.created_at,
                   c.name AS class_name,
                   (SELECT COUNT(*) FROM students peers WHERE peers.class_id = s.class_id) AS classmate_count
            FROM students s
            JOIN classes c ON c.id = s.class_id
            WHERE s.id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

    if row is None:
        raise ValueError("当前账号不存在。")
    return _serialize_profile(row, role=role)


def _notification_unread_count(conn, *, role: str, user_id: int) -> int:
    return int(
        _query_scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM message_center_notifications
            WHERE recipient_role = ?
              AND recipient_user_pk = ?
              AND category != ?
              AND read_at IS NULL
            """,
            (role, user_id, MESSAGE_CATEGORY_PRIVATE),
        )
    )


def _private_unread_count(conn, *, role: str, user_id: int) -> int:
    identity = build_user_identity(role, user_id)
    return int(
        _query_scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM private_messages
            WHERE recipient_identity = ?
              AND read_at IS NULL
            """,
            (identity,),
        )
    )


def build_profile_nav(conn, user: dict, active_section: str) -> list[dict[str, Any]]:
    role = str(user.get("role") or "").strip().lower()
    user_id = _safe_int(user.get("id"))
    notification_count = _notification_unread_count(conn, role=role, user_id=user_id)
    private_count = _private_unread_count(conn, role=role, user_id=user_id)

    nav_items = [
        ("overview", "个人首页", "总览"),
        ("settings", "基础信息", "资料"),
        ("security", "账号安全", "安全"),
        ("notifications", "通知中心", "通知"),
        ("private", "私信", "私信"),
    ]
    badges = {
        "notifications": notification_count,
        "private": private_count,
    }
    return [
        {
            "section": section,
            "label": label,
            "short_label": short_label,
            "href": f"/profile?section={section}",
            "active": section == active_section,
            "badge": badges.get(section) or None,
        }
        for section, label, short_label in nav_items
    ]


def _load_recent_notifications(conn, *, role: str, user_id: int, limit: int = 5) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT title, body_preview, category, actor_display_name, created_at, read_at, link_url
        FROM message_center_notifications
        WHERE recipient_role = ?
          AND recipient_user_pk = ?
          AND category != ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (role, user_id, MESSAGE_CATEGORY_PRIVATE, max(1, min(int(limit), 10))),
    ).fetchall()
    return [
        {
            "type": "通知",
            "title": str(row["title"] or ""),
            "subtitle": str(row["body_preview"] or row["actor_display_name"] or ""),
            "created_at": str(row["created_at"] or ""),
            "href": str(row["link_url"] or "/profile?section=notifications"),
            "is_unread": not bool(row["read_at"]),
        }
        for row in rows
    ]


def _load_teacher_recent_items(conn, teacher_id: int, role: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    submission_rows = conn.execute(
        """
        SELECT s.id, s.student_name, s.status, s.score, s.submitted_at,
               a.title AS assignment_title
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        WHERE (o.teacher_id = ?
           OR a.course_id IN (SELECT id FROM courses WHERE created_by_teacher_id = ?))
          AND NOT EXISTS (
              SELECT 1 FROM learning_stage_exam_attempts lsea
              WHERE lsea.assignment_id = a.id
          )
        ORDER BY s.submitted_at DESC, s.id DESC
        LIMIT 4
        """,
        (teacher_id, teacher_id),
    ).fetchall()
    for row in submission_rows:
        items.append({
            "type": "提交",
            "title": f"{row['student_name']} 提交了 {row['assignment_title']}",
            "subtitle": f"状态 {row['status'] or '-'} · 分数 {row['score'] if row['score'] is not None else '待评'}",
            "created_at": str(row["submitted_at"] or ""),
            "href": f"/submission/{row['id']}",
            "is_unread": False,
        })

    assignment_rows = conn.execute(
        """
        SELECT a.id, a.title, a.status, a.created_at
        FROM assignments a
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        WHERE (o.teacher_id = ?
           OR a.course_id IN (SELECT id FROM courses WHERE created_by_teacher_id = ?))
          AND NOT EXISTS (
              SELECT 1 FROM learning_stage_exam_attempts lsea
              WHERE lsea.assignment_id = a.id
          )
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT 3
        """,
        (teacher_id, teacher_id),
    ).fetchall()
    for row in assignment_rows:
        items.append({
            "type": "任务",
            "title": str(row["title"] or "未命名任务"),
            "subtitle": f"状态 {row['status'] or '-'}",
            "created_at": str(row["created_at"] or ""),
            "href": f"/assignment/{row['id']}",
            "is_unread": False,
        })

    items.extend(_load_recent_notifications(conn, role=role, user_id=teacher_id, limit=4))
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return items[:7]


def _load_student_recent_items(conn, student_id: int, role: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    assignment_rows = conn.execute(
        """
        SELECT a.id, a.title, a.status, a.due_at, a.created_at,
               sub.status AS submission_status, sub.score
        FROM students stu
        JOIN class_offerings o ON o.class_id = stu.class_id
        JOIN assignments a ON a.class_offering_id = o.id
        LEFT JOIN submissions sub ON sub.assignment_id = a.id AND sub.student_pk_id = stu.id
        WHERE stu.id = ?
          AND a.status IN ('published', 'closed')
          AND NOT EXISTS (
              SELECT 1 FROM learning_stage_exam_attempts lsea
              WHERE lsea.assignment_id = a.id
                AND lsea.student_id != stu.id
          )
        ORDER BY COALESCE(a.due_at, a.created_at) DESC, a.id DESC
        LIMIT 5
        """,
        (student_id,),
    ).fetchall()
    for row in assignment_rows:
        submitted = bool(row["submission_status"])
        items.append({
            "type": "作业",
            "title": str(row["title"] or "未命名任务"),
            "subtitle": "已提交" if submitted else "待完成",
            "created_at": str(row["due_at"] or row["created_at"] or ""),
            "href": f"/assignment/{row['id']}",
            "is_unread": not submitted,
        })

    submission_rows = conn.execute(
        """
        SELECT s.id, s.status, s.score, s.submitted_at, a.title AS assignment_title
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        WHERE s.student_pk_id = ?
        ORDER BY s.submitted_at DESC, s.id DESC
        LIMIT 4
        """,
        (student_id,),
    ).fetchall()
    for row in submission_rows:
        items.append({
            "type": "提交",
            "title": str(row["assignment_title"] or "提交记录"),
            "subtitle": f"状态 {row['status'] or '-'} · 分数 {row['score'] if row['score'] is not None else '待评'}",
            "created_at": str(row["submitted_at"] or ""),
            "href": f"/submission/{row['id']}",
            "is_unread": False,
        })

    items.extend(_load_recent_notifications(conn, role=role, user_id=student_id, limit=4))
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return items[:7]


def _build_teacher_overview(conn, profile: dict[str, Any], user: dict) -> dict[str, Any]:
    teacher_id = int(profile["id"])
    role = "teacher"
    assignment_stats = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN a.status = 'new' THEN 1 ELSE 0 END) AS draft_count,
               SUM(CASE WHEN a.status = 'published' THEN 1 ELSE 0 END) AS published_count,
               SUM(CASE WHEN a.status = 'closed' THEN 1 ELSE 0 END) AS closed_count
        FROM assignments a
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        WHERE (o.teacher_id = ?
           OR a.course_id IN (SELECT id FROM courses WHERE created_by_teacher_id = ?))
          AND NOT EXISTS (
              SELECT 1 FROM learning_stage_exam_attempts lsea
              WHERE lsea.assignment_id = a.id
          )
        """,
        (teacher_id, teacher_id),
    ).fetchone()
    submission_stats = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN s.status IN ('submitted', 'grading') AND s.score IS NULL THEN 1 ELSE 0 END) AS pending_count,
               SUM(CASE WHEN s.score IS NOT NULL OR s.status = 'graded' THEN 1 ELSE 0 END) AS graded_count
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        WHERE (o.teacher_id = ?
           OR a.course_id IN (SELECT id FROM courses WHERE created_by_teacher_id = ?))
          AND NOT EXISTS (
              SELECT 1 FROM learning_stage_exam_attempts lsea
              WHERE lsea.assignment_id = a.id
          )
        """,
        (teacher_id, teacher_id),
    ).fetchone()
    student_count = int(
        _query_scalar(
            conn,
            """
            SELECT COUNT(DISTINCT s.id)
            FROM students s
            JOIN class_offerings o ON o.class_id = s.class_id
            WHERE o.teacher_id = ?
            """,
            (teacher_id,),
        )
    )
    class_count = int(
        _query_scalar(
            conn,
            "SELECT COUNT(DISTINCT class_id) FROM class_offerings WHERE teacher_id = ?",
            (teacher_id,),
        )
    )
    course_count = int(
        _query_scalar(
            conn,
            "SELECT COUNT(*) FROM courses WHERE created_by_teacher_id = ?",
            (teacher_id,),
        )
    )
    offering_count = int(
        _query_scalar(
            conn,
            "SELECT COUNT(*) FROM class_offerings WHERE teacher_id = ?",
            (teacher_id,),
        )
    )
    today_login_count = int(
        _query_scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM student_login_audit_logs logs
            JOIN class_offerings o ON o.class_id = logs.class_id
            WHERE o.teacher_id = ?
              AND date(logs.logged_at) = date('now', 'localtime')
            """,
            (teacher_id,),
        )
    )
    behavior_total = int(
        _query_scalar(
            conn,
            """
            SELECT SUM(total_activity_count)
            FROM classroom_behavior_states
            WHERE user_role = 'teacher' AND user_pk = ?
            """,
            (teacher_id,),
        )
    )
    pending_count = int(submission_stats["pending_count"] or 0) if submission_stats else 0
    graded_count = int(submission_stats["graded_count"] or 0) if submission_stats else 0
    draft_count = int(assignment_stats["draft_count"] or 0) if assignment_stats else 0
    published_count = int(assignment_stats["published_count"] or 0) if assignment_stats else 0
    notification_unread = _notification_unread_count(conn, role=role, user_id=teacher_id)

    return {
        "headline": "教学工作总览",
        "metric_cards": [
            {"label": "覆盖学生", "value": student_count, "note": f"{class_count} 个班级"},
            {"label": "授课课堂", "value": offering_count, "note": f"{course_count} 门课程模板"},
            {"label": "待批改", "value": pending_count, "note": f"已评 {graded_count} 份"},
            {"label": "未读通知", "value": notification_unread, "note": f"今日学生登录 {today_login_count} 次"},
        ],
        "charts": [
            {
                "id": "teacher-workload",
                "title": "任务状态",
                "type": "doughnut",
                "labels": ["待批改", "已批改", "草稿", "已发布"],
                "values": [pending_count, graded_count, draft_count, published_count],
            },
            {
                "id": "teacher-activity",
                "title": "教学触达",
                "type": "bar",
                "labels": ["学生", "课堂", "今日登录", "互动"],
                "values": [student_count, offering_count, today_login_count, behavior_total],
            },
        ],
        "recent_items": _load_teacher_recent_items(conn, teacher_id, role),
    }


def _build_student_overview(conn, profile: dict[str, Any], user: dict) -> dict[str, Any]:
    student_id = int(profile["id"])
    role = "student"
    assignment_stats = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN sub.id IS NULL AND a.status = 'published' THEN 1 ELSE 0 END) AS pending_count,
               SUM(CASE WHEN sub.id IS NOT NULL THEN 1 ELSE 0 END) AS submitted_count,
               SUM(CASE WHEN sub.status = 'grading' THEN 1 ELSE 0 END) AS grading_count,
               SUM(CASE WHEN sub.score IS NOT NULL OR sub.status = 'graded' THEN 1 ELSE 0 END) AS graded_count,
               AVG(CASE WHEN sub.score IS NOT NULL THEN sub.score END) AS avg_score
        FROM students stu
        JOIN class_offerings o ON o.class_id = stu.class_id
        JOIN assignments a ON a.class_offering_id = o.id
        LEFT JOIN submissions sub ON sub.assignment_id = a.id AND sub.student_pk_id = stu.id
        WHERE stu.id = ?
          AND a.status IN ('published', 'closed')
        """,
        (student_id,),
    ).fetchone()
    offering_count = int(
        _query_scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM class_offerings
            WHERE class_id = (SELECT class_id FROM students WHERE id = ?)
            """,
            (student_id,),
        )
    )
    behavior_row = conn.execute(
        """
        SELECT SUM(total_activity_count) AS activity_count,
               SUM(focus_total_seconds) AS focus_seconds,
               MAX(last_event_at) AS last_event_at
        FROM classroom_behavior_states
        WHERE user_role = 'student' AND user_pk = ?
        """,
        (student_id,),
    ).fetchone()
    security_summary = build_student_security_summary(conn, student_id)
    total = int(assignment_stats["total"] or 0) if assignment_stats else 0
    pending_count = int(assignment_stats["pending_count"] or 0) if assignment_stats else 0
    submitted_count = int(assignment_stats["submitted_count"] or 0) if assignment_stats else 0
    grading_count = int(assignment_stats["grading_count"] or 0) if assignment_stats else 0
    graded_count = int(assignment_stats["graded_count"] or 0) if assignment_stats else 0
    avg_score = assignment_stats["avg_score"] if assignment_stats and assignment_stats["avg_score"] is not None else None
    activity_count = int(behavior_row["activity_count"] or 0) if behavior_row else 0
    focus_minutes = round(int(behavior_row["focus_seconds"] or 0) / 60) if behavior_row else 0
    notification_unread = _notification_unread_count(conn, role=role, user_id=student_id)
    cultivation = build_student_global_cultivation_profile(conn, student_id)
    cultivation_courses = cultivation.get("courses") or []
    breakthrough_count = sum(1 for item in cultivation_courses if item.get("eligible_stage"))
    cultivation_level = (cultivation.get("highest_level") or {}).get("level_name") or "凡阶"

    return {
        "headline": "学习情况总览",
        "hero_stats": [
            {"label": "参与课堂", "value": offering_count, "suffix": "门"},
            {"label": "待完成", "value": pending_count, "suffix": "项"},
            {
                "label": "平均分",
                "value": round(float(avg_score), 1) if avg_score is not None else "-",
                "suffix": "",
            },
            {"label": "当前修为", "value": cultivation.get("score", 0), "suffix": ""},
        ],
        "metric_cards": [
            {"label": "待完成", "value": pending_count, "note": f"全部任务 {total} 项"},
            {"label": "已提交", "value": submitted_count, "note": f"批改中 {grading_count} 项"},
            {"label": "平均分", "value": round(float(avg_score), 1) if avg_score is not None else "-", "note": f"已批改 {graded_count} 项"},
            {"label": "修炼进度", "value": cultivation.get("score", 0), "note": f"{cultivation_level} · 可破境 {breakthrough_count} 门"},
        ],
        "charts": [
            {
                "id": "student-progress",
                "title": "任务进度",
                "type": "doughnut",
                "labels": ["待完成", "已提交", "批改中", "已批改"],
                "values": [pending_count, submitted_count, grading_count, graded_count],
            },
            {
                "id": "student-activity",
                "title": "学习触点",
                "type": "bar",
                "labels": ["课堂", "登录", "互动", "未读通知"],
                "values": [
                    offering_count,
                    int(security_summary.get("total_logins") or 0),
                    activity_count,
                    notification_unread,
                ],
            },
        ],
        "recent_items": _load_student_recent_items(conn, student_id, role),
        "security_summary": security_summary,
        "cultivation": cultivation,
        "activity_summary": {
            "activity_count": activity_count,
            "focus_minutes": focus_minutes,
            "notification_unread": notification_unread,
        },
    }


def build_profile_overview(conn, profile: dict[str, Any], user: dict) -> dict[str, Any]:
    if profile["role"] == "teacher":
        return _build_teacher_overview(conn, profile, user)
    return _build_student_overview(conn, profile, user)


def build_profile_page_context(conn, user: dict, section: Any) -> dict[str, Any]:
    active_section = normalize_profile_section(section)
    profile = get_user_profile(conn, user)
    overview = build_profile_overview(conn, profile, user)
    nav_items = build_profile_nav(conn, user, active_section)
    return {
        "active_section": active_section,
        "profile": profile,
        "overview": overview,
        "nav_items": nav_items,
        "notification_unread_count": next((item.get("badge") or 0 for item in nav_items if item["section"] == "notifications"), 0),
        "private_unread_count": next((item.get("badge") or 0 for item in nav_items if item["section"] == "private"), 0),
    }


def update_basic_profile(conn, user: dict, payload: dict[str, Any]) -> dict[str, Any]:
    role = str(user.get("role") or "").strip().lower()
    user_id = _safe_int(user.get("id"))
    data = _normalize_profile_payload(payload, role=role)
    table_name = "teachers" if role == "teacher" else "students"
    description_value = data["description"]
    if role == "student":
        existing = conn.execute(f"SELECT description FROM {table_name} WHERE id = ?", (user_id,)).fetchone()
        description_value = str(existing["description"] or "") if existing else ""
    conn.execute(
        f"""
        UPDATE {table_name}
        SET nickname = ?,
            email = ?,
            phone = ?,
            wechat = ?,
            qq = ?,
            homepage_url = ?,
            description = ?
        WHERE id = ?
        """,
        (
            data["nickname"],
            data["email"],
            data["phone"],
            data["wechat"],
            data["qq"],
            data["homepage_url"],
            description_value,
            user_id,
        ),
    )
    return get_user_profile(conn, user)


def update_profile_mood(conn, user: dict, mood: Any) -> dict[str, Any]:
    role = str(user.get("role") or "").strip().lower()
    user_id = _safe_int(user.get("id"))
    table_name = "teachers" if role == "teacher" else "students"
    normalized_mood = _normalize_text(mood, limit=40)
    updated_at = _now_iso()
    conn.execute(
        f"""
        UPDATE {table_name}
        SET today_mood = ?, today_mood_updated_at = ?
        WHERE id = ?
        """,
        (normalized_mood, updated_at, user_id),
    )
    return get_user_profile(conn, user)


def update_profile_avatar(
    conn,
    user: dict,
    *,
    file_hash: str,
    mime_type: str,
) -> dict[str, Any]:
    role = str(user.get("role") or "").strip().lower()
    user_id = _safe_int(user.get("id"))
    table_name = "teachers" if role == "teacher" else "students"
    conn.execute(
        f"""
        UPDATE {table_name}
        SET avatar_file_hash = ?,
            avatar_mime_type = ?,
            avatar_updated_at = ?
        WHERE id = ?
        """,
        (str(file_hash or "").strip(), str(mime_type or "").strip(), _now_iso(), user_id),
    )
    return get_user_profile(conn, user)
