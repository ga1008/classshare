"""On-demand, owner-authorized snapshots of existing classroom conversations."""
import json
import uuid
from datetime import datetime

from fastapi import HTTPException

from .ai_workspace_service import owned_session
from .psych_profile_service import sanitize_hidden_profile_leaks
from .resource_access_service import ensure_classroom_access


def list_legacy_sessions(conn, user):
    rows = conn.execute('''SELECT id,session_uuid,title,class_offering_id,created_at
        FROM ai_chat_sessions WHERE user_pk=? AND user_role=?
        ORDER BY created_at DESC,id DESC LIMIT 100''', (user['id'], user['role'])).fetchall()
    classrooms, sessions = {}, []
    for row in rows:
        offering_id = int(row['class_offering_id'])
        # Reuse the canonical classroom policy once per distinct offering,
        # never once per conversation/message. A revoked class is not listed.
        if offering_id not in classrooms:
            try:
                classrooms[offering_id] = ensure_classroom_access(conn, offering_id, user)
            except HTTPException as exc:
                if exc.status_code not in (403, 404):
                    raise
                classrooms[offering_id] = None
        classroom = classrooms[offering_id]
        if classroom is not None:
            sessions.append({**dict(row), 'class_label': classroom['class_name'],
                             'course_name': classroom['course_name'], 'source': 'classroom'})
    return sessions


def import_legacy_session(conn, legacy_uuid, user, *, message_decoder):
    legacy = conn.execute('''SELECT * FROM ai_chat_sessions
        WHERE session_uuid=? AND user_pk=? AND user_role=?''',
        (legacy_uuid, user['id'], user['role'])).fetchone()
    if legacy is None:
        raise HTTPException(403, '会话不存在或无权访问')
    ensure_classroom_access(conn, int(legacy['class_offering_id']), user)
    session_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL,
        f"lanshare:workspace:legacy:{user['role']}:{user['id']}:{legacy_uuid}"))
    stamp = datetime.now().isoformat()
    try:
        created = conn.execute('''INSERT INTO ai_workspace_sessions
            (session_uuid,user_pk,user_role,title,created_at,updated_at) VALUES(?,?,?,?,?,?)
            ON CONFLICT(session_uuid) DO NOTHING''',
            (session_uuid, user['id'], user['role'], legacy['title'] or '课堂对话',
             legacy['created_at'] or stamp, stamp)).rowcount
        session = owned_session(conn, session_uuid, user)
        if created:
            # Stream bounded batches; one transaction makes concurrent imports
            # observe either the complete snapshot or no snapshot. Imported
            # history does not count as new model rounds or copy hidden prompts.
            cursor = conn.execute('''SELECT * FROM ai_chat_messages
                WHERE session_id=? AND role IN ('user','assistant') ORDER BY id ASC''', (legacy['id'],))
            while True:
                rows = cursor.fetchmany(200)
                if not rows:
                    break
                for row in rows:
                    try:
                        attachments = json.loads(row['attachments_json'] or '[]')
                        if not isinstance(attachments, list):
                            attachments = []
                    except (TypeError, ValueError):
                        attachments = []
                    message = message_decoder(row['message'], row['final_answer'])
                    thinking = row['thinking_content']
                    final_answer = row['final_answer'] or message
                    if row['role'] == 'assistant':
                        message = sanitize_hidden_profile_leaks(message)
                        thinking = sanitize_hidden_profile_leaks(thinking)
                        final_answer = sanitize_hidden_profile_leaks(final_answer)
                    conn.execute('''INSERT INTO ai_workspace_messages
                        (session_id,request_id,role,message,attachments_json,thinking_content,final_answer,created_at,status)
                        VALUES(?,?,?,?,?,?,?,?,'completed')''',
                        (session['id'], f"legacy:{row['id']}", row['role'], message,
                         json.dumps(attachments, ensure_ascii=False), thinking,
                         final_answer, row['timestamp'] or stamp))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {key: session[key] for key in ('id', 'session_uuid', 'title', 'created_at', 'updated_at')}
