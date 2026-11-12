"""Teacher-authorized exam assistance; never trust a browser permission flag."""
from __future__ import annotations

import json
import hashlib
from copy import deepcopy
from datetime import datetime
from typing import Any

from .assignment_lifecycle_service import assignment_accepts_submissions, submission_resubmission_accepts
from .resource_access_service import student_can_read_assignment
from .late_submission_policy import parse_iso_like_datetime


def _object(value: Any) -> dict:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def configured_flag(value: dict, keys: tuple[str, ...]) -> bool:
    """An explicit canonical false overrides obsolete truthy aliases."""
    for key in keys:
        if key in value:
            raw = value[key]
            return raw is True or raw == 1 or (isinstance(raw, str) and raw.strip().lower() in {'1', 'true', 'yes', 'on'})
    return False


def without_question_ai_permissions(data: dict) -> dict:
    """Permission-only edits do not alter the locked question/grading snapshot."""
    result = deepcopy(data)
    for page in result.get('pages') or []:
        if not isinstance(page, dict):
            continue
        for question in page.get('questions') or []:
            if isinstance(question, dict):
                for key in ('allow_ai', 'allow_student_ai', 'ai_allowed'):
                    question.pop(key, None)
    return result


def student_exam_accepts_answers(assignment: dict, submission: dict | None = None, *, window_due_at=None) -> bool:
    """The same answer lifecycle controls the exam entry and cross-tab policy."""
    if str(assignment.get('status') or '').lower() == 'new':
        return False
    if submission and not submission.get('is_absence_score'):
        return submission.get('status') == 'submitted' and submission_resubmission_accepts(submission)
    if assignment_accepts_submissions(assignment):
        return True
    due = parse_iso_like_datetime(window_due_at)
    return bool(due and datetime.now() < due.replace(tzinfo=None))


def exam_assistance_scope(paper: dict) -> dict:
    data, config = _object(paper.get('questions_json')), _object(paper.get('exam_config_json'))
    whole = configured_flag(config, ('allow_student_ai', 'student_ai_enabled', 'allow_ai'))
    pages = data.get('pages')
    questions = [question for page in (pages if isinstance(pages, list) else []) if isinstance(page, dict)
                 for question in (page.get('questions') if isinstance(page.get('questions'), list) else [])
                 if isinstance(question, dict)]
    allowed = [(index, question) for index, question in enumerate(questions, 1)
               if whole or configured_flag(question, ('allow_ai', 'allow_student_ai', 'ai_allowed'))]
    ids = [str(question.get('id') or index) for index, question in allowed]
    lines = ['【教师允许 AI 协助的试卷题目】', f"试卷：{str(paper.get('title') or '')[:200]}",
             '仅协助下列已授权题目；未列出的题目没有授权，不要根据其他页面内容推断或扩展范围。']
    for index, question in allowed:
        lines.append(f"第 {index} 题 [{question.get('id') or index}]：{str(question.get('text') or '')[:2400]}")
        options = question.get('options')
        if isinstance(options, list):
            lines.extend(str(option)[:600] for option in options)
    return {'allowed': bool(allowed), 'whole_paper': whole, 'question_ids': ids, 'context': '\n'.join(lines)[:12000]}


def student_exam_ai_access(conn, user: dict, assignment_id: str) -> dict:
    denied = {'allowed': False, 'reason': 'assessment_not_authorized'}
    if user.get('role') != 'student' or not user.get('id'):
        return denied
    row = conn.execute('SELECT * FROM assignments WHERE id=?', (str(assignment_id),)).fetchone()
    if not row:
        return denied
    assignment = dict(row)
    if assignment.get('status') == 'new' or not assignment.get('exam_paper_id') or not assignment.get('class_offering_id'):
        return denied
    if not student_can_read_assignment(conn, assignment, int(user['id'])):
        return denied
    submission_row = conn.execute('SELECT * FROM submissions WHERE assignment_id=? AND student_pk_id=?',
                                  (str(assignment_id), int(user['id']))).fetchone()
    submission = dict(submission_row) if submission_row else None
    accepting = student_exam_accepts_answers(assignment, submission)
    if not accepting and (not submission or submission.get('is_absence_score')):
        from .assignment_student_window_service import active_window
        window = active_window(conn, assignment_id, user['id'])
        accepting = student_exam_accepts_answers(assignment, submission, window_due_at=(window or {}).get('due_at'))
    if not accepting:
        return {**denied, 'reason': 'assessment_readonly'}
    paper_row = conn.execute('SELECT * FROM exam_papers WHERE id=?', (assignment['exam_paper_id'],)).fetchone()
    if not paper_row:
        return denied
    scope = exam_assistance_scope(dict(paper_row))
    scope_key = hashlib.sha256(json.dumps({'assignment_id': str(assignment_id), 'question_ids': scope['question_ids'],
                                          'context': scope['context']}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {**scope, 'assignment_id': str(assignment_id), 'class_offering_id': int(assignment['class_offering_id']),
            'scope_key': scope_key, 'user_key': f"student:{int(user['id'])}", 'page_path': f'/exam/take/{assignment_id}'}
