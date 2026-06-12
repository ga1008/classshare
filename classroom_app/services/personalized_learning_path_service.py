from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import sqlite3

from .feedback_review_service import build_feedback_review_context
from .learning_progress_service import (
    LEARNING_LEVELS,
    PASSING_STAGE_SCORE,
    STAGE_EXAM_RETREAT_PLAN_KEY,
    public_level_payload,
    safe_float,
    safe_int,
    serialize_student_learning_progress,
    truncate_text,
)


PATH_STATUS_ACTIVE = "active"
PATH_STATUS_DONE = "done"
PATH_STATUS_SNOOZED = "snoozed"
PATH_STATUSES = {PATH_STATUS_ACTIVE, PATH_STATUS_DONE, PATH_STATUS_SNOOZED}
PATH_STATUS_LABELS = {
    PATH_STATUS_ACTIVE: "进行中",
    PATH_STATUS_DONE: "已完成",
    PATH_STATUS_SNOOZED: "稍后处理",
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _compact_text(value: Any, limit: int = 800) -> str:
    return truncate_text(value, limit=limit)


def _student_id(user: dict[str, Any]) -> int:
    return int(user["id"])


def _load_student_offerings(conn: sqlite3.Connection, student_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT o.id,
               o.class_id,
               o.course_id,
               o.semester,
               o.schedule_info,
               c.name AS course_name,
               c.description AS course_description,
               c.sect_name AS course_sect_name,
               cl.name AS class_name,
               t.name AS teacher_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        JOIN teachers t ON t.id = o.teacher_id
        JOIN students s ON s.class_id = o.class_id
        WHERE s.id = ?
          AND COALESCE(s.enrollment_status, 'active') = 'active'
        ORDER BY o.created_at DESC, o.id DESC
        """,
        (int(student_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_path_states(conn: sqlite3.Connection, student_id: int) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM student_learning_path_item_states
        WHERE student_id = ?
        """,
        (int(student_id),),
    ).fetchall()
    return {str(row["item_key"]): dict(row) for row in rows}


def _load_pending_assignments(
    conn: sqlite3.Connection,
    *,
    class_offering_id: int,
    student_id: int,
    limit: int = 3,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT a.id,
               a.title,
               a.exam_paper_id,
               a.due_at,
               a.starts_at,
               a.learning_stage_key,
               s.id AS submission_id,
               s.status AS submission_status
        FROM assignments a
        LEFT JOIN submissions s
               ON s.assignment_id = a.id
              AND s.student_pk_id = ?
              AND COALESCE(s.is_absence_score, 0) = 0
        LEFT JOIN learning_stage_exam_attempts lsea
               ON lsea.assignment_id = a.id
        WHERE a.class_offering_id = ?
          AND a.status != 'new'
          AND lsea.id IS NULL
          AND s.id IS NULL
        ORDER BY
          CASE WHEN a.due_at IS NULL OR TRIM(a.due_at) = '' THEN 1 ELSE 0 END,
          a.due_at ASC,
          a.created_at DESC,
          a.id DESC
        LIMIT ?
        """,
        (int(student_id), int(class_offering_id), max(1, min(int(limit), 8))),
    ).fetchall()
    return [dict(row) for row in rows]


def _format_due_label(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        due = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if due.tzinfo is not None:
            due = due.replace(tzinfo=None)
        today = datetime.now().date()
        delta = (due.date() - today).days
        if delta < 0:
            return f"已逾期 {abs(delta)} 天"
        if delta == 0:
            return "今天截止"
        if delta == 1:
            return "明天截止"
        if delta <= 7:
            return f"{delta} 天后截止"
        return due.strftime("%m-%d 截止")
    except ValueError:
        return raw[:16]


def _material_href(class_offering_id: int, item: dict[str, Any]) -> str:
    href = f"/materials/view/{int(item['id'])}?class_offering_id={int(class_offering_id)}"
    if item.get("session_id"):
        href += f"&session_id={int(item['session_id'])}"
    return href


def _component_ratios(metrics: dict[str, Any]) -> dict[str, float]:
    material = metrics.get("material") or {}
    assignments = metrics.get("assignments") or {}
    interactions = metrics.get("interactions") or {}
    return {
        "material": safe_float(material.get("ratio")),
        "task": safe_float(assignments.get("task_ratio")),
        "interaction": safe_float(interactions.get("interaction_ratio")),
        "consistency": safe_float(interactions.get("consistency_ratio")),
    }


def _bottleneck_from_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    ratios = _component_ratios(metrics)
    labels = {
        "material": ("材料研读", "先补齐必读材料，修为会更稳。"),
        "task": ("作业考试", "优先完成任务并回看批改反馈。"),
        "interaction": ("课堂互动", "多提问、讨论或向 AI 助教求证。"),
        "consistency": ("稳定投入", "用短时间高频次保持节奏。"),
    }
    key = min(ratios, key=lambda name: ratios[name])
    return {
        "key": key,
        "label": labels[key][0],
        "hint": labels[key][1],
        "ratio": round(ratios[key], 4),
    }


def _base_step(
    *,
    key: str,
    kind: str,
    title: str,
    description: str,
    href: str,
    class_offering_id: int | None,
    course_id: int | None,
    course_name: str,
    class_name: str,
    priority: int,
    tone: str = "primary",
    tag: str = "路径",
    action_label: str = "去完成",
    target_type: str = "",
    target_id: str = "",
    due_label: str = "",
    progress_percent: int | None = None,
    estimated_minutes: int = 15,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "kind": kind,
        "title": title,
        "description": description,
        "href": href,
        "class_offering_id": class_offering_id or 0,
        "course_id": course_id or 0,
        "course_name": course_name,
        "class_name": class_name,
        "priority": priority,
        "tone": tone,
        "tag": tag,
        "action_label": action_label,
        "target_type": target_type,
        "target_id": target_id,
        "due_label": due_label,
        "progress_percent": progress_percent,
        "estimated_minutes": estimated_minutes,
        "metadata": metadata or {},
    }


def _apply_step_state(step: dict[str, Any], state: dict[str, Any] | None) -> dict[str, Any]:
    status = str((state or {}).get("status") or PATH_STATUS_ACTIVE)
    if status not in PATH_STATUSES:
        status = PATH_STATUS_ACTIVE
    item = {
        **step,
        "status": status,
        "status_label": PATH_STATUS_LABELS[status],
        "pinned": bool((state or {}).get("pinned")),
        "reflection": str((state or {}).get("reflection") or ""),
        "next_action": str((state or {}).get("next_action") or ""),
        "completed_at": str((state or {}).get("completed_at") or ""),
        "snoozed_until": str((state or {}).get("snoozed_until") or ""),
        "updated_at": str((state or {}).get("updated_at") or ""),
    }
    return item


def _sort_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    status_order = {PATH_STATUS_ACTIVE: 0, PATH_STATUS_SNOOZED: 1, PATH_STATUS_DONE: 2}
    return sorted(
        steps,
        key=lambda item: (
            0 if item.get("pinned") else 1,
            status_order.get(str(item.get("status")), 9),
            int(item.get("priority") or 99),
            str(item.get("course_name") or ""),
            str(item.get("title") or ""),
        ),
    )


def _stage_summary() -> list[dict[str, Any]]:
    summary = []
    for level in LEARNING_LEVELS:
        payload = public_level_payload(level)
        payload["mastery_note"] = (
            "基本掌握线" if int(payload["tier"]) == 6 else
            "完全掌握线" if int(payload["tier"]) == 8 else
            "可为人师" if int(payload["tier"]) == 10 else
            "持续精进"
        )
        summary.append(payload)
    return summary


def _latest_stage_retreat_plan(
    conn: sqlite3.Connection,
    *,
    class_offering_id: int,
    student_id: int,
) -> dict[str, Any] | None:
    rows = conn.execute(
        """
        SELECT lsea.id, lsea.stage_key, lsea.score, lsea.graded_at, lsea.metadata_json
        FROM learning_stage_exam_attempts lsea
        WHERE lsea.class_offering_id = ?
          AND lsea.student_id = ?
          AND lsea.status = 'failed'
          AND NOT EXISTS (
              SELECT 1
              FROM learning_stage_exam_attempts passed
              WHERE passed.class_offering_id = lsea.class_offering_id
                AND passed.student_id = lsea.student_id
                AND passed.stage_key = lsea.stage_key
                AND passed.status = 'passed'
          )
        ORDER BY COALESCE(lsea.graded_at, lsea.generated_at) DESC, lsea.id DESC
        LIMIT 4
        """,
        (int(class_offering_id), int(student_id)),
    ).fetchall()
    for row in rows:
        attempt = dict(row)
        try:
            metadata = json.loads(attempt.get("metadata_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        plan = metadata.get(STAGE_EXAM_RETREAT_PLAN_KEY) if isinstance(metadata, dict) else None
        if not isinstance(plan, dict):
            continue
        items = [item for item in (plan.get("items") or []) if isinstance(item, dict)]
        if not items:
            continue
        return {
            "attempt": attempt,
            "plan": {**plan, "items": items},
        }
    return None


def _load_stage_retreat_path_steps(
    conn: sqlite3.Connection,
    *,
    offering: dict[str, Any],
    states: dict[str, dict[str, Any]],
    student_id: int,
) -> list[dict[str, Any]]:
    offering_id = int(offering["id"])
    course_id = int(offering["course_id"])
    course_name = str(offering.get("course_name") or "未命名课程")
    class_name = str(offering.get("class_name") or "")
    payload = _latest_stage_retreat_plan(conn, class_offering_id=offering_id, student_id=student_id)
    if not payload:
        return []
    attempt = payload["attempt"]
    plan = payload["plan"]
    steps: list[dict[str, Any]] = []
    for index, item in enumerate(plan.get("items") or [], start=1):
        key = str(item.get("key") or f"stage-retreat:{attempt.get('id')}:{index}").strip()
        if not key:
            continue
        weak_point = str(item.get("weak_point") or item.get("description") or "").strip()
        reflection_prompt = str(item.get("reflection_prompt") or "").strip()
        description_parts = [
            str(item.get("description") or weak_point or "复盘本次破境试炼中的薄弱点。").strip(),
            reflection_prompt,
        ]
        description = " ".join(part for part in description_parts if part)
        href = str(item.get("href") or f"/classroom/{offering_id}").strip()
        base = _base_step(
            key=key,
            kind="stage_retreat",
            title=str(item.get("title") or f"闭关复盘 {index}"),
            description=truncate_text(description, 220),
            href=href,
            class_offering_id=offering_id,
            course_id=course_id,
            course_name=course_name,
            class_name=class_name,
            priority=1,
            tone="danger",
            tag="闭关",
            action_label=str(item.get("action_label") or "去复盘"),
            target_type=str(item.get("target_type") or "stage_retreat"),
            target_id=str(item.get("target_id") or attempt.get("id") or ""),
            estimated_minutes=max(5, safe_int(item.get("estimated_minutes"), 12)),
            metadata={
                "attempt_id": safe_int(attempt.get("id")),
                "stage_key": str(attempt.get("stage_key") or plan.get("stage_key") or ""),
                "score": safe_float(plan.get("score", attempt.get("score"))),
                "weak_point": weak_point,
                "reflection_prompt": reflection_prompt,
                "material_id": safe_int(item.get("material_id")),
                "session_id": safe_int(item.get("session_id")),
            },
        )
        steps.append(_apply_step_state(base, states.get(base["key"])))
    return steps


def _build_course_steps(
    conn: sqlite3.Connection,
    *,
    offering: dict[str, Any],
    progress: dict[str, Any],
    states: dict[str, dict[str, Any]],
    review_items: list[dict[str, Any]],
    student_id: int,
) -> list[dict[str, Any]]:
    offering_id = int(offering["id"])
    course_id = int(offering["course_id"])
    course_name = str(offering.get("course_name") or "未命名课程")
    class_name = str(offering.get("class_name") or "")
    steps: list[dict[str, Any]] = []
    next_stage = progress.get("next_stage") or {}
    eligible_stage = progress.get("eligible_stage") or {}

    if next_stage and str(next_stage.get("status")) == "in_exam" and next_stage.get("last_exam_assignment_id"):
        stage_key = str(next_stage.get("key") or "")
        base = _base_step(
            key=f"stage-exam:{offering_id}:{stage_key}:continue",
            kind="stage",
            title=f"继续完成 {next_stage.get('short_name') or next_stage.get('name')} 破境试炼",
            description=f"这一步直接关系到当前境界证书，达到 {PASSING_STAGE_SCORE} 分即可点亮。",
            href=f"/exam/take/{int(next_stage['last_exam_assignment_id'])}",
            class_offering_id=offering_id,
            course_id=course_id,
            course_name=course_name,
            class_name=class_name,
            priority=1,
            tone="danger",
            tag="破境",
            action_label="继续试炼",
            target_type="stage_exam",
            target_id=stage_key,
            estimated_minutes=45,
        )
        steps.append(_apply_step_state(base, states.get(base["key"])))
    elif eligible_stage:
        stage_key = str(eligible_stage.get("key") or "")
        base = _base_step(
            key=f"stage-exam:{offering_id}:{stage_key}:unlock",
            kind="stage",
            title=f"挑战 {eligible_stage.get('short_name') or eligible_stage.get('name')} 破境试炼",
            description=f"当前修为已达到生成试炼条件，试炼通过线为 {PASSING_STAGE_SCORE} 分。",
            href=f"/classroom/{offering_id}",
            class_offering_id=offering_id,
            course_id=course_id,
            course_name=course_name,
            class_name=class_name,
            priority=2,
            tone="success",
            tag="可破境",
            action_label="生成试炼",
            target_type="stage",
            target_id=stage_key,
            progress_percent=100,
            estimated_minutes=40,
        )
        steps.append(_apply_step_state(base, states.get(base["key"])))
    elif next_stage and str(next_stage.get("status")) == "generating":
        stage_key = str(next_stage.get("key") or "")
        base = _base_step(
            key=f"stage-exam:{offering_id}:{stage_key}:generating",
            kind="stage",
            title=f"{next_stage.get('short_name') or next_stage.get('name')} 试炼正在准备",
            description="稍后回到课堂页刷新即可继续，当前可以先处理材料或错题。",
            href=f"/classroom/{offering_id}",
            class_offering_id=offering_id,
            course_id=course_id,
            course_name=course_name,
            class_name=class_name,
            priority=7,
            tone="warning",
            tag="生成中",
            action_label="回到课堂",
            target_type="stage",
            target_id=stage_key,
            estimated_minutes=5,
        )
        steps.append(_apply_step_state(base, states.get(base["key"])))

    steps.extend(
        _load_stage_retreat_path_steps(
            conn,
            offering=offering,
            states=states,
            student_id=student_id,
        )
    )

    for assignment in _load_pending_assignments(conn, class_offering_id=offering_id, student_id=student_id, limit=3):
        assignment_id = int(assignment["id"])
        is_exam = bool(assignment.get("exam_paper_id"))
        due_label = _format_due_label(assignment.get("due_at"))
        base = _base_step(
            key=f"assignment:{offering_id}:{assignment_id}",
            kind="assignment",
            title=str(assignment.get("title") or "待完成任务"),
            description="先把已发布任务纳入路径，避免修为只涨在材料阅读上。",
            href=f"/exam/take/{assignment_id}" if is_exam else f"/assignment/{assignment_id}",
            class_offering_id=offering_id,
            course_id=course_id,
            course_name=course_name,
            class_name=class_name,
            priority=3,
            tone="danger" if due_label.startswith("已逾期") else "warning",
            tag="考试" if is_exam else "任务",
            action_label="去作答" if is_exam else "去提交",
            target_type="assignment",
            target_id=str(assignment_id),
            due_label=due_label,
            estimated_minutes=30 if is_exam else 20,
        )
        steps.append(_apply_step_state(base, states.get(base["key"])))

    material_items = ((progress.get("metrics") or {}).get("material") or {}).get("items") or []
    for material in [item for item in material_items if safe_float(item.get("unit_ratio")) < 0.94][:2]:
        material_id = int(material["id"])
        percent = int(material.get("percent") or 0)
        base = _base_step(
            key=f"material:{offering_id}:{material_id}",
            kind="material",
            title=str(material.get("name") or "继续阅读材料"),
            description=f"这份材料当前完成度约 {percent}%，补齐后会推动课程修为的基础分。",
            href=_material_href(offering_id, material),
            class_offering_id=offering_id,
            course_id=course_id,
            course_name=course_name,
            class_name=class_name,
            priority=5 if percent else 4,
            tone="primary",
            tag="材料",
            action_label="继续阅读",
            target_type="material",
            target_id=str(material_id),
            progress_percent=percent,
            estimated_minutes=12,
        )
        steps.append(_apply_step_state(base, states.get(base["key"])))

    active_review_items = [
        item
        for item in review_items
        if safe_int(item.get("course_id")) == course_id and str(item.get("status")) != "mastered"
    ]
    if active_review_items:
        first = active_review_items[0]
        base = _base_step(
            key=f"review:{course_id}:active",
            kind="review",
            title=f"复盘 {len(active_review_items)} 个反馈点",
            description=f"先看：{truncate_text(first.get('title'), 72)}",
            href=f"/feedback-review?course_id={course_id}&status=active",
            class_offering_id=offering_id,
            course_id=course_id,
            course_name=course_name,
            class_name=class_name,
            priority=4,
            tone="warning",
            tag="错题复盘",
            action_label="去复盘",
            target_type="feedback_review",
            target_id=str(course_id),
            estimated_minutes=15,
            metadata={"review_count": len(active_review_items)},
        )
        steps.append(_apply_step_state(base, states.get(base["key"])))

    ratios = _component_ratios(progress.get("metrics") or {})
    if ratios["interaction"] < 0.28:
        base = _base_step(
            key=f"interaction:{offering_id}",
            kind="interaction",
            title="补一次主动提问或讨论",
            description="把最近卡住的概念写成一个问题，发到课堂讨论或向 AI 助教求证。",
            href=f"/classroom/{offering_id}#discussion-panel",
            class_offering_id=offering_id,
            course_id=course_id,
            course_name=course_name,
            class_name=class_name,
            priority=8,
            tone="neutral",
            tag="互动",
            action_label="去提问",
            target_type="interaction",
            target_id=str(offering_id),
            estimated_minutes=8,
        )
        steps.append(_apply_step_state(base, states.get(base["key"])))

    if ratios["consistency"] < 0.24:
        base = _base_step(
            key=f"consistency:{offering_id}",
            kind="habit",
            title="安排一次 15 分钟稳定学习",
            description="短时间高频投入会补上稳定性分，也能让后续材料和任务更顺。",
            href=f"/classroom/{offering_id}",
            class_offering_id=offering_id,
            course_id=course_id,
            course_name=course_name,
            class_name=class_name,
            priority=9,
            tone="neutral",
            tag="节奏",
            action_label="开始学习",
            target_type="habit",
            target_id=str(offering_id),
            estimated_minutes=15,
        )
        steps.append(_apply_step_state(base, states.get(base["key"])))

    return steps


def build_personalized_learning_path_context(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    *,
    status: str = PATH_STATUS_ACTIVE,
    course_id: int | None = None,
    keyword: str = "",
) -> dict[str, Any]:
    student_id = _student_id(user)
    offerings = _load_student_offerings(conn, student_id)
    states = _load_path_states(conn, student_id)
    review_context = build_feedback_review_context(conn, user, status="active", limit=180)
    review_items = list(review_context.get("items") or [])

    courses: list[dict[str, Any]] = []
    all_steps: list[dict[str, Any]] = []
    for offering in offerings:
        progress = serialize_student_learning_progress(conn, int(offering["id"]), student_id)
        current_level = public_level_payload(progress.get("current_level"))
        next_stage = progress.get("next_stage") or {}
        metrics = progress.get("metrics") or {}
        bottleneck = _bottleneck_from_metrics(metrics)
        course_steps = _build_course_steps(
            conn,
            offering=offering,
            progress=progress,
            states=states,
            review_items=review_items,
            student_id=student_id,
        )
        active_step_count = sum(1 for item in course_steps if item["status"] == PATH_STATUS_ACTIVE)
        courses.append({
            "class_offering_id": int(offering["id"]),
            "course_id": int(offering["course_id"]),
            "course_name": str(offering.get("course_name") or "未命名课程"),
            "class_name": str(offering.get("class_name") or ""),
            "href": f"/classroom/{int(offering['id'])}",
            "score": progress.get("score", 0),
            "progress_percent": progress.get("progress_percent", 0),
            "current_level": current_level,
            "next_stage": next_stage,
            "eligible_stage": progress.get("eligible_stage"),
            "bottleneck": bottleneck,
            "active_step_count": active_step_count,
            "total_step_count": len(course_steps),
            "components": metrics.get("components") or {},
            "stage_marks": [
                {
                    "key": stage["key"],
                    "short_name": stage["short_name"],
                    "status": stage["status"],
                    "theme": stage.get("theme") or stage["key"],
                    "progress_percent": stage["progress_percent"],
                }
                for stage in (progress.get("stages") or [])
            ],
        })
        all_steps.extend(course_steps)

    normalized_status = str(status or PATH_STATUS_ACTIVE)
    if normalized_status not in PATH_STATUSES and normalized_status != "all":
        normalized_status = PATH_STATUS_ACTIVE
    query = str(keyword or "").strip().lower()

    filtered_steps = []
    for step in all_steps:
        if course_id and safe_int(step.get("course_id")) != int(course_id):
            continue
        if normalized_status != "all" and step["status"] != normalized_status:
            continue
        if query:
            haystack = " ".join(
                str(step.get(name) or "")
                for name in ("title", "description", "course_name", "class_name", "tag", "due_label")
            ).lower()
            if query not in haystack:
                continue
        filtered_steps.append(step)

    sorted_steps = _sort_steps(filtered_steps)
    total_count = len(all_steps)
    active_count = sum(1 for item in all_steps if item["status"] == PATH_STATUS_ACTIVE)
    done_count = sum(1 for item in all_steps if item["status"] == PATH_STATUS_DONE)
    snoozed_count = sum(1 for item in all_steps if item["status"] == PATH_STATUS_SNOOZED)
    pinned_count = sum(1 for item in all_steps if item.get("pinned"))
    estimated_minutes = sum(safe_int(item.get("estimated_minutes")) for item in sorted_steps if item["status"] == PATH_STATUS_ACTIVE)
    completion_percent = 100 if total_count == 0 else int(round(done_count / total_count * 100))
    focus_course = next(
        (
            item
            for item in sorted(courses, key=lambda value: (-int(value["active_step_count"]), safe_float(value["score"])))
            if int(item["active_step_count"]) > 0
        ),
        courses[0] if courses else None,
    )
    primary = sorted_steps[0] if sorted_steps else None

    return {
        "title": "个性化学习路径",
        "subtitle": "把修为进度、作业考试、材料阅读、错题复盘和课堂互动合成一条可执行路线。",
        "stages": _stage_summary(),
        "courses": courses,
        "items": sorted_steps,
        "all_count": total_count,
        "visible_count": len(sorted_steps),
        "primary_item": primary,
        "focus_course": focus_course,
        "active_status": normalized_status,
        "active_course_id": course_id or 0,
        "keyword": keyword or "",
        "filters": [
            {"value": "all", "label": "全部", "count": total_count},
            {"value": PATH_STATUS_ACTIVE, "label": "待推进", "count": active_count},
            {"value": PATH_STATUS_SNOOZED, "label": "稍后", "count": snoozed_count},
            {"value": PATH_STATUS_DONE, "label": "已完成", "count": done_count},
        ],
        "stats": [
            {"label": "待推进", "value": active_count, "hint": "当前建议动作", "tone": "warning" if active_count else "success"},
            {"label": "已完成", "value": done_count, "hint": f"完成度 {completion_percent}%", "tone": "success"},
            {"label": "预计投入", "value": estimated_minutes, "hint": "分钟", "tone": "primary"},
            {"label": "错题复盘", "value": review_context.get("progress", {}).get("active", 0), "hint": "反馈点联动", "tone": "danger" if review_context.get("progress", {}).get("active") else "neutral"},
            {"label": "已置顶", "value": pinned_count, "hint": "近期重点", "tone": "warning" if pinned_count else "neutral"},
        ],
        "progress": {
            "total": total_count,
            "active": active_count,
            "done": done_count,
            "snoozed": snoozed_count,
            "percent": completion_percent,
        },
    }


def build_learning_path_summary(conn: sqlite3.Connection, student_id: int) -> dict[str, Any]:
    context = build_personalized_learning_path_context(
        conn,
        {"id": int(student_id), "role": "student"},
        status=PATH_STATUS_ACTIVE,
    )
    primary = context.get("primary_item") or {}
    focus = context.get("focus_course") or {}
    return {
        "active_count": int(context["progress"]["active"]),
        "total_count": int(context["progress"]["total"]),
        "done_count": int(context["progress"]["done"]),
        "progress_percent": int(context["progress"]["percent"]),
        "estimated_minutes": int(next((item["value"] for item in context["stats"] if item["label"] == "预计投入"), 0)),
        "href": "/learning-path",
        "title": primary.get("title") or "推进个性化学习路径",
        "description": primary.get("description") or (
            f"优先关注 {focus.get('course_name')}" if focus else "系统会根据课堂数据生成下一步建议。"
        ),
        "course_name": focus.get("course_name") or "",
    }


def update_learning_path_item(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    *,
    item_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    student_id = _student_id(user)
    normalized_key = str(item_key or "").strip()
    if not normalized_key:
        raise ValueError("缺少学习路径项目")

    context = build_personalized_learning_path_context(conn, user, status="all")
    available = {str(item["key"]): item for item in context.get("items") or []}
    source_item = available.get(normalized_key)
    if not source_item:
        raise ValueError("当前学习路径项目已变化，请刷新后再操作")

    status = str(payload.get("status") or source_item.get("status") or PATH_STATUS_ACTIVE).strip()
    if status not in PATH_STATUSES:
        raise ValueError("学习路径状态不正确")
    pinned = 1 if payload.get("pinned") else 0
    reflection = _compact_text(payload.get("reflection"), limit=1200)
    next_action = _compact_text(payload.get("next_action"), limit=600)
    now = _now_iso()
    completed_at = now if status == PATH_STATUS_DONE else None
    snoozed_until = None
    if status == PATH_STATUS_SNOOZED:
        snoozed_until = str(payload.get("snoozed_until") or "").strip()
        if not snoozed_until:
            snoozed_until = (datetime.now() + timedelta(days=2)).isoformat(timespec="seconds")
    metadata = {
        "kind": source_item.get("kind"),
        "target_type": source_item.get("target_type"),
        "target_id": source_item.get("target_id"),
    }
    conn.execute(
        """
        INSERT INTO student_learning_path_item_states (
            student_id, class_offering_id, item_key, status, pinned,
            reflection, next_action, completed_at, snoozed_until,
            created_at, updated_at, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(student_id, item_key) DO UPDATE SET
            class_offering_id = excluded.class_offering_id,
            status = excluded.status,
            pinned = excluded.pinned,
            reflection = excluded.reflection,
            next_action = excluded.next_action,
            completed_at = CASE
                WHEN excluded.status = 'done' THEN COALESCE(student_learning_path_item_states.completed_at, excluded.completed_at)
                ELSE NULL
            END,
            snoozed_until = excluded.snoozed_until,
            updated_at = excluded.updated_at,
            metadata_json = excluded.metadata_json
        """,
        (
            student_id,
            safe_int(source_item.get("class_offering_id")) or None,
            normalized_key,
            status,
            pinned,
            reflection,
            next_action,
            completed_at,
            snoozed_until,
            now,
            now,
            json.dumps(metadata, ensure_ascii=False),
        ),
    )
    saved = conn.execute(
        """
        SELECT *
        FROM student_learning_path_item_states
        WHERE student_id = ? AND item_key = ?
        LIMIT 1
        """,
        (student_id, normalized_key),
    ).fetchone()
    item = _apply_step_state(source_item, dict(saved) if saved else None)
    return {
        "status": "success",
        "item": item,
        "status_label": item["status_label"],
        "pinned": item["pinned"],
    }
