from __future__ import annotations

from typing import Any, Sequence

from .errors import DatabaseProgrammingError
from .postgres_required_columns import REQUIRED_POSTGRES_COLUMNS
from .row import rows_to_mappings
from .sql import quote_identifier
from .schema_ai_jobs import (
    AI_JOB_POSTGRES_RUNTIME_COLUMNS,
    AI_JOB_POSTGRES_RUNTIME_TABLES,
    AI_JOB_REQUIRED_POSTGRES_COLUMNS,
)


POSTGRES_RUNTIME_UNIQUE_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # Older exports lost this column-level UNIQUE; configuration saves use it
    # as their ON CONFLICT target. Repair at startup, never during requests.
    (
        "idx_ai_class_configs_unique_offering",
        "ai_class_configs",
        ("class_offering_id",),
    ),
    # The organization catalog and teacher membership writers use UPSERTs with
    # these conflict targets. SQLite keeps the UNIQUE declarations from
    # migrations.py, but the SQLite -> PostgreSQL exporter drops table-level
    # constraints. Without recreating them, saving a teacher's organization
    # membership fails on the first organization_schools UPSERT.
    (
        "idx_course_material_assignments_unique_target",
        "course_material_assignments",
        ("material_id", "class_offering_id"),
    ),
    (
        "idx_organization_schools_unique_code",
        "organization_schools",
        ("school_code",),
    ),
    (
        "idx_organization_colleges_unique_name",
        "organization_colleges",
        ("school_code", "college_name"),
    ),
    (
        "idx_organization_departments_unique_name",
        "organization_departments",
        ("school_code", "college_name", "department_name"),
    ),
    (
        "idx_teacher_org_memberships_one_school",
        "teacher_organization_memberships",
        ("teacher_id", "school_code"),
    ),
    (
        "idx_teacher_academic_system_credentials_unique_auth",
        "teacher_academic_system_credentials",
        ("teacher_id", "school_code", "auth_method"),
    ),
    (
        "idx_teacher_academic_course_sync_items_unique_schedule",
        "teacher_academic_course_sync_items",
        (
            "teacher_id",
            "semester_id",
            "course_code",
            "teaching_class_name",
            "weeks_text",
            "weekday",
            "section_text",
            "location",
        ),
    ),
    (
        "idx_teacher_academic_course_occurrences_unique_session",
        "teacher_academic_course_session_occurrences",
        (
            "teacher_id",
            "semester_id",
            "course_id",
            "teaching_class_name",
            "session_date",
            "section_text",
            "location",
        ),
    ),
    (
        "idx_teacher_academic_roster_items_unique_teaching_class",
        "teacher_academic_roster_sync_items",
        ("teacher_id", "school_code", "academic_year", "academic_term", "teaching_class_id"),
    ),
    (
        "idx_teacher_academic_roster_memberships_unique_student",
        "teacher_academic_roster_memberships",
        (
            "teacher_id",
            "school_code",
            "academic_year",
            "academic_term",
            "teaching_class_id",
            "student_number",
        ),
    ),
    (
        "idx_teacher_academic_class_mappings_unique_teaching_class",
        "teacher_academic_teaching_class_mappings",
        (
            "teacher_id",
            "school_code",
            "academic_year",
            "academic_term",
            "course_code",
            "teaching_class_id",
            "teaching_class_name",
        ),
    ),
    (
        "idx_teacher_academic_invigilation_items_unique_key",
        "teacher_academic_invigilation_items",
        ("teacher_id", "school_code", "academic_year", "academic_term", "invigilation_key"),
    ),
    (
        "idx_teacher_academic_course_exam_items_unique_key",
        "teacher_academic_course_exam_items",
        ("teacher_id", "school_code", "academic_year", "academic_term", "exam_key"),
    ),
    (
        "idx_teacher_academic_exam_roster_items_unique_course",
        "teacher_academic_exam_roster_items",
        ("teacher_id", "school_code", "academic_year", "academic_term", "exam_course_key"),
    ),
    (
        "idx_teacher_academic_exam_roster_students_unique_student",
        "teacher_academic_exam_roster_students",
        ("exam_roster_item_id", "student_number"),
    ),
    (
        "idx_teacher_academic_teaching_places_unique_place",
        "teacher_academic_teaching_places",
        ("teacher_id", "school_code", "source", "place_key"),
    ),
    (
        "idx_teacher_smart_classroom_credentials_unique_auth",
        "teacher_smart_classroom_credentials",
        ("teacher_id", "platform_code", "auth_method"),
    ),
    (
        "idx_gongwen_credentials_unique_auth",
        "teacher_gongwen_credentials",
        ("teacher_id", "system_code", "auth_method"),
    ),
    (
        "idx_gongwen_documents_unique_campus",
        "gongwen_documents",
        ("attr_school_code", "system_code", "remote_id"),
    ),
    (
        "idx_gongwen_follow_settings_teacher",
        "teacher_gongwen_follow_settings",
        ("teacher_id",),
    ),
    (
        "idx_gongwen_follow_hits_unique",
        "gongwen_follow_hits",
        ("teacher_id", "document_id"),
    ),
    (
        "idx_smart_classroom_schedule_items_unique_remote",
        "smart_classroom_schedule_items",
        ("teacher_id", "platform_code", "remote_schedule_id"),
    ),
    (
        "idx_smart_classroom_checkin_sessions_unique_remote",
        "smart_classroom_checkin_sessions",
        ("teacher_id", "platform_code", "remote_checkin_id"),
    ),
    (
        "idx_smart_classroom_checkin_students_unique_student",
        "smart_classroom_checkin_students",
        ("checkin_session_id", "student_number"),
    ),
    (
        "idx_smart_attendance_daily_tasks_unique_task",
        "smart_attendance_daily_tasks",
        ("class_offering_id", "teacher_id", "task_type", "task_date"),
    ),
    (
        "idx_smart_attendance_student_advice_unique_fingerprint",
        "smart_attendance_student_advice",
        ("class_offering_id", "student_id", "fingerprint"),
    ),
    (
        "idx_class_offering_sessions_unique_order",
        "class_offering_sessions",
        ("class_offering_id", "order_index"),
    ),
    (
        "idx_learning_material_progress_unique_material",
        "learning_material_progress",
        ("class_offering_id", "student_id", "material_id"),
    ),
    (
        "idx_learning_stage_status_unique_stage",
        "learning_stage_status",
        ("class_offering_id", "student_id", "stage_key"),
    ),
    (
        "idx_learning_progress_snapshots_unique_student",
        "learning_progress_snapshots",
        ("class_offering_id", "student_id"),
    ),
    (
        "idx_cultivation_weekly_snapshots_unique_student_week",
        "cultivation_weekly_snapshots",
        ("class_offering_id", "student_id", "week_start"),
    ),
    (
        "idx_cultivation_score_event_archives_unique_bucket",
        "cultivation_score_event_archives",
        ("class_offering_id", "student_id", "archive_month", "event_type", "component"),
    ),
    (
        "idx_learning_certificates_unique_stage",
        "learning_certificates",
        ("class_offering_id", "student_id", "stage_key"),
    ),
    (
        "idx_student_learning_path_item_states_unique_item",
        "student_learning_path_item_states",
        ("student_id", "item_key"),
    ),
    (
        "idx_student_portfolio_items_unique_source",
        "student_portfolio_items",
        ("student_id", "source_type", "source_id"),
    ),
    (
        "idx_student_portfolio_reflections_unique_item",
        "student_portfolio_reflections",
        ("portfolio_item_id",),
    ),
    (
        "idx_student_feedback_review_notes_unique_question",
        "student_feedback_review_notes",
        ("student_id", "submission_id", "question_key"),
    ),
    (
        "idx_email_outbox_unique_dedupe_key",
        "email_outbox",
        ("dedupe_key",),
    ),
    (
        "idx_email_worker_heartbeats_unique_worker",
        "email_worker_heartbeats",
        ("worker_id",),
    ),
    (
        "idx_scheduled_tasks_dedupe_key",
        "scheduled_tasks",
        ("dedupe_key",),
    ),
    (
        "idx_scheduled_task_worker_heartbeats_worker",
        "scheduled_task_worker_heartbeats",
        ("worker_id",),
    ),
    (
        "idx_private_message_blocks_unique_pair",
        "private_message_blocks",
        ("owner_identity", "blocked_identity"),
    ),
    (
        "idx_blog_media_assets_unique_uploader_file",
        "blog_media_assets",
        ("file_hash", "uploader_identity"),
    ),
    (
        "idx_emoji_usage_stats_unique_target",
        "emoji_usage_stats",
        ("class_offering_id", "user_id", "user_role", "emoji_type", "emoji_key"),
    ),
    # Wrong-summary AI caches and job state use ON CONFLICT with these keys.
    # SQLite keeps table-level UNIQUE constraints, but SQLite->PostgreSQL export
    # drops them; recreate the unique indexes at runtime to keep teacher manual
    # reorganization from failing with a 500.
    (
        "idx_assignment_wrong_answer_ai_cache_unique_entry",
        "assignment_wrong_answer_ai_cache",
        ("assignment_id", "question_key", "answer_signature", "prompt_version"),
    ),
    (
        "idx_exam_paper_difficulty_ai_cache_unique_entry",
        "exam_paper_difficulty_ai_cache",
        ("exam_paper_id", "questions_signature", "prompt_version"),
    ),
    (
        "idx_assignment_wrong_summary_jobs_unique_signature",
        "assignment_wrong_summary_jobs",
        ("assignment_id", "questions_signature", "prompt_version"),
    ),
    # Classroom live activity responses rely on UPSERT (ON CONFLICT) keyed by
    # (activity_id, student_id). The SQLite table-level UNIQUE constraint is
    # dropped during the SQLite->PostgreSQL export, so the runtime must recreate
    # it; without it every student poll/quiz vote raises a 500 on PostgreSQL.
    (
        "idx_classroom_live_responses_unique_vote",
        "classroom_live_responses",
        ("activity_id", "student_id"),
    ),
    # Discussion mood snapshots upsert by class_offering_id (column-level UNIQUE,
    # also lost in the export). Recreate so ON CONFLICT(class_offering_id) works.
    (
        "idx_discussion_mood_snapshots_unique_offering",
        "discussion_mood_snapshots",
        ("class_offering_id",),
    ),
)

