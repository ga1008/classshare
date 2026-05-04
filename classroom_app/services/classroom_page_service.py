from __future__ import annotations

from typing import Any

from .discussion_mood_service import get_discussion_mood_payload
from .ui_copy_service import get_ui_copy_block, render_ui_copy_block
from .prompt_utils import polite_address


def build_classroom_page_context(
    *,
    conn,
    user: dict[str, Any],
    classroom: dict[str, Any],
    assignments: list[dict[str, Any]],
    shared_files: list[dict[str, Any]],
) -> dict[str, Any]:
    role = str(user.get("role") or "student").strip().lower()
    assignment_stats = _build_assignment_stats(role=role, assignments=assignments)
    resource_count = len(shared_files)
    class_size = classroom.get("class_student_count")
    class_size_text = str(class_size) if class_size not in (None, "") else "--"
    raw_ui_copy = get_ui_copy_block(conn, scene="classroom", role=role)
    ui_copy = render_ui_copy_block(
        raw_ui_copy,
        {
            "name": polite_address(user.get("name") or "", role),
            "class_name": classroom.get("class_name") or "",
            "course_name": classroom.get("course_name") or "",
            "alias_or_name": polite_address(user.get("name") or "", role),
        },
    )
    discussion_mood = get_discussion_mood_payload(
        conn,
        int(classroom.get("id") or 0),
    )

    hero = {
        "lead": ui_copy["hero_lead"],
        "primary_meta": _build_hero_primary_meta(classroom=classroom),
        "secondary_meta": _build_hero_secondary_meta(
            classroom=classroom,
        ),
        "detail_stats": _build_hero_detail_stats(
            role=role,
            assignment_stats=assignment_stats,
            resource_count=resource_count,
            class_size_text=class_size_text,
        ),
        "nav": [
            {"target": "learning-progress-panel", "label": "境界区", "note": "进度及等级"},
            {"target": "assignment-panel", "label": "任务区", "note": "作业与考试"},
            {"target": "materials-panel", "label": "材料区", "note": "课程文档"},
            {"target": "resources-panel", "label": "资源区", "note": "共享文件"},
            {"target": "discussion-room", "label": "讨论区", "note": "实时互动"},
        ],
    }

    sections = {
        "assignment": {
            "eyebrow": "Learning Flow",
            "title": ui_copy["assignment_title"],
            "subtitle": ui_copy["assignment_subtitle"],
            "empty_title": ui_copy["assignment_empty_title"],
            "empty_description": ui_copy["assignment_empty_description"],
        },
        "materials": {
            "eyebrow": "Course Library",
            "title": ui_copy["materials_title"],
            "subtitle": ui_copy["materials_subtitle"],
        },
        "resources": {
            "eyebrow": "Shared Resources",
            "title": ui_copy["resources_title"],
            "subtitle": ui_copy["resources_subtitle"],
        },
        "discussion": {
            "eyebrow": "课堂研讨室",
            "title": ui_copy["discussion_title"],
            "subtitle": discussion_mood["headline"],
            "detail": discussion_mood["detail"],
        },
    }

    discussion = {
        "subtitle": sections["discussion"]["subtitle"],
        "detail": sections["discussion"]["detail"],
        "mood": discussion_mood,
    }

    return {
        "theme": role,
        "hero": hero,
        "sections": sections,
        "assignment_stats": assignment_stats,
        "assignment_metrics": _build_assignment_metrics(role=role, assignment_stats=assignment_stats),
        "materials_tags": ["目录浏览", "README 预览", "批量下载"],
        "resource_tags": (
            ["课堂共享", "拖拽上传", f"共 {resource_count} 项资源"]
            if role == "teacher"
            else ["课堂共享", "即下即用", f"共 {resource_count} 项资源"]
        ),
        "discussion": discussion,
    }


