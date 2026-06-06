import re
import unittest
from pathlib import Path
from unittest.mock import patch

from classroom_app import config, database
from classroom_app.db.errors import DatabaseProgrammingError
from classroom_app.db.postgres_schema import (
    POSTGRES_RUNTIME_UNIQUE_INDEXES,
    REQUIRED_POSTGRES_COLUMNS,
    REQUIRED_POSTGRES_TABLES,
    build_postgres_schema_report,
    ensure_postgres_runtime_constraints,
    validate_postgres_schema,
)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class FakePostgresConnection:
    def __init__(self, *, missing_tables=(), missing_columns=None):
        self.missing_tables = set(missing_tables)
        self.missing_columns = missing_columns or {}
        self.executed_sql = []
        self.closed = False
        self.committed = False
        self.rolled_back = False

    def execute(self, sql, params=()):
        self.executed_sql.append((sql, params))
        normalized = " ".join(str(sql).split())
        if "information_schema.tables" in normalized:
            rows = [
                {"table_name": table}
                for table in REQUIRED_POSTGRES_TABLES
                if table not in self.missing_tables
            ]
            return FakeCursor(rows)
        if "information_schema.columns" in normalized:
            rows = []
            for table, columns in REQUIRED_POSTGRES_COLUMNS.items():
                if table in self.missing_tables:
                    continue
                missing = set(self.missing_columns.get(table, ()))
                rows.extend(
                    {"table_name": table, "column_name": column}
                    for column in columns
                    if column not in missing
                )
            return FakeCursor(rows)
        if normalized.startswith("SELECT COUNT(*) AS row_count FROM"):
            return FakeCursor([{"row_count": 1}])
        if "pg_indexes" in normalized:
            return FakeCursor([])
        if "HAVING COUNT(*) > 1" in normalized:
            return FakeCursor([])
        if normalized.startswith("CREATE UNIQUE INDEX IF NOT EXISTS"):
            return FakeCursor([])
        raise AssertionError(f"unexpected sql: {sql}")

    def close(self):
        self.closed = True

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class FakePostgresConstraintConnection(FakePostgresConnection):
    def __init__(self, *, existing_indexes=(), duplicate_indexes=()):
        super().__init__()
        self.existing_indexes = set(existing_indexes)
        self.duplicate_indexes = set(duplicate_indexes)

    def execute(self, sql, params=()):
        self.executed_sql.append((sql, params))
        normalized = " ".join(str(sql).split())
        if "information_schema.tables" in normalized:
            rows = [{"table_name": table} for table in REQUIRED_POSTGRES_TABLES]
            return FakeCursor(rows)
        if "pg_indexes" in normalized:
            index_name = params[1]
            return FakeCursor([{"exists_flag": 1}] if index_name in self.existing_indexes else [])
        if "HAVING COUNT(*) > 1" in normalized:
            for index_name, table, _columns in POSTGRES_RUNTIME_UNIQUE_INDEXES:
                if table in normalized and index_name in self.duplicate_indexes:
                    return FakeCursor([{"row_count": 2}])
            return FakeCursor([])
        if normalized.startswith("CREATE UNIQUE INDEX IF NOT EXISTS"):
            return FakeCursor([])
        return super().execute(sql, params)


