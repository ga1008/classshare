"""One server-owned availability policy for assistant templates and APIs."""
import json
import re
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException

from .assessment_ai_access import student_exam_accepts_answers

_ASSESSMENT_PATH = re.compile(r"^/(?:api/)?(?:mp/)?(?:assignments?|submissions?|exam(?:s)?|exam-papers)(?:/|$)", re.I)
_EXAM_TAKE_PATH = re.compile(r"^/exam/take/([^/]+)$", re.I)


def _path(value):
    # Route matching is case-insensitive; stored assignment IDs are not.
    return unquote(urlsplit(str(value or '')).path).rstrip('/') or '/'


def ai_workspace_policy(user, request=None):
    user = user if isinstance(user, dict) else {}
    role, pk = str(user.get('role') or ''), int(user.get('id') or 0)
    path = _path(request.url.path if request is not None else '/')
    allowed = role in {'student', 'teacher'} and pk > 0
    reason = '' if allowed else 'unauthenticated'
    if role == 'student' and _ASSESSMENT_PATH.match(path):
        allowed, reason = False, 'assessment_page'
        # Only the exam route can attach this server-verified state. Template
        # variables or client-supplied flags cannot authorize the shared mount.
        access = getattr(getattr(request, 'state', None), 'exam_ai_access', None)
        if isinstance(access, dict) and access.get('allowed') and access.get('user_key') == f'{role}:{pk}' and access.get('page_path') == path:
            allowed, reason = True, ''
    return {'allowed': allowed, 'user_key': f'{role}:{pk}' if pk else '', 'page_path': path, 'reason': reason}


def ensure_ai_workspace_access(conn, user, request, *, page_path='', extra_context=''):
    policy = ai_workspace_policy(user, request)
    if not policy['allowed'] and policy['reason'] != 'assessment_page':
        raise HTTPException(403, '当前页面不能使用 AI 助手')
    if user['role'] != 'student':
        return
    # Reject every supplied source independently: a harmless client path must
    # never override an assessment Referer or assessment context.
    document_path = request.url.path if request is not None else ''
    # A document navigation's Referer is the previous page (including submission
    # review -> returned exam). APIs still check all claimed current sources.
    is_exam_document = request is not None and request.method == 'GET' and _EXAM_TAKE_PATH.fullmatch(_path(document_path))
    paths = ([document_path] if is_exam_document else
             [page_path, request.headers.get('referer', '') if request is not None else '', document_path])
    paths.extend(re.findall(r"^(?:路径|当前URL|page_path|pagePath)\s*[:：]\s*(\S+)", str(extra_context or ''), re.I | re.M))
    try:
        context = json.loads(extra_context or '{}')
    except (ValueError, TypeError):
        context = {}
    assessment_context = False
    if isinstance(context, dict):
        paths.extend(context.get(key, '') for key in ('path', 'pagePath', 'page_path', 'url'))
        if isinstance(context.get('page'), dict):
            paths.extend(context['page'].get(key, '') for key in ('path', 'url'))
        assessment_context = any(str(context.get(key, '')).lower() in {'assignment', 'assignment_detail_student', 'exam', 'exam_take'}
                                 for key in ('page', 'page_key', 'pageKey', 'page_type', 'pageType'))
    assessment_paths = {_path(value) for value in paths if value and _ASSESSMENT_PATH.match(_path(value))}
    access = None
    if assessment_paths:
        # A benign path cannot override another assessment source, nor can one
        # permitted exam authorize a second exam or submission review page.
        if len(assessment_paths) != 1:
            raise HTTPException(403, '当前作业或考试未授权 AI 协助')
        match = _EXAM_TAKE_PATH.fullmatch(next(iter(assessment_paths)))
        if match:
            from .assessment_ai_access import student_exam_ai_access
            access = student_exam_ai_access(conn, user, match[1])
        if not access or not access.get('allowed'):
            raise HTTPException(403, '当前作业或考试未授权 AI 协助，或已结束作答')
    elif assessment_context:
        raise HTTPException(403, '作业和考试页面不能使用 AI 助手')
    # Page metadata is not an authorization boundary. An ongoing, timed exam
    # assigned to this student's class also blocks calls made from another tab.
    from .offering_membership_service import offering_student_where

    rows = conn.execute(f"""
        SELECT a.*, s.assignment_id AS ai_submission_assignment_id, s.status AS ai_submission_status,
               s.is_absence_score AS ai_absence_score, s.resubmission_allowed AS ai_resubmission_allowed,
               s.resubmission_due_at AS ai_resubmission_due_at, w.due_at AS ai_window_due_at
        FROM assignments a
        JOIN class_offerings co ON co.id=a.class_offering_id
        JOIN students st ON st.id=? AND {offering_student_where(offering_alias='co', student_alias='st')}
        LEFT JOIN submissions s ON s.assignment_id=a.id AND s.student_pk_id=st.id
        LEFT JOIN assignment_student_windows w ON w.assignment_id=a.id AND w.student_pk_id=st.id AND w.status='active'
        WHERE a.exam_paper_id IS NOT NULL AND a.exam_paper_id<>''
          AND a.availability_mode<>'permanent' AND a.status<>'new'
          AND (a.status<>'closed' OR COALESCE(s.resubmission_allowed,0)=1 OR w.assignment_id IS NOT NULL)
          AND (a.assessment_kind IN ('midterm','final') OR a.availability_mode='countdown')
          AND NOT EXISTS (SELECT 1 FROM learning_stage_exam_attempts e WHERE e.assignment_id=a.id AND e.student_id<>st.id)
    """, (int(user['id']),)).fetchall()
    for row in rows:
        assignment = dict(row)
        submission = ({'status': row['ai_submission_status'], 'is_absence_score': row['ai_absence_score'],
                       'resubmission_allowed': row['ai_resubmission_allowed'], 'resubmission_due_at': row['ai_resubmission_due_at']}
                      if row['ai_submission_assignment_id'] is not None else None)
        if (str(row['id']) != str((access or {}).get('assignment_id') or '')
                and student_exam_accepts_answers(assignment, submission, window_due_at=row['ai_window_due_at'])):
            raise HTTPException(403, '考试进行中，暂时不能使用 AI 助手')
    return access
