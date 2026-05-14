from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from .academic_service import load_teacher_semester_rows, serialize_semester_row, serialize_textbook_row
from .course_planning_service import load_course_lessons_by_course_id, serialize_course_row, truncate_text
from .department_service import collect_department_options, infer_department_from_text, normalize_department
from .materials_service import attach_learning_material_briefs


def _count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int((row[0] if row else 0) or 0)


def _parse_grouped_ids(raw_value: Any) -> list[int]:
    ids: list[int] = []
    for item in str(raw_value or "").split(","):
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in ids:
            ids.append(value)
    return ids


def _load_teacher_courses(conn: sqlite3.Connection, teacher_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.id,
               c.name,
               c.description,
               c.sect_name,
               c.department,
               c.credits,
               c.total_hours,
               c.created_at,
               c.created_by_teacher_id,
               COUNT(DISTINCT o.id) AS offering_count,
               GROUP_CONCAT(DISTINCT o.class_id) AS related_class_ids,
               GROUP_CONCAT(DISTINCT o.textbook_id) AS related_textbook_ids
        FROM courses c
        LEFT JOIN class_offerings o
            ON o.course_id = c.id
           AND o.teacher_id = c.created_by_teacher_id
        WHERE c.created_by_teacher_id = ?
        GROUP BY c.id, c.name, c.description, c.sect_name, c.department, c.credits, c.total_hours, c.created_at, c.created_by_teacher_id
        ORDER BY c.created_at DESC, c.name
        """,
        (teacher_id,),
    ).fetchall()
    course_ids = [int(row["id"]) for row in rows]
    lessons_by_course = load_course_lessons_by_course_id(conn, course_ids)
    for course_id, lesson_items in lessons_by_course.items():
        lessons_by_course[course_id] = attach_learning_material_briefs(
            conn,
            lesson_items,
            teacher_id=teacher_id,
            markdown_only=True,
        )

    courses: list[dict] = []
    for row in rows:
        item = serialize_course_row(
            row,
            lessons=lessons_by_course.get(int(row["id"]), []),
            offering_count=int(row["offering_count"] or 0),
        )
        item["department"] = normalize_department(item.get("department")) or infer_department_from_text(
            item.get("name"),
            item.get("description"),
        )
        item["related_class_ids"] = _parse_grouped_ids(row["related_class_ids"])
        item["related_textbook_ids"] = _parse_grouped_ids(row["related_textbook_ids"])
        courses.append(item)
    return courses


def _load_teacher_classes(conn: sqlite3.Connection, teacher_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.id,
               c.name,
               c.department,
               c.description,
               c.created_at,
               COUNT(DISTINCT s.id) AS student_count,
               GROUP_CONCAT(DISTINCT o.course_id) AS related_course_ids
        FROM classes c
        LEFT JOIN students s
               ON s.class_id = c.id
              AND COALESCE(s.enrollment_status, 'active') = 'active'
        LEFT JOIN class_offerings o ON o.class_id = c.id AND o.teacher_id = c.created_by_teacher_id
        WHERE c.created_by_teacher_id = ?
        GROUP BY c.id, c.name, c.department, c.description, c.created_at
        ORDER BY c.name COLLATE NOCASE
        """,
        (teacher_id,),
    ).fetchall()
    classes: list[dict] = []
    for row in rows:
        department = normalize_department(row["department"]) or infer_department_from_text(row["name"], row["description"])
        classes.append(
            {
                "id": int(row["id"]),
                "name": str(row["name"] or ""),
                "department": department,
                "description": str(row["description"] or ""),
                "student_count": int(row["student_count"] or 0),
                "related_course_ids": _parse_grouped_ids(row["related_course_ids"]),
                "created_at": str(row["created_at"] or ""),
            }
        )
    return classes


