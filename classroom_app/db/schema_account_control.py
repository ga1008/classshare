"""Startup-only AI account control migration; never modifies human credentials."""
from __future__ import annotations

import json
import sqlite3
import time

from ..services.account_control_service import table_exists


def ensure_account_control_schema(conn) -> None:
    if isinstance(conn, sqlite3.Connection):
        columns = {row[1] for row in conn.execute('PRAGMA table_info("students")')}
        if "control_mode" not in columns:
            conn.execute("ALTER TABLE students ADD COLUMN control_mode TEXT NOT NULL DEFAULT 'human' CHECK(control_mode IN ('human','ai'))")
    else:
        conn.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS control_mode TEXT NOT NULL DEFAULT 'human' CHECK(control_mode IN ('human','ai'))")
    conn.execute("""CREATE TABLE IF NOT EXISTS account_control_migrations (
        student_id BIGINT PRIMARY KEY, migrated_at BIGINT NOT NULL, evidence_json TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS wanderer_platform_ai_jobs (
        job_id BIGINT PRIMARY KEY, wanderer_id BIGINT NOT NULL, student_id BIGINT NOT NULL,
        life_epoch BIGINT NOT NULL, authority_version BIGINT NOT NULL, operation_id TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS wanderer_platform_private_ai_jobs (
        job_id BIGINT PRIMARY KEY, wanderer_id BIGINT NOT NULL, student_id BIGINT NOT NULL,
        life_epoch BIGINT NOT NULL, authority_version BIGINT NOT NULL, operation_id TEXT NOT NULL
    )""")
    binding = " OR EXISTS (SELECT 1 FROM wanderer_agents w WHERE w.student_id=students.id)" if table_exists(conn, "wanderer_agents") else ""
    rows = conn.execute("SELECT id,control_mode,hashed_password FROM students WHERE control_mode='ai' OR enrollment_status='wanderer'" + binding).fetchall()
    now = int(time.time())
    for row in rows:
        sid = int(row["id"])
        facts = {"had_human_password": bool(row["hashed_password"])}
        conn.execute("UPDATE students SET control_mode='ai',hashed_password=NULL,password_reset_required=0 WHERE id=?", (sid,))
        for table, sql, params in (
            ("user_sessions", "DELETE FROM user_sessions WHERE role='student' AND user_id=?", (str(sid),)),
            ("remember_login_tokens", "UPDATE remember_login_tokens SET revoked_at=?,revoke_reason='ai_account_control' WHERE role='student' AND user_id=? AND revoked_at=''", (str(now), str(sid))),
            ("mp_sessions", "UPDATE mp_sessions SET revoked=1 WHERE user_role='student' AND user_pk=? AND revoked=0", (sid,)),
            ("wechat_bindings", "UPDATE wechat_bindings SET status='revoked',updated_at=? WHERE user_role='student' AND user_pk=? AND status='active'", (str(now), sid)),
            ("agent_task_delegations", "UPDATE agent_task_delegations SET status='revoked',revoked_at=?,revoke_reason='ai_account_control' WHERE actor_role='student' AND actor_id=? AND status='active'", (now, sid)),
            ("agent_persistent_authorizations", "UPDATE agent_persistent_authorizations SET status='revoked',revoked_at=?,revoke_reason='ai_account_control' WHERE actor_role='student' AND actor_id=? AND status='active'", (now, sid)),
        ):
            if table_exists(conn, table):
                facts[table] = conn.execute(sql, params).rowcount
        conn.execute("INSERT INTO account_control_migrations(student_id,migrated_at,evidence_json) VALUES(?,?,?) ON CONFLICT(student_id) DO NOTHING",
                     (sid, now, json.dumps(facts, sort_keys=True)))