POSTGRES_RUNTIME_COLUMN_DEFINITIONS: dict[str, dict[str, str]] = {
    **AI_JOB_POSTGRES_RUNTIME_COLUMNS,
    "assignment_wrong_summary_jobs": {
        # Added to the CREATE TABLE later without an ALTER migration, so a
        # PostgreSQL provisioned from an older schema lacks it. Auto-add here.
        "run_token": "TEXT NOT NULL DEFAULT ''",
    },
    "classes": {
        "class_kind": "TEXT NOT NULL DEFAULT 'administrative'",
    },
    "class_offerings": {
        "academic_teaching_class_id": "TEXT NOT NULL DEFAULT ''",
        "cultivation_weights_json": "TEXT NOT NULL DEFAULT ''",
        "cultivation_weights_version": "TEXT NOT NULL DEFAULT 'default-v1'",
        "cultivation_weights_updated_at": "TEXT",
        "cultivation_weights_updated_by_teacher_id": "INTEGER",
        "ai_weekly_budget_json": "TEXT NOT NULL DEFAULT ''",
        "ai_weekly_budget_updated_at": "TEXT",
        "group_qr_file_hash": "TEXT NOT NULL DEFAULT ''",
        "group_qr_mime_type": "TEXT NOT NULL DEFAULT ''",
        "group_qr_description": "TEXT NOT NULL DEFAULT ''",
        "group_qr_revision": "TEXT NOT NULL DEFAULT ''",
    },
    "assignments": {
        "ordinary_grade_kind_override": "TEXT",
        "ordinary_grade_kind_updated_at": "TEXT",
        "ordinary_grade_kind_updated_by_teacher_id": "INTEGER",
    },
    "course_materials": {
        "check_questions_json": "TEXT DEFAULT ''",
        "check_questions_status": "TEXT NOT NULL DEFAULT 'idle'",
        "check_questions_error": "TEXT DEFAULT ''",
        "check_questions_generated_at": "TEXT",
    },
    "material_ai_import_records": {
        "signature_revision": "TEXT NOT NULL DEFAULT ''",
    },
    "classroom_behavior_profiles": {
        "interaction_quality": "DOUBLE PRECISION",
        "interaction_quality_label": "TEXT",
        "interaction_quality_reason": "TEXT",
    },
    "learning_material_progress": {
        "mastered": "INTEGER NOT NULL DEFAULT 0",
        "mastered_at": "TEXT",
        "mastery_source": "TEXT NOT NULL DEFAULT ''",
        "mastery_attempts": "INTEGER NOT NULL DEFAULT 0",
        "mastery_last_attempt_json": "TEXT DEFAULT '{}'",
        "progress_rule_version": "TEXT NOT NULL DEFAULT 'material_mastery_v2'",
    },
    "learning_certificates": {
        "revealed_at": "TEXT",
    },
    "blog_posts": {
        "visible_class_offering_id": "INTEGER",
        "section_key": "TEXT NOT NULL DEFAULT 'general'",
    },
    "blog_news_crawler_items": {
        "section_key": "TEXT NOT NULL DEFAULT 'general'",
    },
    "blog_opportunity_user_states": {
        "deadline_reminder_sent_at": "TEXT",
    },
    "teacher_academic_teaching_class_mappings": {
        "teaching_class_aliases_json": "TEXT NOT NULL DEFAULT '[]'",
        "admin_class_aliases_json": "TEXT NOT NULL DEFAULT '[]'",
    },
    "teacher_academic_course_sync_items": {
        "course_internal_id": "TEXT NOT NULL DEFAULT ''",
        "teaching_class_id": "TEXT NOT NULL DEFAULT ''",
    },
    "teacher_academic_course_session_occurrences": {
        "course_internal_id": "TEXT NOT NULL DEFAULT ''",
        "teaching_class_id": "TEXT NOT NULL DEFAULT ''",
    },
}