def _load_teacher_textbooks(conn: sqlite3.Connection, teacher_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT tb.*,
               GROUP_CONCAT(DISTINCT o.course_id) AS related_course_ids,
               COUNT(DISTINCT o.id) AS offering_count
        FROM textbooks tb
        LEFT JOIN class_offerings o ON o.textbook_id = tb.id AND o.teacher_id = tb.teacher_id
        WHERE tb.teacher_id = ?
        GROUP BY tb.id
        ORDER BY offering_count DESC, tb.updated_at DESC, tb.id DESC
        """,
        (teacher_id,),
    ).fetchall()
    textbooks: list[dict] = []
    for row in rows:
        item = serialize_textbook_row(row)
        item["related_course_ids"] = _parse_grouped_ids(row["related_course_ids"])
        item["offering_count"] = int(row["offering_count"] or 0)
        textbooks.append(item)
    return textbooks


def _load_teacher_materials(conn: sqlite3.Connection, teacher_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT m.id,
               m.parent_id,
               m.root_id,
               m.name,
               m.material_path,
               m.node_type,
               m.preview_type,
               m.file_ext,
               m.file_size,
               m.updated_at,
               (SELECT COUNT(*) FROM course_materials child WHERE child.parent_id = m.id AND child.name != '.git') AS child_count,
               GROUP_CONCAT(DISTINCT cl.course_id) AS lesson_course_ids,
               GROUP_CONCAT(DISTINCT o.course_id) AS offering_course_ids
        FROM course_materials m
        LEFT JOIN course_lessons cl ON cl.learning_material_id = m.id
        LEFT JOIN course_material_assignments a ON a.material_id = m.id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        WHERE m.teacher_id = ?
          AND m.name != '.git'
          AND m.parent_id IS NULL
        GROUP BY m.id, m.parent_id, m.root_id, m.name, m.material_path, m.node_type, m.preview_type, m.file_ext, m.file_size, m.updated_at
        ORDER BY
          CASE WHEN m.node_type = 'folder' THEN 0 ELSE 1 END,
          CASE WHEN m.preview_type = 'markdown' THEN 0 ELSE 1 END,
          m.updated_at DESC,
          m.id DESC
        LIMIT 96
        """,
        (teacher_id,),
    ).fetchall()
    materials: list[dict] = []
    for row in rows:
        related_course_ids = _parse_grouped_ids(row["lesson_course_ids"])
        for course_id in _parse_grouped_ids(row["offering_course_ids"]):
            if course_id not in related_course_ids:
                related_course_ids.append(course_id)
        materials.append(
            {
                "id": int(row["id"]),
                "parent_id": int(row["parent_id"]) if row["parent_id"] is not None else None,
                "root_id": int(row["root_id"] or row["id"]),
                "name": str(row["name"] or ""),
                "material_path": str(row["material_path"] or ""),
                "node_type": str(row["node_type"] or ""),
                "preview_type": str(row["preview_type"] or ""),
                "file_ext": str(row["file_ext"] or ""),
                "file_size": int(row["file_size"] or 0),
                "child_count": int(row["child_count"] or 0),
                "updated_at": str(row["updated_at"] or ""),
                "is_markdown": str(row["node_type"] or "") == "file" and str(row["preview_type"] or "") == "markdown",
                "related_course_ids": related_course_ids,
            }
        )
    return materials


