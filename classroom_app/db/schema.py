import sqlite3
import sys

from .. import config
from .connection import get_db_connection
from .postgres_indexes import ensure_postgres_performance_indexes
from .postgres_schema import ensure_postgres_runtime_constraints, validate_postgres_schema
from .schema_assignments import ensure_assignment_schema
from .schema_classroom_activity import ensure_classroom_activity_schema
from .schema_foundation import ensure_foundation_schema
from .schema_learning_blog import ensure_learning_blog_signature_schema
from .schema_materials_integrations import ensure_materials_integrations_schema
from .schema_scheduler import ensure_scheduler_schema
from .schema_gongwen import ensure_gongwen_schema
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
        # Port the SQLite performance indexes onto PostgreSQL. This is isolated
        # in its own connection/transaction and tolerant of individual failures
        # so a missing column or lock never blocks startup.
        try:
            index_conn = get_db_connection()
            try:
                index_report = ensure_postgres_performance_indexes(index_conn)
                index_conn.commit()
            finally:
                index_conn.close()
            report["performance_indexes"] = index_report
            print(
                "[DB] PostgreSQL performance indexes: "
                f"{index_report.get('created', 0)} created, "
                f"{index_report.get('failed', 0)} skipped of {index_report.get('total', 0)}"
            )
        except Exception as exc:
            print(f"[DB] PostgreSQL performance index step skipped: {exc}")
        # The unified scheduler tables are managed at runtime (engine-aware,
        # idempotent). Isolated in their own connection and tolerant of the rare
        # concurrent CREATE race between worker containers — the loser simply
        # finds the tables already present, and the scheduler service also
        # ensures the schema lazily on first use.
        try:
            scheduler_conn = get_db_connection()
            try:
                ensure_scheduler_schema(scheduler_conn)
                scheduler_conn.commit()
            finally:
                scheduler_conn.close()
            print("[DB] PostgreSQL scheduler tables ensured")
        except Exception as exc:
            print(f"[DB] PostgreSQL scheduler schema step skipped: {exc}")
        # The 公文 integration tables follow the same runtime-managed pattern.
        try:
            gongwen_conn = get_db_connection()
            try:
                ensure_gongwen_schema(gongwen_conn)
                gongwen_conn.commit()
            finally:
                gongwen_conn.close()
            print("[DB] PostgreSQL gongwen tables ensured")
        except Exception as exc:
            print(f"[DB] PostgreSQL gongwen schema step skipped: {exc}")
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
            ensure_scheduler_schema(conn)
            ensure_gongwen_schema(conn)
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