POSTGRES_RUNTIME_TABLE_DEFINITIONS: dict[str, str] = {
    **AI_JOB_POSTGRES_RUNTIME_TABLES,
    "blog_sections": """
        CREATE TABLE IF NOT EXISTS blog_sections (
            section_key TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            short_name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            icon TEXT NOT NULL DEFAULT '•',
            accent_color TEXT NOT NULL DEFAULT '#2563eb',
            sort_order INTEGER NOT NULL DEFAULT 100,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            is_career INTEGER NOT NULL DEFAULT 0,
            allow_user_posts INTEGER NOT NULL DEFAULT 1,
            source_keywords_json TEXT NOT NULL DEFAULT '[]',
            source_templates_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "blog_post_views": """
        CREATE TABLE IF NOT EXISTS blog_post_views (
            id SERIAL PRIMARY KEY,
            post_id INTEGER NOT NULL REFERENCES blog_posts (id) ON DELETE CASCADE,
            viewer_identity TEXT NOT NULL,
            view_bucket TEXT NOT NULL,
            first_viewed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_viewed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            view_events INTEGER NOT NULL DEFAULT 1,
            dwell_seconds INTEGER NOT NULL DEFAULT 0,
            max_scroll_ratio DOUBLE PRECISION NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            UNIQUE (post_id, viewer_identity, view_bucket)
        )
    """,
    "blog_post_editorial_metadata": """
        CREATE TABLE IF NOT EXISTS blog_post_editorial_metadata (
            post_id INTEGER PRIMARY KEY REFERENCES blog_posts (id) ON DELETE CASCADE,
            topic TEXT NOT NULL DEFAULT '',
            keywords_json TEXT NOT NULL DEFAULT '[]',
            source_title TEXT NOT NULL DEFAULT '',
            source_name TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            source_published_at TEXT NOT NULL DEFAULT '',
            classification_confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
            classification_reason TEXT NOT NULL DEFAULT '',
            memory_post_ids_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "blog_opportunities": """
        CREATE TABLE IF NOT EXISTS blog_opportunities (
            id SERIAL PRIMARY KEY,
            post_id INTEGER NOT NULL UNIQUE REFERENCES blog_posts (id) ON DELETE CASCADE,
            employer_name TEXT NOT NULL DEFAULT '',
            opportunity_type TEXT NOT NULL DEFAULT 'campus_recruitment',
            positions_text TEXT NOT NULL DEFAULT '',
            regions_json TEXT NOT NULL DEFAULT '[]',
            city TEXT NOT NULL DEFAULT '',
            target_groups_json TEXT NOT NULL DEFAULT '[]',
            education_text TEXT NOT NULL DEFAULT '',
            majors_json TEXT NOT NULL DEFAULT '[]',
            headcount_text TEXT NOT NULL DEFAULT '',
            compensation_text TEXT NOT NULL DEFAULT '',
            application_method TEXT NOT NULL DEFAULT '',
            application_url TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            source_domain TEXT NOT NULL DEFAULT '',
            source_name TEXT NOT NULL DEFAULT '',
            source_level TEXT NOT NULL DEFAULT 'C',
            published_at TEXT,
            deadline_at TEXT,
            last_verified_at TEXT,
            expires_at TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            extraction_confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
            verification_notes TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "blog_opportunity_user_states": """
        CREATE TABLE IF NOT EXISTS blog_opportunity_user_states (
            id SERIAL PRIMARY KEY,
            opportunity_id INTEGER NOT NULL REFERENCES blog_opportunities (id) ON DELETE CASCADE,
            user_identity TEXT NOT NULL,
            user_role TEXT NOT NULL,
            user_pk INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'saved',
            reminder_at TEXT,
            deadline_reminder_sent_at TEXT,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (opportunity_id, user_identity)
        )
    """,
    "blog_follows": """
        CREATE TABLE IF NOT EXISTS blog_follows (
            id SERIAL PRIMARY KEY,
            user_identity TEXT NOT NULL,
            user_role TEXT NOT NULL,
            user_pk INTEGER NOT NULL,
            target_type TEXT NOT NULL,
            target_key TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (user_identity, target_type, target_key)
        )
    """,
    "blog_reports": """
        CREATE TABLE IF NOT EXISTS blog_reports (
            id SERIAL PRIMARY KEY,
            target_type TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            reporter_identity TEXT NOT NULL,
            reporter_role TEXT NOT NULL,
            reporter_user_pk INTEGER NOT NULL,
            reason_code TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            resolved_by_identity TEXT NOT NULL DEFAULT '',
            resolution_notes TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (target_type, target_id, reporter_identity, status)
        )
    """,
    "teacher_academic_teaching_class_mappings": """
        CREATE TABLE IF NOT EXISTS teacher_academic_teaching_class_mappings (
            id SERIAL PRIMARY KEY,
            teacher_id INTEGER NOT NULL,
            semester_id INTEGER,
            school_code TEXT NOT NULL DEFAULT 'gxufl',
            academic_year TEXT NOT NULL DEFAULT '',
            academic_term TEXT NOT NULL DEFAULT '',
            course_code TEXT NOT NULL DEFAULT '',
            course_name TEXT NOT NULL DEFAULT '',
            teaching_class_id TEXT NOT NULL DEFAULT '',
            teaching_class_name TEXT NOT NULL DEFAULT '',
            teaching_class_aliases_json TEXT NOT NULL DEFAULT '[]',
            admin_class_id INTEGER,
            admin_class_code TEXT NOT NULL DEFAULT '',
            admin_class_name TEXT NOT NULL DEFAULT '',
            admin_class_ids_json TEXT NOT NULL DEFAULT '[]',
            admin_class_codes_json TEXT NOT NULL DEFAULT '[]',
            admin_class_names_json TEXT NOT NULL DEFAULT '[]',
            admin_class_aliases_json TEXT NOT NULL DEFAULT '[]',
            admin_class_count INTEGER NOT NULL DEFAULT 0,
            student_count INTEGER NOT NULL DEFAULT 0,
            mapping_status TEXT NOT NULL DEFAULT 'active',
            source_sync_item_ids_json TEXT NOT NULL DEFAULT '[]',
            source_updated_at TEXT,
            synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "teacher_academic_entity_bindings": """
        CREATE TABLE IF NOT EXISTS teacher_academic_entity_bindings (
            id SERIAL PRIMARY KEY,
            teacher_id INTEGER NOT NULL REFERENCES teachers (id) ON DELETE CASCADE,
            semester_scope INTEGER NOT NULL DEFAULT 0,
            source_system TEXT NOT NULL DEFAULT 'gxufl_jwxt',
            entity_type TEXT NOT NULL,
            source_key TEXT NOT NULL,
            local_entity_id INTEGER NOT NULL,
            source_label TEXT NOT NULL DEFAULT '',
            aliases_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            binding_status TEXT NOT NULL DEFAULT 'active',
            confirmed_at TEXT,
            first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (teacher_id, semester_scope, source_system, entity_type, source_key)
        )
    """,
    "teacher_academic_sync_plans": """
        CREATE TABLE IF NOT EXISTS teacher_academic_sync_plans (
            id SERIAL PRIMARY KEY,
            teacher_id INTEGER NOT NULL REFERENCES teachers (id) ON DELETE CASCADE,
            semester_id INTEGER NOT NULL REFERENCES academic_semesters (id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending',
            source_fingerprint TEXT NOT NULL,
            snapshot_json TEXT NOT NULL DEFAULT '{}',
            preview_json TEXT NOT NULL DEFAULT '{}',
            resolution_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT NOT NULL DEFAULT '{}',
            expires_at TEXT NOT NULL,
            applied_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """,
}


REQUIRED_POSTGRES_TABLES = (
    "user_ui_preferences",
    "teachers",
    "system_settings",
    "teacher_onboarding_state",
    "user_sessions",
    "organization_schools",
    "organization_colleges",
    "organization_departments",
    "teacher_organization_memberships",
    "students",
    "student_shared_teacher_notes",
    "classes",
    "courses",
    "class_offerings",
    "assignments",
    "submissions",
    "submission_files",
    "submission_drafts",
    "submission_draft_files",
    "student_feedback_review_notes",
    "student_login_audit_logs",
    "student_password_reset_requests",
    "academic_semesters",
    "teacher_calendar_events",
    "teacher_academic_course_sync_items",
    "teacher_academic_course_session_occurrences",
    "teacher_academic_entity_bindings",
    "teacher_academic_sync_plans",
    "teacher_academic_roster_sync_items",
    "teacher_academic_roster_memberships",
    "teacher_academic_teaching_class_mappings",
    "teacher_academic_invigilation_items",
    "teacher_academic_course_exam_items",
    "teacher_academic_exam_roster_items",
    "teacher_academic_exam_roster_students",
    "teacher_academic_teaching_places",
    "academic_semester_calendar_days",
    "textbooks",
    "course_lessons",
    "class_offering_sessions",
    "ai_class_configs",
    "course_files",
    "chunked_uploads",
    "course_materials",
    "course_material_assignments",
    "session_material_generation_tasks",
    "material_ai_import_records",
    "exam_papers",
    "teacher_git_credentials",
    "teacher_academic_system_credentials",
    "teacher_smart_classroom_credentials",
    "teacher_gongwen_credentials",
    "gongwen_documents",
    "teacher_gongwen_follow_settings",
    "gongwen_follow_hits",
    "smart_classroom_schedule_items",
    "smart_classroom_checkin_sessions",
    "smart_classroom_checkin_students",
    "smart_attendance_daily_tasks",
    "smart_attendance_student_advice",
    "teacher_email_configs",
    "email_outbox",
    "email_worker_heartbeats",
    "scheduled_tasks",
    "scheduled_task_worker_heartbeats",
    "agent_tasks",
    "agent_task_events",
    "agent_task_composers",
    "agent_runtime_api_keys",
    "agent_runtime_key_checks",
    "agent_runtime_usage_snapshots",
    "assignment_wrong_answer_ai_cache",
    "exam_paper_difficulty_ai_cache",
    "blog_news_crawler_runs",
    "blog_news_crawler_config",
    "blog_news_crawler_items",
    "blog_sections",
    "blog_posts",
    "blog_post_editorial_metadata",
    "blog_comments",
    "blog_likes",
    "blog_bookmarks",
    "blog_post_views",
    "blog_opportunities",
    "blog_opportunity_user_states",
    "blog_follows",
    "blog_reports",
    "blog_attachments",
    "blog_media_assets",
    "blog_moderation_logs",
    "blog_ai_reply_jobs",
    "electronic_signatures",
    "signature_usage_logs",
    "signature_access_requests",
    "signature_function_points",
    "signature_access_request_items",
    "signature_access_request_reviewers",
    "signature_point_flows",
    "signature_point_flow_items",
    "signature_point_bindings",
    "signature_image_versions",
    "identity_appointments",
    "assignment_wrong_summary_jobs",
    "classroom_behavior_events",
    "classroom_behavior_states",
    "classroom_behavior_profiles",
    "ui_copy_snapshots",
    "discussion_mood_snapshots",
    "ai_chat_sessions",
    "ai_chat_messages",
    "ai_psychology_profiles",
    "message_center_notifications",
    "private_messages",
    "private_message_blocks",
    "private_message_audit_logs",
    "private_message_attachments",
    "private_message_ai_jobs",
    "study_groups",
    "study_group_members",
    "study_group_files",
    "group_submissions",
    "peer_reviews",
    "classroom_live_activities",
    "classroom_live_options",
    "classroom_live_responses",
    "classroom_live_questions",
    "classroom_live_help_signals",
    "classroom_todos",
    "app_feedback",
    "app_feedback_attachments",
    "chat_logs",
    "chat_log_migrations",
    "discussion_attachments",
    "custom_emojis",
    "emoji_usage_stats",
    "learning_material_progress",
    "learning_progress_snapshots",
    "cultivation_score_events",
    "cultivation_score_event_archives",
    "cultivation_weekly_snapshots",
    "cultivation_alerts",
    "ai_usage_log",
    *AI_JOB_REQUIRED_POSTGRES_COLUMNS,
    "learning_stage_status",
    "learning_stage_exam_attempts",
    "learning_certificates",
    "student_learning_path_item_states",
    "student_portfolio_items",
    "student_portfolio_reflections",
    "student_growth_events",
)


def _fetch_mappings(
    conn: Any,
    sql: str,
    params: Sequence[Any] | None = None,
    *,
    columns: Sequence[str],
) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, tuple(params or ()))
    return rows_to_mappings(cursor.fetchall(), columns)


def _public_tables(conn: Any) -> set[str]:
    rows = _fetch_mappings(
        conn,
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = ?
          AND table_type = ?
        """,
        ("public", "BASE TABLE"),
        columns=("table_name",),
    )
    return {str(row["table_name"]) for row in rows}


def _public_columns(conn: Any) -> dict[str, set[str]]:
    rows = _fetch_mappings(
        conn,
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = ?
        """,
        ("public",),
        columns=("table_name", "column_name"),
    )
    columns_by_table: dict[str, set[str]] = {}
    for row in rows:
        table_name = str(row["table_name"])
        columns_by_table.setdefault(table_name, set()).add(str(row["column_name"]))
    return columns_by_table


def _count_rows(conn: Any, table: str) -> int | None:
    cursor = conn.execute(f"SELECT COUNT(*) AS row_count FROM {quote_identifier(table)}")
    rows = rows_to_mappings(cursor.fetchall(), ("row_count",))
    if not rows:
        return None
    value = rows[0].get("row_count")
    return int(value) if value is not None else None


def _index_exists(conn: Any, index_name: str) -> bool:
    rows = conn.execute(
        """
        SELECT 1 AS exists_flag
        FROM pg_indexes
        WHERE schemaname = ?
          AND indexname = ?
        LIMIT 1
        """,
        ("public", index_name),
    ).fetchall()
    return bool(rows)


def _duplicate_unique_key_rows(conn: Any, table: str, columns: Sequence[str]) -> list[dict[str, Any]]:
    column_sql = ", ".join(quote_identifier(column) for column in columns)
    rows = conn.execute(
        f"""
        SELECT {column_sql}, COUNT(*) AS row_count
        FROM {quote_identifier(table)}
        GROUP BY {column_sql}
        HAVING COUNT(*) > 1
        LIMIT 5
        """
    ).fetchall()
    return rows_to_mappings(rows, (*columns, "row_count"))


def ensure_postgres_runtime_tables(conn: Any) -> dict[str, Any]:
    table_names = _public_tables(conn)
    created_tables: list[str] = []
    for table, sql in POSTGRES_RUNTIME_TABLE_DEFINITIONS.items():
        if table in table_names:
            continue
        conn.execute(sql)
        created_tables.append(table)
    return {
        "created_tables": created_tables,
        "schema_writes_executed": bool(created_tables),
    }


def _ensure_postgres_classroom_todo_optional_scope(conn: Any, table_names: set[str]) -> bool:
    if "classroom_todos" not in table_names:
        return False

    # Multiple app containers can start together during a deploy. Serialize
    # this one-time constraint repair inside their existing transactions.
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext(?))",
        ("lanshare:classroom_todos:optional-scope",),
    )
    nullable_rows = _fetch_mappings(
        conn,
        """
        SELECT is_nullable
        FROM information_schema.columns
        WHERE table_schema = ?
          AND table_name = ?
          AND column_name = ?
        """,
        ("public", "classroom_todos", "class_offering_id"),
        columns=("is_nullable",),
    )
    foreign_keys = _fetch_mappings(
        conn,
        """
        SELECT tc.constraint_name, rc.delete_rule
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON kcu.constraint_schema = tc.constraint_schema
         AND kcu.constraint_name = tc.constraint_name
        JOIN information_schema.referential_constraints rc
          ON rc.constraint_schema = tc.constraint_schema
         AND rc.constraint_name = tc.constraint_name
        WHERE tc.table_schema = ?
          AND tc.table_name = ?
          AND tc.constraint_type = 'FOREIGN KEY'
          AND kcu.column_name = ?
        """,
        ("public", "classroom_todos", "class_offering_id"),
        columns=("constraint_name", "delete_rule"),
    )

    changed = False
    if nullable_rows and str(nullable_rows[0].get("is_nullable") or "").upper() != "YES":
        conn.execute(
            'ALTER TABLE "classroom_todos" '
            'ALTER COLUMN "class_offering_id" DROP NOT NULL'
        )
        changed = True

    correct_foreign_keys = [
        row for row in foreign_keys if str(row.get("delete_rule") or "").upper() == "SET NULL"
    ]
    for row in foreign_keys:
        if str(row.get("delete_rule") or "").upper() == "SET NULL":
            continue
        constraint_name = str(row.get("constraint_name") or "").strip()
        if constraint_name:
            conn.execute(
                f'ALTER TABLE "classroom_todos" DROP CONSTRAINT IF EXISTS '
                f'{quote_identifier(constraint_name)}'
            )
            changed = True

    if not correct_foreign_keys:
        conn.execute(
            'ALTER TABLE "classroom_todos" '
            'ADD CONSTRAINT "fk_classroom_todos_optional_offering" '
            'FOREIGN KEY ("class_offering_id") REFERENCES "class_offerings" ("id") '
            'ON DELETE SET NULL'
        )
        changed = True
    return changed