def _load_teacher_offer_summary(conn: sqlite3.Connection, teacher_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT o.id,
               o.class_id,
               o.course_id,
               o.semester_id,
               o.textbook_id,
               COALESCE(s.name, o.semester) AS semester_name,
               c.name AS class_name,
               co.name AS course_name
        FROM class_offerings o
        JOIN classes c ON c.id = o.class_id
        JOIN courses co ON co.id = o.course_id
        LEFT JOIN academic_semesters s ON s.id = o.semester_id
        WHERE o.teacher_id = ?
        ORDER BY o.created_at DESC, o.id DESC
        LIMIT 60
        """,
        (teacher_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def build_teacher_onboarding_wizard_context(conn: sqlite3.Connection, teacher_id: int) -> dict:
    teacher_row = conn.execute("SELECT id, name FROM teachers WHERE id = ?", (teacher_id,)).fetchone()
    semesters = [serialize_semester_row(row) for row in load_teacher_semester_rows(conn, teacher_id)]
    courses = _load_teacher_courses(conn, teacher_id)
    classes = _load_teacher_classes(conn, teacher_id)
    textbooks = _load_teacher_textbooks(conn, teacher_id)
    materials = _load_teacher_materials(conn, teacher_id)
    offerings = _load_teacher_offer_summary(conn, teacher_id)
    department_options = collect_department_options(
        (item.get("department") for item in classes),
        (item.get("department") for item in courses),
    )

    return {
        "teacher": {
            "id": teacher_id,
            "name": str(teacher_row["name"] if teacher_row else "") or "老师",
        },
        "departments": department_options,
        "semesters": semesters,
        "courses": courses,
        "classes": classes,
        "textbooks": textbooks,
        "materials": materials,
        "offerings": offerings,
        "defaults": {
            "department": courses[0].get("department") if courses and courses[0].get("department") else (
                classes[0].get("department") if classes and classes[0].get("department") else department_options[0]
            ),
            "total_hours": 32,
            "credits": 2.0,
            "weekly_schedule": [{"weekday": 0, "section_count": 2}],
        },
    }


def build_teacher_onboarding_progress(conn: sqlite3.Connection, teacher_id: int) -> dict:
    counts = {
        "semesters": _count(
            conn,
            "SELECT COUNT(*) FROM academic_semesters WHERE teacher_id = ?",
            (teacher_id,),
        ),
        "courses": _count(
            conn,
            "SELECT COUNT(*) FROM courses WHERE created_by_teacher_id = ?",
            (teacher_id,),
        ),
        "textbooks": _count(
            conn,
            "SELECT COUNT(*) FROM textbooks WHERE teacher_id = ?",
            (teacher_id,),
        ),
        "materials": _count(
            conn,
            "SELECT COUNT(*) FROM course_materials WHERE teacher_id = ? AND name != '.git'",
            (teacher_id,),
        ),
        "classes": _count(
            conn,
            "SELECT COUNT(*) FROM classes WHERE created_by_teacher_id = ?",
            (teacher_id,),
        ),
        "offerings": _count(
            conn,
            "SELECT COUNT(*) FROM class_offerings WHERE teacher_id = ?",
            (teacher_id,),
        ),
        "ai_configs": _count(
            conn,
            """
            SELECT COUNT(*)
            FROM ai_class_configs cfg
            JOIN class_offerings o ON o.id = cfg.class_offering_id
            WHERE o.teacher_id = ?
              AND TRIM(COALESCE(cfg.system_prompt, '')) != ''
              AND TRIM(COALESCE(cfg.syllabus, '')) != ''
            """,
            (teacher_id,),
        ),
    }

    step_defs = [
        {
            "id": "semesters",
            "title": "确认学期",
            "description": "开课前先确认教学日历和周次。",
            "href": "/manage/semesters",
            "action_label": "去创建学期",
            "count_key": "semesters",
        },
        {
            "id": "courses",
            "title": "确认课程",
            "description": "课程名称、系别和模板是课堂的基础。",
            "href": "/manage/courses",
            "action_label": "去准备课程",
            "count_key": "courses",
        },
        {
            "id": "textbooks",
            "title": "选择教材",
            "description": "教材会作为课程模板和 AI 助教的重要依据。",
            "href": "/manage/textbooks",
            "action_label": "去准备教材",
            "count_key": "textbooks",
        },
        {
            "id": "materials",
            "title": "整理材料",
            "description": "课件、文档和课堂材料可在开课后继续复用。",
            "href": "/manage/materials",
            "action_label": "去整理材料",
            "count_key": "materials",
        },
        {
            "id": "classes",
            "title": "选择班级",
            "description": "班级按系别管理，后续开课时直接绑定。",
            "href": "/manage/classes",
            "action_label": "去创建班级",
            "count_key": "classes",
        },
        {
            "id": "offerings",
            "title": "开设课堂",
            "description": "把学期、课程、教材、班级和排课设置组合成真实课堂。",
            "href": "/manage/offerings",
            "action_label": "去开设课堂",
            "count_key": "offerings",
        },
        {
            "id": "ai",
            "title": "配置 AI 助教",
            "description": "为新课堂保存系统提示词和课程知识依据。",
            "href": "/manage/ai",
            "action_label": "去配置 AI",
            "count_key": "ai_configs",
        },
    ]

    steps = []
    for index, item in enumerate(step_defs):
        count = counts[item["count_key"]]
        ready = count > 0
        steps.append(
            {
                "index": index,
                **item,
                "count": count,
                "ready": ready,
                "status_label": "已完成" if ready else "待完成",
            }
        )

    completed_count = sum(1 for step in steps if step["ready"])
    next_step = next((step for step in steps if not step["ready"]), steps[0])

    return {
        "counts": counts,
        "steps": steps,
        "completed_count": completed_count,
        "total_count": len(steps),
        "all_core_ready": completed_count == len(steps),
        "next_step_id": next_step["id"],
    }


def build_teacher_onboarding_payload(conn: sqlite3.Connection, teacher_id: int) -> dict:
    state_row = conn.execute(
        """
        SELECT teacher_id, dismissed_at, completed_at, dismiss_reason, updated_at
        FROM teacher_onboarding_state
        WHERE teacher_id = ?
        LIMIT 1
        """,
        (teacher_id,),
    ).fetchone()
    state = dict(state_row) if state_row else {
        "teacher_id": teacher_id,
        "dismissed_at": None,
        "completed_at": None,
        "dismiss_reason": "",
        "updated_at": None,
    }
    progress = build_teacher_onboarding_progress(conn, teacher_id)
    wizard = build_teacher_onboarding_wizard_context(conn, teacher_id)
    has_dismissed = bool(state.get("dismissed_at") or state.get("completed_at"))

    return {
        "status": "success",
        "state": state,
        "progress": progress,
        "wizard": wizard,
        "should_auto_open": (not has_dismissed) and (not progress["all_core_ready"]),
    }


def build_default_course_description(
    *,
    course_name: str,
    department: str,
    textbook: dict | None = None,
) -> str:
    textbook_title = str((textbook or {}).get("title") or "").strip()
    catalog = truncate_text((textbook or {}).get("catalog_text"), 180)
    intro = truncate_text((textbook or {}).get("introduction"), 180)
    lines = [
        f"《{course_name}》面向{department or '相关专业'}学生开设，围绕课程核心概念、实践能力和综合应用展开。",
        "课程将通过讲授、案例分析、实验训练和课堂讨论，帮助学生建立清晰的知识结构，并能把所学内容应用到真实问题中。",
    ]
    if textbook_title:
        lines.append(f"本课程以《{textbook_title}》作为主要教学参考。")
    if intro:
        lines.append(f"教材侧重：{intro}")
    if catalog:
        lines.append(f"章节线索：{catalog}")
    lines.append("教学过程中可结合课堂材料、阶段任务和 AI 助教答疑，逐步完成知识理解、技能训练和项目化应用。")
    return "\n".join(lines)


def build_default_ai_config(
    *,
    teacher_name: str,
    course_name: str,
    class_name: str,
    semester_name: str,
    department: str,
    textbook_title: str = "",
    course_description: str = "",
    material_names: Iterable[str] = (),
) -> dict[str, str]:
    materials_text = "、".join(str(name) for name in material_names if str(name or "").strip()) or "暂无已选材料"
    textbook_text = textbook_title or "暂未绑定教材"
    summary = (
        f"课程名称：{course_name}\n"
        f"授课班级：{class_name}\n"
        f"所属系别：{department or '未填写'}\n"
        f"所属学期：{semester_name}\n"
        f"任课教师：{teacher_name}\n"
        f"教材：{textbook_text}\n"
        f"已选材料：{materials_text}"
    )
    system_prompt = (
        f"你是《{course_name}》课堂的 AI 助教，协助 {teacher_name} 老师服务本课堂的教师和学生。\n\n"
        f"{summary}\n\n"
        "回答学生问题时，优先基于课程简介、教材、课堂材料和教师发布的任务进行解释；"
        "遇到作业、考试或测验相关内容时，引导学生理解思路，不直接代写答案或泄露标准答案。"
        "回答教师问题时，可以协助备课、梳理知识点、设计课堂活动和优化表达。"
        "如果问题超出课程范围，清楚说明边界，并给出可继续查证的方向。始终使用简体中文，表达自然、准确、具体。"
    )
    syllabus = (
        f"{summary}\n\n"
        "一、课程简介\n"
        f"{course_description or '请结合教材与课堂实际补充课程目标、能力要求和实践安排。'}\n\n"
        "二、知识依据\n"
        f"- 教材：{textbook_text}\n"
        f"- 课堂材料：{materials_text}\n\n"
        "三、AI 助教边界\n"
        "- 可以解释概念、拆解步骤、给出学习建议和课堂活动建议。\n"
        "- 不直接完成学生作业，不泄露考试答案。\n"
        "- 发现材料、教材和教师说明不一致时，先提示差异，再建议向任课教师确认。"
    )
    return {"system_prompt": system_prompt, "syllabus": syllabus}


def mark_teacher_onboarding_dismissed(
    conn: sqlite3.Connection,
    teacher_id: int,
    reason: str,
) -> None:
    normalized_reason = str(reason or "").strip().lower()
    if normalized_reason not in {"completed", "manual_exit", "used"}:
        normalized_reason = "manual_exit"

    if normalized_reason == "completed":
        conn.execute(
            """
            INSERT INTO teacher_onboarding_state (
                teacher_id, dismissed_at, completed_at, dismiss_reason, updated_at
            )
            VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(teacher_id) DO UPDATE SET
                dismissed_at = CURRENT_TIMESTAMP,
                completed_at = CURRENT_TIMESTAMP,
                dismiss_reason = excluded.dismiss_reason,
                updated_at = CURRENT_TIMESTAMP
            """,
            (teacher_id, normalized_reason),
        )
        return

    conn.execute(
        """
        INSERT INTO teacher_onboarding_state (
            teacher_id, dismissed_at, completed_at, dismiss_reason, updated_at
        )
        VALUES (?, CURRENT_TIMESTAMP, NULL, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(teacher_id) DO UPDATE SET
            dismissed_at = CURRENT_TIMESTAMP,
            dismiss_reason = excluded.dismiss_reason,
            updated_at = CURRENT_TIMESTAMP
        """,
        (teacher_id, normalized_reason),
    )