class PostgresSchemaValidationTests(unittest.TestCase):
    def test_build_report_passes_without_writing_schema(self):
        conn = FakePostgresConnection()

        report = build_postgres_schema_report(conn)

        self.assertEqual("ok", report["status"])
        self.assertFalse(report["schema_writes_executed"])
        self.assertEqual(len(REQUIRED_POSTGRES_TABLES), report["present_required_table_count"])
        self.assertFalse(any(sql.strip().upper().startswith(("CREATE", "ALTER", "DROP")) for sql, _ in conn.executed_sql))

    def test_ensure_runtime_constraints_creates_missing_unique_indexes(self):
        conn = FakePostgresConstraintConnection()

        report = ensure_postgres_runtime_constraints(conn)

        self.assertTrue(report["schema_writes_executed"])
        self.assertIn("idx_learning_stage_status_unique_stage", report["created_indexes"])
        created_sql = "\n".join(str(sql) for sql, _ in conn.executed_sql)
        self.assertIn('CREATE UNIQUE INDEX IF NOT EXISTS "idx_learning_stage_status_unique_stage"', created_sql)

    def test_ensure_runtime_constraints_refuses_duplicate_keys(self):
        conn = FakePostgresConstraintConnection(
            duplicate_indexes={"idx_learning_stage_status_unique_stage"}
        )

        with self.assertRaises(DatabaseProgrammingError):
            ensure_postgres_runtime_constraints(conn)

    def test_required_schema_covers_all_static_sqlite_schema_tables(self):
        repo_root = Path(__file__).resolve().parents[1]
        schema_tables: set[str] = set()
        for path in (repo_root / "classroom_app" / "db").glob("schema*.py"):
            text = path.read_text(encoding="utf-8")
            schema_tables.update(
                match.group(1)
                for match in re.finditer(
                    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)",
                    text,
                    re.IGNORECASE,
                )
            )

        self.assertTrue(schema_tables)
        self.assertEqual(set(), schema_tables - set(REQUIRED_POSTGRES_TABLES))

    def test_required_schema_covers_runtime_chat_tables(self):
        for table_name in ("chat_logs", "chat_log_migrations", "discussion_attachments"):
            self.assertIn(table_name, REQUIRED_POSTGRES_TABLES)
            self.assertIn(table_name, REQUIRED_POSTGRES_COLUMNS)
        self.assertIn("logged_at", REQUIRED_POSTGRES_COLUMNS["chat_logs"])
        self.assertIn("migrated_at", REQUIRED_POSTGRES_COLUMNS["chat_log_migrations"])
        self.assertIn("preview_file_hash", REQUIRED_POSTGRES_COLUMNS["discussion_attachments"])

    def test_required_schema_covers_email_worker_runtime_tables(self):
        for table_name in ("teacher_email_configs", "email_outbox", "email_worker_heartbeats"):
            self.assertIn(table_name, REQUIRED_POSTGRES_TABLES)
            self.assertIn(table_name, REQUIRED_POSTGRES_COLUMNS)
        for column in ("attempt_count", "sent_at", "last_error"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["email_outbox"])
        self.assertIn("smtp_password_encrypted", REQUIRED_POSTGRES_COLUMNS["teacher_email_configs"])
        self.assertIn("queue_depth", REQUIRED_POSTGRES_COLUMNS["email_worker_heartbeats"])

    def test_required_schema_covers_ai_chat_runtime_tables(self):
        for table_name in ("ai_chat_sessions", "ai_chat_messages", "ai_psychology_profiles"):
            self.assertIn(table_name, REQUIRED_POSTGRES_TABLES)
            self.assertIn(table_name, REQUIRED_POSTGRES_COLUMNS)
        for column in ("thinking_content", "final_answer", "attachments_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["ai_chat_messages"])
        for column in ("hidden_premise_prompt", "support_strategy", "raw_payload"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["ai_psychology_profiles"])

    def test_required_schema_covers_wrong_summary_runtime_tables(self):
        for table_name in (
            "assignment_wrong_answer_ai_cache",
            "exam_paper_difficulty_ai_cache",
            "assignment_wrong_summary_jobs",
        ):
            self.assertIn(table_name, REQUIRED_POSTGRES_TABLES)
            self.assertIn(table_name, REQUIRED_POSTGRES_COLUMNS)
        for column in ("answer_signature", "result_json", "updated_at"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["assignment_wrong_answer_ai_cache"])
        for column in ("questions_signature", "prompt_version", "result_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["exam_paper_difficulty_ai_cache"])
        for column in ("teacher_id", "pending_text_questions", "error_message", "created_at"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["assignment_wrong_summary_jobs"])

    def test_required_schema_covers_behavior_runtime_tables(self):
        for table_name in (
            "classroom_behavior_events",
            "classroom_behavior_states",
            "classroom_behavior_profiles",
        ):
            self.assertIn(table_name, REQUIRED_POSTGRES_TABLES)
            self.assertIn(table_name, REQUIRED_POSTGRES_COLUMNS)
        for column in ("user_pk", "user_role", "action_type", "payload_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["classroom_behavior_events"])
        for column in (
            "profile_generation_pending",
            "next_profile_due_at",
            "online_accumulated_seconds",
            "last_presence_at",
            "ai_panel_open_total_seconds",
        ):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["classroom_behavior_states"])
        for column in (
            "trigger_event_id",
            "activity_count_snapshot",
            "support_strategy",
            "hidden_premise_prompt",
            "trigger_mode",
            "raw_payload",
        ):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["classroom_behavior_profiles"])

    def test_required_schema_covers_smart_attendance_runtime_tables(self):
        for table_name in (
            "teacher_smart_classroom_credentials",
            "smart_classroom_schedule_items",
            "smart_classroom_checkin_sessions",
            "smart_classroom_checkin_students",
            "smart_attendance_daily_tasks",
            "smart_attendance_student_advice",
        ):
            self.assertIn(table_name, REQUIRED_POSTGRES_TABLES)
            self.assertIn(table_name, REQUIRED_POSTGRES_COLUMNS)
        for column in ("password_encrypted", "last_verified_at", "access_method_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["teacher_smart_classroom_credentials"])
        for column in ("remote_schedule_id", "match_status", "metadata_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["smart_classroom_schedule_items"])
        for column in ("remote_checkin_id", "checked_count", "late_or_early_count", "metadata_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["smart_classroom_checkin_sessions"])
        for column in ("student_number", "status_label", "local_match_status"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["smart_classroom_checkin_students"])
        for column in ("task_type", "task_date", "raw_payload_json", "finished_at"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["smart_attendance_daily_tasks"])
        for column in ("fingerprint", "attempts", "context_json", "last_error"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["smart_attendance_student_advice"])

    def test_required_schema_covers_account_support_and_integration_tables(self):
        for table_name in (
            "student_login_audit_logs",
            "student_password_reset_requests",
            "classroom_todos",
            "app_feedback",
            "app_feedback_attachments",
            "teacher_git_credentials",
            "teacher_academic_system_credentials",
            "teacher_academic_teaching_places",
        ):
            self.assertIn(table_name, REQUIRED_POSTGRES_TABLES)
            self.assertIn(table_name, REQUIRED_POSTGRES_COLUMNS)
        for column in ("login_sequence", "identifier_value", "device_label", "logged_at"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["student_login_audit_logs"])
        for column in ("status", "request_student_id_number", "reviewed_by_teacher_id", "review_note"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["student_password_reset_requests"])
        for column in ("owner_role", "owner_user_pk", "deleted_at", "metadata_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["classroom_todos"])
        for column in ("feedback_type", "section", "page_url", "status"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["app_feedback"])
        for column in ("feedback_id", "file_hash", "mime_type"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["app_feedback_attachments"])
        for column in ("remote_key", "auth_mode", "secret_encrypted", "last_used_at"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["teacher_git_credentials"])
        for column in ("school_code", "password_encrypted", "last_verified_at", "access_method_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["teacher_academic_system_credentials"])
        for column in ("place_key", "room_name", "seat_count", "sync_batch_id"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["teacher_academic_teaching_places"])

    def test_required_schema_covers_classroom_collaboration_and_live_tables(self):
        for table_name in (
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
        ):
            self.assertIn(table_name, REQUIRED_POSTGRES_TABLES)
            self.assertIn(table_name, REQUIRED_POSTGRES_COLUMNS)
        for column in ("join_policy", "leader_student_id", "metadata_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["study_groups"])
        for column in ("contribution_score", "added_by_user_pk", "left_at"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["study_group_members"])
        for column in ("uploaded_by_user_pk", "file_hash", "metadata_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["study_group_files"])
        for column in ("final_file_id", "blog_post_id", "teacher_feedback_md"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["group_submissions"])
        for column in ("reviewer_student_id", "reviewee_student_id", "share_with_reviewee"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["peer_reviews"])
        for column in ("kind", "allow_anonymous", "settings_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["classroom_live_activities"])
        for column in ("option_key", "is_correct", "sort_order"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["classroom_live_options"])
        for column in ("option_id", "is_anonymous", "metadata_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["classroom_live_responses"])
        for column in ("question_text", "addressed_by_teacher_id", "metadata_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["classroom_live_questions"])
        for column in ("signal_type", "resolved_by_teacher_id", "metadata_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["classroom_live_help_signals"])

    def test_required_schema_covers_agent_runtime_tables(self):
        for table_name in (
            "agent_tasks",
            "agent_task_events",
            "agent_task_composers",
            "agent_runtime_api_keys",
            "agent_runtime_key_checks",
            "agent_runtime_usage_snapshots",
        ):
            self.assertIn(table_name, REQUIRED_POSTGRES_TABLES)
            self.assertIn(table_name, REQUIRED_POSTGRES_COLUMNS)
        for column in ("event_type", "detail_json", "created_at"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["agent_task_events"])
        for column in ("teacher_id", "teacher_name", "page_label"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["agent_task_composers"])
        for column in ("key_fingerprint", "key_encrypted", "last_test_usage_json", "last_used_at"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["agent_runtime_api_keys"])
        for column in ("key_id", "response_ms", "usage_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["agent_runtime_key_checks"])
        for column in ("runtime_url", "usage_json", "fetched_by_teacher_id"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["agent_runtime_usage_snapshots"])

    def test_required_schema_covers_blog_social_and_crawler_tables(self):
        for table_name in (
            "blog_news_crawler_config",
            "blog_news_crawler_runs",
            "blog_news_crawler_items",
            "blog_posts",
            "blog_comments",
            "blog_likes",
            "blog_bookmarks",
            "blog_attachments",
            "blog_media_assets",
            "blog_moderation_logs",
            "blog_ai_reply_jobs",
        ):
            self.assertIn(table_name, REQUIRED_POSTGRES_TABLES)
            self.assertIn(table_name, REQUIRED_POSTGRES_COLUMNS)
        for column in ("source_templates_json", "worker_status", "updated_by_teacher_id"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["blog_news_crawler_config"])
        for column in ("trigger_source", "candidate_count", "log_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["blog_news_crawler_runs"])
        for column in ("url_hash", "duplicate_of_post_id", "raw_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["blog_news_crawler_items"])
        for column in ("author_identity", "visibility", "system_tags_json", "bookmark_count"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["blog_posts"])
        for column in ("parent_comment_id", "emoji_payload_json", "attachments_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["blog_comments"])
        for column in ("target_type", "user_identity", "user_pk"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["blog_likes"])
        for column in ("post_id", "user_identity", "created_at"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["blog_bookmarks"])
        for column in ("file_hash", "image_width", "display_order"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["blog_attachments"])
        for column in ("uploader_identity", "file_hash", "updated_at"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["blog_media_assets"])
        for column in ("moderator_identity", "action", "reason"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["blog_moderation_logs"])
        for column in ("trigger_type", "assistant_comment_id", "error_message"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["blog_ai_reply_jobs"])

    def test_required_schema_covers_private_message_controls_and_emoji_tables(self):
        for table_name in (
            "private_message_blocks",
            "private_message_audit_logs",
            "custom_emojis",
            "emoji_usage_stats",
        ):
            self.assertIn(table_name, REQUIRED_POSTGRES_TABLES)
            self.assertIn(table_name, REQUIRED_POSTGRES_COLUMNS)
        for column in ("owner_identity", "blocked_identity", "blocked_display_name"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["private_message_blocks"])
        for column in ("message_id", "sender_identity", "recipient_identity"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["private_message_audit_logs"])
        for column in ("owner_user_role", "file_hash", "image_height"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["custom_emojis"])
        for column in ("emoji_type", "emoji_key", "usage_count"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["emoji_usage_stats"])

    def test_required_schema_covers_foundation_upload_and_snapshot_tables(self):
        for table_name in (
            "system_settings",
            "teacher_onboarding_state",
            "user_sessions",
            "organization_schools",
            "organization_colleges",
            "organization_departments",
            "teacher_organization_memberships",
            "student_shared_teacher_notes",
            "academic_semester_calendar_days",
            "course_files",
            "chunked_uploads",
            "submission_draft_files",
            "student_feedback_review_notes",
            "ui_copy_snapshots",
            "discussion_mood_snapshots",
        ):
            self.assertIn(table_name, REQUIRED_POSTGRES_TABLES)
            self.assertIn(table_name, REQUIRED_POSTGRES_COLUMNS)
        for column in ("key", "value", "updated_at"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["system_settings"])
        for column in ("dismissed_at", "completed_at", "dismiss_reason"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["teacher_onboarding_state"])
        for column in ("session_user_key", "expires_at", "updated_at"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["user_sessions"])
        for column in ("school_code", "display_order", "deactivated_at"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["organization_schools"])
        for column in ("college_name", "is_active", "updated_by_teacher_id"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["organization_colleges"])
        for column in ("department_name", "source", "deactivated_at"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["organization_departments"])
        for column in ("teacher_id", "is_primary", "deactivated_at"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["teacher_organization_memberships"])
        for column in ("note_text", "created_by_teacher_id", "updated_by_teacher_id"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["student_shared_teacher_notes"])
        for column in ("date", "week_index", "metadata_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["academic_semester_calendar_days"])
        for column in ("file_hash", "original_link", "uploaded_by_teacher_id"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["course_files"])
        for column in ("upload_id", "received_chunks", "temp_dir"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["chunked_uploads"])
        for column in ("draft_id", "relative_path", "file_hash"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["submission_draft_files"])
        for column in ("question_key", "reflection", "metadata_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["student_feedback_review_notes"])
        for column in ("snapshot_date", "payload_json", "generated_at"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["ui_copy_snapshots"])
        for column in ("mood_label", "latest_message_id", "raw_payload_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["discussion_mood_snapshots"])

    def test_required_schema_covers_learning_portfolio_and_signature_tables(self):
        for table_name in (
            "learning_material_progress",
            "learning_stage_status",
            "student_learning_path_item_states",
            "student_portfolio_items",
            "student_portfolio_reflections",
            "student_growth_events",
            "electronic_signatures",
            "signature_usage_logs",
            "signature_access_requests",
        ):
            self.assertIn(table_name, REQUIRED_POSTGRES_TABLES)
            self.assertIn(table_name, REQUIRED_POSTGRES_COLUMNS)
        for column in ("material_id", "active_seconds", "max_scroll_ratio"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["learning_material_progress"])
        for column in ("stage_key", "readiness_score", "certificate_id"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["learning_stage_status"])
        for column in ("item_key", "snoozed_until", "metadata_json"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["student_learning_path_item_states"])
        for column in ("source_type", "cover_file_hash", "teacher_recommended_by"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["student_portfolio_items"])
        for column in ("reflection_text", "ability_tags_json", "evidence_notes"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["student_portfolio_reflections"])
        for column in ("event_type", "source_id", "importance"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["student_growth_events"])
        for column in ("scope_level", "stored_path", "deleted_at"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["electronic_signatures"])
        for column in ("signature_name_snapshot", "context_label", "user_agent"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["signature_usage_logs"])
        for column in ("requester_teacher_id", "reviewed_at", "reviewed_by_teacher_id"):
            self.assertIn(column, REQUIRED_POSTGRES_COLUMNS["signature_access_requests"])

    def test_validate_schema_blocks_missing_table(self):
        conn = FakePostgresConnection(missing_tables=("submissions",))

        with self.assertRaises(DatabaseProgrammingError) as ctx:
            validate_postgres_schema(conn)

        self.assertIn("submissions", str(ctx.exception))

    def test_validate_schema_blocks_missing_column(self):
        conn = FakePostgresConnection(missing_columns={"submission_files": ("stored_path",)})

        with self.assertRaises(DatabaseProgrammingError) as ctx:
            validate_postgres_schema(conn)

        self.assertIn("submission_files", str(ctx.exception))
        self.assertIn("stored_path", str(ctx.exception))

    def test_init_database_dispatches_postgres_validation_without_sqlite_initializers(self):
        original_engine = config.DB_ENGINE
        config.DB_ENGINE = "postgres"
        conn = FakePostgresConnection()
        try:
            with patch("classroom_app.db.schema.get_db_connection", return_value=conn), patch(
                "classroom_app.db.schema.ensure_foundation_schema"
            ) as sqlite_initializer:
                report = database.init_database()
        finally:
            config.DB_ENGINE = original_engine

        self.assertEqual("ok", report["status"])
        self.assertTrue(conn.closed)
        sqlite_initializer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
