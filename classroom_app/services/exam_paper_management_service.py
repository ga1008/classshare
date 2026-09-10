"""Exam authoring and classroom assignment in the caller's transaction.

Normal Web and Agent share resource authorization, scoring validation,
classification, notifications and reminders. No commits or filesystem writes.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import sqlite3
import uuid
from typing import Any

from fastapi import HTTPException
from ..db.connection import execute_insert_returning_id as insert_and_get_id
from .assessment_classification_service import initialize_assignment_assessment_kind, normalize_assessment_kind
from .assignment_lifecycle_service import build_assignment_schedule_fields
from .assignment_management_service import _get_learning_stage_key, _get_allowed_file_types, _wants_assignment_email_notification, _hide_personal_stage_asset
from .assignment_reminder_service import sync_assignment_due_reminders
from .base_resource_modes_service import ensure_teacher_can_manage_exam_attributes, ensure_teacher_can_view_exam_attributes, serialize_exam_content
from .exam_json_service import normalize_exam_scoring_payload, build_exam_rubric_md
from .learning_progress_service import is_personal_stage_exam_paper
from .message_center_service import create_assignment_published_notifications
from .organization_scope_service import load_teacher_org_scope
from .resource_access_service import SCOPE_PRIVATE, SCOPE_DEPARTMENT, SCOPE_SCHOOL, normalize_scope_level, teacher_can_manage_exam_paper, teacher_can_use_exam_paper
from .submission_assets import encode_allowed_file_types_json

EXAM_OPEN_SCOPES = {SCOPE_PRIVATE, SCOPE_DEPARTMENT, SCOPE_SCHOOL}
EXAM_SCOPE_LABELS = {SCOPE_PRIVATE: "私有", SCOPE_DEPARTMENT: "本系部开放", SCOPE_SCHOOL: "全校开放"}


def lock_exam_paper(conn, paper_id):
    """Order ordinary authoring/assignment against each other, before reading."""
    if isinstance(conn, sqlite3.Connection):
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
    else:
        conn.execute("SELECT id FROM exam_papers WHERE id=? FOR UPDATE", (str(paper_id),)).fetchone()


def exam_paper_revision(paper):
    return hashlib.sha256(json.dumps(dict(paper), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str).encode()).hexdigest()


def _assert_revision(paper, expected):
    if expected is not None and expected != exam_paper_revision(paper):
        raise HTTPException(409, "试卷内容或属性已变化，请重新读取并核对后再操作。")


def _normalize_exam_open_scope(value: Any, default: str = SCOPE_DEPARTMENT) -> str:
    scope = normalize_scope_level(value, default=default)
    return scope if scope in EXAM_OPEN_SCOPES else default


def _exam_scope_label(scope_level: Any) -> str:
    return EXAM_SCOPE_LABELS.get(_normalize_exam_open_scope(scope_level, default=SCOPE_PRIVATE), "私有")


def _get_exam_paper_for_teacher(conn, paper_id: str, teacher_id: int, *, manage: bool = False) -> dict[str, Any]:
    paper = conn.execute("SELECT * FROM exam_papers WHERE id = ?", (paper_id,)).fetchone()
    if not paper:
        raise HTTPException(404, "试卷不存在")
    paper_dict = dict(paper)
    allowed = (
        teacher_can_manage_exam_paper(conn, teacher_id, paper_dict)
        if manage
        else teacher_can_use_exam_paper(conn, teacher_id, paper_dict)
    )
    if not allowed:
        raise HTTPException(403, "无权操作此试卷")
    if is_personal_stage_exam_paper(conn, paper_id):
        _hide_personal_stage_asset()
    return paper_dict


def _auto_add_class_name_tag(conn, paper_row: sqlite3.Row, class_id: int) -> None:
    """自动将课堂名称添加为试卷标签（去重）。"""
    class_row = conn.execute("SELECT name FROM classes WHERE id = ?", (class_id,)).fetchone()
    if not class_row:
        return
    class_name = class_row["name"].strip()
    if not class_name or len(class_name) > 10:
        return

    try:
        existing_tags = json.loads(paper_row["tags_json"]) if paper_row["tags_json"] else []
    except (json.JSONDecodeError, TypeError):
        existing_tags = []

    if class_name not in existing_tags:
        existing_tags.append(class_name)
        conn.execute(
            "UPDATE exam_papers SET tags_json = ? WHERE id = ?",
            (json.dumps(existing_tags, ensure_ascii=False), paper_row["id"]),
        )


def _count_exam_assignments(conn, paper_id: str) -> int:
    row = conn.execute("SELECT COUNT(*) FROM assignments WHERE exam_paper_id = ?", (str(paper_id),)).fetchone()
    return int(row[0] or 0) if row else 0


def _count_exam_submissions(conn, paper_id: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        WHERE a.exam_paper_id = ?
        """,
        (str(paper_id),),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _count_exam_drafts(conn, paper_id: str) -> int:
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM submission_drafts sd
            JOIN assignments a ON a.id = sd.assignment_id
            WHERE a.exam_paper_id = ?
            """,
            (str(paper_id),),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] or 0) if row else 0


def _sync_exam_assignment_content(conn, *, paper_id: str, title: str, description: str, exam_data: dict[str, Any], grading_inputs_changed: bool = True) -> int:
    rubric_md = build_exam_rubric_md(
        title=title,
        description=description,
        exam_data=exam_data,
        require_complete=True,
    )
    requirements_md = f"**试卷**: {title}\n\n{description or ''}"
    cursor = conn.execute(
        """
        UPDATE assignments
        SET title = COALESCE(NULLIF(title, ''), ?),
            requirements_md = ?,
            rubric_md = ?
        WHERE exam_paper_id = ?
        """,
        (title, requirements_md, rubric_md, str(paper_id)),
    )
    if grading_inputs_changed:
        from .ai_grading_service import invalidate_assignment_grading_inputs
        assignment_ids = [row["id"] for row in conn.execute("SELECT id FROM assignments WHERE exam_paper_id = ?", (str(paper_id),)).fetchall()]
        invalidate_assignment_grading_inputs(conn, assignment_ids)
    return int(cursor.rowcount or 0)


def create_exam_paper_record(conn, *, teacher_id: int, data: dict) -> dict:
    if not isinstance(data, dict) or not str(data.get("title") or "").strip():
        raise HTTPException(400, "试卷标题不能为空")
    if not isinstance(data.get("config", {}), dict):
        raise HTTPException(400, "试卷配置必须是对象")
    paper_id = data.get('id') or str(uuid.uuid4())
    now = datetime.now().isoformat()
    scope_level = _normalize_exam_open_scope(data.get("scope_level"), default=SCOPE_DEPARTMENT)
    try:
        questions_payload = normalize_exam_scoring_payload(data.get('questions', {"pages": []}))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    teacher_scope = load_teacher_org_scope(conn, int(teacher_id))
    conn.execute(
        """INSERT INTO exam_papers (
                id, teacher_id, title, description, questions_json, exam_config_json, status,
                owner_role, owner_user_pk, scope_level, school_code, school_name, college, department,
                created_at, updated_at
           )
           VALUES (?, ?, ?, ?, ?, ?, ?, 'teacher', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (paper_id, teacher_id, data['title'], data.get('description', ''),
         json.dumps(questions_payload, ensure_ascii=False),
         json.dumps(data.get('config', {}), ensure_ascii=False),
         data.get('status', 'draft'),
         teacher_id,
         scope_level,
         teacher_scope["school_code"],
         teacher_scope["school_name"],
         teacher_scope["college"],
         teacher_scope["department"],
         now, now)
    )

    return {"status": "success", "paper_id": paper_id}


