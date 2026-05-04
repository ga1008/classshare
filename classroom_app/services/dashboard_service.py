from __future__ import annotations

import sqlite3
import re
from datetime import datetime, timedelta
from typing import Any

from .message_center_service import CATEGORY_LABELS, get_message_center_summary
from .academic_service import (
    build_semester_calendar_payload,
    load_student_semester_rows,
    load_teacher_semester_rows,
)
from .student_auth_service import build_student_security_summary
from .ui_copy_service import get_ui_copy_block, render_ui_copy_block
from .prompt_utils import polite_address
from .learning_progress_service import (
    build_student_global_cultivation_profile,
    serialize_student_learning_progress,
)

RECENT_ACTIVITY_DAYS = 14

ACTIVITY_TONE_BY_CATEGORY = {
    "private_message": "neutral",
    "assignment": "primary",
    "discussion_mention": "warning",
    "submission": "success",
    "grading_result": "success",
    "ai_feedback": "primary",
}

DASHBOARD_FILTER_VALUES = {
    "teacher": ("all", "attention", "recent"),
    "student": ("all", "attention", "progress", "recent"),
}


def build_dashboard_context(
    conn,
    user: dict,
    *,
    initial_filter: Any = None,
    initial_search: Any = None,
) -> dict[str, Any]:
    role = str(user.get("role") or "").strip().lower()
    if role == "teacher":
        return _build_teacher_dashboard_context(
            conn,
            user,
            initial_filter=initial_filter,
            initial_search=initial_search,
        )
    return _build_student_dashboard_context(
        conn,
        user,
        initial_filter=initial_filter,
        initial_search=initial_search,
    )


