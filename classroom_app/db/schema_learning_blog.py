import sqlite3

from .migrations import _ensure_resource_scope_schema, _sync_organization_catalog_from_existing
from .schema_cultivation_progress import ensure_cultivation_progress_schema


def ensure_learning_blog_signature_schema(conn: sqlite3.Connection) -> None:
    ensure_cultivation_progress_schema(conn, engine="sqlite")
    conn.execute('''
        CREATE TABLE IF NOT EXISTS learning_material_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_offering_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            session_id INTEGER,
            view_count INTEGER NOT NULL DEFAULT 0,
            accumulated_seconds INTEGER NOT NULL DEFAULT 0,
            active_seconds INTEGER NOT NULL DEFAULT 0,
            max_scroll_ratio REAL NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            mastered INTEGER NOT NULL DEFAULT 0,
            mastered_at TEXT,
            mastery_source TEXT NOT NULL DEFAULT '',
            mastery_attempts INTEGER NOT NULL DEFAULT 0,
            mastery_last_attempt_json TEXT DEFAULT '{}',
            progress_rule_version TEXT NOT NULL DEFAULT 'material_mastery_v2',
            first_viewed_at TEXT,
            last_viewed_at TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            metadata_json TEXT DEFAULT '{}',
            FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
            FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
            FOREIGN KEY (material_id) REFERENCES course_materials (id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES class_offering_sessions (id) ON DELETE SET NULL,
            UNIQUE (class_offering_id, student_id, material_id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS learning_stage_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_offering_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            stage_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'locked',
            progress_score REAL NOT NULL DEFAULT 0,
            readiness_score REAL NOT NULL DEFAULT 0,
            unlocked_at TEXT,
            passed_at TEXT,
            last_exam_assignment_id INTEGER,
            certificate_id INTEGER,
            last_calculated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            metadata_json TEXT DEFAULT '{}',
            FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
            FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
            FOREIGN KEY (last_exam_assignment_id) REFERENCES assignments (id) ON DELETE SET NULL,
            FOREIGN KEY (certificate_id) REFERENCES learning_certificates (id) ON DELETE SET NULL,
            UNIQUE (class_offering_id, student_id, stage_key)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS learning_stage_exam_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_offering_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            stage_key TEXT NOT NULL,
            assignment_id INTEGER,
            exam_paper_id TEXT,
            status TEXT NOT NULL DEFAULT 'generated',
            score REAL,
            generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            submitted_at TEXT,
            graded_at TEXT,
            passed_at TEXT,
            ai_error TEXT,
            metadata_json TEXT DEFAULT '{}',
            FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
            FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
            FOREIGN KEY (assignment_id) REFERENCES assignments (id) ON DELETE SET NULL,
            FOREIGN KEY (exam_paper_id) REFERENCES exam_papers (id) ON DELETE SET NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS learning_certificates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_offering_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            stage_key TEXT NOT NULL,
            level_key TEXT NOT NULL,
            level_name TEXT NOT NULL,
            tier INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL,
            certificate_code TEXT NOT NULL UNIQUE,
            issued_at TEXT DEFAULT CURRENT_TIMESTAMP,
            revealed_at TEXT,
            metadata_json TEXT DEFAULT '{}',
            FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
            FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
            UNIQUE (class_offering_id, student_id, stage_key)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS student_learning_path_item_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            class_offering_id INTEGER,
            item_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            pinned INTEGER NOT NULL DEFAULT 0,
            reflection TEXT DEFAULT '',
            next_action TEXT DEFAULT '',
            completed_at TEXT,
            snoozed_until TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            metadata_json TEXT DEFAULT '{}',
            FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
            FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
            UNIQUE (student_id, item_key)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS student_portfolio_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            class_offering_id INTEGER,
            course_id INTEGER,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            artifact_type TEXT NOT NULL DEFAULT 'homework',
            cover_file_hash TEXT NOT NULL DEFAULT '',
            visibility TEXT NOT NULL DEFAULT 'private',
            featured INTEGER NOT NULL DEFAULT 0,
            teacher_recommended INTEGER NOT NULL DEFAULT 0,
            teacher_recommended_by INTEGER,
            teacher_recommended_at TEXT,
            sort_order INTEGER NOT NULL DEFAULT 100,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            metadata_json TEXT DEFAULT '{}',
            FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
            FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE SET NULL,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE SET NULL,
            FOREIGN KEY (teacher_recommended_by) REFERENCES teachers (id) ON DELETE SET NULL,
            UNIQUE (student_id, source_type, source_id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS student_portfolio_reflections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_item_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            reflection_text TEXT NOT NULL DEFAULT '',
            ability_tags_json TEXT NOT NULL DEFAULT '[]',
            evidence_notes TEXT NOT NULL DEFAULT '',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (portfolio_item_id) REFERENCES student_portfolio_items (id) ON DELETE CASCADE,
            FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
            UNIQUE (portfolio_item_id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS student_growth_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            class_offering_id INTEGER,
            event_type TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            occurred_at TEXT DEFAULT CURRENT_TIMESTAMP,
            importance TEXT NOT NULL DEFAULT 'normal',
            metadata_json TEXT DEFAULT '{}',
            FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
            FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE SET NULL
        )
    ''')
    try:
        conn.execute("ALTER TABLE learning_certificates ADD COLUMN revealed_at TEXT")
    except sqlite3.OperationalError:
        pass
    for column_name, column_def in (
        ("mastered", "INTEGER NOT NULL DEFAULT 0"),
        ("mastered_at", "TEXT"),
        ("mastery_source", "TEXT NOT NULL DEFAULT ''"),
        ("mastery_attempts", "INTEGER NOT NULL DEFAULT 0"),
        ("mastery_last_attempt_json", "TEXT DEFAULT '{}'"),
        ("progress_rule_version", "TEXT NOT NULL DEFAULT 'material_mastery_v2'"),
    ):
        try:
            conn.execute(f"ALTER TABLE learning_material_progress ADD COLUMN {column_name} {column_def}")
        except sqlite3.OperationalError:
            pass
    conn.execute(
        """
        UPDATE learning_material_progress
        SET mastered = 1,
            mastered_at = COALESCE(mastered_at, last_viewed_at, updated_at, CURRENT_TIMESTAMP),
            mastery_source = CASE
                WHEN COALESCE(TRIM(mastery_source), '') = '' THEN 'legacy_completed'
                ELSE mastery_source
            END,
            progress_rule_version = 'legacy_completed_full_credit'
        WHERE completed = 1
          AND COALESCE(mastered, 0) = 0
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_learning_material_progress_student "
        "ON learning_material_progress (class_offering_id, student_id, completed, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_learning_material_progress_mastery "
        "ON learning_material_progress (class_offering_id, student_id, mastered, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_learning_stage_status_student "
        "ON learning_stage_status (class_offering_id, student_id, stage_key, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_learning_stage_attempt_assignment "
        "ON learning_stage_exam_attempts (assignment_id, student_id, stage_key)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_learning_stage_attempt_active "
        "ON learning_stage_exam_attempts (class_offering_id, student_id, stage_key, status, generated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_learning_certificates_student "
        "ON learning_certificates (class_offering_id, student_id, tier DESC, issued_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_student_learning_path_state_lookup "
        "ON student_learning_path_item_states (student_id, status, pinned, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_student_learning_path_state_course "
        "ON student_learning_path_item_states (class_offering_id, student_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_student_portfolio_items_student "
        "ON student_portfolio_items (student_id, featured DESC, sort_order ASC, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_student_portfolio_items_source "
        "ON student_portfolio_items (source_type, source_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_student_portfolio_items_course "
        "ON student_portfolio_items (class_offering_id, student_id, visibility)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_student_growth_events_student "
        "ON student_growth_events (student_id, occurred_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_student_growth_events_source "
        "ON student_growth_events (source_type, source_id, student_id)"
    )

    # ── 16. 博客中心 ──
    conn.execute('''
        CREATE TABLE IF NOT EXISTS blog_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_identity TEXT NOT NULL,
            author_role TEXT NOT NULL,
            author_user_pk INTEGER NOT NULL,
            author_display_name TEXT NOT NULL,
            author_display_mode TEXT NOT NULL DEFAULT 'real_name',
            author_avatar_hash TEXT DEFAULT '',
            author_avatar_mime TEXT DEFAULT '',
            title TEXT NOT NULL,
            content_md TEXT NOT NULL DEFAULT '',
            summary TEXT DEFAULT '',
            cover_image_hash TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft',
            visibility TEXT NOT NULL DEFAULT 'public',
            visible_class_id INTEGER,
            visible_user_identities_json TEXT DEFAULT '[]',
            allow_comments INTEGER NOT NULL DEFAULT 1,
            is_pinned INTEGER NOT NULL DEFAULT 0,
            is_featured INTEGER NOT NULL DEFAULT 0,
            pinned_at TEXT,
            featured_at TEXT,
            hot_notified_at TEXT,
            view_count INTEGER NOT NULL DEFAULT 0,
            like_count INTEGER NOT NULL DEFAULT 0,
            comment_count INTEGER NOT NULL DEFAULT 0,
            bookmark_count INTEGER NOT NULL DEFAULT 0,
            system_tags_json TEXT DEFAULT '[]',
            tags_json TEXT DEFAULT '[]',
            edited_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS blog_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            parent_comment_id INTEGER,
            author_identity TEXT NOT NULL,
            author_role TEXT NOT NULL,
            author_user_pk INTEGER NOT NULL,
            author_display_name TEXT NOT NULL,
            content_md TEXT NOT NULL,
            emoji_payload_json TEXT DEFAULT '',
            attachments_json TEXT DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'active',
            like_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (post_id) REFERENCES blog_posts (id) ON DELETE CASCADE,
            FOREIGN KEY (parent_comment_id) REFERENCES blog_comments (id) ON DELETE CASCADE
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS blog_likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            user_identity TEXT NOT NULL,
            user_role TEXT NOT NULL,
            user_pk INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (target_type, target_id, user_identity)
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS blog_bookmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_identity TEXT NOT NULL,
            user_role TEXT NOT NULL,
            user_pk INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (post_id, user_identity),
            FOREIGN KEY (post_id) REFERENCES blog_posts (id) ON DELETE CASCADE
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS blog_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            file_hash TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            image_width INTEGER,
            image_height INTEGER,
            display_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (post_id) REFERENCES blog_posts (id) ON DELETE CASCADE
        )
    ''')

    try:
        conn.execute("ALTER TABLE blog_posts ADD COLUMN hot_notified_at TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "ALTER TABLE blog_posts ADD COLUMN author_display_mode TEXT NOT NULL DEFAULT 'real_name'"
        )
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE blog_posts ADD COLUMN system_tags_json TEXT DEFAULT '[]'")
    except sqlite3.OperationalError:
        pass

    conn.execute('''
        CREATE TABLE IF NOT EXISTS blog_media_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_hash TEXT NOT NULL,
            uploader_identity TEXT NOT NULL,
            uploader_role TEXT NOT NULL,
            uploader_user_pk INTEGER NOT NULL,
            original_filename TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            image_width INTEGER,
            image_height INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (file_hash, uploader_identity)
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS blog_moderation_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            moderator_identity TEXT NOT NULL,
            moderator_role TEXT NOT NULL,
            moderator_user_pk INTEGER NOT NULL,
            action TEXT NOT NULL,
            reason TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (post_id) REFERENCES blog_posts (id) ON DELETE CASCADE
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS blog_ai_reply_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_type TEXT NOT NULL,
            trigger_id INTEGER NOT NULL,
            post_id INTEGER NOT NULL,
            trigger_author_identity TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            assistant_comment_id INTEGER,
            error_message TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (trigger_type, trigger_id),
            FOREIGN KEY (post_id) REFERENCES blog_posts (id) ON DELETE CASCADE,
            FOREIGN KEY (assistant_comment_id) REFERENCES blog_comments (id) ON DELETE SET NULL
        )
    ''')

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_posts_author "
        "ON blog_posts (author_identity, status, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_posts_status_created "
        "ON blog_posts (status, is_pinned DESC, is_featured DESC, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_posts_visibility "
        "ON blog_posts (visibility, status, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_posts_hot "
        "ON blog_posts (status, like_count DESC, comment_count DESC, view_count DESC, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_posts_featured "
        "ON blog_posts (is_featured, status, featured_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_posts_visible_class "
        "ON blog_posts (visible_class_id, status, created_at DESC, id DESC)"
        "WHERE visibility = 'class_visible'"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_comments_post "
        "ON blog_comments (post_id, status, created_at ASC, id ASC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_comments_parent "
        "ON blog_comments (parent_comment_id, created_at ASC, id ASC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_likes_target_user "
        "ON blog_likes (target_type, target_id, user_identity)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_bookmarks_user "
        "ON blog_bookmarks (user_identity, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_attachments_post "
        "ON blog_attachments (post_id, display_order, id ASC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_media_assets_hash "
        "ON blog_media_assets (file_hash, updated_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_media_assets_uploader "
        "ON blog_media_assets (uploader_identity, updated_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_moderation_post "
        "ON blog_moderation_logs (post_id, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_ai_reply_jobs_post "
        "ON blog_ai_reply_jobs (post_id, status, created_at DESC, id DESC)"
    )

    conn.execute('''
        CREATE TABLE IF NOT EXISTS blog_news_crawler_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enabled INTEGER NOT NULL DEFAULT 1,
            auto_publish INTEGER NOT NULL DEFAULT 1,
            featured_posts INTEGER NOT NULL DEFAULT 1,
            timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
            schedule_window_start TEXT NOT NULL DEFAULT '01:20',
            schedule_window_end TEXT NOT NULL DEFAULT '04:40',
            recent_days INTEGER NOT NULL DEFAULT 1,
            max_keywords INTEGER NOT NULL DEFAULT 8,
            search_limit_per_keyword INTEGER NOT NULL DEFAULT 20,
            max_candidates_total INTEGER NOT NULL DEFAULT 80,
            max_posts_per_run INTEGER NOT NULL DEFAULT 2,
            article_fetch_limit INTEGER NOT NULL DEFAULT 24,
            fetch_article_pages INTEGER NOT NULL DEFAULT 1,
            fetch_images INTEGER NOT NULL DEFAULT 1,
            max_images_per_post INTEGER NOT NULL DEFAULT 1,
            max_image_bytes INTEGER NOT NULL DEFAULT 6291456,
            request_timeout_seconds REAL NOT NULL DEFAULT 12,
            min_request_interval_seconds REAL NOT NULL DEFAULT 2,
            max_request_interval_seconds REAL NOT NULL DEFAULT 6,
            extra_keywords_json TEXT NOT NULL DEFAULT '[]',
            blocked_domains_json TEXT NOT NULL DEFAULT '[]',
            source_templates_json TEXT NOT NULL DEFAULT '[]',
            enable_global_search_sources INTEGER NOT NULL DEFAULT 0,
            next_run_at TEXT DEFAULT '',
            last_run_id INTEGER,
            last_run_at TEXT DEFAULT '',
            last_heartbeat_at TEXT DEFAULT '',
            worker_id TEXT DEFAULT '',
            worker_status TEXT DEFAULT '',
            updated_by_teacher_id INTEGER,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (last_run_id) REFERENCES blog_news_crawler_runs (id) ON DELETE SET NULL,
            FOREIGN KEY (updated_by_teacher_id) REFERENCES teachers (id) ON DELETE SET NULL
        )
    ''')
    for column_name, column_def in {
        "source_templates_json": "TEXT NOT NULL DEFAULT '[]'",
        "enable_global_search_sources": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        try:
            conn.execute(f"ALTER TABLE blog_news_crawler_config ADD COLUMN {column_name} {column_def}")
        except sqlite3.OperationalError:
            pass
    conn.execute('''
        CREATE TABLE IF NOT EXISTS blog_news_crawler_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_source TEXT NOT NULL DEFAULT 'scheduled',
            status TEXT NOT NULL DEFAULT 'pending',
            scheduled_for TEXT DEFAULT '',
            worker_id TEXT DEFAULT '',
            started_at TEXT DEFAULT '',
            finished_at TEXT DEFAULT '',
            keywords_json TEXT NOT NULL DEFAULT '[]',
            candidate_count INTEGER NOT NULL DEFAULT 0,
            new_candidate_count INTEGER NOT NULL DEFAULT 0,
            duplicate_count INTEGER NOT NULL DEFAULT 0,
            selected_count INTEGER NOT NULL DEFAULT 0,
            published_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT DEFAULT '',
            log_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS blog_news_crawler_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            keyword TEXT NOT NULL,
            course_names_json TEXT NOT NULL DEFAULT '[]',
            source_name TEXT DEFAULT '',
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            canonical_url TEXT DEFAULT '',
            url_hash TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            summary TEXT DEFAULT '',
            published_at TEXT DEFAULT '',
            fetched_at TEXT DEFAULT '',
            media_json TEXT NOT NULL DEFAULT '[]',
            score REAL NOT NULL DEFAULT 0,
            selected INTEGER NOT NULL DEFAULT 0,
            duplicate_of_item_id INTEGER,
            duplicate_of_post_id INTEGER,
            post_id INTEGER,
            raw_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (url_hash),
            UNIQUE (content_hash),
            FOREIGN KEY (run_id) REFERENCES blog_news_crawler_runs (id) ON DELETE CASCADE,
            FOREIGN KEY (duplicate_of_item_id) REFERENCES blog_news_crawler_items (id) ON DELETE SET NULL,
            FOREIGN KEY (duplicate_of_post_id) REFERENCES blog_posts (id) ON DELETE SET NULL,
            FOREIGN KEY (post_id) REFERENCES blog_posts (id) ON DELETE SET NULL
        )
    ''')
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_news_crawler_runs_status "
        "ON blog_news_crawler_runs (status, scheduled_for, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_news_crawler_runs_created "
        "ON blog_news_crawler_runs (created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_news_crawler_items_run "
        "ON blog_news_crawler_items (run_id, selected DESC, score DESC, id ASC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_news_crawler_items_post "
        "ON blog_news_crawler_items (post_id, created_at DESC)"
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO blog_news_crawler_config (id)
        VALUES (1)
        """
    )

    # 14.1 Electronic signature library
    conn.execute('''
        CREATE TABLE IF NOT EXISTS electronic_signatures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            subject_name TEXT NOT NULL DEFAULT '',
            subject_role TEXT NOT NULL DEFAULT 'teacher',
            scope_level TEXT NOT NULL DEFAULT 'college',
            owner_role TEXT NOT NULL,
            owner_id INTEGER,
            owner_name_snapshot TEXT NOT NULL DEFAULT '',
            uploaded_by_role TEXT NOT NULL DEFAULT '',
            uploaded_by_id INTEGER,
            uploaded_by_name_snapshot TEXT NOT NULL DEFAULT '',
            ownership_updated_at TEXT,
            ownership_updated_by_teacher_id INTEGER,
            school_code TEXT NOT NULL DEFAULT 'gxufl',
            school_name TEXT NOT NULL DEFAULT '广西外国语学院',
            college TEXT NOT NULL DEFAULT '',
            department TEXT NOT NULL DEFAULT '',
            file_hash TEXT NOT NULL,
            file_ext TEXT NOT NULL DEFAULT '',
            mime_type TEXT NOT NULL DEFAULT '',
            stored_path TEXT NOT NULL,
            file_size INTEGER NOT NULL DEFAULT 0,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            legacy_source TEXT NOT NULL DEFAULT '',
            legacy_id TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            deleted_at TEXT
        )
    ''')
    for column_name, column_def in {
        "subject_name": "TEXT NOT NULL DEFAULT ''",
        "subject_role": "TEXT NOT NULL DEFAULT 'teacher'",
        "scope_level": "TEXT NOT NULL DEFAULT 'college'",
        "owner_name_snapshot": "TEXT NOT NULL DEFAULT ''",
        "uploaded_by_role": "TEXT NOT NULL DEFAULT ''",
        "uploaded_by_id": "INTEGER",
        "uploaded_by_name_snapshot": "TEXT NOT NULL DEFAULT ''",
        "ownership_updated_at": "TEXT",
        "ownership_updated_by_teacher_id": "INTEGER",
        "school_code": "TEXT NOT NULL DEFAULT 'gxufl'",
        "school_name": "TEXT NOT NULL DEFAULT '广西外国语学院'",
        "college": "TEXT NOT NULL DEFAULT ''",
        "department": "TEXT NOT NULL DEFAULT ''",
        "file_ext": "TEXT NOT NULL DEFAULT ''",
        "mime_type": "TEXT NOT NULL DEFAULT ''",
        "file_size": "INTEGER NOT NULL DEFAULT 0",
        "description": "TEXT NOT NULL DEFAULT ''",
        "status": "TEXT NOT NULL DEFAULT 'active'",
        "legacy_source": "TEXT NOT NULL DEFAULT ''",
        "legacy_id": "TEXT NOT NULL DEFAULT ''",
        "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
        "updated_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
        "deleted_at": "TEXT",
    }.items():
        try:
            conn.execute(f"ALTER TABLE electronic_signatures ADD COLUMN {column_name} {column_def}")
        except sqlite3.OperationalError:
            pass

    conn.execute(
        """
        UPDATE electronic_signatures
        SET uploaded_by_role = COALESCE(NULLIF(TRIM(uploaded_by_role), ''), owner_role, ''),
            uploaded_by_id = COALESCE(uploaded_by_id, owner_id),
            uploaded_by_name_snapshot = COALESCE(
                NULLIF(TRIM(uploaded_by_name_snapshot), ''),
                NULLIF(TRIM(owner_name_snapshot), ''),
                ''
            )
        WHERE TRIM(COALESCE(uploaded_by_role, '')) = ''
           OR uploaded_by_id IS NULL
           OR TRIM(COALESCE(uploaded_by_name_snapshot, '')) = ''
        """
    )
    conn.execute(
        """
        UPDATE electronic_signatures
        SET school_code = COALESCE(NULLIF(TRIM(school_code), ''), (
                SELECT NULLIF(TRIM(t.school_code), '')
                FROM teachers t
                WHERE electronic_signatures.owner_role = 'teacher'
                  AND t.id = electronic_signatures.owner_id
            ), (
                SELECT NULLIF(TRIM(s.school_code), '')
                FROM students s
                WHERE electronic_signatures.owner_role = 'student'
                  AND s.id = electronic_signatures.owner_id
            ), school_code),
            school_name = COALESCE(NULLIF(TRIM(school_name), ''), (
                SELECT NULLIF(TRIM(t.school_name), '')
                FROM teachers t
                WHERE electronic_signatures.owner_role = 'teacher'
                  AND t.id = electronic_signatures.owner_id
            ), (
                SELECT NULLIF(TRIM(s.school_name), '')
                FROM students s
                WHERE electronic_signatures.owner_role = 'student'
                  AND s.id = electronic_signatures.owner_id
            ), school_name),
            college = COALESCE(NULLIF(TRIM(college), ''), (
                SELECT NULLIF(TRIM(t.college), '')
                FROM teachers t
                WHERE electronic_signatures.owner_role = 'teacher'
                  AND t.id = electronic_signatures.owner_id
            ), (
                SELECT NULLIF(TRIM(s.college), '')
                FROM students s
                WHERE electronic_signatures.owner_role = 'student'
                  AND s.id = electronic_signatures.owner_id
            ), ''),
            department = COALESCE(NULLIF(TRIM(department), ''), (
                SELECT NULLIF(TRIM(t.department), '')
                FROM teachers t
                WHERE electronic_signatures.owner_role = 'teacher'
                  AND t.id = electronic_signatures.owner_id
            ), (
                SELECT NULLIF(TRIM(s.department), '')
                FROM students s
                WHERE electronic_signatures.owner_role = 'student'
                  AND s.id = electronic_signatures.owner_id
            ), '')
        """
    )
    conn.execute(
        """
        UPDATE electronic_signatures
        SET scope_level = 'department'
        WHERE scope_level = 'college'
        """
    )

    conn.execute('''
        CREATE TABLE IF NOT EXISTS signature_usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signature_id INTEGER,
            signature_name_snapshot TEXT NOT NULL DEFAULT '',
            actor_role TEXT NOT NULL,
            actor_id INTEGER NOT NULL,
            actor_name_snapshot TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL DEFAULT 'use',
            context_type TEXT NOT NULL DEFAULT '',
            context_id TEXT NOT NULL DEFAULT '',
            context_label TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            ip TEXT NOT NULL DEFAULT '',
            user_agent TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (signature_id) REFERENCES electronic_signatures (id) ON DELETE SET NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS signature_access_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signature_id INTEGER NOT NULL,
            requester_teacher_id INTEGER NOT NULL,
            owner_role TEXT NOT NULL DEFAULT '',
            owner_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            request_note TEXT NOT NULL DEFAULT '',
            review_note TEXT NOT NULL DEFAULT '',
            context_type TEXT NOT NULL DEFAULT '',
            context_id TEXT NOT NULL DEFAULT '',
            context_label TEXT NOT NULL DEFAULT '',
            requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT,
            reviewed_by_teacher_id INTEGER,
            FOREIGN KEY (signature_id) REFERENCES electronic_signatures (id) ON DELETE CASCADE,
            FOREIGN KEY (requester_teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
            FOREIGN KEY (reviewed_by_teacher_id) REFERENCES teachers (id) ON DELETE SET NULL
        )
    ''')
    for column_name, column_def in {
        "signature_name_snapshot": "TEXT NOT NULL DEFAULT ''",
        "actor_name_snapshot": "TEXT NOT NULL DEFAULT ''",
        "context_type": "TEXT NOT NULL DEFAULT ''",
        "context_id": "TEXT NOT NULL DEFAULT ''",
        "context_label": "TEXT NOT NULL DEFAULT ''",
        "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
        "ip": "TEXT NOT NULL DEFAULT ''",
        "user_agent": "TEXT NOT NULL DEFAULT ''",
    }.items():
        try:
            conn.execute(f"ALTER TABLE signature_usage_logs ADD COLUMN {column_name} {column_def}")
        except sqlite3.OperationalError:
            pass

    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_electronic_signatures_owner "
        "ON electronic_signatures (owner_role, owner_id, status, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_electronic_signatures_uploader "
        "ON electronic_signatures (uploaded_by_role, uploaded_by_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_electronic_signatures_org "
        "ON electronic_signatures (school_code, college, subject_role, status, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_electronic_signatures_department "
        "ON electronic_signatures (school_code, department, subject_role, status, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_electronic_signatures_hash "
        "ON electronic_signatures (file_hash, status)",
        "CREATE INDEX IF NOT EXISTS idx_electronic_signatures_legacy "
        "ON electronic_signatures (legacy_source, legacy_id)",
        "CREATE INDEX IF NOT EXISTS idx_signature_usage_signature "
        "ON signature_usage_logs (signature_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_signature_usage_actor "
        "ON signature_usage_logs (actor_role, actor_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_signature_usage_context "
        "ON signature_usage_logs (context_type, context_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_signature_access_requests_incoming "
        "ON signature_access_requests (owner_role, owner_id, status, requested_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_signature_access_requests_outgoing "
        "ON signature_access_requests (requester_teacher_id, status, requested_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_signature_access_requests_signature "
        "ON signature_access_requests (signature_id, status, requested_at DESC)",
    ):
        conn.execute(statement)
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_signature_access_requests_active_unique
        ON signature_access_requests (signature_id, requester_teacher_id)
        WHERE status IN ('pending', 'approved')
        """
    )

    _ensure_resource_scope_schema(conn)
    _sync_organization_catalog_from_existing(conn)

    # App Feedback System
    conn.execute('''
        CREATE TABLE IF NOT EXISTS app_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            user_role TEXT NOT NULL,
            user_name TEXT DEFAULT '',
            feedback_type TEXT NOT NULL,
            section TEXT DEFAULT '',
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            page_url TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS app_feedback_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feedback_id INTEGER NOT NULL,
            file_hash TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            file_size INTEGER DEFAULT 0,
            mime_type TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (feedback_id) REFERENCES app_feedback (id) ON DELETE CASCADE
        )
    ''')

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_app_feedback_user "
        "ON app_feedback (user_id, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_app_feedback_status "
        "ON app_feedback (status, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_app_feedback_attachments "
        "ON app_feedback_attachments (feedback_id, id ASC)"
    )