def update_exam_content_record(conn, *, paper_id: str, teacher_id: int, payload: dict, unassigned_only: bool = False) -> dict:
    if not isinstance(payload, dict):
        raise HTTPException(400, "请求数据格式错误")
    title = str(payload.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "试卷标题不能为空")
    description = str(payload.get("description") or "").strip()
    try:
        questions_payload = normalize_exam_scoring_payload(payload.get("questions", {"pages": []}))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    config_payload = payload.get("config", {})
    if not isinstance(config_payload, dict):
        raise HTTPException(400, "试卷配置必须是对象")

    lock_exam_paper(conn, paper_id)
    paper = ensure_teacher_can_manage_exam_attributes(conn, paper_id, int(teacher_id))
    _assert_revision(paper, payload.get("expected_paper_revision"))
    if unassigned_only and _count_exam_assignments(conn, paper_id):
        raise HTTPException(409, "试卷已经布置；请创建新版本后修改，避免影响学生答题。")
    submission_count = _count_exam_submissions(conn, paper_id)
    draft_count = _count_exam_drafts(conn, paper_id)
    if submission_count > 0 or draft_count > 0:
        raise HTTPException(
            409,
            "试卷已有学生提交或草稿，不能原地修改题目、分值和评分标准；请创建新版本后再编辑。",
        )
    assignment_count = _count_exam_assignments(conn, paper_id)
    synced_assignment_count = 0
    if assignment_count > 0:
        try:
            complete_questions = normalize_exam_scoring_payload(questions_payload, require_complete=True)
            synced_assignment_count = _sync_exam_assignment_content(
                conn,
                paper_id=paper_id,
                title=title,
                description=description,
                exam_data=complete_questions,
                grading_inputs_changed=(complete_questions != normalize_exam_scoring_payload(json.loads(paper["questions_json"] or "{}"))
                                        or description != str(paper["description"] or "")),
            )
            questions_payload = complete_questions
        except ValueError as exc:
            raise HTTPException(
                400,
                f"试卷已分配到课堂，修改内容前必须补齐评分标准：{exc}",
            ) from exc
    conn.execute(
        """
        UPDATE exam_papers
        SET title = ?,
            description = ?,
            questions_json = ?,
            exam_config_json = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            title,
            description,
            json.dumps(questions_payload, ensure_ascii=False),
            json.dumps(config_payload, ensure_ascii=False),
            datetime.now().isoformat(),
            str(paper["id"]),
        ),
    )
    refreshed = ensure_teacher_can_view_exam_attributes(conn, paper_id, int(teacher_id))
    content = serialize_exam_content(conn, refreshed, int(teacher_id))
    return {"status": "success", "message": "试卷内容已保存", "synced_assignment_count": synced_assignment_count, "content": content, "paper_revision": exam_paper_revision(refreshed)}


def assign_exam_paper_record(conn, *, paper_id: str, teacher_id: int, data: dict) -> dict:
    try:
        assessment_kind = normalize_assessment_kind(data.get("assessment_kind"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    class_offering_id = data.get('class_offering_id')
    if not class_offering_id:
        raise HTTPException(400, "请指定课堂")
    learning_stage_key = _get_learning_stage_key(data, class_offering_id=class_offering_id)
    try:
        schedule_fields = build_assignment_schedule_fields(
            data,
            default_status="published",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    lock_exam_paper(conn, paper_id)
    paper = _get_exam_paper_for_teacher(conn, paper_id, int(teacher_id))
    _assert_revision(paper, data.get("expected_paper_revision"))

    # 获取课堂信息
    offering = conn.execute(
        "SELECT * FROM class_offerings WHERE id = ? AND teacher_id = ?",
        (class_offering_id, teacher_id)
    ).fetchone()
    if not offering:
        raise HTTPException(404, "课堂不存在或无权操作")

    # 创建作业记录
    existing_assignment = conn.execute(
        "SELECT id FROM assignments WHERE exam_paper_id = ? AND class_offering_id = ?",
        (paper_id, int(class_offering_id))
    ).fetchone()
    if existing_assignment:
        raise HTTPException(409, "该试卷已添加到当前课堂，请勿重复发布")

    created_at = datetime.now().isoformat()
    try:
        paper_questions = json.loads(paper["questions_json"] or "{}")
        if not isinstance(paper_questions, dict):
            paper_questions = {"pages": []}
        paper_questions = normalize_exam_scoring_payload(paper_questions, require_complete=True)
        exam_rubric_md = build_exam_rubric_md(
            title=str(paper["title"] or ""),
            description=str(paper["description"] or ""),
            exam_data=paper_questions,
            require_complete=True,
        )
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise HTTPException(
            400,
            f"试卷评分标准不完整，请先回到试卷编辑器补齐标准答案、分值、评分指导和扣分点：{exc}",
        ) from exc

    if teacher_can_manage_exam_paper(conn, int(teacher_id), paper):
        conn.execute(
            "UPDATE exam_papers SET questions_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(paper_questions, ensure_ascii=False), created_at, paper_id),
        )

    allowed_file_types_json = encode_allowed_file_types_json(_get_allowed_file_types(data))
    new_assignment_id = insert_and_get_id(
        conn,
        """
        INSERT INTO assignments (
            course_id, title, status, requirements_md, rubric_md, grading_mode,
            exam_paper_id, class_offering_id, created_at, allowed_file_types_json,
            availability_mode, starts_at, due_at, duration_minutes, auto_close, closed_at,
            late_submission_enabled, late_submission_until, late_penalty_strategy,
            late_penalty_interval_hours, late_penalty_points, late_penalty_min_score, late_score_cap,
            learning_stage_key
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(offering['course_id']),
            data.get('title', paper['title']),
            schedule_fields["status"],
            f"**试卷**: {paper['title']}\n\n{paper['description'] or ''}",
            exam_rubric_md,
            'ai',
            paper_id,
            int(class_offering_id),
            created_at,
            allowed_file_types_json,
            schedule_fields["availability_mode"],
            schedule_fields["starts_at"],
            schedule_fields["due_at"],
            schedule_fields["duration_minutes"],
            schedule_fields["auto_close"],
            schedule_fields["closed_at"],
            schedule_fields["late_submission_enabled"],
            schedule_fields["late_submission_until"],
            schedule_fields["late_penalty_strategy"],
            schedule_fields["late_penalty_interval_hours"],
            schedule_fields["late_penalty_points"],
            schedule_fields["late_penalty_min_score"],
            schedule_fields["late_score_cap"],
            learning_stage_key,
        )
    )
    classification = initialize_assignment_assessment_kind(
        conn, new_assignment_id, assessment_kind=assessment_kind, teacher_id=int(teacher_id), source="teacher_assign",
    )
    if schedule_fields["status"] == "published":
        try:
            create_assignment_published_notifications(
                conn,
                new_assignment_id,
                send_email_notification=_wants_assignment_email_notification(data),
            )
        except Exception as exc:
            print(f"[MESSAGE_CENTER] exam assignment publish notify failed: {exc}")

    # 自动将课堂名称添加为试卷标签
    _auto_add_class_name_tag(conn, paper, offering['class_id'])

    sync_assignment_due_reminders(
        conn,
        new_assignment_id,
        status=schedule_fields["status"],
        due_at=schedule_fields["due_at"],
        class_offering_id=class_offering_id,
        title=str(data.get('title') or paper['title'] or ''),
    )

    conn.execute("SELECT 1").fetchone()  # Surface aborted PostgreSQL hooks before claiming success.
    return {
        "status": "success",
        "assignment_id": new_assignment_id,
        **{key: value for key, value in classification.items() if key not in {"assignment_id", "changed"}},
        "assignment_status": schedule_fields["status"],
        "due_at": schedule_fields["due_at"],
        "message": "试卷已成功发布到当前课堂"
    }