def _ensure_postgres_signature_requester_optional(conn: Any, table_names: set[str]) -> bool:
    """Students may file signature requests, so the legacy teacher column must accept NULL."""
    if "signature_access_requests" not in table_names:
        return False
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext(?))",
        ("lanshare:signature_access_requests:optional-requester-teacher",),
    )
    nullable_rows = _fetch_mappings(
        conn,
        """
        SELECT is_nullable
        FROM information_schema.columns
        WHERE table_schema = ?
          AND table_name = ?
          AND column_name = ?
        """,
        ("public", "signature_access_requests", "requester_teacher_id"),
        columns=("is_nullable",),
    )
    if not nullable_rows or str(nullable_rows[0].get("is_nullable") or "").upper() == "YES":
        return False
    conn.execute(
        'ALTER TABLE "signature_access_requests" '
        'ALTER COLUMN "requester_teacher_id" DROP NOT NULL'
    )
    return True


def ensure_postgres_runtime_constraints(conn: Any) -> dict[str, Any]:
    created_indexes: list[str] = []
    skipped_indexes: list[str] = []
    table_names = _public_tables(conn)
    classroom_todo_scope_repaired = _ensure_postgres_classroom_todo_optional_scope(
        conn,
        table_names,
    )
    signature_requester_repaired = _ensure_postgres_signature_requester_optional(
        conn,
        table_names,
    )
    for index_name, table, columns in POSTGRES_RUNTIME_UNIQUE_INDEXES:
        if table not in table_names:
            skipped_indexes.append(index_name)
            continue
        if _index_exists(conn, index_name):
            continue
        duplicate_rows = _duplicate_unique_key_rows(conn, table, columns)
        if duplicate_rows:
            raise DatabaseProgrammingError(
                "PostgreSQL schema repair refused to create unique index "
                f"{index_name} because duplicate keys exist in {table}: {duplicate_rows}"
            )
        column_sql = ", ".join(quote_identifier(column) for column in columns)
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {quote_identifier(index_name)} "
            f"ON {quote_identifier(table)} ({column_sql})"
        )
        created_indexes.append(index_name)
    return {
        "created_indexes": created_indexes,
        "skipped_indexes": skipped_indexes,
        "classroom_todo_scope_repaired": classroom_todo_scope_repaired,
        "signature_requester_repaired": signature_requester_repaired,
        "schema_writes_executed": bool(
            created_indexes or classroom_todo_scope_repaired or signature_requester_repaired
        ),
    }


