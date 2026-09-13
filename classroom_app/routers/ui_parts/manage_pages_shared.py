"""管理页共用的小助手（引擎感知的列探测 / 找回密码统计 SQL / 教务事件标签）。"""
from .common import *
from datetime import timedelta
from ...db.connection import get_configured_db_engine
from ...dependencies import require_teacher_domain
from ...services.ai_usage_budget_service import build_ai_usage_dashboard
from ...services.offering_hub_service import build_offering_hub_context
from ...services.profile_service import build_profile_page_context


def _table_has_column(conn, table_name: str, column_name: str) -> bool:
    if get_configured_db_engine() == "postgres":
        row = conn.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = ?
              AND column_name = ?
            LIMIT 1
            """,
            (table_name, column_name),
        ).fetchone()
        return row is not None
    rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return any(str(row["name"]) == column_name for row in rows)


def _password_reset_login_summary_sql() -> str:
    today_login_expr = (
        "logged_at::date = CURRENT_DATE"
        if get_configured_db_engine() == "postgres"
        else "date(logged_at) = date('now', 'localtime')"
    )
    return f"""
            SELECT
                COUNT(*) AS total_logins,
                SUM(CASE WHEN {today_login_expr} THEN 1 ELSE 0 END) AS today_logins
            FROM student_login_audit_logs logs
            JOIN students s ON s.id = logs.student_id
            JOIN classes c ON c.id = s.class_id
            WHERE ? = 1
               OR c.created_by_teacher_id = ?
               OR EXISTS (
                    SELECT 1 FROM class_offerings o
                    WHERE o.class_id = c.id
                      AND o.teacher_id = ?
               )
            """


def _academic_event_label(row) -> str:
    source_type = str(row["source_type"] or "").lower()
    tone = str(row["tone"] or "").lower()
    marker = f"{source_type} {tone}"
    if "invigilation" in marker:
        return "监考"
    if "exam" in marker:
        return "考试"
    if "adjustment" in marker or "course_adjustment" in marker:
        return "调停课"
    return "教务日程"
