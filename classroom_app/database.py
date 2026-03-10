import sqlite3
import sys
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
                             'draft',
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
                             stored_path
                             TEXT
                             NOT
                             NULL,
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

            conn.commit()
        print("[DB] V4.0 数据库架构初始化/验证完成。")
    except sqlite3.Error as e:
        print(f"[DB ERROR] 初始化 V4.0 数据库失败: {e}")
        sys.exit(1)