def _build_assignment_stats(*, role: str, assignments: list[dict[str, Any]]) -> dict[str, int]:
    assignment_count = len(assignments)
    exam_count = sum(1 for item in assignments if item.get("exam_paper_id"))
    draft_count = sum(1 for item in assignments if item.get("status") == "new") if role == "teacher" else 0
    active_count = sum(1 for item in assignments if item.get("status") == "published") if role == "teacher" else 0
    submitted_count = (
        sum(
            1
            for item in assignments
            if item.get("submission_status")
            and item.get("submission_status") not in {"unsubmitted", "returned"}
        )
        if role != "teacher"
        else 0
    )
    pending_count = assignment_count - submitted_count if role != "teacher" else 0
    grading_count = sum(1 for item in assignments if item.get("submission_status") == "grading") if role != "teacher" else 0
    graded_count = sum(1 for item in assignments if item.get("submission_status") == "graded") if role != "teacher" else 0

    return {
        "assignment_count": assignment_count,
        "exam_count": exam_count,
        "draft_count": draft_count,
        "active_count": active_count,
        "submitted_count": submitted_count,
        "pending_count": pending_count,
        "grading_count": grading_count,
        "graded_count": graded_count,
    }


def _build_assignment_metrics(*, role: str, assignment_stats: dict[str, int]) -> list[dict[str, Any]]:
    if role == "teacher":
        return [
            {"label": "全部任务", "value": assignment_stats["assignment_count"], "note": "课堂总览", "tone": "primary"},
            {"label": "考试", "value": assignment_stats["exam_count"], "note": "试卷任务", "tone": "warning"},
            {"label": "草稿", "value": assignment_stats["draft_count"], "note": "尚未发布", "tone": "danger"},
            {"label": "已发布", "value": assignment_stats["active_count"], "note": "学生可见", "tone": "success"},
        ]

    return [
        {"label": "当前任务", "value": assignment_stats["assignment_count"], "note": "已开放内容", "tone": "primary"},
        {"label": "待提交", "value": assignment_stats["pending_count"], "note": "优先处理", "tone": "danger"},
        {"label": "已提交", "value": assignment_stats["submitted_count"], "note": f"批改中 {assignment_stats['grading_count']} 项", "tone": "warning"},
        {"label": "已评分", "value": assignment_stats["graded_count"], "note": "可回看结果", "tone": "success"},
    ]


def _build_hero_primary_meta(*, classroom: dict[str, Any]) -> list[dict[str, str]]:
    return [{"label": "班级", "value": classroom.get("class_name") or "--"}]


def _build_hero_secondary_meta(*, classroom: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if classroom.get("teacher_name"):
        items.append({"label": "授课教师", "value": str(classroom.get("teacher_name"))})
    if classroom.get("semester"):
        items.append({"label": "学期", "value": str(classroom.get("semester"))})
    if classroom.get("course_credits") is not None:
        items.append({"label": "学分", "value": str(classroom.get("course_credits"))})
    if classroom.get("schedule_info"):
        items.append({"label": "上课安排", "value": str(classroom.get("schedule_info"))})
    return items


def _build_hero_detail_stats(
    *,
    role: str,
    assignment_stats: dict[str, int],
    resource_count: int,
    class_size_text: str,
) -> list[dict[str, Any]]:
    if role == "teacher":
        return [
            {
                "label": "全部任务",
                "value": assignment_stats["assignment_count"],
                "note": f"已发布 {assignment_stats['active_count']} 项 / 草稿 {assignment_stats['draft_count']} 项",
                "tone": "primary",
            },
            {"label": "考试", "value": assignment_stats["exam_count"], "note": "已加入课堂", "tone": "warning"},
            {"label": "共享资源", "value": resource_count, "note": "课堂文件总数", "tone": "success"},
            {"label": "班级人数", "value": class_size_text, "note": "当前课堂规模", "tone": "neutral"},
        ]

    return [
        {
            "label": "当前任务",
            "value": assignment_stats["assignment_count"],
            "note": f"待提交 {assignment_stats['pending_count']} 项",
            "tone": "primary",
        },
        {
            "label": "已提交",
            "value": assignment_stats["submitted_count"],
            "note": f"已评分 {assignment_stats['graded_count']} 项",
            "tone": "success",
        },
        {"label": "考试", "value": assignment_stats["exam_count"], "note": "可作答或查看", "tone": "warning"},
        {"label": "共享资源", "value": resource_count, "note": "可下载文件", "tone": "neutral"},
    ]
