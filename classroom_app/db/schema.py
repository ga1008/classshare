import sqlite3
import sys

from .. import config
from .connection import get_db_connection
from .postgres_indexes import ensure_postgres_performance_indexes
from .postgres_schema import (
    ensure_postgres_runtime_columns,
    ensure_postgres_runtime_constraints,
    ensure_postgres_runtime_tables,
    validate_postgres_schema,
)
from .schema_agent_ext import ensure_agent_task_extension_schema
from .schema_ai_jobs import ensure_ai_job_schema
from .schema_assignments import ensure_assignment_schema
from .schema_classroom_activity import ensure_classroom_activity_schema
from .schema_cultivation_progress import ensure_cultivation_progress_schema
from .schema_foundation import ensure_foundation_schema
from .schema_learning_blog import ensure_learning_blog_signature_schema
from .schema_signature_workflow import ensure_signature_workflow_schema
from .schema_lesson_plans import ensure_lesson_plan_schema
from .schema_assessment_plans import ensure_assessment_plan_schema
from .schema_teacher_evaluations import ensure_teacher_evaluation_schema
from .schema_prompt_pool import ensure_prompt_pool_schema
from .schema_materials_integrations import ensure_materials_integrations_schema
from .schema_academic_final_materials import ensure_academic_final_material_schema
from .schema_academic_evaluations import ensure_academic_evaluation_schema
from .schema_offering_class_links import ensure_offering_class_links_schema
from .schema_offering_merge import ensure_offering_merge_schema
from .schema_polls import ensure_poll_schema
from .schema_course_doc_packs import ensure_course_doc_pack_schema
from .schema_resume import ensure_resume_schema
from .schema_scheduler import ensure_scheduler_schema
from .schema_gongwen import ensure_gongwen_schema
from .schema_study_group_scheme import ensure_study_group_scheme_schema
from .seeds import init_default_exam_paper


