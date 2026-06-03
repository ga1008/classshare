import sqlite3


def ensure_classroom_activity_schema(conn: sqlite3.Connection) -> None:
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS study_groups
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     class_offering_id INTEGER NOT NULL,
                     assignment_id TEXT,
                     name TEXT NOT NULL,
                     description TEXT NOT NULL DEFAULT '',
                     status TEXT NOT NULL DEFAULT 'active',
                     join_policy TEXT NOT NULL DEFAULT 'open',
                     max_members INTEGER NOT NULL DEFAULT 6,
                     leader_student_id INTEGER,
                     created_by_role TEXT NOT NULL,
                     created_by_user_pk INTEGER NOT NULL,
                     created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     archived_at TEXT,
                     metadata_json TEXT NOT NULL DEFAULT '{}',
                     FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
                     FOREIGN KEY (assignment_id) REFERENCES assignments (id) ON DELETE SET NULL,
                     FOREIGN KEY (leader_student_id) REFERENCES students (id) ON DELETE SET NULL
                 )
                 ''')

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS study_group_members
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     group_id INTEGER NOT NULL,
                     student_id INTEGER NOT NULL,
                     member_role TEXT NOT NULL DEFAULT 'member',
                     status TEXT NOT NULL DEFAULT 'active',
                     contribution_summary TEXT NOT NULL DEFAULT '',
                     contribution_score REAL,
                     joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     left_at TEXT,
                     added_by_role TEXT NOT NULL DEFAULT '',
                     added_by_user_pk INTEGER,
                     metadata_json TEXT NOT NULL DEFAULT '{}',
                     FOREIGN KEY (group_id) REFERENCES study_groups (id) ON DELETE CASCADE,
                     FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
                     UNIQUE (group_id, student_id)
                 )
                 ''')

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS study_group_files
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     group_id INTEGER NOT NULL,
                     uploaded_by_role TEXT NOT NULL,
                     uploaded_by_user_pk INTEGER NOT NULL,
                     uploaded_by_name TEXT NOT NULL DEFAULT '',
                     file_hash TEXT NOT NULL,
                     original_filename TEXT NOT NULL,
                     mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                     file_size INTEGER NOT NULL DEFAULT 0,
                     description TEXT NOT NULL DEFAULT '',
                     created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     metadata_json TEXT NOT NULL DEFAULT '{}',
                     FOREIGN KEY (group_id) REFERENCES study_groups (id) ON DELETE CASCADE
                 )
                 ''')

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS group_submissions
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     group_id INTEGER NOT NULL,
                     assignment_id TEXT,
                     submitted_by_role TEXT NOT NULL,
                     submitted_by_user_pk INTEGER NOT NULL,
                     title TEXT NOT NULL DEFAULT '',
                     summary_md TEXT NOT NULL DEFAULT '',
                     final_file_id INTEGER,
                     blog_post_id INTEGER,
                     status TEXT NOT NULL DEFAULT 'submitted',
                     submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     teacher_feedback_md TEXT NOT NULL DEFAULT '',
                     metadata_json TEXT NOT NULL DEFAULT '{}',
                     FOREIGN KEY (group_id) REFERENCES study_groups (id) ON DELETE CASCADE,
                     FOREIGN KEY (assignment_id) REFERENCES assignments (id) ON DELETE SET NULL,
                     FOREIGN KEY (final_file_id) REFERENCES study_group_files (id) ON DELETE SET NULL,
                     UNIQUE (group_id, assignment_id)
                 )
                 ''')
    try:
        conn.execute("ALTER TABLE group_submissions ADD COLUMN blog_post_id INTEGER")
    except sqlite3.OperationalError:
        pass

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS peer_reviews
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     class_offering_id INTEGER NOT NULL,
                     group_id INTEGER NOT NULL,
                     assignment_id TEXT,
                     reviewer_student_id INTEGER NOT NULL,
                     reviewee_student_id INTEGER NOT NULL,
                     responsibility_score INTEGER NOT NULL DEFAULT 0,
                     collaboration_score INTEGER NOT NULL DEFAULT 0,
                     quality_score INTEGER NOT NULL DEFAULT 0,
                     comment TEXT NOT NULL DEFAULT '',
                     share_with_reviewee INTEGER NOT NULL DEFAULT 0,
                     status TEXT NOT NULL DEFAULT 'submitted',
                     created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     metadata_json TEXT NOT NULL DEFAULT '{}',
                     FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
                     FOREIGN KEY (group_id) REFERENCES study_groups (id) ON DELETE CASCADE,
                     FOREIGN KEY (assignment_id) REFERENCES assignments (id) ON DELETE SET NULL,
                     FOREIGN KEY (reviewer_student_id) REFERENCES students (id) ON DELETE CASCADE,
                     FOREIGN KEY (reviewee_student_id) REFERENCES students (id) ON DELETE CASCADE,
                     UNIQUE (group_id, assignment_id, reviewer_student_id, reviewee_student_id)
                 )
                 ''')

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS classroom_live_activities
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     class_offering_id INTEGER NOT NULL,
                     kind TEXT NOT NULL,
                     title TEXT NOT NULL DEFAULT '',
                     prompt TEXT NOT NULL DEFAULT '',
                     status TEXT NOT NULL DEFAULT 'active',
                     allow_anonymous INTEGER NOT NULL DEFAULT 1,
                     show_results TEXT NOT NULL DEFAULT 'after_submit',
                     created_by_teacher_id INTEGER NOT NULL,
                     created_by_name TEXT NOT NULL DEFAULT '',
                     created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     started_at TEXT,
                     closed_at TEXT,
                     settings_json TEXT NOT NULL DEFAULT '{}',
                     FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
                     FOREIGN KEY (created_by_teacher_id) REFERENCES teachers (id) ON DELETE CASCADE
                 )
                 ''')

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS classroom_live_options
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     activity_id INTEGER NOT NULL,
                     option_key TEXT NOT NULL DEFAULT '',
                     label TEXT NOT NULL,
                     is_correct INTEGER NOT NULL DEFAULT 0,
                     sort_order INTEGER NOT NULL DEFAULT 0,
                     created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (activity_id) REFERENCES classroom_live_activities (id) ON DELETE CASCADE
                 )
                 ''')

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS classroom_live_responses
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     activity_id INTEGER NOT NULL,
                     student_id INTEGER NOT NULL,
                     option_id INTEGER,
                     response_text TEXT NOT NULL DEFAULT '',
                     is_anonymous INTEGER NOT NULL DEFAULT 0,
                     created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     metadata_json TEXT NOT NULL DEFAULT '{}',
                     FOREIGN KEY (activity_id) REFERENCES classroom_live_activities (id) ON DELETE CASCADE,
                     FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
                     FOREIGN KEY (option_id) REFERENCES classroom_live_options (id) ON DELETE SET NULL,
                     UNIQUE (activity_id, student_id)
                 )
                 ''')

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS classroom_live_questions
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     activity_id INTEGER NOT NULL,
                     class_offering_id INTEGER NOT NULL,
                     student_id INTEGER NOT NULL,
                     display_name TEXT NOT NULL DEFAULT '',
                     question_text TEXT NOT NULL,
                     is_anonymous INTEGER NOT NULL DEFAULT 1,
                     status TEXT NOT NULL DEFAULT 'open',
                     created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     addressed_at TEXT,
                     addressed_by_teacher_id INTEGER,
                     metadata_json TEXT NOT NULL DEFAULT '{}',
                     FOREIGN KEY (activity_id) REFERENCES classroom_live_activities (id) ON DELETE CASCADE,
                     FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
                     FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
                     FOREIGN KEY (addressed_by_teacher_id) REFERENCES teachers (id) ON DELETE SET NULL
                 )
                 ''')

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS classroom_live_help_signals
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     class_offering_id INTEGER NOT NULL,
                     student_id INTEGER NOT NULL,
                     display_name TEXT NOT NULL DEFAULT '',
                     signal_type TEXT NOT NULL,
                     status TEXT NOT NULL DEFAULT 'active',
                     message TEXT NOT NULL DEFAULT '',
                     created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                     resolved_at TEXT,
                     resolved_by_teacher_id INTEGER,
                     metadata_json TEXT NOT NULL DEFAULT '{}',
                     FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
                     FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
                     FOREIGN KEY (resolved_by_teacher_id) REFERENCES teachers (id) ON DELETE SET NULL
                 )
                 ''')

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS chat_logs
                 (
                     id
                     INTEGER
                     PRIMARY
                     KEY
                     AUTOINCREMENT,
                     class_offering_id
                     INTEGER
                     NOT
                     NULL,
                     user_id
                     TEXT
                     NOT
                     NULL,
                     user_name
                     TEXT
                     NOT
                     NULL,
                     user_role
                     TEXT
                     NOT
                     NULL,
                     message
                     TEXT
                     NOT
                     NULL,
                     timestamp
                     TEXT
                     NOT
                     NULL,
                     FOREIGN
                     KEY
                 (
                     class_offering_id
                 ) REFERENCES class_offerings
                 (
                     id
                 ) ON DELETE CASCADE
                     )
                 ''')

    try:
        conn.execute("ALTER TABLE chat_logs ADD COLUMN logged_at TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE chat_logs ADD COLUMN message_type TEXT DEFAULT 'text'")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE chat_logs ADD COLUMN emoji_payload_json TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE chat_logs ADD COLUMN attachments_json TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE chat_logs ADD COLUMN quote_message_id INTEGER")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE chat_logs ADD COLUMN quote_payload_json TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在

    conn.execute(
        "UPDATE chat_logs SET logged_at = timestamp "
        "WHERE (logged_at IS NULL OR logged_at = '') AND instr(timestamp, 'T') > 0"
    )
    conn.execute(
        "UPDATE chat_logs SET message_type = 'text' "
        "WHERE message_type IS NULL OR message_type = ''"
    )

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS chat_log_migrations
                 (
                     class_offering_id INTEGER PRIMARY KEY,
                     migrated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY
                 (
                     class_offering_id
                 ) REFERENCES class_offerings
                 (
                     id
                 ) ON DELETE CASCADE
                     )
                 ''')

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_logs_room_logged_at "
        "ON chat_logs (class_offering_id, logged_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_logs_sender_logged_at "
        "ON chat_logs (user_role, user_id, logged_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_logs_room_id "
        "ON chat_logs (class_offering_id, id DESC)"
    )
    conn.execute("DROP INDEX IF EXISTS idx_chat_logs_legacy_dedupe")

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS discussion_attachments
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     class_offering_id INTEGER NOT NULL,
                     uploaded_by_user_id TEXT NOT NULL,
                     uploaded_by_role TEXT NOT NULL,
                     file_hash TEXT NOT NULL,
                     original_filename TEXT NOT NULL,
                     mime_type TEXT NOT NULL,
                     file_size INTEGER NOT NULL,
                     image_width INTEGER,
                     image_height INTEGER,
                     thumbnail_file_hash TEXT,
                     thumbnail_mime_type TEXT,
                     thumbnail_file_size INTEGER NOT NULL DEFAULT 0,
                     thumbnail_width INTEGER,
                     thumbnail_height INTEGER,
                     preview_file_hash TEXT,
                     preview_mime_type TEXT,
                     preview_file_size INTEGER NOT NULL DEFAULT 0,
                     preview_width INTEGER,
                     preview_height INTEGER,
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE
                 )
                 ''')
    for column_name, column_type in {
        "thumbnail_file_hash": "TEXT",
        "thumbnail_mime_type": "TEXT",
        "thumbnail_file_size": "INTEGER NOT NULL DEFAULT 0",
        "thumbnail_width": "INTEGER",
        "thumbnail_height": "INTEGER",
        "preview_file_hash": "TEXT",
        "preview_mime_type": "TEXT",
        "preview_file_size": "INTEGER NOT NULL DEFAULT 0",
        "preview_width": "INTEGER",
        "preview_height": "INTEGER",
    }.items():
        try:
            conn.execute(f"ALTER TABLE discussion_attachments ADD COLUMN {column_name} {column_type}")
        except sqlite3.OperationalError:
            pass
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_discussion_attachments_room_created "
        "ON discussion_attachments (class_offering_id, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_discussion_attachments_owner "
        "ON discussion_attachments (class_offering_id, uploaded_by_role, uploaded_by_user_id, created_at DESC, id DESC)"
    )

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS custom_emojis
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     class_offering_id INTEGER NOT NULL,
                     owner_user_id INTEGER NOT NULL,
                     owner_user_role TEXT NOT NULL,
                     display_name TEXT NOT NULL,
                     original_filename TEXT NOT NULL,
                     file_hash TEXT NOT NULL,
                     mime_type TEXT NOT NULL,
                     file_size INTEGER NOT NULL,
                     image_width INTEGER,
                     image_height INTEGER,
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
                     UNIQUE (class_offering_id, owner_user_id, owner_user_role, file_hash)
                 )
                 ''')

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS emoji_usage_stats
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     class_offering_id INTEGER NOT NULL,
                     user_id INTEGER NOT NULL,
                     user_role TEXT NOT NULL,
                     emoji_type TEXT NOT NULL,
                     emoji_key TEXT NOT NULL,
                     usage_count INTEGER NOT NULL DEFAULT 0,
                     last_used_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
                     UNIQUE (class_offering_id, user_id, user_role, emoji_type, emoji_key)
                 )
                 ''')

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_custom_emojis_owner "
        "ON custom_emojis (class_offering_id, owner_user_role, owner_user_id, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_custom_emojis_hash "
        "ON custom_emojis (file_hash)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_emoji_usage_owner "
        "ON emoji_usage_stats (class_offering_id, user_role, user_id, usage_count DESC, last_used_at DESC)"
    )

    # 11. 课堂 AI 配置 (新功能)
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS ai_class_configs
                 (
                     id
                         INTEGER
                         PRIMARY
                             KEY
                         AUTOINCREMENT,
                     class_offering_id
                         INTEGER
                         NOT
                             NULL
                         UNIQUE,
                     system_prompt
                         TEXT,
                     syllabus
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
                          class_offering_id
                             ) REFERENCES class_offerings
                         (
                          id
                             ) ON DELETE CASCADE
                 )
                 ''')

    # (可选) 为 ai_class_configs 创建触发器以自动更新 updated_at
    conn.execute('''
                 CREATE TRIGGER IF NOT EXISTS trigger_ai_class_configs_updated_at
                     AFTER UPDATE
                     ON ai_class_configs
                     FOR EACH ROW
                 BEGIN
                     UPDATE ai_class_configs
                     SET updated_at = CURRENT_TIMESTAMP
                     WHERE id = OLD.id;
                 END;
                 ''')

    # 12. AI 聊天会话 (新功能)
    # 存储每个用户在每个课堂的"对话"列表
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS ai_chat_sessions
                 (
                     id
                         INTEGER
                         PRIMARY
                             KEY
                         AUTOINCREMENT,
                     session_uuid
                         TEXT
                         NOT
                             NULL
                         UNIQUE,
                     class_offering_id
                         INTEGER
                         NOT
                             NULL,
                     user_pk
                         INTEGER
                         NOT
                             NULL, -- 对应 students.id 或 teachers.id
                     user_role
                         TEXT
                         NOT
                             NULL, -- 'student' 或 'teacher'
                     title
                         TEXT,     -- 对话标题，可以由AI生成或取第一句话
                     context_prompt
                         TEXT,     -- 新增: 缓存的用户背景提示
                     created_at
                         TEXT
                         DEFAULT
                             CURRENT_TIMESTAMP,
                     FOREIGN
                         KEY
                         (
                          class_offering_id
                             ) REFERENCES class_offerings
                         (
                          id
                             ) ON DELETE CASCADE
                 )
                 ''')

    # 13. AI 聊天消息 (新功能)
    # 存储具体的每一条消息
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS ai_chat_messages
                 (
                     id
                         INTEGER
                         PRIMARY
                             KEY
                         AUTOINCREMENT,
                     session_id
                         INTEGER
                         NOT
                             NULL,
                     role
                         TEXT
                         NOT
                             NULL, -- 'user' 或 'assistant'
                     message
                         TEXT
                         NOT
                             NULL, -- 消息的文本部分
                     thinking_content
                         TEXT,     -- 推理/思考过程（仅后端内部使用）
                     final_answer
                         TEXT,     -- 最终回答正文，便于前端历史消息恢复
                     attachments_json
                         TEXT,     -- 存储附件信息, e.g., '[{"type": "image", "name": "screenshot.png", ...}]'
                     timestamp
                         TEXT
                         DEFAULT
                             CURRENT_TIMESTAMP,
                     FOREIGN
                         KEY
                         (
                          session_id
                             ) REFERENCES ai_chat_sessions
                         (
                          id
                             ) ON DELETE CASCADE
                 )
                 ''')

    # 兼容已有数据库：为 ai_chat_messages 补充流式思考相关字段
    try:
        conn.execute("ALTER TABLE ai_chat_messages ADD COLUMN thinking_content TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE ai_chat_messages ADD COLUMN final_answer TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在

    # 13.5 内部学习支持快照
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS ai_psychology_profiles
                 (
                     id
                         INTEGER
                         PRIMARY
                             KEY
                         AUTOINCREMENT,
                     class_offering_id
                         INTEGER
                         NOT
                             NULL,
                     session_id
                         INTEGER
                         NOT
                             NULL,
                     user_pk
                         INTEGER
                         NOT
                             NULL,
                     user_role
                         TEXT
                         NOT
                             NULL,
                     round_index
                         INTEGER
                         NOT
                             NULL
                         DEFAULT 0,
                     profile_summary
                         TEXT,
                     mental_state_summary
                         TEXT,
                     support_strategy
                         TEXT,
                     hidden_premise_prompt
                         TEXT,
                     confidence
                         TEXT,
                     raw_payload
                         TEXT,
                     created_at
                         TEXT
                         DEFAULT
                             CURRENT_TIMESTAMP,
                     FOREIGN
                         KEY
                         (
                          class_offering_id
                             ) REFERENCES class_offerings
                         (
                          id
                             ) ON DELETE CASCADE,
                     FOREIGN
                         KEY
                         (
                          session_id
                             ) REFERENCES ai_chat_sessions
                         (
                          id
                             ) ON DELETE CASCADE
                 )
                 ''')

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_chat_sessions_owner "
        "ON ai_chat_sessions (class_offering_id, user_pk, user_role, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_chat_messages_session_time "
        "ON ai_chat_messages (session_id, timestamp ASC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_chat_messages_session_role_time "
        "ON ai_chat_messages (session_id, role, timestamp DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_psych_profiles_lookup "
        "ON ai_psychology_profiles (class_offering_id, user_pk, user_role, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_psych_profiles_session_round "
        "ON ai_psychology_profiles (session_id, round_index DESC)"
    )

    # 13.55 教师 Agent 任务中心：全平台单队列，任务详情仅所有者可见
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS agent_tasks
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     task_uuid TEXT NOT NULL UNIQUE,
                     teacher_id INTEGER NOT NULL,
                     teacher_name TEXT NOT NULL DEFAULT '',
                     task_type TEXT NOT NULL,
                     title TEXT NOT NULL,
                     public_summary TEXT NOT NULL DEFAULT '',
                     private_instruction TEXT NOT NULL,
                     context_snapshot_json TEXT NOT NULL DEFAULT '{}',
                     status TEXT NOT NULL DEFAULT 'queued',
                     priority INTEGER NOT NULL DEFAULT 0,
                     runtime_provider TEXT NOT NULL DEFAULT 'deepseek-tui',
                     runtime_task_id TEXT,
                     runtime_thread_id TEXT,
                     runtime_turn_id TEXT,
                     runtime_status TEXT,
                     result_summary TEXT,
                     result_detail_json TEXT NOT NULL DEFAULT '{}',
                     error_message TEXT NOT NULL DEFAULT '',
                     worker_id TEXT,
                     cancel_requested_at TEXT,
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     started_at TEXT,
                     completed_at TEXT,
                     updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE
                 )
                 ''')
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS agent_task_events
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     task_id INTEGER NOT NULL,
                     event_type TEXT NOT NULL DEFAULT 'status',
                     message TEXT NOT NULL DEFAULT '',
                     detail_json TEXT NOT NULL DEFAULT '{}',
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (task_id) REFERENCES agent_tasks (id) ON DELETE CASCADE
                 )
                 ''')
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS agent_task_composers
                 (
                     teacher_id INTEGER PRIMARY KEY,
                     teacher_name TEXT NOT NULL DEFAULT '',
                     page_label TEXT NOT NULL DEFAULT '',
                     updated_at TEXT NOT NULL,
                     FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE
                 )
                 ''')
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_agent_tasks_status_created "
        "ON agent_tasks (status, priority DESC, created_at ASC, id ASC)",
        "CREATE INDEX IF NOT EXISTS idx_agent_tasks_teacher_created "
        "ON agent_tasks (teacher_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_agent_tasks_runtime "
        "ON agent_tasks (runtime_task_id)",
        "CREATE INDEX IF NOT EXISTS idx_agent_task_events_task_time "
        "ON agent_task_events (task_id, created_at ASC, id ASC)",
        "CREATE INDEX IF NOT EXISTS idx_agent_task_composers_updated "
        "ON agent_task_composers (updated_at DESC)",
    ):
        conn.execute(statement)

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS agent_runtime_api_keys
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     provider TEXT NOT NULL DEFAULT 'deepseek',
                     key_label TEXT NOT NULL,
                     key_fingerprint TEXT NOT NULL UNIQUE,
                     key_encrypted TEXT NOT NULL,
                     key_suffix TEXT NOT NULL DEFAULT '',
                     base_url TEXT NOT NULL DEFAULT 'https://api.deepseek.com',
                     model TEXT NOT NULL DEFAULT 'deepseek-v4-pro',
                     enabled INTEGER NOT NULL DEFAULT 1,
                     is_active INTEGER NOT NULL DEFAULT 0,
                     created_by_teacher_id INTEGER,
                     last_test_status TEXT NOT NULL DEFAULT 'unchecked',
                     last_test_message TEXT NOT NULL DEFAULT '',
                     last_test_usage_json TEXT NOT NULL DEFAULT '{}',
                     last_test_at TEXT,
                     last_used_at TEXT,
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (created_by_teacher_id) REFERENCES teachers (id) ON DELETE SET NULL
                 )
                 ''')
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS agent_runtime_key_checks
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     key_id INTEGER NOT NULL,
                     status TEXT NOT NULL,
                     message TEXT NOT NULL DEFAULT '',
                     response_ms INTEGER NOT NULL DEFAULT 0,
                     usage_json TEXT NOT NULL DEFAULT '{}',
                     checked_by_teacher_id INTEGER,
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (key_id) REFERENCES agent_runtime_api_keys (id) ON DELETE CASCADE,
                     FOREIGN KEY (checked_by_teacher_id) REFERENCES teachers (id) ON DELETE SET NULL
                 )
                 ''')
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS agent_runtime_usage_snapshots
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     source TEXT NOT NULL DEFAULT 'deepseek-tui',
                     runtime_url TEXT NOT NULL DEFAULT '',
                     usage_json TEXT NOT NULL DEFAULT '{}',
                     fetched_by_teacher_id INTEGER,
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (fetched_by_teacher_id) REFERENCES teachers (id) ON DELETE SET NULL
                 )
                 ''')
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_agent_runtime_api_keys_active "
        "ON agent_runtime_api_keys (provider, enabled, is_active, updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_agent_runtime_key_checks_key_time "
        "ON agent_runtime_key_checks (key_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_agent_runtime_usage_snapshots_time "
        "ON agent_runtime_usage_snapshots (created_at DESC)",
    ):
        conn.execute(statement)

    # 13.6 课堂研讨室行为记录
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS classroom_behavior_events
                 (
                     id
                         INTEGER
                         PRIMARY
                             KEY
                         AUTOINCREMENT,
                     class_offering_id
                         INTEGER
                         NOT
                             NULL,
                     user_pk
                         INTEGER
                         NOT
                             NULL,
                     user_role
                         TEXT
                         NOT
                             NULL,
                     display_name
                         TEXT,
                     action_type
                         TEXT
                         NOT
                             NULL,
                     summary_text
                         TEXT
                         NOT
                             NULL,
                     payload_json
                         TEXT,
                     created_at
                         TEXT
                         DEFAULT
                             CURRENT_TIMESTAMP,
                     FOREIGN
                         KEY
                         (
                          class_offering_id
                             ) REFERENCES class_offerings
                         (
                          id
                             ) ON DELETE CASCADE
                 )
                 ''')

    # 13.7 课堂研讨室行为计数状态
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS classroom_behavior_states
                 (
                     class_offering_id
                         INTEGER
                         NOT
                             NULL,
                     user_pk
                         INTEGER
                         NOT
                             NULL,
                     user_role
                         TEXT
                         NOT
                             NULL,
                     total_activity_count
                         INTEGER
                         NOT
                             NULL
                         DEFAULT 0,
                     last_profiled_activity_count
                         INTEGER
                         NOT
                             NULL
                         DEFAULT 0,
                     profile_generation_pending
                         INTEGER
                         NOT
                             NULL
                         DEFAULT 0,
                     last_event_at
                          TEXT,
                     last_profiled_at
                          TEXT,
                     next_profile_interval_seconds
                         INTEGER
                         NOT
                             NULL
                         DEFAULT 0,
                     next_profile_due_at
                          TEXT,
                     online_accumulated_seconds
                         INTEGER
                         NOT
                             NULL
                         DEFAULT 0,
                     current_session_started_at
                         TEXT,
                     last_presence_at
                         TEXT,
                     last_page_key
                         TEXT,
                     last_visibility_state
                         TEXT,
                     last_focus_state
                         TEXT,
                     last_idle_seconds
                         INTEGER
                         NOT
                             NULL
                         DEFAULT 0,
                     focus_total_seconds
                         INTEGER
                         NOT
                             NULL
                         DEFAULT 0,
                     blur_total_seconds
                         INTEGER
                         NOT
                             NULL
                         DEFAULT 0,
                     visible_total_seconds
                         INTEGER
                         NOT
                             NULL
                         DEFAULT 0,
                     hidden_total_seconds
                         INTEGER
                         NOT
                             NULL
                         DEFAULT 0,
                     discussion_lurk_total_seconds
                         INTEGER
                         NOT
                             NULL
                         DEFAULT 0,
                     ai_panel_open_total_seconds
                         INTEGER
                         NOT
                             NULL
                         DEFAULT 0,
                     created_at
                          TEXT
                          DEFAULT
                              CURRENT_TIMESTAMP,
                     updated_at
                         TEXT
                         DEFAULT
                             CURRENT_TIMESTAMP,
                     PRIMARY KEY
                         (
                          class_offering_id,
                          user_pk,
                          user_role
                             ),
                     FOREIGN
                         KEY
                         (
                          class_offering_id
                             ) REFERENCES class_offerings
                         (
                          id
                             ) ON DELETE CASCADE
                 )
                 ''')

    try:
        conn.execute("ALTER TABLE classroom_behavior_states ADD COLUMN next_profile_interval_seconds INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_states ADD COLUMN next_profile_due_at TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_states ADD COLUMN online_accumulated_seconds INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_states ADD COLUMN current_session_started_at TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_states ADD COLUMN last_presence_at TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_states ADD COLUMN last_page_key TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_states ADD COLUMN last_visibility_state TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_states ADD COLUMN last_focus_state TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_states ADD COLUMN last_idle_seconds INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_states ADD COLUMN focus_total_seconds INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_states ADD COLUMN blur_total_seconds INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_states ADD COLUMN visible_total_seconds INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_states ADD COLUMN hidden_total_seconds INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_states ADD COLUMN discussion_lurk_total_seconds INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_states ADD COLUMN ai_panel_open_total_seconds INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # 13.8 课堂研讨室内部学习支持快照
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS classroom_behavior_profiles
                 (
                     id
                         INTEGER
                         PRIMARY
                             KEY
                         AUTOINCREMENT,
                     class_offering_id
                         INTEGER
                         NOT
                             NULL,
                     user_pk
                         INTEGER
                         NOT
                             NULL,
                     user_role
                         TEXT
                         NOT
                             NULL,
                     trigger_event_id
                         INTEGER,
                     round_index
                         INTEGER
                         NOT
                             NULL
                         DEFAULT 0,
                     activity_count_snapshot
                         INTEGER
                         NOT
                             NULL
                         DEFAULT 0,
                     profile_summary
                         TEXT,
                     mental_state_summary
                         TEXT,
                     support_strategy
                          TEXT,
                     hidden_premise_prompt
                          TEXT,
                     personality_traits
                         TEXT,
                     preference_summary
                         TEXT,
                     language_habit_summary
                         TEXT,
                     preferred_ai_style
                         TEXT,
                     interest_hypothesis
                         TEXT,
                     evidence_summary
                         TEXT,
                     trigger_mode
                         TEXT
                         NOT
                             NULL
                         DEFAULT 'scheduled',
                     confidence
                          TEXT,
                     raw_payload
                          TEXT,
                     created_at
                         TEXT
                         DEFAULT
                             CURRENT_TIMESTAMP,
                     FOREIGN
                         KEY
                         (
                          class_offering_id
                             ) REFERENCES class_offerings
                         (
                          id
                             ) ON DELETE CASCADE,
                     FOREIGN
                         KEY
                         (
                          trigger_event_id
                             ) REFERENCES classroom_behavior_events
                         (
                          id
                             ) ON DELETE SET NULL
                 )
                 ''')

    try:
        conn.execute("ALTER TABLE classroom_behavior_profiles ADD COLUMN personality_traits TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_profiles ADD COLUMN preference_summary TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_profiles ADD COLUMN language_habit_summary TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_profiles ADD COLUMN preferred_ai_style TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_profiles ADD COLUMN interest_hypothesis TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_profiles ADD COLUMN evidence_summary TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classroom_behavior_profiles ADD COLUMN trigger_mode TEXT NOT NULL DEFAULT 'scheduled'")
    except sqlite3.OperationalError:
        pass

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classroom_behavior_events_lookup "
        "ON classroom_behavior_events (class_offering_id, user_pk, user_role, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classroom_behavior_events_action "
        "ON classroom_behavior_events (class_offering_id, action_type, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classroom_behavior_profiles_lookup "
        "ON classroom_behavior_profiles (class_offering_id, user_pk, user_role, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classroom_behavior_states_due "
        "ON classroom_behavior_states (profile_generation_pending, last_presence_at, online_accumulated_seconds, next_profile_interval_seconds)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classroom_behavior_states_due_at "
        "ON classroom_behavior_states (profile_generation_pending, last_presence_at, next_profile_due_at)"
    )

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS ui_copy_snapshots
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL UNIQUE,
            schema_version TEXT NOT NULL DEFAULT 'v1',
            source TEXT NOT NULL DEFAULT 'fallback',
            generation_reason TEXT DEFAULT '',
            payload_json TEXT NOT NULL,
            generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ui_copy_snapshots_lookup "
        "ON ui_copy_snapshots (snapshot_date DESC, generated_at DESC, id DESC)"
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS discussion_mood_snapshots
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_offering_id INTEGER NOT NULL UNIQUE,
            schema_version TEXT NOT NULL DEFAULT 'v1',
            source TEXT NOT NULL DEFAULT 'fallback',
            mood_label TEXT NOT NULL DEFAULT 'warm',
            headline TEXT NOT NULL,
            detail TEXT NOT NULL,
            latest_message_id INTEGER NOT NULL DEFAULT 0,
            raw_payload_json TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE
        )
        '''
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_discussion_mood_snapshots_updated "
        "ON discussion_mood_snapshots (updated_at DESC, id DESC)"
    )

    # 13.9 系统信息中心通知
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS message_center_notifications
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     recipient_identity TEXT NOT NULL,
                     recipient_role TEXT NOT NULL,
                     recipient_user_pk INTEGER NOT NULL,
                     category TEXT NOT NULL,
                     severity TEXT NOT NULL DEFAULT 'normal',
                     actor_identity TEXT DEFAULT '',
                     actor_role TEXT DEFAULT '',
                     actor_user_pk INTEGER,
                     actor_display_name TEXT DEFAULT '',
                     title TEXT NOT NULL,
                     body_preview TEXT DEFAULT '',
                     link_url TEXT DEFAULT '',
                     class_offering_id INTEGER,
                     ref_type TEXT DEFAULT '',
                     ref_id TEXT DEFAULT '',
                     metadata_json TEXT DEFAULT '{}',
                     email_status TEXT NOT NULL DEFAULT 'not_required',
                     email_job_id INTEGER,
                     email_queued_at TEXT,
                     email_sent_at TEXT,
                     read_at TEXT,
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE SET NULL
                 )
                 ''')
    for column_name, column_def in {
        "severity": "TEXT NOT NULL DEFAULT 'normal'",
        "email_status": "TEXT NOT NULL DEFAULT 'not_required'",
        "email_job_id": "INTEGER",
        "email_queued_at": "TEXT",
        "email_sent_at": "TEXT",
    }.items():
        try:
            conn.execute(f"ALTER TABLE message_center_notifications ADD COLUMN {column_name} {column_def}")
        except sqlite3.OperationalError:
            pass
    conn.execute(
        """
        UPDATE message_center_notifications
        SET severity = CASE
            WHEN category IN ('assignment', 'discussion_mention', 'submission', 'grading_result', 'learning_progress', 'collaboration') THEN 'important'
            WHEN category IN ('ai_feedback', 'app_feedback', 'password_reset_request') THEN 'system'
            ELSE 'normal'
        END
        WHERE severity IS NULL OR severity = ''
        """
    )

    # 13.10 绉佷俊浼氳瘽
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS private_messages
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     conversation_key TEXT NOT NULL,
                     class_offering_id INTEGER,
                     sender_identity TEXT NOT NULL,
                     sender_role TEXT NOT NULL,
                     sender_user_pk INTEGER,
                     sender_display_name TEXT NOT NULL,
                     recipient_identity TEXT NOT NULL,
                     recipient_role TEXT NOT NULL,
                     recipient_user_pk INTEGER,
                     recipient_display_name TEXT NOT NULL,
                     content TEXT NOT NULL,
                     read_at TEXT,
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE SET NULL
                 )
                 ''')

    # 13.11 绉佷俊榛戝悕鍗?
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS private_message_blocks
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     owner_identity TEXT NOT NULL,
                     owner_role TEXT NOT NULL,
                     owner_user_pk INTEGER NOT NULL,
                     blocked_identity TEXT NOT NULL,
                     blocked_role TEXT NOT NULL,
                     blocked_user_pk INTEGER,
                     blocked_display_name TEXT DEFAULT '',
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     UNIQUE (owner_identity, blocked_identity)
                 )
                 ''')

    # 13.12 私信审计日志（不记录内容）
    conn.execute('''
                 CREATE TABLE IF NOT EXISTS private_message_audit_logs
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     message_id INTEGER NOT NULL,
                     class_offering_id INTEGER,
                     sender_identity TEXT NOT NULL,
                     sender_role TEXT NOT NULL,
                     sender_user_pk INTEGER,
                     sender_display_name TEXT NOT NULL,
                     recipient_identity TEXT NOT NULL,
                     recipient_role TEXT NOT NULL,
                     recipient_user_pk INTEGER,
                     recipient_display_name TEXT NOT NULL,
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (message_id) REFERENCES private_messages (id) ON DELETE CASCADE,
                     FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE SET NULL
                 )
                 ''')

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS private_message_attachments
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     message_id INTEGER NOT NULL,
                     conversation_key TEXT NOT NULL,
                     class_offering_id INTEGER,
                     uploaded_by_identity TEXT NOT NULL,
                     uploaded_by_role TEXT NOT NULL,
                     file_hash TEXT NOT NULL,
                     original_filename TEXT NOT NULL,
                     mime_type TEXT NOT NULL,
                     file_size INTEGER NOT NULL,
                     attachment_kind TEXT NOT NULL DEFAULT 'file',
                     image_width INTEGER,
                     image_height INTEGER,
                     thumbnail_file_hash TEXT,
                     thumbnail_mime_type TEXT,
                     thumbnail_file_size INTEGER NOT NULL DEFAULT 0,
                     thumbnail_width INTEGER,
                     thumbnail_height INTEGER,
                     preview_file_hash TEXT,
                     preview_mime_type TEXT,
                     preview_file_size INTEGER NOT NULL DEFAULT 0,
                     preview_width INTEGER,
                     preview_height INTEGER,
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (message_id) REFERENCES private_messages (id) ON DELETE CASCADE,
                     FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE SET NULL
                 )
                 ''')
    for column_name, column_type in {
        "image_width": "INTEGER",
        "image_height": "INTEGER",
        "thumbnail_file_hash": "TEXT",
        "thumbnail_mime_type": "TEXT",
        "thumbnail_file_size": "INTEGER NOT NULL DEFAULT 0",
        "thumbnail_width": "INTEGER",
        "thumbnail_height": "INTEGER",
        "preview_file_hash": "TEXT",
        "preview_mime_type": "TEXT",
        "preview_file_size": "INTEGER NOT NULL DEFAULT 0",
        "preview_width": "INTEGER",
        "preview_height": "INTEGER",
    }.items():
        try:
            conn.execute(f"ALTER TABLE private_message_attachments ADD COLUMN {column_name} {column_type}")
        except sqlite3.OperationalError:
            pass

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS private_message_ai_jobs
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     conversation_key TEXT NOT NULL,
                     class_offering_id INTEGER NOT NULL,
                     request_message_id INTEGER NOT NULL UNIQUE,
                     requester_identity TEXT NOT NULL,
                     requester_role TEXT NOT NULL,
                     requester_user_pk INTEGER NOT NULL,
                     status TEXT NOT NULL DEFAULT 'pending',
                     error_message TEXT DEFAULT '',
                     reply_message_id INTEGER,
                     attempt_count INTEGER NOT NULL DEFAULT 0,
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     started_at TEXT,
                     finished_at TEXT,
                     updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
                     FOREIGN KEY (request_message_id) REFERENCES private_messages (id) ON DELETE CASCADE,
                     FOREIGN KEY (reply_message_id) REFERENCES private_messages (id) ON DELETE SET NULL
                 )
                 ''')

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS teacher_email_configs
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     teacher_id INTEGER NOT NULL,
                     label TEXT NOT NULL DEFAULT '默认邮箱',
                     provider TEXT DEFAULT '',
                     smtp_host TEXT NOT NULL,
                     smtp_port INTEGER NOT NULL DEFAULT 465,
                     smtp_security TEXT NOT NULL DEFAULT 'ssl',
                     smtp_username TEXT DEFAULT '',
                     smtp_password_encrypted TEXT DEFAULT '',
                     from_email TEXT NOT NULL,
                     from_name TEXT DEFAULT '',
                     imap_host TEXT DEFAULT '',
                     imap_port INTEGER NOT NULL DEFAULT 993,
                     imap_security TEXT NOT NULL DEFAULT 'ssl',
                     imap_username TEXT DEFAULT '',
                     imap_password_encrypted TEXT DEFAULT '',
                     enabled INTEGER NOT NULL DEFAULT 1,
                     is_default INTEGER NOT NULL DEFAULT 0,
                     per_minute_limit INTEGER NOT NULL DEFAULT 25,
                     daily_limit INTEGER NOT NULL DEFAULT 300,
                     last_status TEXT NOT NULL DEFAULT 'unchecked',
                     last_status_at TEXT,
                     last_error TEXT DEFAULT '',
                     sent_success_count INTEGER NOT NULL DEFAULT 0,
                     sent_failure_count INTEGER NOT NULL DEFAULT 0,
                     last_sent_at TEXT,
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE
                 )
                 ''')
    for column_name, column_def in {
        "provider": "TEXT DEFAULT ''",
        "imap_host": "TEXT DEFAULT ''",
        "imap_port": "INTEGER NOT NULL DEFAULT 993",
        "imap_security": "TEXT NOT NULL DEFAULT 'ssl'",
        "imap_username": "TEXT DEFAULT ''",
        "imap_password_encrypted": "TEXT DEFAULT ''",
        "enabled": "INTEGER NOT NULL DEFAULT 1",
        "is_default": "INTEGER NOT NULL DEFAULT 0",
        "per_minute_limit": "INTEGER NOT NULL DEFAULT 25",
        "daily_limit": "INTEGER NOT NULL DEFAULT 300",
        "last_status": "TEXT NOT NULL DEFAULT 'unchecked'",
        "last_status_at": "TEXT",
        "last_error": "TEXT DEFAULT ''",
        "sent_success_count": "INTEGER NOT NULL DEFAULT 0",
        "sent_failure_count": "INTEGER NOT NULL DEFAULT 0",
        "last_sent_at": "TEXT",
    }.items():
        try:
            conn.execute(f"ALTER TABLE teacher_email_configs ADD COLUMN {column_name} {column_def}")
        except sqlite3.OperationalError:
            pass

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS email_outbox
                 (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     config_id INTEGER,
                     teacher_id INTEGER NOT NULL,
                     notification_id INTEGER,
                     dedupe_key TEXT NOT NULL UNIQUE,
                     recipient_identity TEXT NOT NULL,
                     recipient_role TEXT NOT NULL,
                     recipient_user_pk INTEGER NOT NULL,
                     recipient_email TEXT NOT NULL,
                     subject TEXT NOT NULL,
                     body_text TEXT NOT NULL,
                     body_html TEXT DEFAULT '',
                     category TEXT NOT NULL DEFAULT '',
                     severity TEXT NOT NULL DEFAULT 'important',
                     status TEXT NOT NULL DEFAULT 'queued',
                     attempt_count INTEGER NOT NULL DEFAULT 0,
                     next_attempt_at TEXT,
                     locked_at TEXT,
                     sent_at TEXT,
                     last_error TEXT DEFAULT '',
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (config_id) REFERENCES teacher_email_configs (id) ON DELETE SET NULL,
                     FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
                     FOREIGN KEY (notification_id) REFERENCES message_center_notifications (id) ON DELETE SET NULL
                 )
                 ''')
    try:
        conn.execute("ALTER TABLE email_outbox ADD COLUMN body_html TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS email_worker_heartbeats
                 (
                     worker_id TEXT PRIMARY KEY,
                     status TEXT NOT NULL DEFAULT 'starting',
                     queue_depth INTEGER NOT NULL DEFAULT 0,
                     last_error TEXT DEFAULT '',
                     updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                 )
                 ''')

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_center_notifications_recipient_created "
        "ON message_center_notifications (recipient_role, recipient_user_pk, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_center_notifications_recipient_category_read "
        "ON message_center_notifications (recipient_role, recipient_user_pk, category, read_at, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_center_notifications_ref "
        "ON message_center_notifications (ref_type, ref_id, recipient_role, recipient_user_pk)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_center_notifications_unread "
        "ON message_center_notifications (recipient_role, recipient_user_pk, read_at, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_center_notifications_severity "
        "ON message_center_notifications (recipient_role, recipient_user_pk, severity, read_at, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_private_messages_conversation_time "
        "ON private_messages (conversation_key, created_at ASC, id ASC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_private_messages_recipient_read "
        "ON private_messages (recipient_identity, read_at, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_private_messages_sender_time "
        "ON private_messages (sender_identity, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_private_message_blocks_owner "
        "ON private_message_blocks (owner_identity, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_private_message_audit_lookup "
        "ON private_message_audit_logs (sender_identity, recipient_identity, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_private_message_attachments_message "
        "ON private_message_attachments (message_id, id ASC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_private_message_attachments_conversation "
        "ON private_message_attachments (conversation_key, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_private_message_ai_jobs_lookup "
        "ON private_message_ai_jobs (requester_identity, conversation_key, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_private_message_ai_jobs_status "
        "ON private_message_ai_jobs (status, created_at ASC, id ASC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_email_configs_teacher "
        "ON teacher_email_configs (teacher_id, enabled, is_default, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_email_outbox_due "
        "ON email_outbox (status, next_attempt_at, created_at ASC, id ASC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_email_outbox_config_status "
        "ON email_outbox (config_id, status, sent_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_email_outbox_teacher_sent "
        "ON email_outbox (teacher_id, status, sent_at DESC, id DESC)"
    )

    # 14. 试卷库
