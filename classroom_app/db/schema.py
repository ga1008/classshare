import sqlite3
import sys

from .. import config
from .connection import get_db_connection
from .postgres_schema import ensure_postgres_runtime_constraints, validate_postgres_schema
from .schema_assignments import ensure_assignment_schema
from .schema_classroom_activity import ensure_classroom_activity_schema
from .schema_foundation import ensure_foundation_schema
from .schema_learning_blog import ensure_learning_blog_signature_schema
from .schema_materials_integrations import ensure_materials_integrations_schema
from .seeds import init_default_exam_paper


def init_database():
    """
    Initialize the LanShare database schema without changing the public startup entrypoint.
    """
    if getattr(config, "DB_ENGINE", "sqlite") == "postgres":
        print("[DB] Verifying PostgreSQL schema...")
        conn = get_db_connection()
        try:
            runtime_constraint_report = ensure_postgres_runtime_constraints(conn)
            conn.commit()
            report = validate_postgres_schema(conn)
            report["runtime_constraints"] = runtime_constraint_report
            report["schema_writes_executed"] = bool(runtime_constraint_report["schema_writes_executed"])
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        print(
            "[DB] PostgreSQL schema verified: "
            f"{report['present_required_table_count']}/{report['required_table_count']} required tables"
        )
        return report

    print("[DB] Initializing V4.0 database schema...")
    try:
        conn = get_db_connection()
        try:
            ensure_foundation_schema(conn)
            ensure_assignment_schema(conn)
            ensure_classroom_activity_schema(conn)
            ensure_materials_integrations_schema(conn)
            ensure_learning_blog_signature_schema(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        print("[DB] V4.0 数据库架构初始化/验证完成。")

        # 初始化默认试卷（MID.html 期中测试）
        init_default_exam_paper()
    except sqlite3.Error as e:
        print(f"[DB ERROR] 初始化 V4.0 数据库失败: {e}")
        sys.exit(1)