def ensure_postgres_runtime_columns(conn: Any) -> dict[str, Any]:
    table_names = _public_tables(conn)
    columns_by_table = _public_columns(conn)
    added_columns: dict[str, list[str]] = {}
    skipped_tables: list[str] = []
    for table, definitions in POSTGRES_RUNTIME_COLUMN_DEFINITIONS.items():
        if table not in table_names:
            skipped_tables.append(table)
            continue
        actual_columns = columns_by_table.get(table, set())
        for column_name, column_def in definitions.items():
            if column_name in actual_columns:
                continue
            conn.execute(
                f"ALTER TABLE {quote_identifier(table)} "
                f"ADD COLUMN IF NOT EXISTS {quote_identifier(column_name)} {column_def}"
            )
            added_columns.setdefault(table, []).append(column_name)

    legacy_material_mastery_repaired = False
    if "learning_material_progress" in table_names:
        final_columns = set(columns_by_table.get("learning_material_progress", set()))
        final_columns.update(added_columns.get("learning_material_progress", ()))
        if {"completed", "mastered", "mastered_at", "mastery_source", "progress_rule_version"}.issubset(final_columns):
            conn.execute(
                """
                UPDATE learning_material_progress
                SET mastered = 1,
                    mastered_at = COALESCE(mastered_at, last_viewed_at::text, updated_at::text, CURRENT_TIMESTAMP::text),
                    mastery_source = CASE
                        WHEN COALESCE(TRIM(mastery_source), '') = '' THEN 'legacy_completed'
                        ELSE mastery_source
                    END,
                    progress_rule_version = 'legacy_completed_full_credit'
                WHERE completed = 1
                  AND COALESCE(mastered, 0) = 0
                """
            )
            legacy_material_mastery_repaired = True

    return {
        "added_columns": added_columns,
        "skipped_tables": skipped_tables,
        "legacy_material_mastery_repaired": legacy_material_mastery_repaired,
        "schema_writes_executed": bool(added_columns),
    }