def _build_teacher_dashboard_context(
    conn,
    user: dict,
    *,
    initial_filter: Any = None,
    initial_search: Any = None,
) -> dict[str, Any]:
    teacher_id = int(user["id"])
    offerings = _load_teacher_offerings(conn, teacher_id)
    offering_ids = [int(item["id"]) for item in offerings]
    course_ids = sorted({int(item["course_id"]) for item in offerings})

    assignment_stats = _load_teacher_assignment_stats(conn, offering_ids)
    pending_submission_stats = _load_teacher_pending_submission_stats(conn, offering_ids)
    resource_stats = _load_course_resource_stats(conn, course_ids, include_teacher_resources=True)
    material_stats = _load_offering_material_stats(conn, offering_ids)
    recent_activity = _load_recent_activity(conn, user)
    message_summary = get_message_center_summary(conn, user)
    unread_total = int(message_summary.get("unread_total") or 0)
    unique_student_count = _query_scalar(
        conn,
        """
        SELECT COUNT(DISTINCT s.id)
        FROM students s
        JOIN (
            SELECT DISTINCT class_id
            FROM class_offerings
            WHERE teacher_id = ?
        ) active_classes ON active_classes.class_id = s.class_id
        """,
        (teacher_id,),
    )
    today_login_count = _query_scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM student_login_audit_logs logs
        JOIN (
            SELECT DISTINCT class_id
            FROM class_offerings
            WHERE teacher_id = ?
        ) active_classes ON active_classes.class_id = logs.class_id
        WHERE date(logged_at) = date('now', 'localtime')
        """,
        (teacher_id,),
    )
    pending_reset_count = _query_scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM student_password_reset_requests
        WHERE teacher_id = ? AND status = 'pending'
        """,
        (teacher_id,),
    )

    enriched_offerings: list[dict[str, Any]] = []
    pending_review_total = 0
    draft_total = 0
    attention_count = 0
    recent_count = 0

    for offering in offerings:
        offering_id = int(offering["id"])
        course_id = int(offering["course_id"])
        assignment_item = assignment_stats.get(offering_id, {})
        pending_item = pending_submission_stats.get(offering_id, {})
        resource_item = resource_stats.get(course_id, {})
        material_item = material_stats.get(offering_id, {})

        student_count = int(offering.get("student_count") or 0)
        assignment_count = int(assignment_item.get("assignment_count") or 0)
        draft_count = int(assignment_item.get("draft_count") or 0)
        published_count = int(assignment_item.get("published_count") or 0)
        exam_count = int(assignment_item.get("exam_count") or 0)
        pending_review_count = int(pending_item.get("pending_review_count") or 0)
        resource_count = int(resource_item.get("resource_count") or 0)
        material_count = int(material_item.get("material_count") or 0)
        resource_total = resource_count + material_count
        last_activity_at = _pick_latest_datetime(
            offering.get("created_at"),
            assignment_item.get("latest_assignment_at"),
            pending_item.get("latest_submission_at"),
            resource_item.get("latest_resource_at"),
            material_item.get("latest_material_at"),
        )
        needs_attention = pending_review_count > 0 or draft_count > 0
        has_recent_activity = _is_recent(last_activity_at)

        pending_review_total += pending_review_count
        draft_total += draft_count
        attention_count += 1 if needs_attention else 0
        recent_count += 1 if has_recent_activity else 0

        badges = []
        if pending_review_count > 0:
            badges.append({"label": f"待批改 {pending_review_count}", "tone": "danger"})
        if draft_count > 0:
            badges.append({"label": f"草稿 {draft_count}", "tone": "warning"})
        if published_count > 0:
            badges.append({"label": f"已发布 {published_count}", "tone": "success"})
        if exam_count > 0:
            badges.append({"label": f"考试 {exam_count}", "tone": "neutral"})

        meta = [
            item
            for item in [
                offering.get("semester"),
                offering.get("schedule_info"),
                f"{student_count} 名学生" if student_count else "待导入学生",
            ]
            if item
        ]

        description = (
            str(offering.get("course_description") or "").strip()
            or str(offering.get("class_description") or "").strip()
            or "从这里继续管理作业、考试、课程资料与课堂互动。"
        )

        if pending_review_count > 0:
            summary = f"当前有 {pending_review_count} 份学生提交等待处理。"
        elif draft_count > 0:
            summary = f"还有 {draft_count} 项草稿未发布，课堂内容可以继续补齐。"
        elif assignment_count > 0:
            summary = f"当前共配置 {assignment_count} 项课堂任务，课堂结构已经成型。"
        else:
            summary = "建议优先补充任务与资料，让学生进入课堂后立即可用。"

        offering["summary"] = summary
        offering["description"] = description
        offering["meta"] = meta
        offering["badges"] = badges
        offering["resource_total"] = resource_total
        offering["resource_count"] = resource_count
        offering["material_count"] = material_count
        offering["assignment_count"] = assignment_count
        offering["draft_count"] = draft_count
        offering["exam_count"] = exam_count
        offering["pending_review_count"] = pending_review_count
        offering["last_activity_at"] = last_activity_at or ""
        offering["needs_attention"] = needs_attention
        offering["has_recent_activity"] = has_recent_activity
        offering["has_progress"] = assignment_count > 0 or resource_total > 0
        offering["metrics"] = [
            {"label": "学生", "value": student_count, "note": "班级规模"},
            {"label": "任务", "value": assignment_count, "note": f"考试 {exam_count}"},
            {"label": "待批改", "value": pending_review_count, "note": "含已提交与批改中"},
            {"label": "资料", "value": resource_total, "note": f"文件 {resource_count} · 材料 {material_count}"},
        ]
        offering["search_text"] = _build_dashboard_search_text(
            offering.get("course_name"),
            offering.get("class_name"),
            offering.get("semester"),
            offering.get("schedule_info"),
            description,
            summary,
            *meta,
            *(badge.get("label") for badge in badges),
            *(f"{metric['label']} {metric['value']} {metric['note']}" for metric in offering["metrics"]),
        )
        enriched_offerings.append(offering)

    distinct_class_count = len({int(item["class_id"]) for item in offerings})
    distinct_course_count = len({int(item["course_id"]) for item in offerings})
    ui_copy = render_ui_copy_block(
        get_ui_copy_block(conn, scene="dashboard", role="teacher"),
        {
            "name": polite_address(user.get("name") or "", "teacher"),
            "unread_total": unread_total,
            "pending_reset_count": pending_reset_count,
            "today_login_count": today_login_count,
        },
    )

    spotlight = {
        "label": ui_copy["spotlight_pending_label"],
        "value": pending_review_total,
        "suffix": "份",
        "note": ui_copy["spotlight_pending_note"],
    }
    if pending_review_total <= 0 and pending_reset_count > 0:
        spotlight = {
            "label": ui_copy["spotlight_reset_label"],
            "value": pending_reset_count,
            "suffix": "条",
            "note": ui_copy["spotlight_reset_note"],
        }
    elif pending_review_total <= 0 and unread_total > 0:
        spotlight = {
            "label": ui_copy["spotlight_unread_label"],
            "value": unread_total,
            "suffix": "条",
            "note": ui_copy["spotlight_unread_note"],
        }
    elif pending_review_total <= 0:
        spotlight = {
            "label": ui_copy["spotlight_login_label"],
            "value": today_login_count,
            "suffix": "次",
            "note": ui_copy["spotlight_login_note"],
        }

    quick_actions = [
        {
            "mode": "link",
            "label": ui_copy["action_offering_label"],
            "description": ui_copy["action_offering_description"],
            "href": "/manage/offerings",
            "badge": None,
        },
        {
            "mode": "link",
            "label": ui_copy["action_materials_label"],
            "description": ui_copy["action_materials_description"],
            "href": "/manage/materials",
            "badge": None,
        },
        {
            "mode": "link",
            "label": ui_copy["action_exams_label"],
            "description": ui_copy["action_exams_description"],
            "href": "/manage/exams",
            "badge": None,
        },
        {
            "mode": "link",
            "label": ui_copy["action_system_label"],
            "description": ui_copy["action_system_description"],
            "href": "/manage/system/password-resets",
            "badge": pending_reset_count or None,
        },
    ]

    focus_items = []
    if pending_reset_count > 0:
        focus_items.append({
            "title": "学生找回密码审核",
            "description": f"当前有 {pending_reset_count} 条申请待处理。",
            "href": "/manage/system/password-resets",
            "tone": "danger",
        })
    if unread_total > 0:
        focus_items.append({
            "title": "消息中心未读提醒",
            "description": f"还有 {unread_total} 条通知未读，建议及时回看课堂互动。",
            "href": "/message-center",
            "tone": "primary",
        })

    for offering in sorted(
        enriched_offerings,
        key=lambda item: (
            -int(item.get("pending_review_count") or 0),
            -int(item.get("draft_count") or 0),
            -int(bool(item.get("has_recent_activity"))),
            -int(item.get("id") or 0),
        ),
    ):
        if not offering["needs_attention"]:
            continue
        fragments = []
        if offering["pending_review_count"] > 0:
            fragments.append(f"{offering['pending_review_count']} 份待批改")
        if offering["draft_count"] > 0:
            fragments.append(f"{offering['draft_count']} 项草稿")
        focus_items.append({
            "title": f"{offering['class_name']} · {offering['course_name']}",
            "description": "，".join(fragments) or "课堂内容仍可继续完善。",
            "href": f"/classroom/{offering['id']}",
            "tone": "warning",
        })
        if len(focus_items) >= 4:
            break

    if not focus_items:
        focus_items.append({
            "title": ui_copy["focus_empty_title"],
            "description": ui_copy["focus_empty_description"],
            "href": "/manage/materials" if offerings else "/manage/offerings",
            "tone": "neutral",
        })

    dashboard_filters = [
        {"value": "all", "label": "全部", "count": len(offerings)},
        {"value": "attention", "label": "待处理", "count": attention_count},
        {"value": "recent", "label": "近期活跃", "count": recent_count},
    ]
    selected_filter = _normalize_dashboard_filter("teacher", initial_filter)
    search_query = _normalize_dashboard_search(initial_search)
    initial_visible_count = _apply_dashboard_view_state(
        enriched_offerings,
        filter_value=selected_filter,
        search_query=search_query,
    )
    initial_results_summary = _build_dashboard_results_summary(
        dashboard_filters,
        filter_value=selected_filter,
        search_query=search_query,
    )
    semester_calendar = build_semester_calendar_payload(
        load_teacher_semester_rows(conn, teacher_id),
    )

    return {
        "dashboard_theme": "teacher",
        "dashboard_hero": {
            "eyebrow": ui_copy["hero_eyebrow"],
            "title": ui_copy["hero_title"],
            "subtitle": ui_copy["hero_subtitle"],
            "chips": [
                f"{distinct_course_count} 门课程模板",
                f"{distinct_class_count} 个班级",
                f"今日登录 {today_login_count} 次",
            ],
            "spotlight": spotlight,
        },
        "dashboard_stats": [
            {"label": "活跃课堂", "value": len(offerings), "note": "可直接进入的教学空间"},
            {"label": "覆盖学生", "value": unique_student_count, "note": f"{distinct_class_count} 个班级"},
            {"label": "待批改", "value": pending_review_total, "note": f"草稿 {draft_total} 项"},
            {"label": "未读提醒", "value": unread_total, "note": f"待审核 {pending_reset_count} 条"},
        ],
        "dashboard_quick_actions": quick_actions,
        "dashboard_sections": {
            "quick_actions": {
                "title": ui_copy["quick_actions_title"],
                "subtitle": ui_copy["quick_actions_subtitle"],
            },
        },
        "dashboard_focus": {
            "title": ui_copy["focus_title"],
            "subtitle": ui_copy["focus_subtitle"],
            "items": focus_items,
        },
        "dashboard_activity": {
            "title": ui_copy["activity_title"],
            "subtitle": ui_copy["activity_subtitle"],
            "items": recent_activity,
        },
        "dashboard_filters": dashboard_filters,
        "dashboard_search_placeholder": "搜索课程、班级或学期",
        "dashboard_initial_filter": selected_filter,
        "dashboard_initial_search": search_query,
        "dashboard_initial_visible_count": initial_visible_count,
        "dashboard_initial_results_summary": initial_results_summary,
        "dashboard_empty_state": {
            "title": ui_copy["empty_title"],
            "description": ui_copy["empty_description"],
            "action_label": ui_copy["empty_action_label"],
            "action_href": "/manage/offerings",
        },
        "class_offerings": enriched_offerings,
        "dashboard_semester_calendar": semester_calendar,
        "student_security_summary": None,
    }


