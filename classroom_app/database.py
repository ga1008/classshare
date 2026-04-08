import sqlite3
import sys
import json
import uuid
from datetime import datetime
from .config import DB_PATH


def get_db_connection():
    """获取 SQLite 数据库连接"""
    try:
        # 增加 timeout 避免高并发时的瞬间锁死报错
        conn = sqlite3.connect(DB_PATH, timeout=20.0)
        conn.execute("PRAGMA journal_mode=WAL;")  # 核心优化：开启 WAL 模式支持高并发读写
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        print(f"[DB ERROR] 无法连接到数据库: {e}")
        sys.exit(1)


def init_database():
    """
    初始化 V4.0 数据库架构
    包含教师、学生、班级、课程、课堂(关联)和所有资源表
    """
    print("[DB] Initializing V4.0 database schema...")
    try:
        with get_db_connection() as conn:
            # 1. 用户 (教师)
            conn.execute('''
                         CREATE TABLE IF NOT EXISTS teachers
                         (
                             id
                             INTEGER
                             PRIMARY
                             KEY
                             AUTOINCREMENT,
                             name
                             TEXT
                             NOT
                             NULL,
                             email
                             TEXT
                             NOT
                             NULL
                             UNIQUE,
                             hashed_password
                             TEXT
                             NOT
                             NULL,
                             profile_info
                             TEXT,
                             nickname TEXT,
                             description
                             TEXT,
                             created_at
                             TEXT
                             DEFAULT
                             CURRENT_TIMESTAMP
                         )
                         ''')

            # 2. 班级
            conn.execute('''
                         CREATE TABLE IF NOT EXISTS classes
                         (
                             id
                             INTEGER
                             PRIMARY
                             KEY
                             AUTOINCREMENT,
                             name
                             TEXT
                             NOT
                             NULL
                             UNIQUE,
                             created_by_teacher_id
                             INTEGER
                             NOT
                             NULL,
                                description TEXT,
                             created_at
                             TEXT
                             DEFAULT
                             CURRENT_TIMESTAMP,
                             FOREIGN
                             KEY
                         (
                             created_by_teacher_id
                         ) REFERENCES teachers
                         (
                             id
                         )
                             )
                         ''')

            # 3. 学生
            conn.execute('''
                         CREATE TABLE IF NOT EXISTS students
                         (
                             id
                             INTEGER
                             PRIMARY
                             KEY
                             AUTOINCREMENT,
                             student_id_number
                             TEXT
                             NOT
                             NULL
                             UNIQUE,
                             name
                             TEXT
                             NOT
                             NULL,
                             class_id
                             INTEGER
                             NOT
                             NULL,
                             gender
                             TEXT,
                             email
                             TEXT,
                             phone
                             TEXT,
                             hashed_password
                             TEXT,
                             password_reset_required
                             INTEGER
                             NOT
                             NULL
                             DEFAULT
                             0,
                             password_updated_at
                             TEXT,
                             profile_info
                             TEXT,
                             nickname
                             TEXT,
                                description TEXT,
                             created_at
                             TEXT
                             DEFAULT
                             CURRENT_TIMESTAMP,
                             FOREIGN
                             KEY
                         (
                             class_id
                         ) REFERENCES classes
                         (
                             id
                         )
                             )
                         ''')

            try:
                conn.execute("ALTER TABLE students ADD COLUMN hashed_password TEXT")
            except sqlite3.OperationalError:
                pass  # 列已存在
            try:
                conn.execute(
                    "ALTER TABLE students ADD COLUMN password_reset_required INTEGER NOT NULL DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass  # 列已存在
            try:
                conn.execute("ALTER TABLE students ADD COLUMN password_updated_at TEXT")
            except sqlite3.OperationalError:
                pass  # 列已存在

            # 4. 课程 (模板)
            conn.execute('''
                         CREATE TABLE IF NOT EXISTS courses
                         (
                             id
                                 INTEGER
                                 PRIMARY
                                     KEY
                                 AUTOINCREMENT,
                             name
                                 TEXT
                                 NOT
                                     NULL,
                             description
                                 TEXT,
                             credits
                                 FLOAT,
                             created_by_teacher_id
                                 INTEGER
                                 NOT
                                     NULL,
                                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                             FOREIGN
                                 KEY
                                 (
                                  created_by_teacher_id
                                     ) REFERENCES teachers
                                 (
                                  id
                                     )
                         )
                         ''')

            # 5. 班级课堂 (核心关联表)
            conn.execute('''
                         CREATE TABLE IF NOT EXISTS class_offerings
                         (
                             id
                             INTEGER
                             PRIMARY
                             KEY
                             AUTOINCREMENT,
                             class_id
                             INTEGER
                             NOT
                             NULL,
                             course_id
                             INTEGER
                             NOT
                             NULL,
                             teacher_id
                             INTEGER
                             NOT
                             NULL,
                             semester
                             TEXT,
                                schedule_info TEXT,
                             created_at
                             TEXT
                             DEFAULT
                             CURRENT_TIMESTAMP,
                             FOREIGN
                             KEY
                         (
                             class_id
                         ) REFERENCES classes
                         (
                             id
                         ),
                             FOREIGN KEY
                         (
                             course_id
                         ) REFERENCES courses
                         (
                             id
                         ),
                             FOREIGN KEY
                         (
                             teacher_id
                         ) REFERENCES teachers
                         (
                             id
                         ),
                             UNIQUE
                         (
                             class_id,
                             course_id,
                             semester
                         )
                             )
                         ''')

            # 6. 课程资源 (替换旧的 shared_files)

            # 6.1 学生登录审计
            conn.execute('''
                         CREATE TABLE IF NOT EXISTS student_login_audit_logs
                         (
                             id INTEGER PRIMARY KEY AUTOINCREMENT,
                             student_id INTEGER NOT NULL,
                             class_id INTEGER NOT NULL,
                             class_name_snapshot TEXT NOT NULL,
                             login_sequence INTEGER NOT NULL,
                             login_method TEXT NOT NULL,
                             identifier_type TEXT NOT NULL,
                             identifier_value TEXT NOT NULL,
                             ip_address TEXT,
                             user_agent TEXT,
                             device_type TEXT,
                             os_name TEXT,
                             browser_name TEXT,
                             device_label TEXT,
                             logged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                             FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
                             FOREIGN KEY (class_id) REFERENCES classes (id) ON DELETE CASCADE
                         )
                         ''')

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_student_login_audit_student "
                "ON student_login_audit_logs (student_id, logged_at DESC, id DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_student_login_audit_class "
                "ON student_login_audit_logs (class_id, logged_at DESC, id DESC)"
            )

            # 6.2 学生找回密码申请
            conn.execute('''
                         CREATE TABLE IF NOT EXISTS student_password_reset_requests
                         (
                             id INTEGER PRIMARY KEY AUTOINCREMENT,
                             student_id INTEGER NOT NULL,
                             class_id INTEGER NOT NULL,
                             teacher_id INTEGER NOT NULL,
                             status TEXT NOT NULL DEFAULT 'pending',
                             request_name TEXT NOT NULL,
                             request_student_id_number TEXT NOT NULL,
                             request_class_name TEXT NOT NULL,
                             requester_ip TEXT,
                             requester_user_agent TEXT,
                             requester_device_type TEXT,
                             requester_os_name TEXT,
                             requester_browser_name TEXT,
                             requester_device_label TEXT,
                             submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
                             reviewed_at TEXT,
                             completed_at TEXT,
                             reviewed_by_teacher_id INTEGER,
                             review_note TEXT,
                             FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
                             FOREIGN KEY (class_id) REFERENCES classes (id) ON DELETE CASCADE,
                             FOREIGN KEY (teacher_id) REFERENCES teachers (id),
                             FOREIGN KEY (reviewed_by_teacher_id) REFERENCES teachers (id)
                         )
                         ''')

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_student_password_reset_requests_status "
                "ON student_password_reset_requests (teacher_id, status, submitted_at DESC, id DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_student_password_reset_requests_student "
                "ON student_password_reset_requests (student_id, submitted_at DESC, id DESC)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_student_password_reset_requests_one_pending "
                "ON student_password_reset_requests (student_id) WHERE status = 'pending'"
            )


            # 7. 作业 (关联到课程)
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
                conn.execute("ALTER TABLE course_files ADD COLUMN uploaded_by_teacher_id INTEGER")
            except sqlite3.OperationalError:
                pass  # 列已存在

            # 兼容已有数据库：为 submissions 添加 answers_json 列
            try:
                conn.execute("ALTER TABLE submissions ADD COLUMN answers_json TEXT")
            except sqlite3.OperationalError:
                pass  # 列已存在

            # 兼容已有数据库：为 assignments 添加 exam_paper_id 列
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
                             allowed_file_types_json
                             TEXT,
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

            # 10. 聊天记录 (关联到班级课堂)
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
                "CREATE INDEX IF NOT EXISTS idx_chat_logs_room_id "
                "ON chat_logs (class_offering_id, id DESC)"
            )
            conn.execute("DROP INDEX IF EXISTS idx_chat_logs_legacy_dedupe")

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

            # 13.5 隐藏心理侧写快照
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

            # 13.8 课堂研讨室隐藏心理侧写快照
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

            # 13.9 绯荤粺淇℃伅涓績閫氱煡
            conn.execute('''
                         CREATE TABLE IF NOT EXISTS message_center_notifications
                         (
                             id INTEGER PRIMARY KEY AUTOINCREMENT,
                             recipient_identity TEXT NOT NULL,
                             recipient_role TEXT NOT NULL,
                             recipient_user_pk INTEGER NOT NULL,
                             category TEXT NOT NULL,
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
                             read_at TEXT,
                             created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                             FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE SET NULL
                         )
                         ''')

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

            # 13.12 绉佷俊瀹¤鏃ュ織锛堜笉璁板綍鍐呭锛?
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

            # 14. 试卷库
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

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_course_materials_teacher_parent ON course_materials (teacher_id, parent_id, name)"
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
                "CREATE INDEX IF NOT EXISTS idx_course_material_assignments_offering ON course_material_assignments (class_offering_id, material_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_teacher_git_credentials_lookup ON teacher_git_credentials (teacher_id, remote_host, updated_at DESC)"
            )

            conn.commit()
        print("[DB] V4.0 数据库架构初始化/验证完成。")

        # 初始化默认试卷（MID.html 期中测试）
        _init_default_exam_paper()
    except sqlite3.Error as e:
        print(f"[DB ERROR] 初始化 V4.0 数据库失败: {e}")
        sys.exit(1)