def build_postgres_schema_report(conn: Any) -> dict[str, Any]:
    table_names = _public_tables(conn)
    columns_by_table = _public_columns(conn)
    missing_tables = [
        table
        for table in REQUIRED_POSTGRES_TABLES
        if table not in table_names
    ]
    missing_columns: dict[str, list[str]] = {}
    for table, required_columns in REQUIRED_POSTGRES_COLUMNS.items():
        if table in missing_tables:
            continue
        actual_columns = columns_by_table.get(table, set())
        missing = [column for column in required_columns if column not in actual_columns]
        if missing:
            missing_columns[table] = missing

    row_counts: dict[str, int | None] = {}
    for table in REQUIRED_POSTGRES_TABLES:
        if table in missing_tables:
            row_counts[table] = None
            continue
        row_counts[table] = _count_rows(conn, table)

    return {
        "status": "ok" if not missing_tables and not missing_columns else "failed",
        "required_table_count": len(REQUIRED_POSTGRES_TABLES),
        "present_required_table_count": len(REQUIRED_POSTGRES_TABLES) - len(missing_tables),
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "row_counts": row_counts,
        "schema_writes_executed": False,
    }


def validate_postgres_schema(conn: Any) -> dict[str, Any]:
    report = build_postgres_schema_report(conn)
    if report["status"] != "ok":
        details: list[str] = []
        if report["missing_tables"]:
            details.append("missing tables: " + ", ".join(report["missing_tables"]))
        if report["missing_columns"]:
            formatted = [
                f"{table}({', '.join(columns)})"
                for table, columns in report["missing_columns"].items()
            ]
            details.append("missing columns: " + "; ".join(formatted))
        raise DatabaseProgrammingError(
            "PostgreSQL schema validation failed; refusing to run SQLite schema initializers. "
            + " ".join(details)
        )
    return report