def _build_student_dashboard_context(
    conn,
    user: dict,
    *,
    initial_filter: Any = None,
    initial_search: Any = None,
) -> dict[str, Any]:
    student_id = int(user["id"])
    student_security_summary = build_student_security_summary(conn, student_id)
    student_profile = conn.execute(
        """
        SELECT s.id, s.class_id, c.name AS class_name,
               (SELECT COUNT(*) FROM students peers WHERE peers.class_id = s.class_id) AS classmate_count
        FROM students s
        JOIN classes c ON c.id = s.class_id
        WHERE s.id = ?
        LIMIT 1
        """,
        (student_id,),
    ).fetchone()
    class_name = str(student_profile["class_name"] or "") if student_profile else ""
    classmate_count = int(student_profile["classmate_count"] or 0) if student_profile else 0

    offerings = _load_student_offerings(conn, student_id)
    offering_ids = [int(item["id"]) for item in offerings]
    course_ids = sorted({int(item["course_id"]) for item in offerings})
    assignment_stats = _load_student_assignment_stats(conn, offering_ids, student_id)
    resource_stats = _load_course_resource_stats(conn, course_ids, include_teacher_resources=False)
    material_stats = _load_offering_material_stats(conn, offering_ids)
    recent_activity = _load_recent_activity(conn, user)
    message_summary = get_message_center_summary(conn, user)
    unread_total = int(message_summary.get("unread_total") or 0)
    cultivation_profile = build_student_global_cultivation_profile(conn, student_id)
    ui_copy = render_ui_copy_block(
        get_ui_copy_block(conn, scene="dashboard", role="student"),
        {
            "name": cultivation_profile.get("address_name") or polite_address(user.get("name") or "", "student"),
            "class_name": class_name or "当前班级",
            "unread_total": unread_total,
        },
    )

    enriched_offerings: list[dict[str, Any]] = []
    pending_total = 0
    submitted_total = 0
    attention_count = 0
    progress_count = 0

    for offering in offerings:
        offering_id = int(offering["id"])
        course_id = int(offering["course_id"])
        assignment_item = assignment_stats.get(offering_id, {})
        resource_item = resource_stats.get(course_id, {})
        material_item = material_stats.get(offering_id, {})
        learning_progress = serialize_student_learning_progress(conn, offering_id, student_id)
        cultivation_level = learning_progress.get("current_level") or {}

        assignment_count = int(assignment_item.get("assignment_count") or 0)
        pending_count = int(assignment_item.get("pending_count") or 0)
        submitted_count = int(assignment_item.get("submitted_count") or 0)
        graded_count = int(assignment_item.get("graded_count") or 0)
        grading_count = int(assignment_item.get("grading_count") or 0)
        exam_count = int(assignment_item.get("exam_count") or 0)
        resource_count = int(resource_item.get("resource_count") or 0)
        material_count = int(material_item.get("material_count") or 0)
        resource_total = resource_count + material_count
        last_activity_at = _pick_latest_datetime(
            offering.get("created_at"),
            assignment_item.get("last_activity_at"),
            resource_item.get("latest_resource_at"),
            material_item.get("latest_material_at"),
        )

        pending_total += pending_count
        submitted_total += submitted_count
        attention_count += 1 if pending_count > 0 else 0
        progress_count += 1 if submitted_count > 0 or grading_count > 0 or graded_count > 0 else 0

        badges = []
        if pending_count > 0:
            badges.append({"label": f"待完成 {pending_count}", "tone": "danger"})
        if grading_count > 0:
            badges.append({"label": f"批改中 {grading_count}", "tone": "warning"})
        if graded_count > 0:
            badges.append({"label": f"已批改 {graded_count}", "tone": "success"})
        if exam_count > 0:
            badges.append({"label": f"考试 {exam_count}", "tone": "primary"})
        if cultivation_level.get("tier"):
            badges.append({"label": str(cultivation_level.get("short_name") or cultivation_level.get("level_name")), "tone": "success"})

        description = (
            str(offering.get("course_description") or "").strip()
            or "进入课堂继续查看资料、作业、考试与讨论内容。"
        )
        if pending_count > 0:
            summary = f"还有 {pending_count} 项已发布任务等待完成。"
        elif grading_count > 0:
            summary = f"有 {grading_count} 项任务正在批改，可以稍后回来查看结果。"
        elif submitted_count > 0:
            summary = f"你已经完成 {submitted_count} 项任务，继续保持。"
        elif assignment_count > 0:
            summary = f"当前共有 {assignment_count} 项可查看任务，建议先浏览要求。"
        else:
            summary = "当前以资料和课堂互动为主，进入课堂即可查看完整内容。"

        meta = [
            item
            for item in [
                f"授课教师 {offering['teacher_name']}" if offering.get("teacher_name") else "",
                offering.get("semester"),
                offering.get("schedule_info"),
            ]
            if item
        ]

        offering["summary"] = summary
        offering["description"] = description
        offering["meta"] = meta
        offering["badges"] = badges
        offering["assignment_count"] = assignment_count
        offering["pending_count"] = pending_count
        offering["submitted_count"] = submitted_count
        offering["graded_count"] = graded_count
        offering["grading_count"] = grading_count
        offering["exam_count"] = exam_count
        offering["resource_total"] = resource_total
        offering["resource_count"] = resource_count
        offering["material_count"] = material_count
        offering["last_activity_at"] = last_activity_at or ""
        offering["needs_attention"] = pending_count > 0
        offering["has_recent_activity"] = _is_recent(last_activity_at)
        offering["has_progress"] = (
            submitted_count > 0
            or graded_count > 0
            or grading_count > 0
            or float(learning_progress.get("score") or 0) > 0
        )
        offering["cultivation"] = {
            "score": learning_progress.get("score", 0),
            "progress_percent": learning_progress.get("progress_percent", 0),
            "level_name": cultivation_level.get("level_name") or "未入道",
            "short_name": cultivation_level.get("short_name") or "未入道",
            "theme": cultivation_level.get("theme") or "mortal",
            "next_stage_name": (learning_progress.get("next_stage") or {}).get("name"),
        }
        offering["metrics"] = [
            {"label": "修为", "value": learning_progress.get("score", 0), "note": offering["cultivation"]["level_name"]},
            {"label": "待完成", "value": pending_count, "note": "仅统计已发布任务"},
            {"label": "已提交", "value": submitted_count, "note": f"已批改 {graded_count}"},
            {"label": "资料", "value": resource_total, "note": f"文件 {resource_count} · 材料 {material_count}"},
        ]
        offering["search_text"] = _build_dashboard_search_text(
            offering.get("course_name"),
            offering.get("class_name"),
            offering.get("teacher_name"),
            offering.get("semester"),
            offering.get("schedule_info"),
            description,
            summary,
            *meta,
            *(badge.get("label") for badge in badges),
            *(f"{metric['label']} {metric['value']} {metric['note']}" for metric in offering["metrics"]),
        )
        enriched_offerings.append(offering)

    priority_items = _load_student_priority_items(conn, student_id)
    if unread_total > 0:
        priority_items.append({
            "title": ui_copy["priority_unread_title"],
            "description": ui_copy["priority_unread_description"],
            "href": "/message-center",
            "tone": "primary",
        })
    if not priority_items:
        fallback_href = f"/classroom/{offerings[0]['id']}" if offerings else "/message-center"
        priority_items.append({
            "title": ui_copy["priority_empty_title"],
            "description": ui_copy["priority_empty_description"],
            "href": fallback_href,
            "tone": "neutral",
        })

    first_pending_href = priority_items[0]["href"] if priority_items else (f"/classroom/{offerings[0]['id']}" if offerings else "/message-center")

    total_logins = int(student_security_summary.get("total_logins") or 0) if student_security_summary else 0
    spotlight = {
        "label": ui_copy["spotlight_pending_label"],
        "value": pending_total,
        "suffix": "项",
        "note": ui_copy["spotlight_pending_note"],
    }
    if pending_total <= 0 and unread_total > 0:
        spotlight = {
            "label": ui_copy["spotlight_unread_label"],
            "value": unread_total,
            "suffix": "条",
            "note": ui_copy["spotlight_unread_note"],
        }
    elif pending_total <= 0:
        last_device = ""
        if student_security_summary and student_security_summary.get("last_login"):
            last_device = str(student_security_summary["last_login"].get("device_label") or "")
        spotlight = {
            "label": ui_copy["spotlight_login_label"],
            "value": total_logins,
            "suffix": "次",
            "note": last_device or ui_copy["spotlight_login_note"],
        }

    quick_actions = [
        {
            "mode": "link",
            "label": ui_copy["action_priority_label"],
            "description": ui_copy["action_priority_description"],
            "href": first_pending_href,
            "badge": pending_total or None,
        },
        {
            "mode": "link",
            "label": ui_copy["action_message_label"],
            "description": ui_copy["action_message_description"],
            "href": "/message-center",
            "badge": unread_total or None,
        },
        {
            "mode": "button",
            "label": ui_copy["action_security_label"],
            "description": ui_copy["action_security_description"],
            "button_attrs": {"data-open-student-security": "true"},
            "badge": None,
        },
    ]
    recent_count = sum(1 for item in enriched_offerings if item["has_recent_activity"])
    dashboard_filters = [
        {"value": "all", "label": "全部", "count": len(offerings)},
        {"value": "attention", "label": "待完成", "count": attention_count},
        {"value": "progress", "label": "有进展", "count": progress_count},
        {"value": "recent", "label": "近期活跃", "count": recent_count},
    ]
    selected_filter = _normalize_dashboard_filter("student", initial_filter)
    search_query = _normalize_dashboard_search(initial_search)
    initial_visible_count = _apply_dashboard_view_state(
        enriched_offerings,
        filter_value=selected_filter,
        search_query=search_query,
    )
    initial_results_summary = _build_dashboard_results_summary(
        dashboard_filters,
        filter_value=selected_filter,
        search_query=search_query,
    )
    semester_calendar = build_semester_calendar_payload(
        load_student_semester_rows(conn, student_id),
    )

    return {
        "dashboard_theme": "student",
        "dashboard_hero": {
            "eyebrow": ui_copy["hero_eyebrow"],
            "title": ui_copy["hero_title"],
            "subtitle": ui_copy["hero_subtitle"],
            "chips": [
                class_name or "当前班级",
                f"{cultivation_profile['highest_level']['level_name']} · 修为 {cultivation_profile['score']:g}",
                f"累计登录 {total_logins} 次",
                f"同班 {classmate_count} 人",
            ],
            "spotlight": spotlight,
        },
        "dashboard_stats": [
            {"label": "最高境界", "value": cultivation_profile["highest_level"]["short_name"], "note": cultivation_profile.get("best_course", {}).get("course_name") or class_name or "当前班级"},
            {"label": "待完成", "value": pending_total, "note": "仅统计已发布任务"},
            {"label": "已提交", "value": submitted_total, "note": "含待批改与已批改"},
            {"label": "未读提醒", "value": unread_total, "note": f"累计登录 {total_logins} 次"},
        ],
        "dashboard_quick_actions": quick_actions,
        "dashboard_sections": {
            "quick_actions": {
                "title": ui_copy["quick_actions_title"],
                "subtitle": ui_copy["quick_actions_subtitle"],
            },
        },
        "dashboard_focus": {
            "title": ui_copy["focus_title"],
            "subtitle": ui_copy["focus_subtitle"],
            "items": priority_items[:4],
        },
        "dashboard_activity": {
            "title": ui_copy["activity_title"],
            "subtitle": ui_copy["activity_subtitle"],
            "items": recent_activity,
        },
        "dashboard_filters": dashboard_filters,
        "dashboard_search_placeholder": "搜索课程、教师或学期",
        "dashboard_initial_filter": selected_filter,
        "dashboard_initial_search": search_query,
        "dashboard_initial_visible_count": initial_visible_count,
        "dashboard_initial_results_summary": initial_results_summary,
        "dashboard_empty_state": {
            "title": ui_copy["empty_title"],
            "description": ui_copy["empty_description"],
            "action_label": ui_copy["empty_action_label"],
            "action_href": "/message-center",
        },
        "class_offerings": enriched_offerings,
        "dashboard_semester_calendar": semester_calendar,
        "student_security_summary": student_security_summary,
        "cultivation_profile": cultivation_profile,
    }


