from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from fastapi import HTTPException

from ..config import AGENT_TASK_MAX_RUNTIME_SECONDS, AGENT_TASK_RUNTIME_POLL_SECONDS, HOMEWORK_SUBMISSIONS_DIR
from ..database import get_db_connection
from ..db.connection import execute_insert_returning_id
from .agent_task_service import (
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    append_task_event,
    agent_workflow_catalog,
    finish_agent_task,
    utcnow_iso,
)
from .session_material_generation_service import (
    ACTIVE_TASK_STATUSES as MATERIAL_ACTIVE_TASK_STATUSES,
    TASK_STATUS_COMPLETED as MATERIAL_TASK_COMPLETED,
    create_generation_task,
    get_teacher_session_with_material_state,
    normalize_document_type,
    normalize_requirement_text,
    run_generation_task,
)


def _load_json(raw_value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(raw_value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_text(value: Any, *, max_chars: int = 0) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def _set_agent_runtime_state(
    conn,
    task_id: int,
    *,
    provider: str = "lanshare-session-material",
    runtime_status: str,
) -> None:
    conn.execute(
        """
        UPDATE agent_tasks
        SET runtime_provider = ?, runtime_status = ?, updated_at = ?
        WHERE id = ?
        """,
        (provider, runtime_status, utcnow_iso(), int(task_id)),
    )


def _target_from_context(task: dict[str, Any]) -> dict[str, Any]:
    context = _load_json(task.get("context_snapshot_json"))
    server_context = context.get("server_context") or {}
    target = server_context.get("lesson_document_target") or {}
    return target if isinstance(target, dict) else {}


def _server_context_from_task(task: dict[str, Any]) -> dict[str, Any]:
    context = _load_json(task.get("context_snapshot_json"))
    server_context = context.get("server_context") or {}
    return server_context if isinstance(server_context, dict) else {}


def _public_context_label(server_context: dict[str, Any]) -> str:
    classroom = server_context.get("classroom") or {}
    assignment = server_context.get("assignment") or {}
    material = server_context.get("material") or {}
    classroom = classroom if isinstance(classroom, dict) else {}
    assignment = assignment if isinstance(assignment, dict) else {}
    material = material if isinstance(material, dict) else {}
    parts = [
        classroom.get("course_name"),
        classroom.get("class_name"),
        assignment.get("title"),
        material.get("name"),
    ]
    return " / ".join(_safe_text(item, max_chars=80) for item in parts if _safe_text(item, max_chars=80))


def _finish_platform_business_task(
    task_id: int,
    *,
    platform_action: str,
    result_summary: str,
    detail: dict[str, Any],
    event_message: str = "",
    status: str = TASK_STATUS_COMPLETED,
    error_message: str = "",
) -> None:
    detail = {
        "platform_action": platform_action,
        "workflow_catalog": agent_workflow_catalog(),
        **(detail or {}),
    }
    with get_db_connection() as conn:
        _set_agent_runtime_state(
            conn,
            task_id,
            provider="lanshare-business-agent",
            runtime_status="completed" if status == TASK_STATUS_COMPLETED else "failed",
        )
        append_task_event(
            conn,
            task_id,
            "platform_business_result" if status == TASK_STATUS_COMPLETED else "platform_business_failed",
            event_message or result_summary,
            detail,
            commit=False,
        )
        finish_agent_task(
            conn,
            task_id,
            status=status,
            result_summary=result_summary,
            result_detail=detail,
            error_message=error_message,
        )


def _owned_classroom_snapshot(conn, *, teacher_id: int, class_offering_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT co.id,
               co.class_id,
               co.course_id,
               co.semester,
               co.schedule_info,
               co.first_class_date,
               c.name AS course_name,
               c.description AS course_description,
               cl.name AS class_name,
               cl.description AS class_description,
               t.name AS teacher_name
        FROM class_offerings co
        JOIN courses c ON c.id = co.course_id
        JOIN classes cl ON cl.id = co.class_id
        LEFT JOIN teachers t ON t.id = co.teacher_id
        WHERE co.id = ? AND co.teacher_id = ?
        LIMIT 1
        """,
        (int(class_offering_id), int(teacher_id)),
    ).fetchone()
    if not row:
        return None
    classroom = dict(row)
    sessions = [
        dict(item)
        for item in conn.execute(
            """
            SELECT s.id,
                   s.order_index,
                   s.title,
                   s.content,
                   s.session_date,
                   s.learning_material_id,
                   m.name AS learning_material_name,
                   m.material_path AS learning_material_path,
                   m.preview_type AS learning_material_preview_type
            FROM class_offering_sessions s
            LEFT JOIN course_materials m ON m.id = s.learning_material_id
            WHERE s.class_offering_id = ?
            ORDER BY s.order_index ASC
            LIMIT 120
            """,
            (int(class_offering_id),),
        ).fetchall()
    ]
    materials = [
        dict(item)
        for item in conn.execute(
            """
            SELECT m.id,
                   m.name,
                   m.material_path,
                   m.node_type,
                   m.preview_type,
                   m.ai_parse_status,
                   m.ai_parse_result_json,
                   a.created_at AS assigned_at
            FROM course_material_assignments a
            JOIN course_materials m ON m.id = a.material_id
            WHERE a.class_offering_id = ?
              AND m.teacher_id = ?
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT 80
            """,
            (int(class_offering_id), int(teacher_id)),
        ).fetchall()
    ]
    assignments = [
        dict(item)
        for item in conn.execute(
            """
            SELECT a.id,
                   a.title,
                   a.status,
                   a.grading_mode,
                   a.due_at,
                   a.exam_paper_id,
                   COUNT(s.id) AS submission_count,
                   COUNT(CASE WHEN s.score IS NOT NULL THEN 1 END) AS scored_count,
                   ROUND(AVG(CASE WHEN s.score IS NOT NULL THEN CAST(s.score AS REAL) END), 1) AS avg_score
            FROM assignments a
            LEFT JOIN submissions s ON s.assignment_id = a.id
            WHERE a.class_offering_id = ?
            GROUP BY a.id
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT 20
            """,
            (int(class_offering_id),),
        ).fetchall()
    ]
    classroom["sessions"] = sessions
    classroom["materials"] = materials
    classroom["assignments"] = assignments
    return classroom


def _class_offering_id_from_task(task: dict[str, Any]) -> int:
    server_context = _server_context_from_task(task)
    classroom = server_context.get("classroom") or {}
    assignment = server_context.get("assignment") or {}
    target = server_context.get("lesson_document_target") or {}
    candidates = (
        classroom.get("id") if isinstance(classroom, dict) else None,
        assignment.get("class_offering_id") if isinstance(assignment, dict) else None,
        target.get("class_offering_id") if isinstance(target, dict) else None,
    )
    for value in candidates:
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            return parsed
    return 0


def _markdown_bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items if _safe_text(item))


def _context_required_result(task: dict[str, Any], *, action: str, label: str) -> None:
    server_context = _server_context_from_task(task)
    context_label = _public_context_label(server_context)
    _finish_platform_business_task(
        int(task["id"]),
        platform_action=action,
        status=TASK_STATUS_FAILED,
        result_summary=f"{label}需要先限定到一个教师拥有的课堂。",
        error_message="请从课堂页、学习文档页、材料页或作业页打开任务中心，或在任务要求里明确课堂范围后重试。",
        detail={
            "display_title": label,
            "context_label": context_label,
            "next_actions": [
                "进入目标课堂后重新提交任务。",
                "如果是在首页发起，请先打开具体课堂卡片，再让 Agent 接管。",
            ],
        },
    )


def _selected_session_label(server_context: dict[str, Any]) -> str:
    target = server_context.get("lesson_document_target") or {}
    selected = server_context.get("selected_session") or {}
    item = target if isinstance(target, dict) and target else selected if isinstance(selected, dict) else {}
    if not item:
        return ""
    order = item.get("order_index") or ""
    title = item.get("title") or ""
    return f"第 {order} 次课 {title}".strip()


def _material_summary(item: dict[str, Any]) -> str:
    parsed = _load_json(item.get("ai_parse_result_json"))
    summary = parsed.get("summary") if isinstance(parsed, dict) else ""
    return _safe_text(summary, max_chars=220)


def _score_threshold(instruction: str) -> float | None:
    patterns = (
        r"(?:低于|小于|少于|不足|<)\s*([0-9]+(?:\.[0-9]+)?)\s*分?",
        r"([0-9]+(?:\.[0-9]+)?)\s*分\s*(?:以下|以内)",
    )
    for pattern in patterns:
        match = re.search(pattern, instruction)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
    return None


def _plain_summary(markdown: str, *, max_chars: int = 180) -> str:
    text = re.sub(r"[*_`#>\-|]+", " ", _safe_text(markdown))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _create_teacher_blog_draft(conn, *, teacher_id: int, title: str, content_md: str, tags: list[str]) -> dict[str, Any]:
    teacher = conn.execute(
        """
        SELECT id, name, nickname, avatar_file_hash, avatar_mime_type
        FROM teachers
        WHERE id = ?
        LIMIT 1
        """,
        (int(teacher_id),),
    ).fetchone()
    if not teacher:
        raise HTTPException(404, "教师账户不存在，无法创建博客草稿。")
    display_name = _safe_text(teacher["name"] or teacher["nickname"], max_chars=80) or f"教师{teacher_id}"
    now = utcnow_iso()
    post_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO blog_posts (
            author_identity, author_role, author_user_pk, author_display_name, author_display_mode,
            author_avatar_hash, author_avatar_mime, title, content_md, summary, status, visibility,
            visible_user_identities_json, allow_comments, system_tags_json, tags_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"teacher:{int(teacher_id)}",
            "teacher",
            int(teacher_id),
            display_name,
            "real_name",
            _safe_text(teacher["avatar_file_hash"], max_chars=160),
            _safe_text(teacher["avatar_mime_type"], max_chars=120),
            _safe_text(title, max_chars=120),
            _safe_text(content_md, max_chars=60000),
            _plain_summary(content_md),
            "draft",
            "public",
            "[]",
            1,
            json.dumps([], ensure_ascii=False),
            json.dumps([_safe_text(item, max_chars=24) for item in tags if _safe_text(item, max_chars=24)][:8], ensure_ascii=False),
            now,
            now,
        ),
    )
    return {"id": post_id, "status": "draft", "created_at": now}


def _instruction_is_exam_like(text: str) -> bool:
    return bool(re.search(r"(考试|试卷|测验|随堂测|quiz|exam)", _safe_text(text), flags=re.IGNORECASE))


def _create_assignment_draft(
    conn,
    *,
    course_id: int,
    class_offering_id: int,
    title: str,
    requirements_md: str,
    rubric_md: str,
) -> dict[str, Any]:
    now = utcnow_iso()
    assignment_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO assignments (
            course_id,
            title,
            status,
            requirements_md,
            rubric_md,
            grading_mode,
            class_offering_id,
            created_at,
            allowed_file_types_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(course_id),
            _safe_text(title, max_chars=120) or "Agent 作业草稿",
            "new",
            requirements_md,
            rubric_md,
            "manual",
            int(class_offering_id),
            now,
            "[]",
        ),
    )
    try:
        (HOMEWORK_SUBMISSIONS_DIR / str(course_id) / str(assignment_id)).mkdir(parents=True, exist_ok=True)
    except OSError:
        # The assignment itself is the source of truth; submission storage can still be created lazily later.
        pass
    return {"id": assignment_id, "status": "new", "created_at": now, "url": f"/assignment/{assignment_id}"}


def _generation_task_row(conn, generation_task_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM session_material_generation_tasks WHERE id = ? LIMIT 1",
        (int(generation_task_id),),
    ).fetchone()
    return dict(row) if row else None


def _execute_course_material_digest_task(task: dict[str, Any]) -> None:
    task_id = int(task["id"])
    teacher_id = int(task["teacher_id"])
    instruction = _safe_text(task.get("private_instruction"), max_chars=4000)
    class_offering_id = _class_offering_id_from_task(task)
    if not class_offering_id:
        _context_required_result(task, action="course_material_digest", label="课程材料整理")
        return

    with get_db_connection() as conn:
        snapshot = _owned_classroom_snapshot(conn, teacher_id=teacher_id, class_offering_id=class_offering_id)
    if not snapshot:
        _context_required_result(task, action="course_material_digest", label="课程材料整理")
        return

    sessions = snapshot["sessions"]
    materials = snapshot["materials"]
    assignments = snapshot["assignments"]
    bound_sessions = [item for item in sessions if item.get("learning_material_id")]
    missing_sessions = [item for item in sessions if not item.get("learning_material_id")][:8]
    markdown_materials = [item for item in materials if str(item.get("preview_type") or "") == "markdown"]
    parsed_materials = [item for item in materials if str(item.get("ai_parse_status") or "") == "completed"]
    recent_material_items = [
        {
            "title": item.get("name") or item.get("material_path") or "未命名材料",
            "meta": f"{item.get('preview_type') or item.get('node_type') or 'material'} · {item.get('material_path') or ''}",
            "note": _material_summary(item),
        }
        for item in materials[:10]
    ]
    markdown = f"""## 课程材料盘点

- 课堂：{_safe_text(snapshot.get("course_name"))} / {_safe_text(snapshot.get("class_name"))}
- 已分配材料：{len(materials)} 个，其中 Markdown/学习文档类 {len(markdown_materials)} 个，已完成 AI 解析 {len(parsed_materials)} 个。
- 课时进度：共 {len(sessions)} 次课，已有 {len(bound_sessions)} 次课绑定学习文档。
- 最近作业/考试：{len(assignments)} 个，最近一个是「{_safe_text((assignments[0] if assignments else {}).get("title")) or "暂无"}」。

## 建议优先处理

{_markdown_bullets([
        f"为第 {item.get('order_index')} 次课《{item.get('title') or '未命名课时'}》补齐学习文档"
        for item in missing_sessions[:5]
    ]) or "- 暂未发现缺失学习文档的课时。"}

## Agent 安全边界

- 本次只读取当前教师拥有的课堂、课时、材料与作业摘要。
- 不会重命名、删除或移动任何材料；如需生成学习文档，请切换到“生成学习文档”任务类型。
""".strip()
    detail = {
        "display_title": "课程材料整理报告",
        "context_label": f"{snapshot.get('course_name') or ''} / {snapshot.get('class_name') or ''}",
        "metrics": [
            {"label": "课堂材料", "value": len(materials)},
            {"label": "Markdown 材料", "value": len(markdown_materials)},
            {"label": "已绑定文档课时", "value": f"{len(bound_sessions)}/{len(sessions)}"},
            {"label": "近期作业", "value": len(assignments)},
        ],
        "markdown": markdown,
        "items": recent_material_items,
        "next_actions": [
            "若要自动生成并绑定下一节学习文档，改用“生成学习文档”。",
            "若要出下次课作业，改用“生成作业/考试草案”，Agent 会基于本报告生成结构化草稿。",
        ],
        "instruction": instruction,
    }
    _finish_platform_business_task(
        task_id,
        platform_action="course_material_digest",
        result_summary="已完成当前课堂的材料盘点，并输出缺口与下一步建议。",
        detail=detail,
        event_message="已读取课堂材料、课时学习文档和近期作业，生成材料整理报告。",
    )


def _execute_assignment_blueprint_task(task: dict[str, Any]) -> None:
    task_id = int(task["id"])
    teacher_id = int(task["teacher_id"])
    instruction = _safe_text(task.get("private_instruction"), max_chars=4000)
    server_context = _server_context_from_task(task)
    class_offering_id = _class_offering_id_from_task(task)
    if not class_offering_id:
        _context_required_result(task, action="assignment_blueprint", label="作业/考试草案")
        return

    with get_db_connection() as conn:
        snapshot = _owned_classroom_snapshot(conn, teacher_id=teacher_id, class_offering_id=class_offering_id)
    if not snapshot:
        _context_required_result(task, action="assignment_blueprint", label="作业/考试草案")
        return

    selected_session = _selected_session_label(server_context) or "当前课堂最近课时"
    material_names = [
        _safe_text(item.get("name") or item.get("material_path"), max_chars=80)
        for item in snapshot["materials"][:6]
    ]
    title = _safe_text(task.get("title"), max_chars=90) or f"{snapshot.get('course_name') or '课程'}课堂任务草案"
    requirements_md = f"""# {title}

## 适用范围

- 课堂：{snapshot.get('course_name') or ''} / {snapshot.get('class_name') or ''}
- 参考课时：{selected_session}
- 参考材料：{", ".join(material_names) if material_names else "当前课堂已绑定材料"}

## 任务要求

1. 阅读并整理本次课堂相关材料，提炼关键概念、操作步骤或案例结论。
2. 完成一份结构化作答，包含“知识理解”“应用分析”“反思改进”三个部分。
3. 若任务包含代码、截图或文档附件，请在提交中说明文件用途与关键结论。

## 教师补充要求

{instruction}
""".strip()
    rubric_md = """| 评分维度 | 分值 | 评价要点 |
| --- | ---: | --- |
| 知识理解 | 30 | 能准确解释课堂核心概念，术语使用清晰 |
| 材料应用 | 30 | 能结合课堂材料、案例或模板完成分析 |
| 完成质量 | 25 | 结构完整，过程可追踪，结论明确 |
| 表达与规范 | 15 | 格式规范，命名清楚，按时提交 |
""".strip()
    exam_like = _instruction_is_exam_like(f"{title}\n{instruction}")
    created_assignment: dict[str, Any] | None = None
    if not exam_like:
        with get_db_connection() as conn:
            owned = _owned_classroom_snapshot(conn, teacher_id=teacher_id, class_offering_id=class_offering_id)
            if owned:
                created_assignment = _create_assignment_draft(
                    conn,
                    course_id=int(owned["course_id"]),
                    class_offering_id=int(class_offering_id),
                    title=title,
                    requirements_md=requirements_md,
                    rubric_md=rubric_md,
                )
                conn.commit()
    detail = {
        "display_title": "作业/考试草案",
        "context_label": f"{snapshot.get('course_name') or ''} / {snapshot.get('class_name') or ''}",
        "metrics": [
            {"label": "参考材料", "value": len(snapshot["materials"])},
            {"label": "参考课时", "value": selected_session},
            {"label": "建议评分", "value": "100 分"},
            {"label": "平台草稿", "value": f"#{created_assignment['id']}" if created_assignment else "需在考试编辑器确认"},
        ],
        "markdown": f"{requirements_md}\n\n## 评分标准\n\n{rubric_md}",
        "draft": {
            "title": title,
            "requirements_md": requirements_md,
            "rubric_md": rubric_md,
            "grading_mode": "manual",
            "status": "new",
            "allowed_file_types": [],
        },
        "created_assignment": created_assignment or {},
        "links": [
            {"label": "打开作业草稿", "url": created_assignment["url"]}
            if created_assignment
            else {"label": "打开考试管理", "url": "/manage/exams"}
        ],
        "next_actions": [
            "打开平台草稿继续补充截止时间、附件要求和发布范围。" if created_assignment else "复制草案到考试编辑器，生成正式试卷后再发布。",
            "正式发布、截止时间、邮件通知必须由教师在平台界面确认。",
        ],
        "safety": [
            "Agent 只创建教师可见的草稿，未向学生发布。" if created_assignment else "Agent 未创建正式考试，未向学生发布。",
            "Agent 未修改学生提交、成绩或课堂配置。",
        ],
    }
    _finish_platform_business_task(
        task_id,
        platform_action="assignment_blueprint",
        result_summary=(
            f"已生成作业草案并创建平台草稿 #{created_assignment['id']}，等待教师确认后发布。"
            if created_assignment
            else "已生成考试草案和评分标准，等待教师在考试编辑器确认后发布。"
        ),
        detail=detail,
        event_message=(
            f"已基于当前课堂、课时和材料创建作业草稿 #{created_assignment['id']}。"
            if created_assignment
            else "已基于当前课堂、课时和材料生成考试草案，未创建正式考试。"
        ),
    )


def _execute_blog_draft_task(task: dict[str, Any]) -> None:
    task_id = int(task["id"])
    teacher_id = int(task["teacher_id"])
    instruction = _safe_text(task.get("private_instruction"), max_chars=4000)
    server_context = _server_context_from_task(task)
    class_offering_id = _class_offering_id_from_task(task)
    if not class_offering_id:
        _context_required_result(task, action="blog_draft", label="课堂博客草稿")
        return

    with get_db_connection() as conn:
        snapshot = _owned_classroom_snapshot(conn, teacher_id=teacher_id, class_offering_id=class_offering_id)
        if not snapshot:
            _context_required_result(task, action="blog_draft", label="课堂博客草稿")
            return
        selected_session = _selected_session_label(server_context) or "近期课堂"
        title = _safe_text(task.get("title"), max_chars=90) or f"{snapshot.get('course_name') or '课堂'}教学札记"
        material_names = [
            _safe_text(item.get("name") or item.get("material_path"), max_chars=80)
            for item in snapshot["materials"][:5]
        ]
        content_md = f"""# {title}

今天的课堂围绕 **{snapshot.get('course_name') or '本课程'}** 展开，重点关联 {selected_session}。

## 课堂脉络

- 面向班级：{snapshot.get('class_name') or '当前班级'}
- 参考材料：{", ".join(material_names) if material_names else "课堂已绑定材料"}
- 教师补充主题：{instruction}

## 值得记录的学习重点

1. 先用一个具体问题引出本次主题，让学生知道“为什么要学”。
2. 再把材料中的关键概念拆成可观察、可练习的小任务。
3. 最后用课堂作业或讨论收束，帮助学生把知识转成作品或结论。

## 下一步

- 将本文作为草稿审阅，补充课堂中的真实案例、学生常见问题和优秀作品链接。
- 确认无学生隐私信息后再公开发布。
""".strip()
        post = _create_teacher_blog_draft(
            conn,
            teacher_id=teacher_id,
            title=title,
            content_md=content_md,
            tags=[_safe_text(snapshot.get("course_name"), max_chars=24) or "课堂记录", "Agent草稿"],
        )
        conn.commit()

    post_id = int(post.get("id") or 0)
    detail = {
        "display_title": "课堂博客草稿",
        "context_label": f"{snapshot.get('course_name') or ''} / {snapshot.get('class_name') or ''}",
        "metrics": [
            {"label": "草稿 ID", "value": post_id},
            {"label": "状态", "value": "草稿"},
            {"label": "可见性", "value": "公开草稿，未发布"},
        ],
        "markdown": content_md,
        "created_post": {
            "id": post_id,
            "status": post.get("status") or "draft",
            "url": f"/blog?post_id={post_id}" if post_id else "",
        },
        "links": [
            {"label": "打开博客草稿", "url": f"/blog?post_id={post_id}"} if post_id else {},
        ],
        "next_actions": [
            "打开博客草稿，补充真实课堂细节后再发布。",
            "发布前检查是否包含学生隐私、成绩或未授权作品。",
        ],
        "safety": [
            "Agent 只创建教师本人的博客草稿，没有自动发布。",
            "草稿内容不包含学生个人成绩或隐私名单。",
        ],
    }
    _finish_platform_business_task(
        task_id,
        platform_action="blog_draft",
        result_summary=f"已创建课堂博客草稿 #{post_id}，等待教师审阅后发布。",
        detail=detail,
        event_message=f"已创建博客草稿 #{post_id}，未公开发布。",
    )


def _assignment_for_notification(conn, *, teacher_id: int, server_context: dict[str, Any], class_offering_id: int) -> dict[str, Any] | None:
    assignment_context = server_context.get("assignment") or {}
    assignment_id = 0
    if isinstance(assignment_context, dict):
        try:
            assignment_id = int(assignment_context.get("id") or 0)
        except (TypeError, ValueError):
            assignment_id = 0
    params: list[Any] = [int(teacher_id)]
    where = "co.teacher_id = ?"
    if assignment_id:
        where += " AND a.id = ?"
        params.append(assignment_id)
    elif class_offering_id:
        where += " AND a.class_offering_id = ?"
        params.append(int(class_offering_id))
    else:
        return None
    row = conn.execute(
        f"""
        SELECT a.id,
               a.title,
               a.class_offering_id,
               co.class_id,
               c.name AS course_name,
               cl.name AS class_name
        FROM assignments a
        JOIN class_offerings co ON co.id = a.class_offering_id
        JOIN courses c ON c.id = a.course_id
        JOIN classes cl ON cl.id = co.class_id
        WHERE {where}
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    return dict(row) if row else None


def _execute_student_notification_task(task: dict[str, Any]) -> None:
    task_id = int(task["id"])
    teacher_id = int(task["teacher_id"])
    instruction = _safe_text(task.get("private_instruction"), max_chars=4000)
    server_context = _server_context_from_task(task)
    class_offering_id = _class_offering_id_from_task(task)
    threshold = _score_threshold(instruction)

    with get_db_connection() as conn:
        assignment = _assignment_for_notification(
            conn,
            teacher_id=teacher_id,
            server_context=server_context,
            class_offering_id=class_offering_id,
        )
        if not assignment:
            _context_required_result(task, action="student_notification", label="学生通知草稿")
            return
        if threshold is None:
            threshold = 60.0
        recipients = [
            dict(item)
            for item in conn.execute(
                """
                SELECT st.id,
                       st.name,
                       st.student_id_number,
                       st.email,
                       s.score,
                       s.status
                FROM submissions s
                JOIN students st ON st.id = s.student_pk_id
                JOIN assignments a ON a.id = s.assignment_id
                JOIN class_offerings co ON co.id = a.class_offering_id
                WHERE a.id = ?
                  AND co.teacher_id = ?
                  AND s.score IS NOT NULL
                  AND CAST(s.score AS REAL) < ?
                ORDER BY CAST(s.score AS REAL) ASC, st.name ASC
                LIMIT 200
                """,
                (int(assignment["id"]), int(teacher_id), float(threshold)),
            ).fetchall()
        ]

    preview_items = [
        {
            "title": item.get("name") or item.get("student_id_number") or "学生",
            "meta": f"{item.get('student_id_number') or ''} · {item.get('score')} 分",
            "note": item.get("email") or "未记录邮箱",
        }
        for item in recipients[:30]
    ]
    message_draft = f"""同学你好：

我看到你在《{assignment.get('title') or '本次作业/考试'}》中的成绩暂低于 {threshold:g} 分。请先不要着急，建议你按下面步骤复盘：

1. 对照评分标准查看主要失分点。
2. 回到课堂材料中复习相关概念和示例。
3. 如果有不理解的地方，请带着具体问题来找我，我会帮你一起拆解。

这条提醒的目的不是批评，而是帮你尽快把薄弱点补起来。"""
    markdown = f"""## 通知名单预览

- 课堂：{assignment.get('course_name') or ''} / {assignment.get('class_name') or ''}
- 作业/考试：{assignment.get('title') or ''}
- 筛选条件：分数低于 {threshold:g} 分
- 命中人数：{len(recipients)}

## 通知草稿

{message_draft}

## 安全边界

- Agent 只生成名单预览和文案草稿，没有发送消息。
- 教师确认名单和措辞后，再到消息中心或作业页执行发送。
""".strip()
    detail = {
        "display_title": "学生通知草稿",
        "context_label": f"{assignment.get('course_name') or ''} / {assignment.get('class_name') or ''}",
        "metrics": [
            {"label": "筛选阈值", "value": f"< {threshold:g} 分"},
            {"label": "命中学生", "value": len(recipients)},
            {"label": "目标作业", "value": assignment.get("title") or ""},
        ],
        "markdown": markdown,
        "items": preview_items,
        "draft": {
            "assignment_id": int(assignment["id"]),
            "threshold": threshold,
            "recipient_count": len(recipients),
            "recipient_ids": [int(item["id"]) for item in recipients],
            "message": message_draft,
        },
        "next_actions": [
            "检查名单是否符合预期，必要时调整分数阈值后重试。",
            "确认后在消息中心或作业详情页发送，避免误触达。",
        ],
        "safety": [
            "Agent 未发送任何通知或邮件。",
            "仅任务发起教师可查看名单详情。",
        ],
    }
    _finish_platform_business_task(
        task_id,
        platform_action="student_notification",
        result_summary=f"已生成 {len(recipients)} 名学生的通知名单预览和文案草稿，尚未发送。",
        detail=detail,
        event_message=f"已按低于 {threshold:g} 分筛选学生并生成通知草稿，未发送。",
    )


async def _execute_gongwen_lookup_task(task: dict[str, Any]) -> None:
    """公文检索任务：进程内直连公文库（无需外部运行时/数据库连接）。

    复用对话同款链路：意图提炼 → 可见范围候选 → AI 相关性筛选，
    再用一次 AI 调用把命中公文整理成面向教师的解读报告。"""
    from .gongwen_ai_search_service import (
        build_gongwen_context_block,
        detect_gongwen_intent,
        search_gongwen_for_question,
    )

    task_id = int(task["id"])
    teacher_id = int(task["teacher_id"])
    instruction = _safe_text(task.get("private_instruction"), max_chars=4000)

    with get_db_connection() as conn:
        _set_agent_runtime_state(conn, task_id, provider="lanshare-business-agent", runtime_status="searching")
        append_task_event(
            conn,
            task_id,
            "platform_action_started",
            "正在公文中心检索相关公文（标题/文号/解析正文均参与匹配）。",
            {"platform_action": "gongwen_lookup"},
            commit=False,
        )
        conn.commit()

    # 这是显式的公文检索任务：即使意图 AI 判定「无关」或不可用，也按指令本身检索。
    intent = await detect_gongwen_intent(instruction)
    if not intent:
        from .gongwen_ai_search_service import _extract_local_keywords

        intent = {
            "related": True,
            "query": instruction[:80],
            "keywords": _extract_local_keywords(instruction),
            "recent_months": 0,
            "fallback": True,
        }
    result = await search_gongwen_for_question(teacher_id, instruction, intent)
    docs = result["documents"]

    if not docs:
        _finish_platform_business_task(
            task_id,
            platform_action="gongwen_lookup",
            result_summary="公文库中未找到与该问题直接相关的公文。",
            detail={
                "display_title": "公文检索",
                "markdown": (
                    "## 检索结果\n\n未在你可见范围内的公文库中找到直接相关的公文。\n\n"
                    "## 建议\n\n- 换用公文标题中的关键词重试；\n"
                    "- 到 [公文中心](/manage/gongwen) 直接搜索或筛选；\n"
                    "- 如果公文尚未同步，请先在「对接与申请 → 公文同步」配置凭据并同步。"
                ),
                "metrics": [
                    {"label": "候选公文", "value": int(result.get("candidate_count") or 0)},
                    {"label": "命中", "value": 0},
                ],
                "links": [{"label": "打开公文中心", "url": "/manage/gongwen"}],
                "next_actions": ["调整关键词后重新提交，或到公文中心人工检索。"],
            },
            event_message="公文检索完成：无直接命中。",
        )
        return

    context_block = build_gongwen_context_block(docs, intent=intent)
    interpretation = ""
    try:
        from ..core import ai_client

        resp = await ai_client.post(
            "/api/ai/chat",
            json={
                "system_prompt": (
                    "你是 LanShare 平台的公文解读助手。基于给定的公文检索结果回答教师的问题，"
                    "输出结构清晰的 Markdown：先直接回答，再分公文列出要点（标题、文号、时间、"
                    "关键要求/截止时间），最后给出建议。不要编造公文内容；内容不足时如实说明。"
                ),
                "messages": [],
                "new_message": f"【教师的问题】\n{instruction}\n\n{context_block}",
                "base64_urls": [],
                "file_texts": [],
                "model_capability": "standard",
                "task_type": "fast_text_response",
                "task_priority": "background",
                "task_label": "gongwen_agent_lookup",
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("status") == "success":
            interpretation = _safe_text(data.get("response_text") or data.get("text"), max_chars=12000)
    except Exception as exc:  # noqa: BLE001 — 解读 AI 故障时退化为纯检索清单
        print(f"[AGENT_TASK] gongwen lookup interpretation failed for task {task_id}: {exc}")

    doc_items = [
        {
            "title": f"《{doc.get('title') or '(无标题)'}》",
            "meta": " · ".join(
                piece
                for piece in (
                    str(doc.get("sn") or ""),
                    str(doc.get("author") or ""),
                    str(doc.get("publish_time") or "")[:10],
                )
                if piece
            ),
            "note": str(doc.get("relevance_reason") or doc.get("parsed_summary") or "")[:200],
        }
        for doc in docs
    ]
    doc_list_md = "\n".join(
        f"- 《{doc.get('title') or '(无标题)'}》 {doc.get('sn') or ''} "
        f"（{str(doc.get('publish_time') or '')[:10]}） → [查看原文](/manage/gongwen?doc={int(doc['id'])})"
        for doc in docs
    )
    markdown = (
        (interpretation + "\n\n" if interpretation else "")
        + f"## 命中公文（{len(docs)} 篇）\n\n{doc_list_md}\n\n"
        + "## 安全边界\n\n- 本次只读公文库，未修改任何公文或归属范围。\n- 解读仅供参考，正式执行以公文原文为准。"
    )
    _finish_platform_business_task(
        task_id,
        platform_action="gongwen_lookup",
        result_summary=f"已检索公文库并命中 {len(docs)} 篇相关公文" + ("，附 AI 解读。" if interpretation else "。"),
        detail={
            "display_title": "公文检索与解读",
            "markdown": markdown,
            "items": doc_items,
            "metrics": [
                {"label": "候选公文", "value": int(result.get("candidate_count") or 0)},
                {"label": "命中", "value": len(docs)},
                {"label": "AI 筛选", "value": "是" if result.get("ai_selected") else "关键词兜底"},
            ],
            "links": [
                {"label": "打开公文中心", "url": "/manage/gongwen"},
                *(
                    {"label": f"《{str(doc.get('title') or '')[:30]}》", "url": f"/manage/gongwen?doc={int(doc['id'])}"}
                    for doc in docs[:4]
                ),
            ],
            "next_actions": ["点击公文链接查看原文与附件。", "需要持续跟踪某主题时，可在公文中心设置「关注」自动提醒。"],
        },
        event_message=f"公文检索完成：候选 {int(result.get('candidate_count') or 0)} 篇，命中 {len(docs)} 篇。",
    )


def _execute_general_teaching_task(task: dict[str, Any]) -> None:
    task_id = int(task["id"])
    instruction = _safe_text(task.get("private_instruction"), max_chars=4000)
    server_context = _server_context_from_task(task)
    context_label = _public_context_label(server_context) or "当前页面"
    workflow_items = agent_workflow_catalog()
    recommended = []
    normalized = instruction.lower()
    keyword_map = (
        ("lesson_document", ("学习文档", "导学", "下一节课", "下次课", "lesson", "document")),
        ("assignment_exam_workflow", ("作业", "考试", "试卷", "测验", "题目", "assignment", "exam", "quiz")),
        ("student_support", ("通知", "低分", "未交", "提醒", "学生", "message", "notify")),
        ("material_operations", ("材料", "课件", "文档", "资料", "material")),
        ("blog_and_reflection", ("博客", "札记", "反思", "blog")),
        ("submission_grading_feedback", ("批改", "成绩", "提交", "反馈", "grading", "score")),
    )
    workflow_by_key = {item.get("key"): item for item in workflow_items}
    for key, keywords in keyword_map:
        if any(keyword.lower() in normalized for keyword in keywords):
            item = workflow_by_key.get(key)
            if item:
                recommended.append(item)
    if not recommended:
        recommended = workflow_items[:4]

    recommended_actions = []
    for item in recommended[:4]:
        steps = item.get("steps") or []
        recommended_actions.append(
            {
                "title": item.get("name") or item.get("key") or "教学流程",
                "meta": item.get("agent_capability") or "",
                "note": " → ".join(_safe_text(step, max_chars=60) for step in steps[:3]),
            }
        )

    markdown = f"""## 任务理解

{instruction}

## 推荐接管方式

{_markdown_bullets([f"{item.get('name')}：{item.get('agent_capability')}" for item in recommended[:4]])}

## 安全执行原则

- 如果任务涉及发布、发送、改分、删除、批量导入、同步教务或管理员配置，Agent 只生成草案和检查清单。
- 如果任务涉及学习文档生成，请改用“生成学习文档”，平台会走可审计的白名单动作。
- 如果任务涉及低分提醒，请改用“拟定学生通知”，平台会生成名单预览和通知草稿但不会直接发送。
""".strip()
    _finish_platform_business_task(
        task_id,
        platform_action="teaching_workflow_plan",
        result_summary="已完成教学事务预检，给出可安全接管的流程与下一步建议。",
        detail={
            "display_title": "教学事务预检",
            "context_label": context_label,
            "markdown": markdown,
            "items": recommended_actions,
            "next_actions": [
                "按推荐任务类型重新提交，可获得更具体的结构化产物。",
                "涉及学生可见或高影响数据的操作，请在原业务页面最终确认。",
            ],
            "safety": [
                "本次未修改任何平台业务数据。",
                "本次未调用外部运行时执行开放式操作。",
            ],
        },
        event_message="已完成通用教学事务的安全预检，未修改业务数据。",
    )


async def _wait_generation_task(generation_task_id: int) -> dict[str, Any] | None:
    started_at = time.monotonic()
    while True:
        with get_db_connection() as conn:
            row = _generation_task_row(conn, generation_task_id)
        if not row:
            return None
        if str(row.get("status") or "").lower() not in MATERIAL_ACTIVE_TASK_STATUSES:
            return row
        if time.monotonic() - started_at > AGENT_TASK_MAX_RUNTIME_SECONDS:
            return row
        await asyncio.sleep(max(1, AGENT_TASK_RUNTIME_POLL_SECONDS))


def _finish_missing_target(task_id: int) -> None:
    with get_db_connection() as conn:
        _set_agent_runtime_state(conn, task_id, runtime_status="failed")
        finish_agent_task(
            conn,
            task_id,
            status=TASK_STATUS_FAILED,
            result_summary="未能定位要生成学习文档的课堂课时。",
            error_message="请在课堂时间轴选中目标课时，或在任务要求中明确写出第几次课。",
            result_detail={"platform_action": "lesson_document_generation", "reason": "missing_target"},
        )


async def _execute_lesson_document_task(task: dict[str, Any]) -> None:
    task_id = int(task["id"])
    teacher_id = int(task["teacher_id"])
    instruction = _safe_text(task.get("private_instruction"), max_chars=4000)
    target = _target_from_context(task)
    class_offering_id = int(target.get("class_offering_id") or 0)
    session_id = int(target.get("id") or target.get("session_id") or 0)
    if not class_offering_id or not session_id:
        _finish_missing_target(task_id)
        return

    with get_db_connection() as conn:
        session_item = get_teacher_session_with_material_state(
            conn,
            class_offering_id=class_offering_id,
            session_id=session_id,
            teacher_id=teacher_id,
        )
        if not session_item:
            _set_agent_runtime_state(conn, task_id, runtime_status="failed")
            finish_agent_task(
                conn,
                task_id,
                status=TASK_STATUS_FAILED,
                result_summary="未找到可操作的课堂课时。",
                error_message="目标课时不存在，或当前教师没有该课堂的权限。",
                result_detail={
                    "platform_action": "lesson_document_generation",
                    "class_offering_id": class_offering_id,
                    "session_id": session_id,
                },
            )
            return

        _set_agent_runtime_state(conn, task_id, runtime_status="preparing")
        append_task_event(
            conn,
            task_id,
            "platform_action_started",
            (
                f"已定位到第 {session_item.get('order_index')} 次课"
                f"《{session_item.get('title') or '未命名课时'}》，准备生成并绑定学习文档。"
            ),
            {
                "platform_action": "lesson_document_generation",
                "class_offering_id": class_offering_id,
                "session_id": session_id,
                "target_reason": target.get("reason") or "",
                "previous_bound_count": int(target.get("previous_bound_count") or 0),
            },
            commit=False,
        )

        existing_task = session_item.get("material_generation_task")
        if existing_task and existing_task.get("is_active"):
            generation_task = existing_task
            already_running = True
            append_task_event(
                conn,
                task_id,
                "generation_task_attached",
                "该课时已有学习文档生成任务在执行，任务中心将接管观察结果。",
                {"generation_task_id": generation_task.get("id")},
                commit=False,
            )
        else:
            document_type = normalize_document_type(
                "课堂学习文档",
                session_title=session_item.get("title") or "",
                session_content=session_item.get("content") or "",
            )
            requirement_text = normalize_requirement_text(
                f"{instruction}\n\n由任务中心 Agent 发起：请读取目标课时之前已绑定的学习文档，延续结构与风格，生成当前目标课时文档并自动绑定。"
            )
            generation_task = create_generation_task(
                conn,
                class_offering_id=class_offering_id,
                session_id=session_id,
                teacher_id=teacher_id,
                trigger_mode="auto",
                document_type=document_type,
                requirement_text=requirement_text,
                example_documents=[],
            )
            already_running = bool(generation_task.get("already_running"))
            append_task_event(
                conn,
                task_id,
                "generation_task_created",
                f"已创建课时学习文档生成任务 #{generation_task.get('id')}，开始读取前序文档并生成材料。",
                {"generation_task_id": generation_task.get("id"), "document_type": document_type},
                commit=False,
            )
        _set_agent_runtime_state(conn, task_id, runtime_status="generation_running")
        conn.commit()

    generation_task_id = int(generation_task.get("id") or 0)
    if not already_running:
        await run_generation_task(generation_task_id)
    final_generation_row = await _wait_generation_task(generation_task_id)

    with get_db_connection() as conn:
        final_session = get_teacher_session_with_material_state(
            conn,
            class_offering_id=class_offering_id,
            session_id=session_id,
            teacher_id=teacher_id,
        )
        final_task = (final_session or {}).get("material_generation_task") or {}
        if not final_task and final_generation_row:
            final_task = final_generation_row

        status = str((final_generation_row or final_task).get("status") or "").lower()
        generated_material_id = int((final_generation_row or {}).get("generated_material_id") or 0) or (
            final_task.get("generated_material_id")
        )
        generated_path = _safe_text(
            (final_generation_row or {}).get("generated_material_path")
            or final_task.get("generated_material_path")
            or (final_session or {}).get("learning_material_path")
        )
        detail = {
            "platform_action": "lesson_document_generation",
            "class_offering_id": class_offering_id,
            "session_id": session_id,
            "session_order_index": (final_session or session_item or {}).get("order_index"),
            "session_title": (final_session or session_item or {}).get("title") or "",
            "generation_task": final_task,
            "generated_material_id": generated_material_id,
            "generated_material_path": generated_path,
            "generated_material_viewer_url": (
                final_task.get("generated_material_viewer_url")
                or ((final_session or {}).get("learning_material_viewer_url") or "")
            ),
            "target": target,
        }

        if status == MATERIAL_TASK_COMPLETED and generated_material_id:
            _set_agent_runtime_state(conn, task_id, runtime_status="completed")
            append_task_event(
                conn,
                task_id,
                "platform_action_completed",
                f"学习文档已生成并绑定到第 {detail['session_order_index']} 次课：{generated_path}",
                detail,
                commit=False,
            )
            finish_agent_task(
                conn,
                task_id,
                status=TASK_STATUS_COMPLETED,
                result_summary=(
                    f"已成功生成并绑定第 {detail['session_order_index']} 次课"
                    f"《{detail['session_title'] or '未命名课时'}》的学习文档：{generated_path}"
                ),
                result_detail=detail,
            )
            return

        error_message = _safe_text(
            (final_generation_row or final_task).get("error_message"),
            max_chars=1200,
        ) or "学习文档生成任务结束，但没有生成可绑定的 Markdown 文档。"
        _set_agent_runtime_state(conn, task_id, runtime_status="failed")
        append_task_event(
            conn,
            task_id,
            "platform_action_failed",
            f"学习文档生成未完成：{error_message}",
            detail,
            commit=False,
        )
        finish_agent_task(
            conn,
            task_id,
            status=TASK_STATUS_FAILED,
            result_summary="学习文档生成未完成。",
            error_message=error_message,
            result_detail=detail,
        )


# 运行时优先的开放式任务类型：交给独立运行时（带桥接工具）执行，产出更智能；
# 运行时不可用时回退到下面的平台模板处理器（降级优先，不能比现在差）。
RUNTIME_OPEN_ENDED_TASK_TYPES = frozenset({
    "course_material_digest",
    "assignment_blueprint",
    "blog_draft",
    "student_notification",
    "general_teaching_task",
})


async def try_execute_platform_agent_task(task: dict[str, Any], *, runtime_available: bool = False) -> bool:
    task_type = str(task.get("task_type") or "")
    if task_type not in {
        "lesson_document",
        "course_material_digest",
        "assignment_blueprint",
        "blog_draft",
        "student_notification",
        "gongwen_lookup",
        "general_teaching_task",
    }:
        return False
    if runtime_available and task_type in RUNTIME_OPEN_ENDED_TASK_TYPES:
        from ..config import AGENT_TASK_RUNTIME_FIRST
        from .gongwen_ai_search_service import message_may_mention_gongwen

        instruction = _safe_text(task.get("private_instruction"), max_chars=4000)
        # 公文类问题平台检索服务又快又准，仍走平台路径；其余开放式任务交给运行时。
        is_gongwen_question = task_type == "general_teaching_task" and message_may_mention_gongwen(instruction)
        if AGENT_TASK_RUNTIME_FIRST and not is_gongwen_question:
            return False
    task_id = int(task["id"])
    platform_action = {
        "lesson_document": "lesson_document_generation",
        "course_material_digest": "course_material_digest",
        "assignment_blueprint": "assignment_blueprint",
        "blog_draft": "blog_draft",
        "student_notification": "student_notification",
        "gongwen_lookup": "gongwen_lookup",
        "general_teaching_task": "teaching_workflow_plan",
    }.get(task_type, task_type)
    try:
        if task_type == "lesson_document":
            await _execute_lesson_document_task(task)
        elif task_type == "gongwen_lookup":
            await _execute_gongwen_lookup_task(task)
        elif task_type == "course_material_digest":
            _execute_course_material_digest_task(task)
        elif task_type == "assignment_blueprint":
            _execute_assignment_blueprint_task(task)
        elif task_type == "blog_draft":
            _execute_blog_draft_task(task)
        elif task_type == "student_notification":
            _execute_student_notification_task(task)
        elif task_type == "general_teaching_task":
            from .gongwen_ai_search_service import message_may_mention_gongwen

            # 通用教学事务里明确问公文/规定/通知的，直接走公文检索（不再只给流程建议）。
            if message_may_mention_gongwen(_safe_text(task.get("private_instruction"), max_chars=4000)):
                await _execute_gongwen_lookup_task(task)
            else:
                _execute_general_teaching_task(task)
    except Exception as exc:
        error_message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        with get_db_connection() as conn:
            _set_agent_runtime_state(
                conn,
                task_id,
                provider="lanshare-session-material" if task_type == "lesson_document" else "lanshare-business-agent",
                runtime_status="failed",
            )
            finish_agent_task(
                conn,
                task_id,
                status=TASK_STATUS_FAILED,
                result_summary="平台业务执行失败。",
                error_message=_safe_text(error_message, max_chars=1200) or "未知错误",
                result_detail={"platform_action": platform_action},
            )
    return True
