"""Run feedback races in a new synthetic database on an explicitly verified offline PG cluster."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cluster-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--control-database', default='lanshare_assessment_rehearsal')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    cluster = args.cluster_dir.resolve(strict=True)
    output = args.output_dir.resolve()
    if output.exists() or output.is_relative_to(cluster):
        raise ValueError('Use a new evidence directory outside the PostgreSQL cluster')
    output.mkdir(parents=True)
    runtime = output / 'runtime-data'
    runtime.mkdir()
    os.environ.update(PYTHON_DOTENV_DISABLED='1', PYTHONUTF8='1', DB_ENGINE='postgres', DATABASE_URL='',
                      POSTGRES_POOL_ENABLED='false', POSTGRES_BACKEND_READY='true', AGENT_DSH_ENABLED='0',
                      MAIN_DATA_DIR=str(runtime), LANSHARE_DATA_ROOT=str(runtime),
                      MAIN_DB_PATH=str(runtime / 'unused.sqlite'),
                      SECRET_KEY='synthetic-feedback-concurrency-only', AI_ASSISTANT_URL='http://127.0.0.1:9')
    for name in ('MAIN_HOMEWORK_SUBMISSIONS_DIR', 'MAIN_SHARE_DIR', 'MAIN_ROSTER_DIR', 'MAIN_ATTENDANCE_DIR',
                 'MAIN_CHAT_LOG_DIR', 'MAIN_GLOBAL_FILES_DIR', 'MAIN_SIGNATURES_DIR',
                 'MAIN_TEXTBOOK_ATTACHMENT_DIR', 'MAIN_CHUNKED_UPLOADS_DIR'):
        os.environ[name] = str(runtime / name.lower())
    sys.path.insert(0, str(source))
    from fastapi import HTTPException
    from unittest.mock import patch
    from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
    from classroom_app.db.schema_feedback_conversations import ensure_feedback_conversation_schema
    from classroom_app.services import feedback_conversation_service as service
    from classroom_app.services import message_center_service
    from tools.assessment_postgres_rehearsal import connect_offline

    # Reuses the existing rehearsal boundary: exact data_directory, loopback-only
    # listener, non-default port, fixed user, and dedicated database-name prefix.
    control = connect_offline(cluster_dir=cluster, port=args.port, database=args.control_database)
    control.autocommit = True
    database = 'lanshare_assessment_rehearsal_feedback_races_' + uuid.uuid4().hex[:10]
    created = False
    finished = False
    checks = {}

    def connection():
        raw = connect_offline(cluster_dir=cluster, port=args.port, database=database)
        raw.row_factory = sqlite_compatible_dict_row
        return LanSharePostgresConnection(raw)

    student = {'role': 'student', 'id': 7, 'name': 'Synthetic Student'}
    admin = {'role': 'teacher', 'id': 11, 'name': 'Synthetic Administrator'}

    def create_feedback():
        with connection() as conn:
            row = conn.execute("""INSERT INTO app_feedback(user_id,user_role,user_name,feedback_type,title,description)
                VALUES('7','student','Synthetic Student','bug','Synthetic native test','Synthetic native test') RETURNING id""").fetchone()
            conn.commit()
            return row['id']

    def invoke(function, *arguments):
        with connection() as conn:
            try:
                value = function(conn, *arguments)
                conn.commit()
                return {'status': 200, 'value': value}
            except HTTPException as error:
                conn.rollback()
                return {'status': error.status_code}

    def waiting(future):
        deadline = time.monotonic() + 3
        with connection() as observer:
            while time.monotonic() < deadline and not future.done():
                blocked = observer.execute("""SELECT COUNT(*) AS count FROM pg_locks l
                    JOIN pg_stat_activity a ON a.pid=l.pid WHERE a.datname=? AND NOT l.granted""", (database,)).fetchone()['count']
                if blocked:
                    return True
                time.sleep(0.01)
        raise AssertionError('Competing operation did not wait on a real PostgreSQL lock')

    def rows(feedback_id):
        with connection() as conn:
            events = [dict(row) for row in conn.execute('SELECT * FROM app_feedback_messages WHERE feedback_id=? ORDER BY id', (feedback_id,)).fetchall()]
            status = conn.execute('SELECT status FROM app_feedback WHERE id=?', (feedback_id,)).fetchone()['status']
            return status, events

    source_paths = ['classroom_app/services/feedback_conversation_service.py',
                    'classroom_app/services/message_center_service.py', 'classroom_app/db/schema_feedback_conversations.py']
    hashes = lambda: {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in source_paths}
    initial_hashes = hashes()
    try:
        # Never reuse or clear a caller's database; only drop our successful CREATE.
        control.execute(f'CREATE DATABASE "{database}" TEMPLATE template0')
        created = True
        with connection() as conn:
            conn.execute("""CREATE TABLE teachers(id BIGINT PRIMARY KEY,name TEXT,email TEXT,is_active INTEGER,is_super_admin INTEGER);
                INSERT INTO teachers VALUES(11,'Admin A','',1,1),(12,'Admin B','',1,1),(7,'Teacher same id','',1,0),(14,'Inactive Admin','',0,1);
                CREATE TABLE app_feedback(id BIGSERIAL PRIMARY KEY,user_id TEXT NOT NULL,user_role TEXT NOT NULL,
                    user_name TEXT DEFAULT '',feedback_type TEXT NOT NULL,section TEXT DEFAULT '',title TEXT NOT NULL,
                    description TEXT NOT NULL,page_url TEXT DEFAULT '',status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE app_feedback_attachments(id BIGSERIAL PRIMARY KEY,feedback_id BIGINT NOT NULL REFERENCES app_feedback(id),
                    file_hash TEXT NOT NULL,original_filename TEXT NOT NULL,file_size BIGINT DEFAULT 0,mime_type TEXT DEFAULT '',created_at TEXT);
                CREATE TABLE message_center_notifications(id BIGSERIAL PRIMARY KEY,recipient_identity TEXT NOT NULL,
                    recipient_role TEXT NOT NULL,recipient_user_pk BIGINT NOT NULL,category TEXT NOT NULL,severity TEXT,
                    actor_identity TEXT,actor_role TEXT,actor_user_pk BIGINT,actor_display_name TEXT,title TEXT,
                    body_preview TEXT,link_url TEXT,class_offering_id BIGINT,ref_type TEXT,ref_id TEXT,metadata_json TEXT,
                    created_at TEXT,read_at TEXT);
            """)
            ensure_feedback_conversation_schema(conn, engine='postgres')
            conn.commit()
        # Email delivery is outside this synthetic DB; keep the real notification insertion path.
        with patch.object(message_center_service, 'queue_notification_email_if_applicable', return_value=None):
            fid = create_feedback()
            first = connection()
            try:
                service.reply_to_feedback(first, fid, student, {'content': 'race reply', 'client_message_id': 'race-reply-0001'})
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(invoke, service.change_feedback_status, fid, admin,
                        {'status': 'closed', 'client_message_id': 'race-close-0001', 'expected_last_message_id': 0})
                    lock_waited = waiting(future)
                    first.commit()
                    result = future.result(timeout=10)
                status, events = rows(fid)
                assert result['status'] == 409 and status == 'processing' and [e['event_type'] for e in events] == ['reply']
                checks['reply_wins_stale_close_409'] = {'ok': True, 'native_lock_wait_observed': lock_waited}
            finally:
                first.rollback()
                first.close()

            fid = create_feedback()
            first = connection()
            try:
                service.change_feedback_status(first, fid, admin,
                    {'status': 'closed', 'client_message_id': 'close-wins-0001', 'expected_last_message_id': 0})
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(invoke, service.reply_to_feedback, fid, student,
                        {'content': 'late reply', 'client_message_id': 'late-reply-0001'})
                    lock_waited = waiting(future)
                    first.commit()
                    result = future.result(timeout=10)
                status, events = rows(fid)
                assert result['status'] == 409 and status == 'closed' and [e['event_type'] for e in events] == ['closed']
                checks['close_wins_reply_409'] = {'ok': True, 'native_lock_wait_observed': lock_waited}
            finally:
                first.rollback()
                first.close()

            fid = create_feedback()
            barrier = threading.Barrier(6)
            def retry(_):
                barrier.wait(timeout=10)
                return invoke(service.reply_to_feedback, fid, student, {'content': 'same message', 'client_message_id': 'same-token-0001'})
            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(retry, range(6)))
            assert all(result['status'] == 200 for result in results)
            message_ids = {result['value']['message']['id'] for result in results}
            assert len(message_ids) == 1 and sum(result['value']['deduplicated'] for result in results) == 5
            with connection() as conn:
                notices = conn.execute("SELECT recipient_role,recipient_user_pk FROM message_center_notifications WHERE ref_type='app_feedback_message' AND ref_id=? ORDER BY recipient_user_pk", (str(next(iter(message_ids))),)).fetchall()
            assert [(r['recipient_role'], r['recipient_user_pk']) for r in notices] == [('teacher', 11), ('teacher', 12)]
            checks['six_concurrent_retries_one_message'] = {'ok': True, 'attempts': 6, 'messages': 1, 'deduplicated': 5, 'notification_count': 2}

            low = invoke(service.reply_to_feedback, fid, admin, {'content': 'older admin message', 'client_message_id': 'admin-older-0001'})['value']['message']['id']
            high = invoke(service.reply_to_feedback, fid, admin, {'content': 'latest admin message', 'client_message_id': 'admin-latest-0001'})['value']['message']['id']
            barrier = threading.Barrier(6)
            def read_cursor(cursor):
                barrier.wait(timeout=10)
                return invoke(service.mark_feedback_read, fid, student, cursor)
            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(read_cursor, [high, low, 0, high, low, 0]))
            assert all(result['status'] == 200 for result in results)
            invoke(service.mark_feedback_read, fid, student, low)
            with connection() as conn:
                cursor = conn.execute("SELECT last_read_message_id FROM app_feedback_reads WHERE feedback_id=? AND user_role='student' AND user_id='7'", (fid,)).fetchone()[0]
                detail = service.get_feedback_detail(conn, fid, student)
                unread_notices = conn.execute("SELECT COUNT(*) FROM message_center_notifications WHERE recipient_role='student' AND recipient_user_pk=7 AND read_at IS NULL AND ref_id IN (?,?)", (str(low), str(high))).fetchone()[0]
            assert cursor == high and detail['feedback']['unread_count'] == 0 and unread_notices == 0
            checks['concurrent_and_delayed_read_cursor_monotonic'] = {'ok': True, 'attempts': 7, 'unread_count': 0}

            forbidden = invoke(service.get_feedback_detail, fid, {'role': 'teacher', 'id': 7})
            assert forbidden['status'] == 403
            checks['same_numeric_id_other_role_rejected'] = {'ok': True}

            fid = create_feedback()
            with connection() as conn:
                event = service.reply_to_feedback(conn, fid, student, {'content': 'rolled back', 'client_message_id': 'rollback-event-0001'})['message']
                conn.rollback()
            status, events = rows(fid)
            with connection() as conn:
                notices = conn.execute("SELECT COUNT(*) FROM message_center_notifications WHERE ref_type='app_feedback_message' AND ref_id=?", (str(event['id']),)).fetchone()[0]
            assert status == 'pending' and not events and notices == 0
            checks['event_status_notification_rollback_atomic'] = {'ok': True}
        if hashes() != initial_hashes:
            raise RuntimeError('Service source changed while native concurrency probe ran')
        finished = True
    finally:
        removed = False
        try:
            if created:
                control.execute(f'DROP DATABASE "{database}"')
                removed = True
        finally:
            control.close()
        result = {'kind': 'feedback_native_concurrency', 'ok': finished and removed and len(checks) == 6 and all(item['ok'] for item in checks.values()),
            'checks': checks, 'source_sha256': initial_hashes,
            'isolation': {'explicit_verified_offline_cluster': True, 'dedicated_synthetic_database': True,
                          'listener': '127.0.0.1', 'port': args.port, 'production_data_modified': False,
                          'synthetic_database_removed': removed, 'caller_cluster_lifecycle_unchanged': True,
                          'email_enqueue_stubbed': True, 'notification_insertion_stubbed': False}}
        (output / 'concurrency-report.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