def _load_teacher_offerings(conn, teacher_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT o.id, o.class_id, o.course_id, o.teacher_id, o.semester, o.schedule_info, o.created_at,
               c.name AS course_name, c.description AS course_description, c.credits AS course_credits,
               cl.name AS class_name, cl.description AS class_description,
               COUNT(s.id) AS student_count
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        LEFT JOIN students s ON s.class_id = o.class_id
        WHERE o.teacher_id = ?
        GROUP BY o.id, o.class_id, o.course_id, o.teacher_id, o.semester, o.schedule_info, o.created_at,
                 c.name, c.description, c.credits, cl.name, cl.description
        ORDER BY o.id DESC
        """,
        (teacher_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_student_offerings(conn, student_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT o.id, o.class_id, o.course_id, o.teacher_id, o.semester, o.schedule_info, o.created_at,
               c.name AS course_name, c.description AS course_description, c.credits AS course_credits,
               cl.name AS class_name, cl.description AS class_description,
               t.name AS teacher_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        JOIN teachers t ON t.id = o.teacher_id
        WHERE o.class_id = (
            SELECT class_id
            FROM students
            WHERE id = ?
        )
        ORDER BY o.id DESC
        """,
        (student_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_teacher_assignment_stats(conn, offering_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not offering_ids:
        return {}
    placeholders = ",".join("?" for _ in offering_ids)
    rows = conn.execute(
        f"""
        SELECT o.id AS offering_id,
               COUNT(DISTINCT a.id) AS assignment_count,
               COUNT(DISTINCT CASE WHEN a.status = 'new' THEN a.id END) AS draft_count,
               COUNT(DISTINCT CASE WHEN a.status = 'published' THEN a.id END) AS published_count,
               COUNT(DISTINCT CASE WHEN a.exam_paper_id IS NOT NULL THEN a.id END) AS exam_count,
               MAX(a.created_at) AS latest_assignment_at
        FROM class_offerings o
        LEFT JOIN assignments a
            ON a.course_id = o.course_id
           AND (a.class_offering_id = o.id OR a.class_offering_id IS NULL)
           AND NOT EXISTS (
               SELECT 1 FROM learning_stage_exam_attempts lsea
               WHERE lsea.assignment_id = a.id
           )
        WHERE o.id IN ({placeholders})
        GROUP BY o.id
        """,
        tuple(offering_ids),
    ).fetchall()
    return {int(row["offering_id"]): dict(row) for row in rows}


def _load_student_assignment_stats(conn, offering_ids: list[int], student_id: int) -> dict[int, dict[str, Any]]:
    if not offering_ids:
        return {}
    placeholders = ",".join("?" for _ in offering_ids)
    params = [student_id, student_id, *offering_ids]
    rows = conn.execute(
        f"""
        SELECT o.id AS offering_id,
               COUNT(DISTINCT CASE WHEN a.status != 'new' THEN a.id END) AS assignment_count,
               COUNT(DISTINCT CASE WHEN a.status != 'new' AND a.exam_paper_id IS NOT NULL THEN a.id END) AS exam_count,
               COUNT(DISTINCT CASE WHEN a.status = 'published' AND s.id IS NULL THEN a.id END) AS pending_count,
               COUNT(DISTINCT CASE WHEN s.id IS NOT NULL THEN a.id END) AS submitted_count,
               COUNT(DISTINCT CASE WHEN s.status = 'graded' THEN a.id END) AS graded_count,
               COUNT(DISTINCT CASE WHEN s.status = 'grading' THEN a.id END) AS grading_count,
               MAX(COALESCE(s.submitted_at, a.created_at)) AS last_activity_at
        FROM class_offerings o
        LEFT JOIN assignments a
            ON a.course_id = o.course_id
           AND (a.class_offering_id = o.id OR a.class_offering_id IS NULL)
           AND NOT EXISTS (
               SELECT 1 FROM learning_stage_exam_attempts lsea
               WHERE lsea.assignment_id = a.id
                 AND lsea.student_id != ?
           )
        LEFT JOIN submissions s
            ON s.assignment_id = a.id
           AND s.student_pk_id = ?
        WHERE o.id IN ({placeholders})
        GROUP BY o.id
        """,
        tuple(params),
    ).fetchall()
    return {int(row["offering_id"]): dict(row) for row in rows}


def _load_teacher_pending_submission_stats(conn, offering_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not offering_ids:
        return {}
    placeholders = ",".join("?" for _ in offering_ids)
    rows = conn.execute(
        f"""
        SELECT a.class_offering_id AS offering_id,
               COUNT(*) AS pending_review_count,
               MAX(s.submitted_at) AS latest_submission_at
        FROM assignments a
        JOIN submissions s ON s.assignment_id = a.id
        WHERE a.class_offering_id IN ({placeholders})
          AND s.status IN ('submitted', 'grading')
          AND NOT EXISTS (
              SELECT 1 FROM learning_stage_exam_attempts lsea
              WHERE lsea.assignment_id = a.id
          )
        GROUP BY a.class_offering_id
        """,
        tuple(offering_ids),
    ).fetchall()
    return {int(row["offering_id"]): dict(row) for row in rows}


def _load_course_resource_stats(
    conn,
    course_ids: list[int],
    *,
    include_teacher_resources: bool,
) -> dict[int, dict[str, Any]]:
    if not course_ids:
        return {}
    placeholders = ",".join("?" for _ in course_ids)
    conditions = [f"course_id IN ({placeholders})"]
    if not include_teacher_resources:
        conditions.append("is_public = 1")
        conditions.append("is_teacher_resource = 0")
    rows = conn.execute(
        f"""
        SELECT course_id,
               COUNT(*) AS resource_count,
               MAX(uploaded_at) AS latest_resource_at
        FROM course_files
        WHERE {' AND '.join(conditions)}
        GROUP BY course_id
        """,
        tuple(course_ids),
    ).fetchall()
    return {int(row["course_id"]): dict(row) for row in rows}


def _load_offering_material_stats(conn, offering_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not offering_ids:
        return {}
    placeholders = ",".join("?" for _ in offering_ids)
    rows = conn.execute(
        f"""
        SELECT class_offering_id AS offering_id,
               COUNT(*) AS material_count,
               MAX(created_at) AS latest_material_at
        FROM course_material_assignments
        WHERE class_offering_id IN ({placeholders})
        GROUP BY class_offering_id
        """,
        tuple(offering_ids),
    ).fetchall()
    return {int(row["offering_id"]): dict(row) for row in rows}


def _load_recent_activity(conn, user: dict, limit: int = 6) -> list[dict[str, Any]]:
    role = str(user.get("role") or "").strip().lower()
    user_pk = int(user["id"])
    primary_sql = """
        SELECT id, category, title, body_preview, link_url, read_at, created_at
        FROM message_center_notifications
        WHERE recipient_role = ? AND recipient_user_pk = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """
    fallback_sql = """
        SELECT id, category, title, body_preview, link_url, read_at, created_at
        FROM message_center_notifications NOT INDEXED
        WHERE recipient_role = ? AND recipient_user_pk = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """
    try:
        rows = conn.execute(
            primary_sql,
            (role, user_pk, limit),
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        print(f"[DB WARN] Failed to load recent activity with index: {exc}")
        try:
            rows = conn.execute(
                fallback_sql,
                (role, user_pk, limit),
            ).fetchall()
        except sqlite3.DatabaseError as fallback_exc:
            print(f"[DB WARN] Failed to load recent activity without index: {fallback_exc}")
            return []
    items = []
    for row in rows:
        category = str(row["category"] or "")
        items.append({
            "title": str(row["title"] or "新提醒"),
            "description": str(row["body_preview"] or "点击查看详情"),
            "href": str(row["link_url"] or "/message-center"),
            "label": CATEGORY_LABELS.get(category, category or "提醒"),
            "tone": ACTIVITY_TONE_BY_CATEGORY.get(category, "neutral"),
            "is_unread": not row["read_at"],
            "created_at": str(row["created_at"] or ""),
        })
    return items


def _load_student_priority_items(conn, student_id: int, limit: int = 4) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT a.id AS assignment_id,
               a.title,
               a.exam_paper_id,
               a.created_at,
               o.id AS offering_id,
               c.name AS course_name,
               cl.name AS class_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        JOIN assignments a
            ON a.course_id = o.course_id
           AND (a.class_offering_id = o.id OR a.class_offering_id IS NULL)
           AND NOT EXISTS (
               SELECT 1 FROM learning_stage_exam_attempts lsea
               WHERE lsea.assignment_id = a.id
                 AND lsea.student_id != ?
           )
        LEFT JOIN submissions s
            ON s.assignment_id = a.id
           AND s.student_pk_id = ?
        WHERE o.class_id = (
            SELECT class_id
            FROM students
            WHERE id = ?
        )
          AND a.status = 'published'
          AND s.id IS NULL
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT ?
        """,
        (student_id, student_id, student_id, limit),
    ).fetchall()

    items = []
    for row in rows:
        items.append({
            "title": str(row["title"] or "待完成任务"),
            "description": f"{row['course_name']} · {row['class_name']}"
            + (" · 考试" if row["exam_paper_id"] else " · 作业"),
            "href": f"/assignment/{row['assignment_id']}",
            "tone": "danger" if not row["exam_paper_id"] else "warning",
        })
    return items


def _query_scalar(conn, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    if not row:
        return 0
    return int(row[0] or 0)


def _normalize_dashboard_filter(role: str, value: Any) -> str:
    allowed_values = DASHBOARD_FILTER_VALUES.get(role, ("all",))
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed_values else "all"


def _normalize_dashboard_search(value: Any, *, max_length: int = 80) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:max_length]


def _normalize_dashboard_search_token(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _build_dashboard_search_text(*parts: Any) -> str:
    tokens: list[str] = []
    seen: set[str] = set()
    for part in parts:
        token = _normalize_dashboard_search_token(part)
        if not token:
            continue
        for candidate in (token, token.replace(" ", "")):
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            tokens.append(candidate)
    return " ".join(tokens)


def _matches_dashboard_filter(offering: dict[str, Any], filter_value: str) -> bool:
    if filter_value == "attention":
        return bool(offering.get("needs_attention"))
    if filter_value == "recent":
        return bool(offering.get("has_recent_activity"))
    if filter_value == "progress":
        return bool(offering.get("has_progress"))
    return True


def _matches_dashboard_search(offering: dict[str, Any], search_query: str) -> bool:
    if not search_query:
        return True
    normalized_query = _normalize_dashboard_search_token(search_query)
    if not normalized_query:
        return True
    haystack = str(offering.get("search_text") or "")
    if normalized_query in haystack:
        return True
    compact_query = normalized_query.replace(" ", "")
    return bool(compact_query) and compact_query in haystack.replace(" ", "")


def _apply_dashboard_view_state(
    offerings: list[dict[str, Any]],
    *,
    filter_value: str,
    search_query: str,
) -> int:
    visible_count = 0
    for offering in offerings:
        is_visible = _matches_dashboard_filter(offering, filter_value) and _matches_dashboard_search(offering, search_query)
        offering["initially_visible"] = is_visible
        if is_visible:
            visible_count += 1
    return visible_count


def _build_dashboard_results_summary(
    filters: list[dict[str, Any]],
    *,
    filter_value: str,
    search_query: str,
) -> str:
    filter_labels = {str(item.get("value") or ""): str(item.get("label") or "") for item in filters}
    fragments: list[str] = []
    if filter_value != "all":
        fragments.append(f"筛选：{filter_labels.get(filter_value, filter_value)}")
    if search_query:
        fragments.append(f"关键词：{search_query}")
    return " · ".join(fragments) if fragments else "显示全部课堂"


def _pick_latest_datetime(*values: Any) -> str:
    latest_value = ""
    latest_dt: datetime | None = None
    for value in values:
        parsed = _parse_datetime(value)
        if parsed is None:
            continue
        if latest_dt is None or parsed > latest_dt:
            latest_dt = parsed
            latest_value = str(value or "")
    return latest_value


def _parse_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidates = [raw]
    if raw.endswith("Z"):
        candidates.append(raw[:-1] + "+00:00")
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, pattern)
        except ValueError:
            continue
    return None


def _is_recent(value: Any, days: int = RECENT_ACTIVITY_DAYS) -> bool:
    parsed = _parse_datetime(value)
    if parsed is None:
        return False
    return parsed >= datetime.now() - timedelta(days=days)
