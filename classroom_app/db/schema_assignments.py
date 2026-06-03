import sqlite3


def ensure_assignment_schema(conn: sqlite3.Connection) -> None:
    conn.execute('''
                CREATE TABLE IF NOT EXISTS course_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_id INTEGER NOT NULL,
                    file_name TEXT NOT NULL,
                    file_hash TEXT NOT NULL,  -- 文件哈希值
                    file_size INTEGER NOT NULL,  -- 文件大小(字节)
                    is_public BOOLEAN DEFAULT TRUE,
                    is_teacher_resource BOOLEAN DEFAULT FALSE,
                    description TEXT DEFAULT '',  -- 文件简介
                    original_link TEXT DEFAULT '',
                    uploaded_by_teacher_id INTEGER,  -- 上传者教师ID
                    uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
                    FOREIGN KEY (uploaded_by_teacher_id) REFERENCES teachers (id)
                )
                 ''')

    # 兼容已有数据库：为 course_files 添加新列
    try:
        conn.execute("ALTER TABLE course_files ADD COLUMN description TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE course_files ADD COLUMN original_link TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE course_files ADD COLUMN uploaded_by_teacher_id INTEGER")
    except sqlite3.OperationalError:
        pass  # 列已存在

    # 兼容已有数据库：为 submissions 添加 answers_json 列
    try:
        conn.execute("ALTER TABLE submissions ADD COLUMN answers_json TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    submission_extension_columns = (
        ("submitted_by_role", "TEXT NOT NULL DEFAULT 'student'"),
        ("submitted_by_teacher_id", "INTEGER"),
        ("submission_channel", "TEXT NOT NULL DEFAULT 'online'"),
        ("resubmission_allowed", "INTEGER NOT NULL DEFAULT 0"),
        ("resubmission_due_at", "TEXT"),
        ("returned_at", "TEXT"),
        ("returned_by_teacher_id", "INTEGER"),
        ("returned_reason", "TEXT"),
        ("is_absence_score", "INTEGER NOT NULL DEFAULT 0"),
        ("absence_scored_at", "TEXT"),
        ("absence_scored_by_teacher_id", "INTEGER"),
        ("grading_started_at", "TEXT"),
        ("grading_attempt_fingerprint", "TEXT"),
        ("started_at", "TEXT"),
        ("is_late_submission", "INTEGER NOT NULL DEFAULT 0"),
        ("late_by_seconds", "INTEGER NOT NULL DEFAULT 0"),
        ("late_policy_snapshot_json", "TEXT"),
        ("score_before_late_penalty", "REAL"),
        ("late_penalty_points", "REAL NOT NULL DEFAULT 0"),
        ("late_score_cap_applied", "INTEGER NOT NULL DEFAULT 0"),
    )
    for column_name, column_def in submission_extension_columns:
        try:
            conn.execute(f"ALTER TABLE submissions ADD COLUMN {column_name} {column_def}")
        except sqlite3.OperationalError:
            pass  # 列已存在

    # 兼容已有数据库：为 assignments 添加 exam_paper_id 列
    assignments_table_exists = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'assignments' LIMIT 1"
        ).fetchone()
        is not None
    )
    try:
        conn.execute("ALTER TABLE assignments ADD COLUMN exam_paper_id TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在

    # 兼容已有数据库：为 assignments 添加 class_offering_id 列
    try:
        conn.execute("ALTER TABLE assignments ADD COLUMN class_offering_id INTEGER")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE assignments ADD COLUMN allowed_file_types_json TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE assignments ADD COLUMN availability_mode TEXT NOT NULL DEFAULT 'permanent'")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE assignments ADD COLUMN starts_at TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE assignments ADD COLUMN due_at TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE assignments ADD COLUMN duration_minutes INTEGER")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE assignments ADD COLUMN auto_close INTEGER NOT NULL DEFAULT 1")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE assignments ADD COLUMN closed_at TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    assignment_late_policy_columns = (
        ("late_submission_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("late_submission_until", "TEXT"),
        ("late_penalty_strategy", "TEXT NOT NULL DEFAULT 'fixed'"),
        ("late_penalty_interval_hours", "REAL NOT NULL DEFAULT 1"),
        ("late_penalty_points", "REAL NOT NULL DEFAULT 0"),
        ("late_penalty_min_score", "REAL NOT NULL DEFAULT 0"),
        ("late_score_cap", "REAL"),
    )
    for column_name, column_def in assignment_late_policy_columns:
        try:
            conn.execute(f"ALTER TABLE assignments ADD COLUMN {column_name} {column_def}")
        except sqlite3.OperationalError:
            pass  # 列已存在
    if assignments_table_exists:
        conn.execute(
            """
            UPDATE assignments
            SET availability_mode = 'permanent'
            WHERE availability_mode IS NULL OR availability_mode = ''
            """
        )
        conn.execute(
            """
            UPDATE assignments
            SET auto_close = 1
            WHERE auto_close IS NULL
            """
        )
    try:
        conn.execute("ALTER TABLE submission_files ADD COLUMN relative_path TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE submission_files ADD COLUMN mime_type TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE submission_files ADD COLUMN file_size INTEGER")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE submission_files ADD COLUMN file_ext TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE submission_files ADD COLUMN file_hash TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在

    conn.execute('''
        CREATE TABLE IF NOT EXISTS classroom_todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_offering_id INTEGER NOT NULL,
            owner_role TEXT NOT NULL,
            owner_user_pk INTEGER NOT NULL,
            title TEXT NOT NULL,
            notes TEXT DEFAULT '',
            start_at TEXT,
            due_at TEXT,
            completed_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            deleted_at TEXT,
            metadata_json TEXT DEFAULT '{}',
            FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE
        )
    ''')
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classroom_todos_owner "
        "ON classroom_todos (class_offering_id, owner_role, owner_user_pk, deleted_at, due_at, start_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classroom_todos_due "
        "ON classroom_todos (due_at, completed_at, deleted_at)"
    )

    # 分块上传跟踪表
    conn.execute('''
                CREATE TABLE IF NOT EXISTS chunked_uploads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    upload_id TEXT NOT NULL UNIQUE,
                    course_id INTEGER NOT NULL,
                    teacher_id INTEGER NOT NULL,
                    file_name TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    chunk_size INTEGER NOT NULL,
                    total_chunks INTEGER NOT NULL,
                    received_chunks TEXT DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'uploading',
                    temp_dir TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    is_public BOOLEAN DEFAULT TRUE,
                    is_teacher_resource BOOLEAN DEFAULT FALSE,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
                    FOREIGN KEY (teacher_id) REFERENCES teachers (id)
                )
                 ''')

    # 7.5 作业 (关联到课程)
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS assignments
                 (
                     id
                     INTEGER
                     PRIMARY
                     KEY
                     AUTOINCREMENT,
                     course_id
                     INTEGER
                     NOT
                     NULL,
                     title
                     TEXT
                     NOT
                     NULL,
                     status
                     TEXT
                     NOT
                     NULL
                     DEFAULT
                     'new',
                     requirements_md
                     TEXT,
                     rubric_md
                     TEXT,
                     grading_mode
                     TEXT
                     NOT
                     NULL
                     DEFAULT
                     'manual',
                     created_at
                     TEXT
                     DEFAULT
                     CURRENT_TIMESTAMP,
                     exam_paper_id
                     TEXT,
                     class_offering_id
                     INTEGER,
                     allowed_file_types_json
                     TEXT,
                     availability_mode
                     TEXT
                     NOT
                     NULL
                     DEFAULT
                     'permanent',
                     starts_at
                     TEXT,
                     due_at
                     TEXT,
                     duration_minutes
                     INTEGER,
                     auto_close
                     INTEGER
                     NOT
                     NULL
                     DEFAULT
                     1,
                     closed_at
                     TEXT,
                     late_submission_enabled
                     INTEGER
                     NOT
                     NULL
                     DEFAULT
                     0,
                     late_submission_until
                     TEXT,
                     late_penalty_strategy
                     TEXT
                     NOT
                     NULL
                     DEFAULT
                     'fixed',
                     late_penalty_interval_hours
                     REAL
                     NOT
                     NULL
                     DEFAULT
                     1,
                     late_penalty_points
                     REAL
                     NOT
                     NULL
                     DEFAULT
                     0,
                     late_penalty_min_score
                     REAL
                     NOT
                     NULL
                     DEFAULT
                     0,
                     late_score_cap
                     REAL,
                     FOREIGN
                     KEY
                 (
                     course_id
                 ) REFERENCES courses
                 (
                     id
                 ) ON DELETE CASCADE
                     )
                 ''')

    # 8. 提交 (关联到作业和学生)
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS submissions
                 (
                     id
                     INTEGER
                     PRIMARY
                     KEY
                     AUTOINCREMENT,
                     assignment_id
                     TEXT
                     NOT
                     NULL,
                     student_pk_id
                     INTEGER
                     NOT
                     NULL,
                     student_name
                     TEXT
                     NOT
                     NULL,
                     status
                     TEXT
                     NOT
                     NULL
                     DEFAULT
                     'submitted',
                     score
                     INTEGER,
                     feedback_md
                     TEXT,
                     grading_started_at
                     TEXT,
                     grading_attempt_fingerprint
                     TEXT,
                     answers_json
                     TEXT,
                     submitted_by_role
                     TEXT
                     NOT
                     NULL
                     DEFAULT
                     'student',
                     submitted_by_teacher_id
                     INTEGER,
                     submission_channel
                     TEXT
                     NOT
                     NULL
                     DEFAULT
                     'online',
                     resubmission_allowed
                     INTEGER
                     NOT
                     NULL
                     DEFAULT
                     0,
                     resubmission_due_at
                     TEXT,
                     returned_at
                     TEXT,
                     returned_by_teacher_id
                     INTEGER,
                     returned_reason
                     TEXT,
                     is_absence_score
                     INTEGER
                     NOT
                     NULL
                     DEFAULT
                     0,
                     absence_scored_at
                     TEXT,
                     absence_scored_by_teacher_id
                     INTEGER,
                     started_at
                     TEXT,
                     is_late_submission
                     INTEGER
                     NOT
                     NULL
                     DEFAULT
                     0,
                     late_by_seconds
                     INTEGER
                     NOT
                     NULL
                     DEFAULT
                     0,
                     late_policy_snapshot_json
                     TEXT,
                     score_before_late_penalty
                     REAL,
                     late_penalty_points
                     REAL
                     NOT
                     NULL
                     DEFAULT
                     0,
                     late_score_cap_applied
                     INTEGER
                     NOT
                     NULL
                     DEFAULT
                     0,
                     submitted_at
                     TEXT
                     NOT
                     NULL,
                     FOREIGN
                     KEY
                 (
                     assignment_id
                 ) REFERENCES assignments
                 (
                     id
                 ) ON DELETE CASCADE,
                     FOREIGN KEY
                 (
                     student_pk_id
                 ) REFERENCES students
                 (
                     id
                 )
                   ON DELETE CASCADE,
                     UNIQUE
                 (
                     assignment_id,
                     student_pk_id
                 )
                     )
                 ''')

    # 9. 提交的文件
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS submission_files
                 (
                     id
                     INTEGER
                     PRIMARY
                     KEY
                     AUTOINCREMENT,
                     submission_id
                     INTEGER
                     NOT
                     NULL,
                     original_filename
                     TEXT
                     NOT
                     NULL,
                     relative_path
                     TEXT,
                     stored_path
                     TEXT
                     NOT
                     NULL,
                     mime_type
                     TEXT,
                     file_size
                     INTEGER,
                     file_ext
                     TEXT,
                     file_hash
                     TEXT,
                     FOREIGN
                     KEY
                 (
                     submission_id
                 ) REFERENCES submissions
                 (
                     id
                 ) ON DELETE CASCADE
                     )
                 ''')

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS submission_drafts
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     assignment_id TEXT NOT NULL,
                     student_pk_id INTEGER NOT NULL,
                     answers_json TEXT DEFAULT '',
                     current_page INTEGER NOT NULL DEFAULT 0,
                     client_updated_at TEXT DEFAULT '',
                     server_updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     server_version INTEGER NOT NULL DEFAULT 0,
                     status TEXT NOT NULL DEFAULT 'active',
                     FOREIGN KEY (assignment_id) REFERENCES assignments (id) ON DELETE CASCADE,
                     FOREIGN KEY (student_pk_id) REFERENCES students (id) ON DELETE CASCADE,
                     UNIQUE (assignment_id, student_pk_id)
                 )
                 ''')

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS submission_draft_files
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     draft_id INTEGER NOT NULL,
                     question_id TEXT DEFAULT '',
                     kind TEXT DEFAULT 'file',
                     original_filename TEXT NOT NULL,
                     relative_path TEXT NOT NULL,
                     stored_path TEXT NOT NULL,
                     mime_type TEXT,
                     file_size INTEGER,
                     file_ext TEXT,
                     file_hash TEXT,
                     created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (draft_id) REFERENCES submission_drafts (id) ON DELETE CASCADE
                 )
                 ''')

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS student_feedback_review_notes
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     student_id INTEGER NOT NULL,
                     submission_id INTEGER NOT NULL,
                     question_key TEXT NOT NULL,
                     status TEXT NOT NULL DEFAULT 'open',
                     reflection TEXT NOT NULL DEFAULT '',
                     next_action TEXT NOT NULL DEFAULT '',
                     pinned INTEGER NOT NULL DEFAULT 0,
                     reviewed_at TEXT,
                     mastered_at TEXT,
                     created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     metadata_json TEXT NOT NULL DEFAULT '{}',
                     FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
                     FOREIGN KEY (submission_id) REFERENCES submissions (id) ON DELETE CASCADE,
                     UNIQUE (student_id, submission_id, question_key)
                 )
                 ''')

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS assignment_wrong_answer_ai_cache
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     assignment_id TEXT NOT NULL,
                     question_key TEXT NOT NULL,
                     answer_signature TEXT NOT NULL,
                     prompt_version TEXT NOT NULL,
                     result_json TEXT NOT NULL,
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     UNIQUE (assignment_id, question_key, answer_signature, prompt_version)
                 )
                 ''')

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS exam_paper_difficulty_ai_cache
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     exam_paper_id TEXT NOT NULL,
                     questions_signature TEXT NOT NULL,
                     prompt_version TEXT NOT NULL,
                     result_json TEXT NOT NULL,
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     UNIQUE (exam_paper_id, questions_signature, prompt_version)
                 )
                 ''')

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS assignment_wrong_summary_jobs
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     assignment_id TEXT NOT NULL,
                     teacher_id INTEGER NOT NULL,
                     questions_signature TEXT NOT NULL,
                     prompt_version TEXT NOT NULL,
                     status TEXT NOT NULL DEFAULT 'queued',
                     pending_text_questions INTEGER NOT NULL DEFAULT 0,
                     pending_difficulty INTEGER NOT NULL DEFAULT 0,
                     error_message TEXT NOT NULL DEFAULT '',
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     started_at TEXT,
                     completed_at TEXT,
                     updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     UNIQUE (assignment_id, questions_signature, prompt_version)
                 )
                 ''')

    # 10. 聊天记录 (关联到班级课堂)
