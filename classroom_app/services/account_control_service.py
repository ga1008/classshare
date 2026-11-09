"""Trusted account control mode and human authentication channel eligibility.

The enrollment marker remains compatible with old rosters. Authentication must
also consult the persisted AI binding: a caller-provided flag never grants a
service identity or converts an AI-controlled account into a human account.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import HTTPException


def table_exists(conn: Any, name: str) -> bool:
    from ..db.connection import get_configured_db_engine
    from ..db.postgres import LanSharePostgresConnection
    # Ordinary business code can wrap connections (for transaction failure
    # handling, for example). A wrapper is not evidence of PostgreSQL.
    sqlite = isinstance(conn, sqlite3.Connection) or (
        not isinstance(conn, LanSharePostgresConnection) and get_configured_db_engine() == 'sqlite')
    if sqlite:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=?",
            (name,),
        ).fetchone()
    return row is not None


def _value(row: Any, key: str, default=None):
    try:
        return row[key] if key in row.keys() else default
    except (AttributeError, TypeError):
        return default


def student_is_ai_controlled(conn: Any, student: Any) -> bool:
    """Fail closed for either AI marker or binding, including legacy accounts."""
    row = student
    if isinstance(student, (str, int)):
        row = conn.execute("SELECT * FROM students WHERE id=? LIMIT 1", (int(student),)).fetchone()
    if row is None:
        return False
    if (str(_value(row, "control_mode", "human") or "human").lower() == "ai"
            or str(_value(row, "enrollment_status", "") or "").lower() == "wanderer"):
        return True
    student_id = _value(row, "id")
    return bool(student_id and table_exists(conn, "wanderer_agents") and conn.execute(
        "SELECT 1 FROM wanderer_agents WHERE student_id=? LIMIT 1", (int(student_id),)
    ).fetchone())


def ensure_student_human_auth(conn: Any, student: Any) -> None:
    if student_is_ai_controlled(conn, student):
        raise HTTPException(403, "此账号由 AI 专管，不允许人工登录、设密或绑定。",
                            headers={"X-Auth-Error": "ai_account_human_auth_forbidden"})


def mark_student_ai_controlled(conn: Any, student_id: int) -> None:
    """Creation hook after the startup-owned account-control migration."""
    conn.execute("UPDATE students SET control_mode='ai',hashed_password=NULL,password_reset_required=0 WHERE id=?", (int(student_id),))
    from ..dependencies import invalidate_session_for_user
    invalidate_session_for_user(str(student_id), "student", conn=conn)
    for table, sql in (
        ("mp_sessions", "UPDATE mp_sessions SET revoked=1 WHERE user_role='student' AND user_pk=?"),
        ("wechat_bindings", "UPDATE wechat_bindings SET status='revoked' WHERE user_role='student' AND user_pk=?"),
    ):
        if table_exists(conn, table):
            conn.execute(sql, (int(student_id),))


def row_allows_human_auth(student: Any) -> bool:
    """Cheap precheck for a row already loaded by a trusted authentication query.

    Final credential issuance additionally verifies the live persisted binding.
    """
    return (str(_value(student, "control_mode", "human") or "human").lower() != "ai"
            and str(_value(student, "enrollment_status", "") or "").lower() != "wanderer")