def _init_default_exam_paper():
    """初始化默认试卷：计算机网络期中测试 (来自 MID.html)"""
    default_exam_id = "mid-computer-network-2024"
    try:
        with get_db_connection() as conn:
            existing = conn.execute("SELECT id FROM exam_papers WHERE id = ?", (default_exam_id,)).fetchone()
            if existing:
                return  # 已存在，跳过

            # 获取第一个教师作为默认创建者
            teacher = conn.execute("SELECT id FROM teachers LIMIT 1").fetchone()
            if not teacher:
                print("[DB] 无教师账户，跳过默认试卷初始化。")
                return

            questions_json = json.dumps({
                "pages": [
                    {
                        "name": "第一关·宿舍的网络通了",
                        "questions": [
                            {"id": "q1", "type": "radio", "text": "1. 室友问：“100兆宽带怎么下载只有11.2MB/s？” 正确解释是？", "options": ["网线坏了", "100Mbps = 11.2MB/s左右，单位不同", "迅雷限速", "高峰期拥堵"]},
                            {"id": "q2", "type": "radio", "text": "2. 教务系统卡顿，ping延迟15ms，主要卡顿原因可能是？", "options": ["网线被咬断", "教务系统服务器处理时延大", "CPU不够好", "电磁波变慢"]},
                            {"id": "q3", "type": "radio", "text": "3. 网络分层最主要好处比喻正确的是？", "options": ["食堂打饭排队", "快递公司各司其职，换货车不影响寄件人", "高速车道越多越快", "对讲机轮流说"]},
                            {"id": "q4", "type": "radio", "text": "4. 手机开热点给电脑上网，手机在网络架构中的角色？", "options": ["只属于边缘部分", "只属于核心部分", "同时属于边缘和核心", "无线网络特殊"]},
                            {"id": "q5", "type": "radio", "text": "5. ping百度请求超时，以下说法正确的是？", "options": ["百度服务器宕机", "网线断了", "百度可能禁ping", "IP被拉黑"]},
                            {"id": "q6", "type": "radio", "text": "6. 发送时延取决于数据块长度和带宽。", "options": ["正确", "错误"]},
                            {"id": "q7", "type": "radio", "text": "7. 传播时延只受物理距离影响，与带宽无关。", "options": ["正确", "错误"]},
                            {"id": "q8", "type": "radio", "text": "8. 排队时延可能是四个时延中唯一可能为零的时延。", "options": ["正确", "错误"]},
                            {"id": "q9", "type": "radio", "text": "9. tracert某行全是* * * 表示那个路由器肯定坏了。", "options": ["正确", "错误"]},
                            {"id": "q10", "type": "radio", "text": "10. “透明传输”是指数据完全可见无加密。", "options": ["正确", "错误"]}
                        ]
                    },
                    {
                        "name": "第二关·信号里的秘密",
                        "questions": [
                            {"id": "q11_1", "type": "textarea", "text": "11.(1) KTV包厢噪声大，为了让对方听清，可以采取哪两种策略？分别对应香农公式中的哪个变量？", "placeholder": "例如：提高信号功率/降低速率..."},
                            {"id": "q11_2", "type": "textarea", "text": "11.(2) 噪声N趋近0时，信道容量会怎样变化？为什么不能无限大？", "placeholder": ""},
                            {"id": "q12_1", "type": "textarea", "text": "12.(1) 为什么大多数办公室仍用双绞线而非光纤？（写出2个理由）", "placeholder": ""},
                            {"id": "q12_2", "type": "text", "text": "12.(2) 食堂窗口轮流打饭5分钟，属于哪种复用技术？", "placeholder": ""},
                            {"id": "q13_1", "type": "textarea", "text": "13.(1) 手电筒狂闪1000次看到常亮，物理信道存在什么现象？", "placeholder": ""},
                            {"id": "q13_2", "type": "textarea", "text": "13.(2) 对应哪个著名定律？核心结论是什么？", "placeholder": ""}
                        ]
                    },
                    {
                        "name": "第三关·丢包的心跳",
                        "questions": [
                            {"id": "q14_1", "type": "textarea", "text": "14.(1) 数据包传输中，IP地址和MAC地址分别由谁负责“导航”和“送货”？", "placeholder": ""},
                            {"id": "q14_2", "type": "textarea", "text": "14.(2) 为什么需要同时存在IP和MAC地址？只用其中一个不行吗？", "placeholder": ""},
                            {"id": "q15_1", "type": "textarea", "text": "15.(1) 电脑发出什么请求获取MAC？该协议名称？", "placeholder": ""},
                            {"id": "q15_2", "type": "textarea", "text": "15.(2) 坏同学想偷听通信可以伪造什么攻击？叫什么？", "placeholder": ""},
                            {"id": "q15_3", "type": "textarea", "text": "15.(3) ARP缓存表为什么不永久保存？", "placeholder": ""},
                            {"id": "q16_1", "type": "textarea", "text": "16.(1) 以太网用什么协议解决“谁先说话”？用一句话描述核心规则。", "placeholder": ""},
                            {"id": "q16_2", "type": "textarea", "text": "16.(2) 两台电脑同时发送数据会发生什么？", "placeholder": ""},
                            {"id": "q16_3", "type": "textarea", "text": "16.(3) 为什么以太网规定最短帧长64字节？不遵守会怎样？", "placeholder": ""},
                            {"id": "q17_1", "type": "text", "text": "17.(1) 数据M=1011，生成多项式10011，计算FCS和最终完整比特流。", "placeholder": "例如：余数xxxx，最终帧："},
                            {"id": "q17_2", "type": "textarea", "text": "17.(2) 接收端收到10111110余数为0，说明什么？", "placeholder": ""}
                        ]
                    },
                    {
                        "name": "第四关·宿管大妈的账本",
                        "questions": [
                            {"id": "q18_1", "type": "text", "text": "18.(1) 网段192.168.10.64/26 的子网掩码是多少？", "placeholder": "例如255.255.255.192"},
                            {"id": "q18_2", "type": "text", "text": "18.(2) 该网段的广播地址？", "placeholder": ""},
                            {"id": "q18_3", "type": "text", "text": "18.(3) 可用IP范围？", "placeholder": ""},
                            {"id": "q18_4", "type": "text", "text": "18.(4) 最多能连多少台设备？", "placeholder": ""},
                            {"id": "q19_1", "type": "textarea", "text": "19.(1) 路由表匹配: 目的IP 10.1.1.5, 10.1.2.3, 8.8.8.8分别从哪个接口转发？", "placeholder": ""},
                            {"id": "q19_2", "type": "textarea", "text": "19.(2) 判断依据是什么原则？", "placeholder": ""},
                            {"id": "q19_3", "type": "textarea", "text": "19.(3) 0.0.0.0/0路由叫什么？作用？", "placeholder": ""},
                            {"id": "q20_1", "type": "textarea", "text": "20.(1) 离开电脑时，源IP:端口 目的IP:端口？", "placeholder": ""},
                            {"id": "q20_2", "type": "textarea", "text": "20.(2) NAT映射表新增记录？", "placeholder": ""},
                            {"id": "q20_3", "type": "textarea", "text": "20.(3) 服务器回复时目的IP端口？路由器如何找到内网主机？", "placeholder": ""}
                        ]
                    },
                    {
                        "name": "极客进阶·附加挑战",
                        "questions": [
                            {"id": "add1", "type": "textarea", "text": "附加题1：tracert第6跳超时但后续正常，路由器真的宕机了吗？为什么“沉默”？tracert如何利用TTL发现路由？", "placeholder": ""},
                            {"id": "add2", "type": "textarea", "text": "附加题2：RIP与OSPF选路场景：A-B高速，B-C低速，RIP如何选路？OSPF如何选？为什么大厂抛弃RIP？", "placeholder": ""}
                        ]
                    }
                ]
            }, ensure_ascii=False)

            conn.execute(
                "INSERT OR IGNORE INTO exam_papers (id, teacher_id, title, description, questions_json, status) VALUES (?, ?, ?, ?, ?, ?)",
                (default_exam_id, teacher['id'], "计算机网络·期中测试 — 连接时光的故事",
                 "基于MID.html的计算机网络期中测试试卷，包含网络分层、时延、香农定理、IP/MAC、ARP、以太网、CIDR、路由、NAT等知识点。",
                 questions_json, 'ready')
            )
            conn.commit()
            print("[DB] 默认试卷「计算机网络·期中测试」初始化完成。")
    except Exception as e:
        print(f"[DB WARN] 初始化默认试卷失败: {e}")