def init_database():
    """
    Initialize the LanShare database schema without changing the public startup entrypoint.
    """
    if getattr(config, "DB_ENGINE", "sqlite") == "postgres":
        print("[DB] Verifying PostgreSQL schema...")
        conn = get_db_connection()
        try:
            ensure_cultivation_progress_schema(conn, engine="postgres")
            runtime_table_report = ensure_postgres_runtime_tables(conn)
            runtime_column_report = ensure_postgres_runtime_columns(conn)
            runtime_constraint_report = ensure_postgres_runtime_constraints(conn)
            ensure_signature_workflow_schema(conn)
            conn.commit()
            report = validate_postgres_schema(conn)
            report["runtime_tables"] = runtime_table_report
            report["runtime_columns"] = runtime_column_report
            report["runtime_constraints"] = runtime_constraint_report
            report["schema_writes_executed"] = bool(
                runtime_table_report["schema_writes_executed"]
                or runtime_column_report["schema_writes_executed"]
                or runtime_constraint_report["schema_writes_executed"]
            )
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
        # The random study-group scheme tables/columns follow the same
        # runtime-managed, engine-aware pattern.
        try:
            scheme_conn = get_db_connection()
            try:
                ensure_study_group_scheme_schema(scheme_conn)
                scheme_conn.commit()
            finally:
                scheme_conn.close()
            print("[DB] PostgreSQL study-group scheme tables ensured")
        except Exception as exc:
            print(f"[DB] PostgreSQL study-group scheme schema step skipped: {exc}")
        # The poll / vote tables follow the same runtime-managed, engine-aware
        # pattern (shared cross-class vote data lives here).
        try:
            poll_conn = get_db_connection()
            try:
                ensure_poll_schema(poll_conn)
                poll_conn.commit()
            finally:
                poll_conn.close()
            print("[DB] PostgreSQL poll tables ensured")
        except Exception as exc:
            print(f"[DB] PostgreSQL poll schema step skipped: {exc}")
        # The LessonDoc course doc pack tables (课程学习文档包) follow the
        # same runtime-managed, engine-aware pattern.
        try:
            doc_pack_conn = get_db_connection()
            try:
                ensure_course_doc_pack_schema(doc_pack_conn)
                doc_pack_conn.commit()
            finally:
                doc_pack_conn.close()
            print("[DB] PostgreSQL course doc pack tables ensured")
        except Exception as exc:
            print(f"[DB] PostgreSQL course doc pack schema step skipped: {exc}")
        # The offering↔class link table (合班课堂) follows the same
        # runtime-managed, engine-aware pattern; ensure also backfills a
        # primary link per existing offering (idempotent).
        try:
            offering_link_conn = get_db_connection()
            try:
                ensure_offering_class_links_schema(offering_link_conn)
                offering_link_conn.commit()
            finally:
                offering_link_conn.close()
            print("[DB] PostgreSQL offering class-link table ensured")
        except Exception as exc:
            print(f"[DB] PostgreSQL offering class-link schema step skipped: {exc}")
        try:
            offering_merge_conn = get_db_connection()
            try:
                ensure_offering_merge_schema(offering_merge_conn)
                offering_merge_conn.commit()
            finally:
                offering_merge_conn.close()
            print("[DB] PostgreSQL offering merge tables ensured")
        except Exception as exc:
            print(f"[DB] PostgreSQL offering merge schema step skipped: {exc}")
        # Agent task extension columns follow the same runtime-managed pattern.
        try:
            agent_ext_conn = get_db_connection()
            try:
                ensure_agent_task_extension_schema(agent_ext_conn)
                agent_ext_conn.commit()
            finally:
                agent_ext_conn.close()
            print("[DB] PostgreSQL agent task extension columns ensured")
        except Exception as exc:
            print(f"[DB] PostgreSQL agent task extension step skipped: {exc}")
        # The lesson-plan (教案) table follows the same runtime-managed,
        # engine-aware pattern (isolated connection so the validate path above
        # stays DDL-free) — owned content asset with org-scoped sharing.
        try:
            lesson_plan_conn = get_db_connection()
            try:
                ensure_lesson_plan_schema(lesson_plan_conn)
                lesson_plan_conn.commit()
            finally:
                lesson_plan_conn.close()
            print("[DB] PostgreSQL lesson-plan table ensured")
        except Exception as exc:
            print(f"[DB] PostgreSQL lesson-plan schema step skipped: {exc}")
        # The assessment-plan (考核计划表 / 过程材料) table uses the same
        # runtime-managed, engine-aware pattern (isolated connection so the
        # validate path above stays DDL-free).
        try:
            assessment_plan_conn = get_db_connection()
            try:
                ensure_assessment_plan_schema(assessment_plan_conn)
                assessment_plan_conn.commit()
            finally:
                assessment_plan_conn.close()
            print("[DB] PostgreSQL assessment-plan table ensured")
        except Exception as exc:
            print(f"[DB] PostgreSQL assessment-plan schema step skipped: {exc}")
        # The teacher-evaluation (教师评学表 / 过程材料) table uses the same
        # runtime-managed, engine-aware pattern (isolated connection so the
        # validate path above stays DDL-free).
        try:
            teacher_evaluation_conn = get_db_connection()
            try:
                ensure_teacher_evaluation_schema(teacher_evaluation_conn)
                teacher_evaluation_conn.commit()
            finally:
                teacher_evaluation_conn.close()
            print("[DB] PostgreSQL teacher-evaluation table ensured")
        except Exception as exc:
            print(f"[DB] PostgreSQL teacher-evaluation schema step skipped: {exc}")
        # The resume console (简历管理与优化) tables follow the same
        # runtime-managed, engine-aware pattern (isolated connection so the
        # validate path above stays DDL-free) — student-owned résumé workbench.
        try:
            resume_conn = get_db_connection()
            try:
                ensure_resume_schema(resume_conn)
                resume_conn.commit()
            finally:
                resume_conn.close()
            print("[DB] PostgreSQL resume console tables ensured")
        except Exception as exc:
            print(f"[DB] PostgreSQL resume console schema step skipped: {exc}")
        try:
            prompt_pool_conn = get_db_connection()
            try:
                ensure_prompt_pool_schema(prompt_pool_conn)
                prompt_pool_conn.commit()
            finally:
                prompt_pool_conn.close()
            print("[DB] PostgreSQL prompt-pool table ensured")
        except Exception as exc:
            print(f"[DB] PostgreSQL prompt-pool schema step skipped: {exc}")
        try:
            final_material_conn = get_db_connection()
            try:
                ensure_academic_final_material_schema(final_material_conn)
                final_material_conn.commit()
            finally:
                final_material_conn.close()
            print("[DB] PostgreSQL academic final-material table ensured")
        except Exception as exc:
            print(f"[DB] PostgreSQL academic final-material schema step skipped: {exc}")
        try:
            academic_evaluation_conn = get_db_connection()
            try:
                ensure_academic_evaluation_schema(academic_evaluation_conn)
                academic_evaluation_conn.commit()
            finally:
                academic_evaluation_conn.close()
            print("[DB] PostgreSQL academic evaluation tables ensured")
        except Exception as exc:
            print(f"[DB] PostgreSQL academic evaluation schema step skipped: {exc}")
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
            ensure_ai_job_schema(conn, engine="sqlite")
            ensure_classroom_activity_schema(conn)
            ensure_study_group_scheme_schema(conn)
            ensure_poll_schema(conn)
            ensure_course_doc_pack_schema(conn)
            ensure_offering_class_links_schema(conn)
            ensure_offering_merge_schema(conn)
            ensure_materials_integrations_schema(conn)
            ensure_learning_blog_signature_schema(conn)
            ensure_signature_workflow_schema(conn)
            ensure_scheduler_schema(conn)
            ensure_gongwen_schema(conn)
            ensure_agent_task_extension_schema(conn)
            ensure_lesson_plan_schema(conn)
            ensure_assessment_plan_schema(conn)
            ensure_teacher_evaluation_schema(conn)
            ensure_resume_schema(conn)
            ensure_prompt_pool_schema(conn)
            ensure_academic_final_material_schema(conn)
            ensure_academic_evaluation_schema(conn)
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
