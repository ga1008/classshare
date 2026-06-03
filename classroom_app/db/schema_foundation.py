import sqlite3

from ..services.organization_scope_service import DEFAULT_SCHOOL_CODE, DEFAULT_SCHOOL_NAME
from .migrations import _backfill_academic_departments, _seed_initial_super_admin
from .repair import _ensure_user_sessions_schema, repair_user_sessions_storage


def ensure_foundation_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS system_settings
        (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )

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
                     phone
                     TEXT,
                     wechat
                     TEXT,
                     qq
                     TEXT,
                     homepage_url
                     TEXT,
                     hashed_password
                     TEXT
                     NOT
                     NULL,
                     password_updated_at
                     TEXT,
                     profile_info
                     TEXT,
                     nickname TEXT,
                     description
                     TEXT,
                     avatar_file_hash
                     TEXT,
                     avatar_mime_type
                     TEXT,
                     avatar_updated_at
                     TEXT,
                     today_mood
                     TEXT,
                      today_mood_updated_at
                      TEXT,
                      is_super_admin
                      INTEGER
                      NOT
                      NULL
                      DEFAULT
                      0,
                      is_active
                      INTEGER
                      NOT
                      NULL
                      DEFAULT
                      1,
                      created_by_teacher_id
                      INTEGER,
                      school_code TEXT NOT NULL DEFAULT 'gxufl',
                      school_name TEXT NOT NULL DEFAULT '广西外国语学院',
                      college TEXT NOT NULL DEFAULT '',
                      department TEXT NOT NULL DEFAULT '',
                      updated_at
                      TEXT
                      DEFAULT
                      CURRENT_TIMESTAMP,
                      deactivated_at
                      TEXT,
                      deactivated_by_teacher_id
                      INTEGER,
                      created_at
                      TEXT
                      DEFAULT
                     CURRENT_TIMESTAMP
                 )
                 ''')

    for column_name, column_def in {
        "phone": "TEXT",
        "wechat": "TEXT",
        "qq": "TEXT",
        "homepage_url": "TEXT",
        "password_updated_at": "TEXT",
        "avatar_file_hash": "TEXT",
        "avatar_mime_type": "TEXT",
        "avatar_updated_at": "TEXT",
        "today_mood": "TEXT",
        "today_mood_updated_at": "TEXT",
        "is_super_admin": "INTEGER NOT NULL DEFAULT 0",
        "is_active": "INTEGER NOT NULL DEFAULT 1",
        "created_by_teacher_id": "INTEGER",
        "school_code": f"TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_CODE}'",
        "school_name": f"TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_NAME}'",
        "college": "TEXT NOT NULL DEFAULT ''",
        "department": "TEXT NOT NULL DEFAULT ''",
        "updated_at": "TEXT",
        "deactivated_at": "TEXT",
        "deactivated_by_teacher_id": "INTEGER",
    }.items():
        try:
            conn.execute(f"ALTER TABLE teachers ADD COLUMN {column_name} {column_def}")
        except sqlite3.OperationalError:
            pass

    conn.execute("UPDATE teachers SET is_active = 1 WHERE is_active IS NULL")
    conn.execute("UPDATE teachers SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teachers_super_admin "
        "ON teachers (is_super_admin, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teachers_active_super_admin "
        "ON teachers (is_active, is_super_admin, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teachers_active_email "
        "ON teachers (is_active, email COLLATE NOCASE)"
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS teacher_onboarding_state
        (
            teacher_id INTEGER PRIMARY KEY,
            dismissed_at TEXT,
            completed_at TEXT,
            dismiss_reason TEXT DEFAULT '',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE
        )
        '''
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_onboarding_updated "
        "ON teacher_onboarding_state (updated_at DESC)"
    )
    _seed_initial_super_admin(conn)

    _ensure_user_sessions_schema(conn)
    repair_user_sessions_storage(conn)

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
                     school_code TEXT NOT NULL DEFAULT 'gxufl',
                     school_name TEXT NOT NULL DEFAULT '广西外国语学院',
                     college TEXT NOT NULL DEFAULT '',
                     department TEXT DEFAULT '',
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
    try:
        conn.execute("ALTER TABLE classes ADD COLUMN description TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE classes ADD COLUMN department TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    for statement in (
        "ALTER TABLE classes ADD COLUMN academic_source TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE classes ADD COLUMN academic_class_code TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE classes ADD COLUMN academic_class_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE classes ADD COLUMN academic_college TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE classes ADD COLUMN academic_grade TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE classes ADD COLUMN academic_major TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE classes ADD COLUMN academic_sync_at TEXT",
        "ALTER TABLE classes ADD COLUMN academic_sync_message TEXT DEFAULT ''",
        "ALTER TABLE classes ADD COLUMN academic_metadata_json TEXT NOT NULL DEFAULT '{}'",
        f"ALTER TABLE classes ADD COLUMN school_code TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_CODE}'",
        f"ALTER TABLE classes ADD COLUMN school_name TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_NAME}'",
        "ALTER TABLE classes ADD COLUMN college TEXT NOT NULL DEFAULT ''",
    ):
        try:
            conn.execute(statement)
        except sqlite3.OperationalError:
            pass

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
                     wechat
                     TEXT,
                     qq
                     TEXT,
                     homepage_url
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
                     avatar_file_hash
                     TEXT,
                     avatar_mime_type
                     TEXT,
                     avatar_updated_at
                     TEXT,
                     today_mood
                     TEXT,
                      today_mood_updated_at
                      TEXT,
                      enrollment_status
                      TEXT
                      NOT
                      NULL
                      DEFAULT
                      'active',
                      enrollment_status_updated_at
                      TEXT,
                      enrollment_note
                      TEXT,
                      school_code TEXT NOT NULL DEFAULT 'gxufl',
                      school_name TEXT NOT NULL DEFAULT '广西外国语学院',
                      college TEXT NOT NULL DEFAULT '',
                      department TEXT NOT NULL DEFAULT '',
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
    for column_name, column_def in {
        "wechat": "TEXT",
        "qq": "TEXT",
        "homepage_url": "TEXT",
        "avatar_file_hash": "TEXT",
        "avatar_mime_type": "TEXT",
        "avatar_updated_at": "TEXT",
        "today_mood": "TEXT",
        "today_mood_updated_at": "TEXT",
        "enrollment_status": "TEXT NOT NULL DEFAULT 'active'",
        "enrollment_status_updated_at": "TEXT",
        "enrollment_note": "TEXT",
        "academic_source": "TEXT NOT NULL DEFAULT ''",
        "academic_student_id": "TEXT NOT NULL DEFAULT ''",
        "academic_class_code": "TEXT NOT NULL DEFAULT ''",
        "academic_class_name": "TEXT NOT NULL DEFAULT ''",
        "academic_college": "TEXT NOT NULL DEFAULT ''",
        "academic_grade": "TEXT NOT NULL DEFAULT ''",
        "academic_major": "TEXT NOT NULL DEFAULT ''",
        "academic_school_status": "TEXT NOT NULL DEFAULT ''",
        "academic_student_flags": "TEXT NOT NULL DEFAULT ''",
        "academic_sync_at": "TEXT",
        "academic_sync_message": "TEXT DEFAULT ''",
        "academic_metadata_json": "TEXT NOT NULL DEFAULT '{}'",
        "school_code": f"TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_CODE}'",
        "school_name": f"TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_NAME}'",
        "college": "TEXT NOT NULL DEFAULT ''",
        "department": "TEXT NOT NULL DEFAULT ''",
    }.items():
        try:
            conn.execute(f"ALTER TABLE students ADD COLUMN {column_name} {column_def}")
        except sqlite3.OperationalError:
            pass
    conn.execute(
        """
        UPDATE students
        SET enrollment_status = 'active'
        WHERE enrollment_status IS NULL OR TRIM(enrollment_status) = ''
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_students_class_status_lookup "
        "ON students (class_id, enrollment_status, student_id_number, id)"
    )

    conn.execute('''
                 CREATE TABLE IF NOT EXISTS student_shared_teacher_notes
                 (
                     student_id
                         INTEGER
                         PRIMARY KEY,
                     note_text
                         TEXT
                         NOT NULL
                         DEFAULT '',
                     created_by_teacher_id
                         INTEGER,
                     updated_by_teacher_id
                         INTEGER,
                     created_at
                         TEXT
                         DEFAULT CURRENT_TIMESTAMP,
                     updated_at
                         TEXT
                         DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
                     FOREIGN KEY (created_by_teacher_id) REFERENCES teachers (id) ON DELETE SET NULL,
                     FOREIGN KEY (updated_by_teacher_id) REFERENCES teachers (id) ON DELETE SET NULL
                 )
                 ''')
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_student_shared_teacher_notes_updated "
        "ON student_shared_teacher_notes (updated_at DESC, student_id)"
    )

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
                     sect_name
                          TEXT
                          DEFAULT '',
                     department
                           TEXT
                           DEFAULT '',
                     school_code TEXT NOT NULL DEFAULT 'gxufl',
                     school_name TEXT NOT NULL DEFAULT '广西外国语学院',
                     college TEXT NOT NULL DEFAULT '',
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
    try:
        conn.execute(
            "ALTER TABLE courses "
            "ADD COLUMN total_hours INTEGER NOT NULL DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "ALTER TABLE courses "
            "ADD COLUMN sect_name TEXT DEFAULT ''"
        )
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "ALTER TABLE courses "
            "ADD COLUMN department TEXT DEFAULT ''"
        )
    except sqlite3.OperationalError:
        pass
    for statement in (
        "ALTER TABLE courses ADD COLUMN academic_source TEXT DEFAULT ''",
        "ALTER TABLE courses ADD COLUMN academic_course_code TEXT DEFAULT ''",
        "ALTER TABLE courses ADD COLUMN academic_sync_at TEXT",
        "ALTER TABLE courses ADD COLUMN academic_sync_message TEXT DEFAULT ''",
        "ALTER TABLE courses ADD COLUMN academic_metadata_json TEXT NOT NULL DEFAULT '{}'",
        f"ALTER TABLE courses ADD COLUMN school_code TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_CODE}'",
        f"ALTER TABLE courses ADD COLUMN school_name TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_NAME}'",
        "ALTER TABLE courses ADD COLUMN college TEXT NOT NULL DEFAULT ''",
    ):
        try:
            conn.execute(statement)
        except sqlite3.OperationalError:
            pass

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS course_lessons
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            order_index INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT DEFAULT '',
            section_count INTEGER NOT NULL DEFAULT 1,
            source_type TEXT NOT NULL DEFAULT 'manual',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
            UNIQUE (course_id, order_index)
        )
        '''
    )
    try:
        conn.execute(
            "ALTER TABLE course_lessons "
            "ADD COLUMN learning_material_id INTEGER REFERENCES course_materials (id) ON DELETE SET NULL"
        )
    except sqlite3.OperationalError:
        pass

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS teacher_academic_course_sync_items
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            semester_id INTEGER,
            course_id INTEGER,
            academic_year TEXT NOT NULL DEFAULT '',
            academic_year_name TEXT NOT NULL DEFAULT '',
            academic_term TEXT NOT NULL DEFAULT '',
            academic_term_name TEXT NOT NULL DEFAULT '',
            teacher_name TEXT NOT NULL DEFAULT '',
            teacher_org_id TEXT NOT NULL DEFAULT '',
            teacher_org_name TEXT NOT NULL DEFAULT '',
            course_name TEXT NOT NULL DEFAULT '',
            course_code TEXT NOT NULL DEFAULT '',
            teaching_class_name TEXT NOT NULL DEFAULT '',
            time_text TEXT NOT NULL DEFAULT '',
            weeks_text TEXT NOT NULL DEFAULT '',
            weekday INTEGER,
            weekday_label TEXT NOT NULL DEFAULT '',
            section_text TEXT NOT NULL DEFAULT '',
            campus TEXT NOT NULL DEFAULT '',
            campus_id TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            classroom_id TEXT NOT NULL DEFAULT '',
            classroom_code TEXT NOT NULL DEFAULT '',
            classroom_type TEXT NOT NULL DEFAULT '',
            class_composition TEXT NOT NULL DEFAULT '',
            course_nature TEXT NOT NULL DEFAULT '',
            exam_method TEXT NOT NULL DEFAULT '',
            exam_mode TEXT NOT NULL DEFAULT '',
            course_hour_text TEXT NOT NULL DEFAULT '',
            weekly_hours_text TEXT NOT NULL DEFAULT '',
            total_hours_text TEXT NOT NULL DEFAULT '',
            course_total_hours_text TEXT NOT NULL DEFAULT '',
            major_direction TEXT NOT NULL DEFAULT '',
            course_note TEXT NOT NULL DEFAULT '',
            online_info TEXT NOT NULL DEFAULT '',
            course_topic_name TEXT NOT NULL DEFAULT '',
            block_level TEXT NOT NULL DEFAULT '',
            teaching_class_student_count INTEGER NOT NULL DEFAULT 0,
            credits REAL NOT NULL DEFAULT 0,
            student_count INTEGER NOT NULL DEFAULT 0,
            raw_text TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}',
            source_url TEXT NOT NULL DEFAULT '',
            synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
            FOREIGN KEY (semester_id) REFERENCES academic_semesters (id) ON DELETE SET NULL,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE SET NULL,
            UNIQUE (
                teacher_id,
                semester_id,
                course_code,
                teaching_class_name,
                weeks_text,
                weekday,
                section_text,
                location
            )
        )
        '''
    )
    for statement in (
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN academic_year TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN academic_year_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN academic_term TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN academic_term_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN teacher_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN teacher_org_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN teacher_org_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN time_text TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN campus_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN classroom_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN classroom_code TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN classroom_type TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN weekly_hours_text TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN total_hours_text TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN course_total_hours_text TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN major_direction TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN course_note TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN online_info TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN course_topic_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN block_level TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE teacher_academic_course_sync_items ADD COLUMN teaching_class_student_count INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            conn.execute(statement)
        except sqlite3.OperationalError:
            pass

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS teacher_academic_course_session_occurrences
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            semester_id INTEGER,
            course_id INTEGER,
            sync_item_id INTEGER,
            academic_year TEXT NOT NULL DEFAULT '',
            academic_term TEXT NOT NULL DEFAULT '',
            course_name TEXT NOT NULL DEFAULT '',
            course_code TEXT NOT NULL DEFAULT '',
            teaching_class_name TEXT NOT NULL DEFAULT '',
            class_composition TEXT NOT NULL DEFAULT '',
            session_date TEXT NOT NULL,
            week_index INTEGER NOT NULL DEFAULT 0,
            weekday INTEGER NOT NULL DEFAULT 0,
            weekday_label TEXT NOT NULL DEFAULT '',
            section_text TEXT NOT NULL DEFAULT '',
            section_start INTEGER NOT NULL DEFAULT 0,
            section_end INTEGER NOT NULL DEFAULT 0,
            section_count INTEGER NOT NULL DEFAULT 1,
            time_text TEXT NOT NULL DEFAULT '',
            weeks_text TEXT NOT NULL DEFAULT '',
            campus TEXT NOT NULL DEFAULT '',
            campus_id TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            classroom_id TEXT NOT NULL DEFAULT '',
            classroom_code TEXT NOT NULL DEFAULT '',
            classroom_type TEXT NOT NULL DEFAULT '',
            schedule_source TEXT NOT NULL DEFAULT 'academic_sync',
            schedule_status TEXT NOT NULL DEFAULT 'scheduled',
            is_non_periodic INTEGER NOT NULL DEFAULT 0,
            schedule_note TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}',
            synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
            FOREIGN KEY (semester_id) REFERENCES academic_semesters (id) ON DELETE CASCADE,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE SET NULL,
            FOREIGN KEY (sync_item_id) REFERENCES teacher_academic_course_sync_items (id) ON DELETE CASCADE,
            UNIQUE (
                teacher_id,
                semester_id,
                course_id,
                teaching_class_name,
                session_date,
                section_text,
                location
            )
        )
        '''
    )

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS teacher_academic_roster_sync_items
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            semester_id INTEGER,
            course_id INTEGER,
            class_id INTEGER,
            school_code TEXT NOT NULL DEFAULT 'gxufl',
            academic_year TEXT NOT NULL DEFAULT '',
            academic_year_name TEXT NOT NULL DEFAULT '',
            academic_term TEXT NOT NULL DEFAULT '',
            academic_term_name TEXT NOT NULL DEFAULT '',
            course_code TEXT NOT NULL DEFAULT '',
            course_name TEXT NOT NULL DEFAULT '',
            teaching_class_id TEXT NOT NULL DEFAULT '',
            teaching_class_name TEXT NOT NULL DEFAULT '',
            class_composition TEXT NOT NULL DEFAULT '',
            college TEXT NOT NULL DEFAULT '',
            teacher_name TEXT NOT NULL DEFAULT '',
            schedule_text TEXT NOT NULL DEFAULT '',
            location_text TEXT NOT NULL DEFAULT '',
            declared_student_count INTEGER NOT NULL DEFAULT 0,
            selected_student_count INTEGER NOT NULL DEFAULT 0,
            imported_student_count INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT NOT NULL DEFAULT '{}',
            source_url TEXT NOT NULL DEFAULT '',
            synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
            FOREIGN KEY (semester_id) REFERENCES academic_semesters (id) ON DELETE SET NULL,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE SET NULL,
            FOREIGN KEY (class_id) REFERENCES classes (id) ON DELETE SET NULL,
            UNIQUE (teacher_id, school_code, academic_year, academic_term, teaching_class_id)
        )
        '''
    )

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS teacher_academic_roster_memberships
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            semester_id INTEGER,
            sync_item_id INTEGER,
            class_id INTEGER,
            student_id INTEGER,
            school_code TEXT NOT NULL DEFAULT 'gxufl',
            academic_year TEXT NOT NULL DEFAULT '',
            academic_term TEXT NOT NULL DEFAULT '',
            course_code TEXT NOT NULL DEFAULT '',
            course_name TEXT NOT NULL DEFAULT '',
            teaching_class_id TEXT NOT NULL DEFAULT '',
            teaching_class_name TEXT NOT NULL DEFAULT '',
            admin_class_code TEXT NOT NULL DEFAULT '',
            admin_class_name TEXT NOT NULL DEFAULT '',
            student_number TEXT NOT NULL DEFAULT '',
            student_name TEXT NOT NULL DEFAULT '',
            school_status TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}',
            source_url TEXT NOT NULL DEFAULT '',
            synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
            FOREIGN KEY (semester_id) REFERENCES academic_semesters (id) ON DELETE SET NULL,
            FOREIGN KEY (sync_item_id) REFERENCES teacher_academic_roster_sync_items (id) ON DELETE CASCADE,
            FOREIGN KEY (class_id) REFERENCES classes (id) ON DELETE SET NULL,
            FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE SET NULL,
            UNIQUE (teacher_id, school_code, academic_year, academic_term, teaching_class_id, student_number)
        )
        '''
    )

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS teacher_academic_invigilation_items
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            semester_id INTEGER,
            school_code TEXT NOT NULL DEFAULT 'gxufl',
            academic_year TEXT NOT NULL DEFAULT '',
            academic_year_name TEXT NOT NULL DEFAULT '',
            academic_term TEXT NOT NULL DEFAULT '',
            academic_term_name TEXT NOT NULL DEFAULT '',
            exam_batch_id TEXT NOT NULL DEFAULT '',
            exam_name TEXT NOT NULL DEFAULT '',
            exam_paper_id TEXT NOT NULL DEFAULT '',
            exam_paper_code TEXT NOT NULL DEFAULT '',
            invigilation_key TEXT NOT NULL,
            invigilation_role TEXT NOT NULL DEFAULT '',
            invigilation_teachers TEXT NOT NULL DEFAULT '',
            course_code TEXT NOT NULL DEFAULT '',
            course_name TEXT NOT NULL DEFAULT '',
            course_display_name TEXT NOT NULL DEFAULT '',
            teaching_class_name TEXT NOT NULL DEFAULT '',
            class_composition TEXT NOT NULL DEFAULT '',
            student_college TEXT NOT NULL DEFAULT '',
            course_college TEXT NOT NULL DEFAULT '',
            campus TEXT NOT NULL DEFAULT '',
            building TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            location_short_name TEXT NOT NULL DEFAULT '',
            location_type TEXT NOT NULL DEFAULT '',
            location_type_id TEXT NOT NULL DEFAULT '',
            exam_student_count INTEGER NOT NULL DEFAULT 0,
            seat_count INTEGER NOT NULL DEFAULT 0,
            exam_time_text TEXT NOT NULL DEFAULT '',
            exam_date TEXT NOT NULL DEFAULT '',
            starts_at TEXT,
            ends_at TEXT,
            note TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}',
            source_url TEXT NOT NULL DEFAULT '',
            sync_status TEXT NOT NULL DEFAULT 'active',
            synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
            FOREIGN KEY (semester_id) REFERENCES academic_semesters (id) ON DELETE SET NULL,
            UNIQUE (teacher_id, school_code, academic_year, academic_term, invigilation_key)
        )
        '''
    )

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS teacher_academic_course_exam_items
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            semester_id INTEGER,
            class_offering_id INTEGER,
            course_id INTEGER,
            class_id INTEGER,
            school_code TEXT NOT NULL DEFAULT 'gxufl',
            academic_year TEXT NOT NULL DEFAULT '',
            academic_year_name TEXT NOT NULL DEFAULT '',
            academic_term TEXT NOT NULL DEFAULT '',
            academic_term_name TEXT NOT NULL DEFAULT '',
            exam_key TEXT NOT NULL,
            exam_batch_id TEXT NOT NULL DEFAULT '',
            exam_name TEXT NOT NULL DEFAULT '',
            exam_paper_id TEXT NOT NULL DEFAULT '',
            exam_paper_code TEXT NOT NULL DEFAULT '',
            course_code TEXT NOT NULL DEFAULT '',
            course_name TEXT NOT NULL DEFAULT '',
            course_display_name TEXT NOT NULL DEFAULT '',
            teaching_class_name TEXT NOT NULL DEFAULT '',
            class_composition TEXT NOT NULL DEFAULT '',
            teacher_name TEXT NOT NULL DEFAULT '',
            chief_invigilator TEXT NOT NULL DEFAULT '',
            assistant_invigilator TEXT NOT NULL DEFAULT '',
            course_college TEXT NOT NULL DEFAULT '',
            campus TEXT NOT NULL DEFAULT '',
            campus_id TEXT NOT NULL DEFAULT '',
            building TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            location_type TEXT NOT NULL DEFAULT '',
            location_type_id TEXT NOT NULL DEFAULT '',
            exam_student_count INTEGER NOT NULL DEFAULT 0,
            seat_count INTEGER NOT NULL DEFAULT 0,
            credits REAL,
            course_nature TEXT NOT NULL DEFAULT '',
            exam_time_text TEXT NOT NULL DEFAULT '',
            exam_date TEXT NOT NULL DEFAULT '',
            starts_at TEXT,
            ends_at TEXT,
            note TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}',
            source_url TEXT NOT NULL DEFAULT '',
            sync_status TEXT NOT NULL DEFAULT 'active',
            synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
            FOREIGN KEY (semester_id) REFERENCES academic_semesters (id) ON DELETE SET NULL,
            FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE SET NULL,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE SET NULL,
            FOREIGN KEY (class_id) REFERENCES classes (id) ON DELETE SET NULL,
            UNIQUE (teacher_id, school_code, academic_year, academic_term, exam_key)
        )
        '''
    )

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS teacher_academic_exam_roster_items
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            semester_id INTEGER,
            class_offering_id INTEGER,
            course_id INTEGER,
            class_id INTEGER,
            school_code TEXT NOT NULL DEFAULT 'gxufl',
            academic_year TEXT NOT NULL DEFAULT '',
            academic_year_name TEXT NOT NULL DEFAULT '',
            academic_term TEXT NOT NULL DEFAULT '',
            academic_term_name TEXT NOT NULL DEFAULT '',
            exam_course_key TEXT NOT NULL,
            course_code TEXT NOT NULL DEFAULT '',
            course_internal_id TEXT NOT NULL DEFAULT '',
            course_name TEXT NOT NULL DEFAULT '',
            teaching_class_id TEXT NOT NULL DEFAULT '',
            teaching_class_name TEXT NOT NULL DEFAULT '',
            class_composition TEXT NOT NULL DEFAULT '',
            teacher_name TEXT NOT NULL DEFAULT '',
            schedule_text TEXT NOT NULL DEFAULT '',
            exam_method TEXT NOT NULL DEFAULT '',
            grade_entry_status TEXT NOT NULL DEFAULT '',
            credits REAL,
            declared_student_count INTEGER NOT NULL DEFAULT 0,
            roster_student_count INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT NOT NULL DEFAULT '{}',
            source_url TEXT NOT NULL DEFAULT '',
            sync_status TEXT NOT NULL DEFAULT 'active',
            synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
            FOREIGN KEY (semester_id) REFERENCES academic_semesters (id) ON DELETE SET NULL,
            FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE SET NULL,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE SET NULL,
            FOREIGN KEY (class_id) REFERENCES classes (id) ON DELETE SET NULL,
            UNIQUE (teacher_id, school_code, academic_year, academic_term, exam_course_key)
        )
        '''
    )

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS teacher_academic_exam_roster_students
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            semester_id INTEGER,
            exam_roster_item_id INTEGER NOT NULL,
            class_offering_id INTEGER,
            class_id INTEGER,
            student_id INTEGER,
            school_code TEXT NOT NULL DEFAULT 'gxufl',
            academic_year TEXT NOT NULL DEFAULT '',
            academic_term TEXT NOT NULL DEFAULT '',
            exam_course_key TEXT NOT NULL DEFAULT '',
            student_number TEXT NOT NULL DEFAULT '',
            student_name TEXT NOT NULL DEFAULT '',
            gender TEXT NOT NULL DEFAULT '',
            admin_class_code TEXT NOT NULL DEFAULT '',
            admin_class_name TEXT NOT NULL DEFAULT '',
            college TEXT NOT NULL DEFAULT '',
            grade TEXT NOT NULL DEFAULT '',
            major TEXT NOT NULL DEFAULT '',
            school_status TEXT NOT NULL DEFAULT '',
            selection_type TEXT NOT NULL DEFAULT '',
            seat_no INTEGER NOT NULL DEFAULT 0,
            row_order INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT NOT NULL DEFAULT '{}',
            source_url TEXT NOT NULL DEFAULT '',
            synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
            FOREIGN KEY (semester_id) REFERENCES academic_semesters (id) ON DELETE SET NULL,
            FOREIGN KEY (exam_roster_item_id) REFERENCES teacher_academic_exam_roster_items (id) ON DELETE CASCADE,
            FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE SET NULL,
            FOREIGN KEY (class_id) REFERENCES classes (id) ON DELETE SET NULL,
            FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE SET NULL,
            UNIQUE (exam_roster_item_id, student_number)
        )
        '''
    )

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS teacher_academic_teaching_places
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            school_code TEXT NOT NULL DEFAULT 'gxufl',
            source TEXT NOT NULL DEFAULT 'gxufl_jwxt',
            place_key TEXT NOT NULL,
            place_id TEXT NOT NULL DEFAULT '',
            room_code TEXT NOT NULL DEFAULT '',
            room_name TEXT NOT NULL DEFAULT '',
            room_full_name TEXT NOT NULL DEFAULT '',
            campus_id TEXT NOT NULL DEFAULT '',
            campus_name TEXT NOT NULL DEFAULT '',
            building_id TEXT NOT NULL DEFAULT '',
            building_name TEXT NOT NULL DEFAULT '',
            floor_name TEXT NOT NULL DEFAULT '',
            room_type_id TEXT NOT NULL DEFAULT '',
            room_type_name TEXT NOT NULL DEFAULT '',
            room_subtype_id TEXT NOT NULL DEFAULT '',
            room_subtype_name TEXT NOT NULL DEFAULT '',
            organization_id TEXT NOT NULL DEFAULT '',
            organization_name TEXT NOT NULL DEFAULT '',
            manager_name TEXT NOT NULL DEFAULT '',
            usage_department TEXT NOT NULL DEFAULT '',
            usage_class TEXT NOT NULL DEFAULT '',
            borrow_type TEXT NOT NULL DEFAULT '',
            seat_count INTEGER NOT NULL DEFAULT 0,
            scheduling_seat_count INTEGER NOT NULL DEFAULT 0,
            exam_seat_count INTEGER NOT NULL DEFAULT 0,
            building_area TEXT NOT NULL DEFAULT '',
            is_schedulable INTEGER NOT NULL DEFAULT 0,
            is_borrowable INTEGER NOT NULL DEFAULT 0,
            is_exam_schedulable INTEGER NOT NULL DEFAULT 0,
            conflict_ignored INTEGER NOT NULL DEFAULT 0,
            status_text TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}',
            source_url TEXT NOT NULL DEFAULT '',
            sync_status TEXT NOT NULL DEFAULT 'active',
            sync_batch_id TEXT NOT NULL DEFAULT '',
            synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
            UNIQUE (teacher_id, school_code, source, place_key)
        )
        '''
    )
    conn.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_teacher_academic_teaching_places_teacher
        ON teacher_academic_teaching_places (teacher_id, school_code, sync_status)
        '''
    )
    conn.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_teacher_academic_teaching_places_lookup
        ON teacher_academic_teaching_places (
            teacher_id, campus_id, building_id, room_type_id, room_name
        )
        '''
    )

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS teacher_calendar_events
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            semester_id INTEGER,
            source_type TEXT NOT NULL,
            source_id INTEGER,
            source_key TEXT NOT NULL,
            title TEXT NOT NULL,
            subtitle TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            starts_at TEXT,
            ends_at TEXT,
            due_at TEXT,
            location TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            tone TEXT NOT NULL DEFAULT 'neutral',
            link_url TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            synced_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            deleted_at TEXT,
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
            FOREIGN KEY (semester_id) REFERENCES academic_semesters (id) ON DELETE SET NULL,
            UNIQUE (teacher_id, source_type, source_key)
        )
        '''
    )

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS academic_semesters
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            school_code TEXT NOT NULL DEFAULT 'gxufl',
            school_name TEXT NOT NULL DEFAULT '广西外国语学院',
            name TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            week_count INTEGER NOT NULL DEFAULT 1,
            calendar_sync_status TEXT NOT NULL DEFAULT 'pending',
            calendar_sync_at TEXT,
            calendar_sync_message TEXT DEFAULT '',
            calendar_source_summary_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
            UNIQUE (teacher_id, name)
        )
        '''
    )
    for statement in (
        "ALTER TABLE academic_semesters ADD COLUMN calendar_sync_status TEXT NOT NULL DEFAULT 'pending'",
        "ALTER TABLE academic_semesters ADD COLUMN calendar_sync_at TEXT",
        "ALTER TABLE academic_semesters ADD COLUMN calendar_sync_message TEXT DEFAULT ''",
        "ALTER TABLE academic_semesters ADD COLUMN calendar_source_summary_json TEXT NOT NULL DEFAULT '[]'",
        f"ALTER TABLE academic_semesters ADD COLUMN school_code TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_CODE}'",
        f"ALTER TABLE academic_semesters ADD COLUMN school_name TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_NAME}'",
    ):
        try:
            conn.execute(statement)
        except sqlite3.OperationalError:
            pass

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS academic_semester_calendar_days
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            semester_id INTEGER NOT NULL,
            teacher_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            week_index INTEGER NOT NULL DEFAULT 1,
            weekday INTEGER NOT NULL DEFAULT 0,
            day_type TEXT NOT NULL DEFAULT 'teaching_day',
            label TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'generated',
            source_url TEXT DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0.5,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (semester_id) REFERENCES academic_semesters (id) ON DELETE CASCADE,
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
            UNIQUE (semester_id, date)
        )
        '''
    )

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS textbooks
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            authors_json TEXT NOT NULL DEFAULT '[]',
            publisher TEXT DEFAULT '',
            publication_date TEXT,
            introduction TEXT DEFAULT '',
            catalog_text TEXT DEFAULT '',
            attachment_name TEXT DEFAULT '',
            attachment_path TEXT DEFAULT '',
            attachment_size INTEGER NOT NULL DEFAULT 0,
            attachment_mime_type TEXT DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE
        )
        '''
    )

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

    try:
        conn.execute(
            "ALTER TABLE class_offerings "
            "ADD COLUMN semester_id INTEGER REFERENCES academic_semesters (id) ON DELETE SET NULL"
        )
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "ALTER TABLE class_offerings "
            "ADD COLUMN textbook_id INTEGER REFERENCES textbooks (id) ON DELETE SET NULL"
        )
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "ALTER TABLE class_offerings "
            "ADD COLUMN first_class_date TEXT"
        )
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "ALTER TABLE class_offerings "
            "ADD COLUMN weekly_schedule_json TEXT NOT NULL DEFAULT '[]'"
        )
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "ALTER TABLE class_offerings "
            "ADD COLUMN home_learning_material_id INTEGER REFERENCES course_materials (id) ON DELETE SET NULL"
        )
    except sqlite3.OperationalError:
        pass
    for statement in (
        "ALTER TABLE class_offerings ADD COLUMN schedule_source TEXT NOT NULL DEFAULT 'fixed_cycle'",
        "ALTER TABLE class_offerings ADD COLUMN academic_teaching_class_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE class_offerings ADD COLUMN academic_schedule_sync_at TEXT",
        "ALTER TABLE class_offerings ADD COLUMN academic_schedule_sync_message TEXT DEFAULT ''",
    ):
        try:
            conn.execute(statement)
        except sqlite3.OperationalError:
            pass
    _backfill_academic_departments(conn)

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS class_offering_sessions
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_offering_id INTEGER NOT NULL,
            course_lesson_id INTEGER,
            order_index INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT DEFAULT '',
            section_count INTEGER NOT NULL DEFAULT 1,
            slot_section_count INTEGER NOT NULL DEFAULT 1,
            session_date TEXT NOT NULL,
            weekday INTEGER NOT NULL,
            week_index INTEGER NOT NULL DEFAULT 0,
            schedule_source TEXT NOT NULL DEFAULT 'fixed_cycle',
            academic_occurrence_id INTEGER,
            academic_sync_item_id INTEGER,
            academic_course_code TEXT NOT NULL DEFAULT '',
            academic_teaching_class_name TEXT NOT NULL DEFAULT '',
            academic_weeks_text TEXT NOT NULL DEFAULT '',
            academic_section_text TEXT NOT NULL DEFAULT '',
            academic_time_text TEXT NOT NULL DEFAULT '',
            academic_campus TEXT NOT NULL DEFAULT '',
            academic_location TEXT NOT NULL DEFAULT '',
            academic_classroom_id TEXT NOT NULL DEFAULT '',
            academic_classroom_code TEXT NOT NULL DEFAULT '',
            academic_classroom_type TEXT NOT NULL DEFAULT '',
            schedule_status TEXT NOT NULL DEFAULT 'scheduled',
            is_non_periodic INTEGER NOT NULL DEFAULT 0,
            schedule_note TEXT NOT NULL DEFAULT '',
            schedule_metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
            FOREIGN KEY (course_lesson_id) REFERENCES course_lessons (id) ON DELETE SET NULL,
            FOREIGN KEY (academic_occurrence_id) REFERENCES teacher_academic_course_session_occurrences (id) ON DELETE SET NULL,
            FOREIGN KEY (academic_sync_item_id) REFERENCES teacher_academic_course_sync_items (id) ON DELETE SET NULL,
            UNIQUE (class_offering_id, order_index)
        )
        '''
    )
    try:
        conn.execute(
            "ALTER TABLE class_offering_sessions "
            "ADD COLUMN learning_material_id INTEGER REFERENCES course_materials (id) ON DELETE SET NULL"
        )
    except sqlite3.OperationalError:
        pass
    for statement in (
        "ALTER TABLE class_offering_sessions ADD COLUMN schedule_source TEXT NOT NULL DEFAULT 'fixed_cycle'",
        "ALTER TABLE class_offering_sessions ADD COLUMN academic_occurrence_id INTEGER REFERENCES teacher_academic_course_session_occurrences (id) ON DELETE SET NULL",
        "ALTER TABLE class_offering_sessions ADD COLUMN academic_sync_item_id INTEGER REFERENCES teacher_academic_course_sync_items (id) ON DELETE SET NULL",
        "ALTER TABLE class_offering_sessions ADD COLUMN academic_course_code TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE class_offering_sessions ADD COLUMN academic_teaching_class_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE class_offering_sessions ADD COLUMN academic_weeks_text TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE class_offering_sessions ADD COLUMN academic_section_text TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE class_offering_sessions ADD COLUMN academic_time_text TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE class_offering_sessions ADD COLUMN academic_campus TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE class_offering_sessions ADD COLUMN academic_location TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE class_offering_sessions ADD COLUMN academic_classroom_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE class_offering_sessions ADD COLUMN academic_classroom_code TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE class_offering_sessions ADD COLUMN academic_classroom_type TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE class_offering_sessions ADD COLUMN schedule_status TEXT NOT NULL DEFAULT 'scheduled'",
        "ALTER TABLE class_offering_sessions ADD COLUMN is_non_periodic INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE class_offering_sessions ADD COLUMN schedule_note TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE class_offering_sessions ADD COLUMN schedule_metadata_json TEXT NOT NULL DEFAULT '{}'",
    ):
        try:
            conn.execute(statement)
        except sqlite3.OperationalError:
            pass

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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_students_name_lookup "
        "ON students (name, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_students_class_lookup "
        "ON students (class_id, student_id_number, id)"
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
