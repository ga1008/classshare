from __future__ import annotations

from typing import Any


def build_classroom_page_context(
    *,
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

    stats = _build_stat_cards(
        role=role,
        assignment_stats=assignment_stats,
        resource_count=resource_count,
    )
    spotlight = _build_spotlight(
        role=role,
        assignment_stats=assignment_stats,
        resource_count=resource_count,
        class_size_text=class_size_text,
    )

    hero = {
        "eyebrow": "Teaching Studio" if role == "teacher" else "Learning Space",
        "role_label": "教师视角" if role == "teacher" else "学生视角",
        "lead": (
            "把任务发布、资源分发和课堂讨论放进同一个工作台里，便于持续推进课堂节奏。"
            if role == "teacher"
            else "在一个更清晰的课堂工作台中完成任务查看、材料浏览、资源下载和实时讨论。"
        ),
        "feature_chips": (
            ["任务发布", "材料库同步", "课堂共享", "AI 助手"]
            if role == "teacher"
            else ["任务追踪", "材料浏览", "资源下载", "课堂讨论"]
        ),
        "nav": [
            {"target": "assignment-panel", "label": "任务区", "note": "作业与考试"},
            {"target": "materials-panel", "label": "材料区", "note": "课程文档"},
            {"target": "resources-panel", "label": "资源区", "note": "共享文件"},
            {"target": "discussion-room", "label": "讨论区", "note": "实时互动"},
        ],
    }

    discussion = {
        "subtitle": (
            "围绕作业安排、资料提醒和课堂反馈保持同步。"
            if role == "teacher"
            else "围绕任务难点、实验进展和资料阅读随时交流。"
        ),
        "detail": (
            "教师实名发言，学生以课堂代号参与，互动记录会实时同步。"
            if role == "teacher"
            else "你的公开身份会以课堂代号展示，更方便聚焦内容本身。"
        ),
    }

    return {
        "theme": role,
        "hero": hero,
        "spotlight": spotlight,
        "stats": stats,
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
    active_count = assignment_count - draft_count if role == "teacher" else 0
    submitted_count = (
        sum(1 for item in assignments if item.get("submission_status") and item.get("submission_status") != "unsubmitted")
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


def _build_stat_cards(*, role: str, assignment_stats: dict[str, int], resource_count: int) -> list[dict[str, Any]]:
    if role == "teacher":
        return [
            {"label": "课堂资源", "value": resource_count, "note": "共享文件总数", "tone": "neutral"},
            {
                "label": "作业总数",
                "value": assignment_stats["assignment_count"],
                "note": f"考试 {assignment_stats['exam_count']} 项",
                "tone": "primary",
            },
            {"label": "考试试卷", "value": assignment_stats["exam_count"], "note": "已加入课堂", "tone": "warning"},
            {
                "label": "已发布",
                "value": assignment_stats["active_count"],
                "note": f"草稿 {assignment_stats['draft_count']} 项",
                "tone": "success",
            },
        ]

    return [
        {"label": "课堂资源", "value": resource_count, "note": "可下载共享文件", "tone": "neutral"},
        {
            "label": "当前任务",
            "value": assignment_stats["assignment_count"],
            "note": f"考试 {assignment_stats['exam_count']} 项",
            "tone": "primary",
        },
        {"label": "考试试卷", "value": assignment_stats["exam_count"], "note": "进入作答或提交", "tone": "warning"},
        {
            "label": "已提交",
            "value": assignment_stats["submitted_count"],
            "note": f"待提交 {assignment_stats['pending_count']} 项",
            "tone": "success",
        },
    ]


def _build_spotlight(
    *,
    role: str,
    assignment_stats: dict[str, int],
    resource_count: int,
    class_size_text: str,
) -> dict[str, Any]:
    if role == "teacher":
        if assignment_stats["draft_count"] > 0:
            label = "待发布任务"
            value = assignment_stats["draft_count"]
            note = "还有课堂任务停留在草稿状态，整理后即可直接发布给学生。"
        else:
            label = "已运行任务"
            value = assignment_stats["active_count"]
            note = "当前课堂任务已经进入稳定运行，可以继续补充材料与共享资源。"
        return {
            "label": label,
            "value": value,
            "suffix": "项",
            "note": note,
            "highlights": [
                {"label": "班级人数", "value": class_size_text},
                {"label": "共享资源", "value": resource_count},
                {"label": "考试数量", "value": assignment_stats["exam_count"]},
            ],
        }

    if assignment_stats["pending_count"] > 0:
        label = "待完成任务"
        value = assignment_stats["pending_count"]
        note = "先处理仍未提交的任务，课堂进度会更清晰。"
    elif assignment_stats["assignment_count"] > 0:
        label = "已提交任务"
        value = assignment_stats["submitted_count"]
        note = "你已经有任务进入提交流程，可以继续跟进批改结果。"
    else:
        label = "当前任务"
        value = 0
        note = "教师发布新任务后，这里会第一时间更新。"

    return {
        "label": label,
        "value": value,
        "suffix": "项",
        "note": note,
        "highlights": [
            {"label": "班级人数", "value": class_size_text},
            {"label": "已评分", "value": assignment_stats["graded_count"]},
            {"label": "共享资源", "value": resource_count},
        ],
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