def get_exam_review(conn, *, paper_id: str, teacher_id: int) -> dict:
    paper = _get_exam_paper_for_teacher(conn, paper_id, teacher_id)
    return {"status": "success", "paper": {key: paper.get(key) for key in
        ("id", "title", "description", "questions_json", "exam_config_json", "status", "scope_level", "tags_json")},
        "paper_revision": exam_paper_revision(paper),
        "can_manage": teacher_can_manage_exam_paper(conn, teacher_id, paper),
        "assigned_count": _count_exam_assignments(conn, paper_id),
        "authoring_policy": "Edit unassigned drafts; create a new version after assignment."}


def list_exam_reviews(conn, *, teacher_id: int, limit: int = 30, offset: int = 0, q: str = "") -> dict:
    if not 1 <= limit <= 50 or not 0 <= offset <= 10000 or len(q) > 200:
        raise HTTPException(400, "试卷目录分页或检索范围不正确。")
    admin = conn.execute("SELECT COALESCE(is_super_admin,0) FROM teachers WHERE id=?", (teacher_id,)).fetchone()
    term = '%' + q.strip().replace('~', '~~').replace('%', '~%').replace('_', '~_') + '%'
    # Page the candidate scan itself; inaccessible rows never appear in output.
    # A page may be empty yet have a next offset. No unbounded org scans/N+1s.
    rows = conn.execute("""SELECT ep.* FROM exam_papers ep
        WHERE (?=1 OR ep.teacher_id=? OR COALESCE(ep.scope_level,'private')!='private')
          AND NOT EXISTS (SELECT 1 FROM learning_stage_exam_attempts lsea WHERE lsea.exam_paper_id=ep.id)
          AND ep.title LIKE ? ESCAPE '~'
        ORDER BY ep.updated_at DESC,ep.id LIMIT ? OFFSET ?""",
        (int(bool(admin and admin[0])), teacher_id, term, limit + 1, offset)).fetchall()
    visible = []
    for row in rows[:limit]:
        paper = dict(row)
        if teacher_can_use_exam_paper(conn, teacher_id, paper):
            visible.append({**{key: paper.get(key) for key in ("id", "title", "description", "status", "scope_level", "updated_at")},
                "can_manage": teacher_can_manage_exam_paper(conn, teacher_id, paper)})
    return {"status": "success", "papers": visible, "has_more": len(rows) > limit, "next_offset": offset + min(len(rows), limit)}
