"""One server-owned availability policy for assistant templates and APIs."""
import json
import re
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException

from .assignment_lifecycle_service import assignment_accepts_submissions

_ASSESSMENT_PATH = re.compile(r"^/(?:api/)?(?:mp/)?(?:assignments?|submissions?|exam(?:s)?|exam-papers)(?:/|$)", re.I)


def _path(value):
    return unquote(urlsplit(str(value or '')).path).lower().rstrip('/') or '/'


def ai_workspace_policy(user, request=None):
    user = user if isinstance(user, dict) else {}
    role, pk = str(user.get('role') or ''), int(user.get('id') or 0)
    path = _path(request.url.path if request is not None else '/')
    allowed = role in {'student', 'teacher'} and pk > 0
    reason = '' if allowed else 'unauthenticated'
    if role == 'student' and _ASSESSMENT_PATH.match(path):
        allowed, reason = False, 'assessment_page'
    return {'allowed': allowed, 'user_key': f'{role}:{pk}' if pk else '', 'page_path': path, 'reason': reason}


def ensure_ai_workspace_access(conn, user, request, *, page_path='', extra_context=''):
    policy = ai_workspace_policy(user, request)
    if not policy['allowed']:
        raise HTTPException(403, '当前页面不能使用 AI 助手')
    if user['role'] != 'student':
        return
    # Reject every supplied source independently: a harmless client path must
    # never override an assessment Referer or assessment context.
    paths = [page_path, request.headers.get('referer', '') if request is not None else '']
    paths.extend(re.findall(r"^(?:路径|当前URL|page_path|pagePath)\s*[:：]\s*(\S+)", str(extra_context or ''), re.I | re.M))
    try:
        context = json.loads(extra_context or '{}')
    except (ValueError, TypeError):
        context = {}
    if isinstance(context, dict):
        paths.extend(context.get(key, '') for key in ('path', 'pagePath', 'page_path', 'url'))
        if isinstance(context.get('page'), dict):
            paths.extend(context['page'].get(key, '') for key in ('path', 'url'))
        if any(str(context.get(key, '')).lower() in {'assignment', 'assignment_detail_student', 'exam', 'exam_take'}
               for key in ('page', 'page_key', 'pageKey', 'page_type', 'pageType')):
            raise HTTPException(403, '作业和考试页面不能使用 AI 助手')
    if any(_ASSESSMENT_PATH.match(_path(value)) for value in paths if value):
        raise HTTPException(403, '作业和考试页面不能使用 AI 助手')
    # Page metadata is not an authorization boundary. An ongoing, timed exam
    # assigned to this student's class also blocks calls made from another tab.
    from .offering_membership_service import offering_student_where

    rows = conn.execute(f"""
        SELECT a.* FROM assignments a
        JOIN class_offerings co ON co.id=a.class_offering_id
        JOIN students st ON st.id=? AND {offering_student_where(offering_alias='co', student_alias='st')}
        WHERE a.exam_paper_id IS NOT NULL AND a.exam_paper_id<>''
          AND a.availability_mode<>'permanent' AND a.status NOT IN ('new','closed')
          AND (a.assessment_kind IN ('midterm','final') OR a.availability_mode='countdown')
          AND NOT EXISTS (SELECT 1 FROM submissions s WHERE s.assignment_id=a.id
                          AND s.student_pk_id=st.id AND COALESCE(s.is_absence_score,0)=0)
          AND NOT EXISTS (SELECT 1 FROM learning_stage_exam_attempts e WHERE e.assignment_id=a.id AND e.student_id<>st.id)
    """, (int(user['id']),)).fetchall()
    if any(assignment_accepts_submissions(dict(row)) for row in rows):
        raise HTTPException(403, '考试进行中，暂时不能使用 AI 助手')
