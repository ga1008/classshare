import sqlite3

from .migrations import (
    _backfill_organization_scopes,
    _ensure_teacher_organization_memberships_schema,
)


def ensure_materials_integrations_schema(conn: sqlite3.Connection) -> None:
    conn.execute('''
                CREATE TABLE IF NOT EXISTS exam_papers
                (
                    id
                    TEXT
                    PRIMARY KEY,
                    teacher_id
                    INTEGER
                    NOT
                    NULL,
                    title
                    TEXT
                    NOT
                    NULL,
                    description
                    TEXT,
                    questions_json
                    TEXT
                    NOT
                    NULL,
                    exam_config_json
                    TEXT,
                    status
                    TEXT
                    NOT
                    NULL
                    DEFAULT
                    'draft',
                    ai_gen_task_id
                    TEXT,
                    ai_gen_status
                    TEXT
                    DEFAULT
                    NULL,
                    ai_gen_error
                    TEXT,
                    created_at
                    TEXT
                    DEFAULT
                    CURRENT_TIMESTAMP,
                    updated_at
                    TEXT
                    DEFAULT
                    CURRENT_TIMESTAMP,
                    FOREIGN
                    KEY
                (
                    teacher_id
                ) REFERENCES teachers
                (
                    id
                )
                    )
                ''')

    # 兼容已有数据库：为 exam_papers 添加AI生成相关列
    try:
        conn.execute("ALTER TABLE exam_papers ADD COLUMN ai_gen_task_id TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE exam_papers ADD COLUMN ai_gen_status TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE exam_papers ADD COLUMN ai_gen_error TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在

    # 兼容已有数据库：为 exam_papers 添加标签列
    try:
        conn.execute("ALTER TABLE exam_papers ADD COLUMN tags_json TEXT DEFAULT '[]'")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE assignments ADD COLUMN learning_stage_key TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assignments_learning_stage "
        "ON assignments (class_offering_id, learning_stage_key)"
    )

    # 15. 课程材料库
    conn.execute('''
                CREATE TABLE IF NOT EXISTS course_materials
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    teacher_id INTEGER NOT NULL,
                    parent_id INTEGER,
                    root_id INTEGER,
                    material_path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    node_type TEXT NOT NULL DEFAULT 'file',
                    mime_type TEXT,
                    preview_type TEXT NOT NULL DEFAULT 'binary',
                    ai_capability TEXT NOT NULL DEFAULT 'none',
                    file_ext TEXT DEFAULT '',
                    file_hash TEXT,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    ai_parse_status TEXT NOT NULL DEFAULT 'idle',
                    ai_parse_result_json TEXT,
                    ai_optimize_status TEXT NOT NULL DEFAULT 'idle',
                    ai_optimized_markdown TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
                    FOREIGN KEY (parent_id) REFERENCES course_materials (id) ON DELETE CASCADE
                )
                 ''')

    conn.execute('''
                CREATE TABLE IF NOT EXISTS material_ai_import_records
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    teacher_id INTEGER NOT NULL,
                    package_material_id INTEGER REFERENCES course_materials (id) ON DELETE SET NULL,
                    source_material_id INTEGER REFERENCES course_materials (id) ON DELETE SET NULL,
                    parsed_material_id INTEGER REFERENCES course_materials (id) ON DELETE SET NULL,
                    parent_material_id INTEGER REFERENCES course_materials (id) ON DELETE SET NULL,
                    document_group TEXT NOT NULL,
                    document_type TEXT NOT NULL,
                    document_type_label TEXT NOT NULL DEFAULT '',
                    parse_status TEXT NOT NULL DEFAULT 'completed',
                    parse_mode TEXT NOT NULL DEFAULT 'ai',
                    extraction_method TEXT NOT NULL DEFAULT '',
                    source_file_name TEXT NOT NULL DEFAULT '',
                    source_file_hash TEXT DEFAULT '',
                    source_file_size INTEGER NOT NULL DEFAULT 0,
                    source_mime_type TEXT DEFAULT '',
                    metadata_json TEXT,
                    content_markdown TEXT,
                    parsed_payload_json TEXT,
                    export_payload_json TEXT,
                    warnings_json TEXT,
                    content_quality_status TEXT NOT NULL DEFAULT 'unchecked',
                    content_quality_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    started_at TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    completed_at TEXT,
                    failed_at TEXT,
                    FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE
                )
                 ''')

    conn.execute('''
                CREATE TABLE IF NOT EXISTS course_material_assignments
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    material_id INTEGER NOT NULL,
                    class_offering_id INTEGER NOT NULL,
                    assigned_by_teacher_id INTEGER NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (material_id) REFERENCES course_materials (id) ON DELETE CASCADE,
                    FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
                    FOREIGN KEY (assigned_by_teacher_id) REFERENCES teachers (id),
                    UNIQUE (material_id, class_offering_id)
                )
                 ''')

    conn.execute('''
                CREATE TABLE IF NOT EXISTS session_material_generation_tasks
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    class_offering_id INTEGER NOT NULL,
                    session_id INTEGER NOT NULL,
                    teacher_id INTEGER NOT NULL,
                    trigger_mode TEXT NOT NULL DEFAULT 'guided',
                    status TEXT NOT NULL DEFAULT 'queued',
                    document_type TEXT DEFAULT '',
                    requirement_text TEXT DEFAULT '',
                    request_payload_json TEXT,
                    result_payload_json TEXT,
                    generated_material_id INTEGER REFERENCES course_materials (id) ON DELETE SET NULL,
                    generated_material_path TEXT DEFAULT '',
                    error_message TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    started_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
                    FOREIGN KEY (session_id) REFERENCES class_offering_sessions (id) ON DELETE CASCADE,
                    FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE
                )
                 ''')

    conn.execute('''
                CREATE TABLE IF NOT EXISTS teacher_git_credentials
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    teacher_id INTEGER NOT NULL,
                    remote_key TEXT NOT NULL,
                    remote_host TEXT NOT NULL,
                    remote_url TEXT NOT NULL,
                    provider TEXT DEFAULT '',
                    auth_mode TEXT NOT NULL DEFAULT 'password',
                    username TEXT DEFAULT '',
                    secret_encrypted TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TEXT,
                    FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
                    UNIQUE (teacher_id, remote_key)
                )
                 ''')

    conn.execute('''
                CREATE TABLE IF NOT EXISTS teacher_academic_system_credentials
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    teacher_id INTEGER NOT NULL,
                    school_code TEXT NOT NULL,
                    school_name TEXT NOT NULL,
                    adapter_key TEXT NOT NULL,
                    auth_method TEXT NOT NULL DEFAULT 'password_rsa',
                    base_url TEXT NOT NULL,
                    login_url TEXT NOT NULL,
                    username TEXT NOT NULL,
                    password_encrypted TEXT NOT NULL,
                    display_name TEXT DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_status TEXT NOT NULL DEFAULT 'unchecked',
                    last_status_at TEXT,
                    last_error TEXT DEFAULT '',
                    last_verified_at TEXT,
                    access_method_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
                    UNIQUE (teacher_id, school_code, auth_method)
                )
                 ''')

    conn.execute('''
                CREATE TABLE IF NOT EXISTS teacher_smart_classroom_credentials
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    teacher_id INTEGER NOT NULL,
                    platform_code TEXT NOT NULL,
                    platform_name TEXT NOT NULL,
                    adapter_key TEXT NOT NULL,
                    auth_method TEXT NOT NULL DEFAULT 'password_token',
                    base_url TEXT NOT NULL,
                    api_base_url TEXT NOT NULL,
                    login_url TEXT NOT NULL,
                    username TEXT NOT NULL,
                    password_encrypted TEXT NOT NULL,
                    display_name TEXT DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_status TEXT NOT NULL DEFAULT 'unchecked',
                    last_status_at TEXT,
                    last_error TEXT DEFAULT '',
                    last_verified_at TEXT,
                    access_method_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
                    UNIQUE (teacher_id, platform_code, auth_method)
                )
                 ''')

    conn.execute('''
                CREATE TABLE IF NOT EXISTS smart_classroom_schedule_items
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    teacher_id INTEGER NOT NULL,
                    class_offering_id INTEGER,
                    platform_code TEXT NOT NULL,
                    remote_schedule_id TEXT NOT NULL,
                    remote_course_id TEXT DEFAULT '',
                    remote_course_name TEXT DEFAULT '',
                    remote_teaching_class_id TEXT DEFAULT '',
                    remote_teaching_class_name TEXT DEFAULT '',
                    academic_year TEXT DEFAULT '',
                    academic_term TEXT DEFAULT '',
                    weeks_text TEXT DEFAULT '',
                    sections_text TEXT DEFAULT '',
                    weekday INTEGER,
                    classroom_name TEXT DEFAULT '',
                    student_count INTEGER NOT NULL DEFAULT 0,
                    match_status TEXT NOT NULL DEFAULT 'unmatched',
                    match_message TEXT DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
                    FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE SET NULL,
                    UNIQUE (teacher_id, platform_code, remote_schedule_id)
                )
                 ''')

    conn.execute('''
                CREATE TABLE IF NOT EXISTS smart_classroom_checkin_sessions
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    teacher_id INTEGER NOT NULL,
                    class_offering_id INTEGER,
                    session_id INTEGER,
                    schedule_item_id INTEGER,
                    platform_code TEXT NOT NULL,
                    remote_checkin_id TEXT NOT NULL,
                    remote_schedule_id TEXT DEFAULT '',
                    course_code TEXT DEFAULT '',
                    course_name TEXT DEFAULT '',
                    teaching_class_name TEXT DEFAULT '',
                    academic_year TEXT DEFAULT '',
                    academic_term TEXT DEFAULT '',
                    week_index INTEGER NOT NULL DEFAULT 0,
                    weekday INTEGER,
                    section_index INTEGER,
                    checkin_time TEXT DEFAULT '',
                    stop_time TEXT DEFAULT '',
                    method TEXT DEFAULT '',
                    checked_rate TEXT DEFAULT '',
                    checked_count INTEGER NOT NULL DEFAULT 0,
                    unchecked_count INTEGER NOT NULL DEFAULT 0,
                    sick_leave_count INTEGER NOT NULL DEFAULT 0,
                    personal_leave_count INTEGER NOT NULL DEFAULT 0,
                    late_or_early_count INTEGER NOT NULL DEFAULT 0,
                    total_count INTEGER NOT NULL DEFAULT 0,
                    match_status TEXT NOT NULL DEFAULT 'unmatched',
                    match_message TEXT DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
                    FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE SET NULL,
                    FOREIGN KEY (session_id) REFERENCES class_offering_sessions (id) ON DELETE SET NULL,
                    FOREIGN KEY (schedule_item_id) REFERENCES smart_classroom_schedule_items (id) ON DELETE SET NULL,
                    UNIQUE (teacher_id, platform_code, remote_checkin_id)
                )
                 ''')

    conn.execute('''
                CREATE TABLE IF NOT EXISTS smart_classroom_checkin_students
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    checkin_session_id INTEGER NOT NULL,
                    teacher_id INTEGER NOT NULL,
                    class_offering_id INTEGER,
                    session_id INTEGER,
                    student_id INTEGER,
                    student_number TEXT NOT NULL,
                    student_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    status_label TEXT NOT NULL,
                    local_match_status TEXT NOT NULL DEFAULT 'unmatched',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (checkin_session_id) REFERENCES smart_classroom_checkin_sessions (id) ON DELETE CASCADE,
                    FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
                    FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE SET NULL,
                    FOREIGN KEY (session_id) REFERENCES class_offering_sessions (id) ON DELETE SET NULL,
                    FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE SET NULL,
                    UNIQUE (checkin_session_id, student_number)
                )
                 ''')

    conn.execute('''
                CREATE TABLE IF NOT EXISTS smart_attendance_daily_tasks
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    class_offering_id INTEGER NOT NULL,
                    teacher_id INTEGER NOT NULL,
                    task_type TEXT NOT NULL,
                    task_date TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    message TEXT DEFAULT '',
                    raw_payload_json TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT,
                    finished_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
                    FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
                    UNIQUE (class_offering_id, teacher_id, task_type, task_date)
                )
                 ''')

    conn.execute('''
                CREATE TABLE IF NOT EXISTS smart_attendance_student_advice
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    class_offering_id INTEGER NOT NULL,
                    student_id INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    advice_json TEXT NOT NULL DEFAULT '{}',
                    fallback_insights_json TEXT NOT NULL DEFAULT '[]',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT DEFAULT '',
                    first_requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    last_requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    started_at TEXT,
                    completed_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
                    FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
                    UNIQUE (class_offering_id, student_id, fingerprint)
                )
                 ''')

    try:
        conn.execute("ALTER TABLE course_materials ADD COLUMN git_repo_status TEXT NOT NULL DEFAULT 'unscanned'")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE course_materials ADD COLUMN git_provider TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE course_materials ADD COLUMN git_remote_name TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE course_materials ADD COLUMN git_remote_url TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE course_materials ADD COLUMN git_remote_host TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE course_materials ADD COLUMN git_remote_protocol TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE course_materials ADD COLUMN git_default_branch TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE course_materials ADD COLUMN git_head_branch TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE course_materials ADD COLUMN git_detect_error TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE course_materials ADD COLUMN git_detected_at TEXT")
    except sqlite3.OperationalError:
        pass

    for column_name, column_def in (
        ("parent_material_id", "INTEGER REFERENCES course_materials (id) ON DELETE SET NULL"),
        ("source_file_hash", "TEXT DEFAULT ''"),
        ("source_file_size", "INTEGER NOT NULL DEFAULT 0"),
        ("source_mime_type", "TEXT DEFAULT ''"),
        ("parsed_payload_json", "TEXT"),
        ("content_quality_status", "TEXT NOT NULL DEFAULT 'unchecked'"),
        ("content_quality_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("started_at", "TEXT"),
        ("failed_at", "TEXT"),
    ):
        try:
            conn.execute(f"ALTER TABLE material_ai_import_records ADD COLUMN {column_name} {column_def}")
        except sqlite3.OperationalError:
            pass

    _backfill_organization_scopes(conn)
    _ensure_teacher_organization_memberships_schema(conn)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_academic_semesters_teacher_period "
        "ON academic_semesters (teacher_id, start_date DESC, end_date DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_academic_semesters_school_period "
        "ON academic_semesters (school_code COLLATE NOCASE, start_date DESC, end_date DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teachers_org_scope "
        "ON teachers (school_code COLLATE NOCASE, college COLLATE NOCASE, department COLLATE NOCASE, is_active)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_smart_credentials_teacher "
        "ON teacher_smart_classroom_credentials (teacher_id, enabled, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_smart_schedule_teacher_offering "
        "ON smart_classroom_schedule_items (teacher_id, class_offering_id, synced_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_smart_checkin_session_lookup "
        "ON smart_classroom_checkin_sessions (teacher_id, class_offering_id, session_id, synced_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_smart_checkin_remote_schedule "
        "ON smart_classroom_checkin_sessions (teacher_id, remote_schedule_id, checkin_time DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_smart_checkin_students_lookup "
        "ON smart_classroom_checkin_students (checkin_session_id, status, student_number)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_smart_attendance_daily_tasks_lookup "
        "ON smart_attendance_daily_tasks (class_offering_id, teacher_id, task_type, task_date, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_smart_attendance_student_advice_lookup "
        "ON smart_attendance_student_advice (class_offering_id, student_id, fingerprint, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_smart_attendance_student_advice_queue "
        "ON smart_attendance_student_advice (status, updated_at, attempts)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_academic_semester_calendar_days_lookup "
        "ON academic_semester_calendar_days (semester_id, date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_academic_semester_calendar_days_teacher "
        "ON academic_semester_calendar_days (teacher_id, date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_textbooks_teacher_updated "
        "ON textbooks (teacher_id, updated_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_textbooks_teacher_title "
        "ON textbooks (teacher_id, title COLLATE NOCASE)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_textbooks_teacher_publisher "
        "ON textbooks (teacher_id, publisher COLLATE NOCASE)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_courses_teacher_name "
        "ON courses (created_by_teacher_id, name COLLATE NOCASE)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classes_teacher_department "
        "ON classes (created_by_teacher_id, department COLLATE NOCASE, name COLLATE NOCASE)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classes_org_department "
        "ON classes (school_code COLLATE NOCASE, college COLLATE NOCASE, department COLLATE NOCASE, name COLLATE NOCASE)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_courses_teacher_department "
        "ON courses (created_by_teacher_id, department COLLATE NOCASE, name COLLATE NOCASE)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_courses_org_department "
        "ON courses (school_code COLLATE NOCASE, college COLLATE NOCASE, department COLLATE NOCASE, name COLLATE NOCASE)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_courses_teacher_academic_code "
        "ON courses (created_by_teacher_id, academic_source, academic_course_code COLLATE NOCASE)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classes_teacher_academic_code "
        "ON classes (created_by_teacher_id, academic_source, academic_class_code COLLATE NOCASE)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classes_teacher_academic_name "
        "ON classes (created_by_teacher_id, academic_source, academic_class_name COLLATE NOCASE)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_students_academic_student_id "
        "ON students (academic_source, academic_student_id COLLATE NOCASE)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_students_academic_class_lookup "
        "ON students (class_id, academic_source, academic_class_code COLLATE NOCASE)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_course_lessons_course_order "
        "ON course_lessons (course_id, order_index)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_course_lessons_material_lookup "
        "ON course_lessons (course_id, learning_material_id, order_index)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_course_sync_items_teacher_semester "
        "ON teacher_academic_course_sync_items (teacher_id, semester_id, synced_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_course_sync_items_course "
        "ON teacher_academic_course_sync_items (course_id, weekday, section_text)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_course_sync_items_term "
        "ON teacher_academic_course_sync_items (teacher_id, academic_year, academic_term, course_code COLLATE NOCASE)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_course_sync_items_classroom "
        "ON teacher_academic_course_sync_items (teacher_id, classroom_id, classroom_code)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_course_occurrences_course "
        "ON teacher_academic_course_session_occurrences (teacher_id, semester_id, course_id, session_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_course_occurrences_class "
        "ON teacher_academic_course_session_occurrences (teacher_id, semester_id, course_id, teaching_class_name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_course_occurrences_sync_item "
        "ON teacher_academic_course_session_occurrences (sync_item_id, session_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_roster_items_teacher_semester "
        "ON teacher_academic_roster_sync_items (teacher_id, semester_id, synced_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_roster_items_class "
        "ON teacher_academic_roster_sync_items (teacher_id, class_id, academic_year, academic_term)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_roster_memberships_class "
        "ON teacher_academic_roster_memberships (teacher_id, class_id, synced_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_roster_memberships_student "
        "ON teacher_academic_roster_memberships (teacher_id, student_id, academic_year, academic_term)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_invigilation_items_teacher_semester "
        "ON teacher_academic_invigilation_items (teacher_id, semester_id, starts_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_invigilation_items_term "
        "ON teacher_academic_invigilation_items (teacher_id, academic_year, academic_term, sync_status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_course_exam_items_teacher_semester "
        "ON teacher_academic_course_exam_items (teacher_id, semester_id, starts_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_course_exam_items_offering "
        "ON teacher_academic_course_exam_items (teacher_id, class_offering_id, starts_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_course_exam_items_term "
        "ON teacher_academic_course_exam_items (teacher_id, academic_year, academic_term, sync_status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_exam_roster_items_offering "
        "ON teacher_academic_exam_roster_items (teacher_id, class_offering_id, synced_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_exam_roster_items_term "
        "ON teacher_academic_exam_roster_items (teacher_id, academic_year, academic_term, sync_status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_exam_roster_students_item "
        "ON teacher_academic_exam_roster_students (exam_roster_item_id, row_order, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_exam_roster_students_lookup "
        "ON teacher_academic_exam_roster_students (teacher_id, class_offering_id, student_number)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_calendar_events_teacher_semester "
        "ON teacher_calendar_events (teacher_id, semester_id, starts_at, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_calendar_events_source "
        "ON teacher_calendar_events (teacher_id, source_type, source_key)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_class_offerings_unique_semester_id "
        "ON class_offerings (class_id, course_id, semester_id) "
        "WHERE semester_id IS NOT NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_class_offerings_teacher_semester "
        "ON class_offerings (teacher_id, semester_id, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_class_offerings_teacher_textbook "
        "ON class_offerings (teacher_id, textbook_id, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_class_offerings_teacher_first_date "
        "ON class_offerings (teacher_id, first_class_date, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_class_offerings_home_material_lookup "
        "ON class_offerings (teacher_id, home_learning_material_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_class_offering_sessions_lookup "
        "ON class_offering_sessions (class_offering_id, session_date, order_index)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_class_offering_sessions_material_lookup "
        "ON class_offering_sessions (class_offering_id, learning_material_id, order_index)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_class_offering_sessions_academic_occurrence "
        "ON class_offering_sessions (academic_occurrence_id, class_offering_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_class_offering_sessions_schedule_source "
        "ON class_offering_sessions (class_offering_id, schedule_source, session_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_course_materials_teacher_parent ON course_materials (teacher_id, parent_id, name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assignments_offering_created "
        "ON assignments (class_offering_id, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assignments_runtime_closure "
        "ON assignments (status, auto_close, due_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assignments_late_window "
        "ON assignments (late_submission_enabled, due_at, late_submission_until)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_submissions_assignment_status "
        "ON submissions (assignment_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_submissions_resubmission_window "
        "ON submissions (assignment_id, resubmission_allowed, resubmission_due_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_submissions_grading_started "
        "ON submissions (status, grading_started_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_submissions_late_assignment "
        "ON submissions (assignment_id, is_late_submission, submitted_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_submission_files_submission "
        "ON submission_files (submission_id, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assignments_exam_offering "
        "ON assignments (exam_paper_id, class_offering_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_exam_papers_teacher_updated "
        "ON exam_papers (teacher_id, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_exam_papers_ai_generation "
        "ON exam_papers (teacher_id, status, ai_gen_status, updated_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_submission_drafts_assignment_student "
        "ON submission_drafts (assignment_id, student_pk_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_submission_draft_files_draft_question "
        "ON submission_draft_files (draft_id, question_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_feedback_review_student_status "
        "ON student_feedback_review_notes (student_id, status, pinned, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_feedback_review_submission_question "
        "ON student_feedback_review_notes (submission_id, question_key)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_wrong_answer_ai_cache_assignment "
        "ON assignment_wrong_answer_ai_cache (assignment_id, question_key, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_exam_difficulty_ai_cache_paper "
        "ON exam_paper_difficulty_ai_cache (exam_paper_id, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_wrong_summary_jobs_assignment "
        "ON assignment_wrong_summary_jobs (assignment_id, questions_signature, status, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_study_groups_offering_status "
        "ON study_groups (class_offering_id, status, updated_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_study_groups_assignment "
        "ON study_groups (assignment_id, class_offering_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_study_group_members_student "
        "ON study_group_members (student_id, status, group_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_study_group_members_group "
        "ON study_group_members (group_id, status, member_role, student_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_study_group_files_group "
        "ON study_group_files (group_id, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_group_submissions_group_assignment "
        "ON group_submissions (group_id, assignment_id, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_group_submissions_blog_post "
        "ON group_submissions (blog_post_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_peer_reviews_group_reviewer "
        "ON peer_reviews (group_id, reviewer_student_id, assignment_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_peer_reviews_reviewee "
        "ON peer_reviews (reviewee_student_id, class_offering_id, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classroom_live_activities_lookup "
        "ON classroom_live_activities (class_offering_id, status, updated_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classroom_live_options_activity "
        "ON classroom_live_options (activity_id, sort_order, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classroom_live_responses_activity "
        "ON classroom_live_responses (activity_id, option_id, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classroom_live_responses_student "
        "ON classroom_live_responses (student_id, activity_id, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classroom_live_questions_activity "
        "ON classroom_live_questions (activity_id, status, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classroom_live_questions_offering "
        "ON classroom_live_questions (class_offering_id, status, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classroom_live_signals_active "
        "ON classroom_live_help_signals (class_offering_id, status, updated_at ASC, id ASC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classroom_live_signals_student "
        "ON classroom_live_help_signals (class_offering_id, student_id, status, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_course_materials_root_path ON course_materials (root_id, material_path)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_course_materials_teacher_path ON course_materials (teacher_id, material_path)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_course_materials_teacher_parent_created ON course_materials (teacher_id, parent_id, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_course_materials_teacher_parent_updated ON course_materials (teacher_id, parent_id, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_material_ai_import_teacher_updated "
        "ON material_ai_import_records (teacher_id, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_material_ai_import_teacher_type "
        "ON material_ai_import_records (teacher_id, document_group, document_type, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_material_ai_import_source "
        "ON material_ai_import_records (source_material_id, parsed_material_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_material_ai_import_teacher_parent_status "
        "ON material_ai_import_records (teacher_id, parent_material_id, parse_status, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_course_material_assignments_offering ON course_material_assignments (class_offering_id, material_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_git_credentials_lookup ON teacher_git_credentials (teacher_id, remote_host, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_credentials_lookup "
        "ON teacher_academic_system_credentials (teacher_id, school_code, enabled, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_material_generation_tasks_session "
        "ON session_material_generation_tasks (session_id, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_material_generation_tasks_offering "
        "ON session_material_generation_tasks (class_offering_id, status, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_material_generation_tasks_teacher "
        "ON session_material_generation_tasks (teacher_id, created_at DESC, id DESC)"
    )

    # 15.6 学习进度、等级考核与证书
