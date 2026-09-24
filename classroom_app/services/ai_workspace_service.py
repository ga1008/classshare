"""Private assistant conversations and bounded, asynchronous profile refreshes."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException

from ..database import get_db_connection
from ..db.connection import execute_insert_returning_id
from .psych_profile_service import sanitize_hidden_profile_leaks

PROFILE_ROUND_INTERVAL = 4
REQUEST_LEASE_SECONDS = 300


def _now():
    return datetime.now().isoformat()


def owned_session(conn, session_uuid, user):
    row = conn.execute('SELECT * FROM ai_workspace_sessions WHERE session_uuid=? AND user_pk=? AND user_role=?',
                       (session_uuid, user['id'], user['role'])).fetchone()
    if not row:
        raise HTTPException(403, '会话不存在或无权访问')
    return dict(row)


def list_sessions(conn, user):
    return [dict(row) for row in conn.execute('''
        SELECT id,session_uuid,title,created_at,updated_at FROM ai_workspace_sessions
        WHERE user_pk=? AND user_role=? ORDER BY updated_at DESC,id DESC LIMIT 100
    ''', (user['id'], user['role'])).fetchall()]


def create_session(conn, user):
    stamp, session_uuid = _now(), str(uuid.uuid4())
    session_id = execute_insert_returning_id(conn, '''
        INSERT INTO ai_workspace_sessions(session_uuid,user_pk,user_role,title,created_at,updated_at)
        VALUES(?,?,?,'新对话',?,?)
    ''', (session_uuid, user['id'], user['role'], stamp, stamp))
    conn.commit()
    return {'id': session_id, 'session_uuid': session_uuid, 'title': '新对话', 'created_at': stamp}


def delete_session(conn, session_uuid, user):
    from .ai_workspace_attachment_service import (
        delete_session_images, referenced_session_images, remove_empty_session_folder,
    )

    owned_session(conn, session_uuid, user)
    try:
        # Share admission's account lock, then reread the lease: a request may
        # have started while this deletion waited for another worker.
        conn.execute('''INSERT INTO ai_workspace_profile_states(user_pk,user_role) VALUES(?,?)
                        ON CONFLICT(user_role,user_pk) DO NOTHING''', (user['id'], user['role']))
        conn.execute('UPDATE ai_workspace_profile_states SET completed_rounds=completed_rounds WHERE user_pk=? AND user_role=?',
                     (user['id'], user['role']))
        session = owned_session(conn, session_uuid, user)
        if session.get('active_request_id') and str(session.get('request_started_at') or '') >= (datetime.now() - timedelta(seconds=REQUEST_LEASE_SECONDS)).isoformat():
            raise HTTPException(409, '这个对话仍在回复中，请等待回复结束后删除。')
        paths = set()
        for row in conn.execute("SELECT attachments_json FROM ai_workspace_messages WHERE session_id=? AND attachments_json<>'[]'", (session['id'],)):
            try:
                attachments = json.loads(row['attachments_json'] or '[]')
                if not isinstance(attachments, list):
                    raise ValueError()
            except (TypeError, ValueError):
                raise HTTPException(503, '附件记录暂时无法读取，对话尚未删除。') from None
            paths.update(referenced_session_images(user, session_uuid, attachments))
        # Exercise DB constraints before removing files. On an I/O failure the
        # transaction rolls back, retaining exact references for a safe retry.
        conn.execute('DELETE FROM ai_workspace_messages WHERE session_id=?', (session['id'],))
        conn.execute('DELETE FROM ai_workspace_sessions WHERE id=? AND user_pk=? AND user_role=?',
                     (session['id'], user['id'], user['role']))
        delete_session_images(paths)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    remove_empty_session_folder(user, session_uuid)
    return {'status': 'success'}


def load_history(conn, session_uuid, user):
    session = owned_session(conn, session_uuid, user)
    rows = conn.execute('''SELECT role,message,attachments_json,thinking_content,final_answer,
                           created_at AS timestamp,request_id,status FROM ai_workspace_messages
                           WHERE session_id=? ORDER BY id DESC LIMIT 200''', (session['id'],)).fetchall()
    messages = []
    for row in reversed(rows):
        message = dict(row)
        message['attachments'] = json.loads(message.pop('attachments_json') or '[]')
        messages.append(message)
    pending = session.get('active_request_id')
    started = str(session.get('request_started_at') or '')
    if pending and started < (datetime.now() - timedelta(seconds=REQUEST_LEASE_SECONDS)).isoformat():
        pending = None
    return {'status': 'success', 'messages': messages,
            'pending_request_id': pending, 'pending': bool(pending)}


def begin_request(conn, session_uuid, user, request_id, message, attachments, fingerprint_payload, *, image_uploads=None):
    session = owned_session(conn, session_uuid, user)
    conn.execute('''INSERT INTO ai_workspace_profile_states(user_pk,user_role) VALUES(?,?)
                    ON CONFLICT(user_role,user_pk) DO NOTHING''', (user['id'], user['role']))
    # A short row lock serializes the account across tabs and application
    # workers. No lock is held during network/model work.
    conn.execute('UPDATE ai_workspace_profile_states SET completed_rounds=completed_rounds WHERE user_pk=? AND user_role=?',
                 (user['id'], user['role']))
    request_id = str(request_id or uuid.uuid4()).strip()
    if not 1 <= len(request_id) <= 100:
        raise HTTPException(400, '请求标识无效')
    if image_uploads:
        fingerprint_payload = {**fingerprint_payload, 'image_originals': [
            {'index': image['attachment_index'], 'sha256': hashlib.sha256(image['contents']).hexdigest()}
            for image in image_uploads
        ]}
    fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    previous = conn.execute('''SELECT status,request_fingerprint FROM ai_workspace_messages
                               WHERE session_id=? AND request_id=? AND role='user' ''',
                            (session['id'], request_id)).fetchone()
    if previous:
        if previous['request_fingerprint'] != fingerprint:
            raise HTTPException(409, '同一个请求标识不能用于不同的消息')
        if previous['status'] == 'completed':
            return {'replay': True, 'request_id': request_id, 'session_id': session['id']}
        if previous['status'] == 'running' and session.get('active_request_id') == request_id and str(session.get('request_started_at') or '') >= (datetime.now()-timedelta(seconds=REQUEST_LEASE_SECONDS)).isoformat():
            return {'pending': True, 'request_id': request_id, 'session_id': session['id']}
    stamp = _now()
    other = conn.execute('''SELECT id FROM ai_workspace_sessions WHERE user_pk=? AND user_role=? AND id<>?
        AND active_request_id IS NOT NULL AND request_started_at>=? LIMIT 1''',
        (user['id'], user['role'], session['id'], (datetime.now()-timedelta(seconds=REQUEST_LEASE_SECONDS)).isoformat())).fetchone()
    if other:
        raise HTTPException(409, '你的另一个对话仍在回复中，请稍后查看')
    acquired = conn.execute('''UPDATE ai_workspace_sessions SET active_request_id=?,request_started_at=?,updated_at=?
        WHERE id=? AND (active_request_id IS NULL OR request_started_at<?)''',
        (request_id, stamp, stamp, session['id'], (datetime.now()-timedelta(seconds=REQUEST_LEASE_SECONDS)).isoformat()))
    if not acquired.rowcount:
        raise HTTPException(409, '这个对话仍在回复中，请稍后查看')
    from .ai_workspace_attachment_service import remove_created, save_request_images
    created_images = []
    try:
        attachments, created_images = save_request_images(user, session_uuid, request_id, attachments, image_uploads or [])
        conn.execute('''INSERT INTO ai_workspace_messages(session_id,request_id,role,message,attachments_json,created_at,status,request_fingerprint)
            VALUES(?,?,'user',?,?,?,'running',?) ON CONFLICT(session_id,request_id,role)
            DO UPDATE SET status='running',created_at=excluded.created_at,attachments_json=excluded.attachments_json''',
            (session['id'], request_id, message, json.dumps(attachments, ensure_ascii=False), stamp, fingerprint))
        conn.execute("UPDATE ai_workspace_sessions SET title=? WHERE id=? AND title='新对话'", (message[:36], session['id']))
        conn.commit()
    except Exception:
        conn.rollback()
        remove_created(created_images)
        raise
    return {'session_id': session['id'], 'request_id': request_id}


def finish_request(conn, session_id, request_id, user, answer, thinking='', *, success):
    # Conditional transition is also the cadence idempotency barrier. Neither
    # replays nor failed attempts increment completed_rounds.
    session = conn.execute('''SELECT id FROM ai_workspace_sessions WHERE id=? AND user_pk=? AND user_role=? AND active_request_id=?''',
                           (session_id, user['id'], user['role'], request_id)).fetchone()
    if not session:
        return False
    status = 'completed' if success else 'failed'
    updated = conn.execute("UPDATE ai_workspace_messages SET status=? WHERE session_id=? AND request_id=? AND role='user' AND status='running'",
                           (status, session_id, request_id))
    if not updated.rowcount:
        return False
    safe_answer = sanitize_hidden_profile_leaks(answer)
    conn.execute('''INSERT INTO ai_workspace_messages(session_id,request_id,role,message,thinking_content,final_answer,created_at,status)
        VALUES(?,?,'assistant',?,?,?,?,?) ON CONFLICT(session_id,request_id,role)
        DO UPDATE SET message=excluded.message,thinking_content=excluded.thinking_content,final_answer=excluded.final_answer,status=excluded.status''',
        (session_id, request_id, safe_answer, sanitize_hidden_profile_leaks(thinking), safe_answer, _now(), status))
    conn.execute('''UPDATE ai_workspace_sessions SET active_request_id=NULL,request_started_at=NULL,updated_at=? WHERE id=? AND active_request_id=?''',
                 (_now(), session_id, request_id))
    if success:
        conn.execute('''INSERT INTO ai_workspace_profile_states(user_pk,user_role,completed_rounds) VALUES(?,?,1)
            ON CONFLICT(user_role,user_pk) DO UPDATE SET completed_rounds=ai_workspace_profile_states.completed_rounds+1''',
                     (user['id'], user['role']))
    conn.commit()
    return True


def ai_history(conn, session_id, request_id):
    rows = conn.execute('''SELECT role,message,final_answer FROM ai_workspace_messages
        WHERE session_id=? AND request_id<>? AND status='completed' ORDER BY id DESC LIMIT 40''',
                        (session_id, request_id)).fetchall()
    # Explicit budget keeps long-lived user conversations cheap and bounded.
    messages, size = [], 0
    for row in rows:
        content = str(row['final_answer'] or row['message'] or '')[:6000]
        if size + len(content) > 24000:
            break
        messages.append({'role': row['role'], 'content': content})
        size += len(content)
    return list(reversed(messages))


def completed_reply(conn, session_id, request_id):
    # session_id is from the ownership-checked begin_request, not client input.
    row = conn.execute("SELECT message,final_answer FROM ai_workspace_messages WHERE session_id=? AND request_id=? AND role='assistant' AND status='completed'",
                       (session_id, request_id)).fetchone()
    return dict(row) if row else {'message': '', 'final_answer': ''}


def load_personal_hidden_profile(conn, user_pk, user_role):
    """Personal strategy may follow the account across classrooms; never peers."""
    row = conn.execute('SELECT profile_json,profile_updated_at FROM ai_workspace_profile_states WHERE user_pk=? AND user_role=?',
                       (user_pk, user_role)).fetchone()
    candidates = []
    if row and row['profile_updated_at']:
        profile = json.loads(row['profile_json'] or '{}')
        candidates.append((str(row['profile_updated_at']), profile))
    for table in ('classroom_behavior_profiles', 'ai_psychology_profiles'):
        row = conn.execute(f'SELECT * FROM {table} WHERE user_pk=? AND user_role=? ORDER BY created_at DESC,id DESC LIMIT 1',
                           (user_pk, user_role)).fetchone()
        if row:
            candidates.append((str(row['created_at'] or ''), dict(row)))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def claim_due_profile_candidates(limit=1):
    now = _now()
    with get_db_connection() as conn:
        rows = conn.execute('''SELECT user_pk,user_role,completed_rounds FROM ai_workspace_profile_states
            WHERE completed_rounds-profiled_rounds>=? AND next_due_at<=?
              AND (claim_token IS NULL OR lease_until<?) ORDER BY next_due_at,user_role,user_pk LIMIT ?''',
                            (PROFILE_ROUND_INTERVAL, now, now, limit)).fetchall()
        claimed = []
        for row in rows:
            token = str(uuid.uuid4())
            changed = conn.execute('''UPDATE ai_workspace_profile_states SET claim_token=?,claimed_rounds=?,lease_until=?
                WHERE user_pk=? AND user_role=? AND (claim_token IS NULL OR lease_until<?)
                  AND completed_rounds-profiled_rounds>=?''',
                (token, row['completed_rounds'], (datetime.now()+timedelta(minutes=5)).isoformat(),
                 row['user_pk'], row['user_role'], now, PROFILE_ROUND_INTERVAL))
            if changed.rowcount:
                claimed.append({**dict(row), 'claim_token': token})
        conn.commit()
    return claimed


async def run_profile_candidate(candidate):
    from ..core import ai_client
    from .ai_gateway_service import ai_gateway_post
    from .behavior_tracking_service import _build_behavior_profile_prompt
    from .psych_profile_service import build_explicit_user_profile_prompt, load_explicit_user_profile, normalize_psych_profile_payload

    pk, role = candidate['user_pk'], candidate['user_role']
    profile = None
    try:
        with get_db_connection() as conn:
            explicit = load_explicit_user_profile(conn, pk, role)
            previous = load_personal_hidden_profile(conn, pk, role)
            rows = conn.execute('''SELECT m.role,m.message FROM ai_workspace_messages m
                JOIN ai_workspace_sessions s ON s.id=m.session_id
                WHERE s.user_pk=? AND s.user_role=? AND m.status='completed'
                ORDER BY m.id DESC LIMIT 24''', (pk, role)).fetchall()
        prompt = _build_behavior_profile_prompt(
            class_summary='平台个人助手对话', class_ai_config={}, user_name=explicit.get('name', ''),
            user_role=role, current_description=explicit.get('description', ''),
            explicit_profile_prompt=build_explicit_user_profile_prompt(explicit), previous_hidden_profile=previous,
            behavior_transcript='\n'.join(f"{row['role']}: {row['message'][:1500]}" for row in reversed(rows))[:24000],
            presence_summary='', login_audit_summary='',
        )
        response = await ai_gateway_post(ai_client, '/api/ai/chat', json_payload={
            'system_prompt': '你是一名学习支持分析师，只允许输出合法 JSON。', 'messages': [],
            'new_message': prompt, 'model_capability': 'thinking', 'task_type': 'deep_text_reasoning',
            'response_format': 'json', 'task_priority': 'background', 'task_label': 'behavior_profile',
            'web_search_enabled': False,
        }, timeout=180.0, task_type='behavior_profile', priority='P1',
            student_id=pk if role=='student' else None, teacher_id=pk if role=='teacher' else None,
            source_ref=f'workspace-profile:{role}:{pk}:{candidate["completed_rounds"]}')
        response.raise_for_status()
        payload = response.json()
        if payload.get('status') != 'success' or not isinstance(payload.get('response_json'), dict):
            raise ValueError('Invalid profile response')
        profile = normalize_psych_profile_payload(payload['response_json'])
        if not any(profile.get(key) for key in ('profile_summary', 'support_strategy', 'hidden_premise_prompt')):
            raise ValueError('Empty profile response')
    except Exception:
        # The interactive reply already succeeded; retry via the existing
        # scheduler with a backoff, without exposing internal work to users.
        profile = None
    finally:
        with get_db_connection() as conn:
            if profile:
                conn.execute('''UPDATE ai_workspace_profile_states SET profile_json=?,profile_updated_at=?,
                    profiled_rounds=claimed_rounds,claim_token=NULL,lease_until=NULL,next_due_at=''
                    WHERE user_pk=? AND user_role=? AND claim_token=?''',
                    (json.dumps(profile, ensure_ascii=False), _now(), pk, role, candidate['claim_token']))
            else:
                conn.execute('''UPDATE ai_workspace_profile_states SET claim_token=NULL,lease_until=NULL,next_due_at=?
                    WHERE user_pk=? AND user_role=? AND claim_token=?''',
                    ((datetime.now()+timedelta(minutes=5)).isoformat(), pk, role, candidate['claim_token']))
            conn.commit()
