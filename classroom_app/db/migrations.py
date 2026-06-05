import sqlite3

from passlib.context import CryptContext

from ..config import (
    INITIAL_SUPER_ADMIN_EMAIL,
    INITIAL_SUPER_ADMIN_NAME,
    INITIAL_SUPER_ADMIN_PASSWORD,
)
from ..services.department_service import infer_department_from_text
from ..services.organization_scope_service import (
    DEFAULT_SCHOOL_CODE,
    DEFAULT_SCHOOL_NAME,
    build_org_scope,
    load_teacher_org_scope,
    normalize_org_text,
)


_teacher_seed_pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def _setting_exists(conn: sqlite3.Connection, key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM system_settings WHERE key = ? LIMIT 1",
        (key,),
    ).fetchone()
    return row is not None

def _upsert_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO system_settings (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (key, value),
    )

def _seed_initial_super_admin(conn: sqlite3.Connection) -> None:
    marker_key = "teacher_accounts.initial_super_admin_seeded"
    if _setting_exists(conn, marker_key):
        active_super_admin = conn.execute(
            """
            SELECT id
            FROM teachers
            WHERE COALESCE(is_active, 1) = 1
              AND COALESCE(is_super_admin, 0) = 1
            LIMIT 1
            """
        ).fetchone()
        if active_super_admin:
            return
    initial_email = str(INITIAL_SUPER_ADMIN_EMAIL or "").strip().lower()
    initial_teacher = None
    if initial_email:
        initial_teacher = conn.execute(
            """
            SELECT id
            FROM teachers
            WHERE lower(email) = ?
              AND COALESCE(is_active, 1) = 1
            ORDER BY id ASC
            LIMIT 1
            """,
            (initial_email,),
        ).fetchone()

    if initial_teacher is not None and not _setting_exists(conn, marker_key):
        conn.execute("UPDATE teachers SET is_super_admin = 0 WHERE COALESCE(is_active, 1) = 1")
        conn.execute(
            "UPDATE teachers SET email = ? WHERE id = ?",
            (initial_email, initial_teacher["id"]),
        )
        conn.execute(
            "UPDATE teachers SET is_super_admin = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (initial_teacher["id"],),
        )
        _upsert_setting(conn, marker_key, f"teacher:{int(initial_teacher['id'])}")
        return

    if initial_teacher is not None:
        conn.execute(
            "UPDATE teachers SET email = ?, is_super_admin = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (initial_email, initial_teacher["id"]),
        )
        _upsert_setting(conn, marker_key, f"recovered:{int(initial_teacher['id'])}")
        return

    fallback_teacher = conn.execute(
        """
        SELECT id
        FROM teachers
        WHERE COALESCE(is_active, 1) = 1
        ORDER BY id ASC
        LIMIT 1
        """
    ).fetchone()
    if fallback_teacher is not None:
        conn.execute(
            "UPDATE teachers SET is_super_admin = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (fallback_teacher["id"],),
        )
        _upsert_setting(conn, marker_key, f"fallback:{int(fallback_teacher['id'])}")
        return

    initial_password = str(INITIAL_SUPER_ADMIN_PASSWORD or "").strip()
    if not initial_email or not initial_password:
        return
    if len(initial_password) < 8:
        print("[DB] INITIAL_SUPER_ADMIN_PASSWORD must be at least 8 characters; initial super-admin was not created.")
        return

    cursor = conn.execute(
        """
        INSERT INTO teachers (
            name, email, hashed_password, password_updated_at,
            is_super_admin, is_active, updated_at
        )
        VALUES (?, ?, ?, CURRENT_TIMESTAMP, 1, 1, CURRENT_TIMESTAMP)
        """,
        (
            str(INITIAL_SUPER_ADMIN_NAME or "系统超管").strip() or "系统超管",
            initial_email,
            _teacher_seed_pwd_context.hash(initial_password),
        ),
    )
    _upsert_setting(conn, marker_key, f"created:{int(cursor.lastrowid)}")

def _backfill_academic_departments(conn: sqlite3.Connection) -> None:
    class_rows = conn.execute(
        """
        SELECT id, name, description
        FROM classes
        WHERE TRIM(COALESCE(department, '')) = ''
        """
    ).fetchall()
    for row in class_rows:
        department = infer_department_from_text(row["name"], row["description"])
        if department:
            conn.execute(
                "UPDATE classes SET department = ? WHERE id = ?",
                (department, row["id"]),
            )

    course_rows = conn.execute(
        """
        SELECT id, name, description
        FROM courses
        WHERE TRIM(COALESCE(department, '')) = ''
        """
    ).fetchall()
    for row in course_rows:
        department = infer_department_from_text(row["name"], row["description"])
        if department:
            conn.execute(
                "UPDATE courses SET department = ? WHERE id = ?",
                (department, row["id"]),
            )

    linked_rows = conn.execute(
        """
        SELECT co.course_id, c.department, COUNT(*) AS usage_count
        FROM class_offerings co
        JOIN classes c ON c.id = co.class_id
        JOIN courses course ON course.id = co.course_id
        WHERE TRIM(COALESCE(course.department, '')) = ''
          AND TRIM(COALESCE(c.department, '')) != ''
        GROUP BY co.course_id, c.department
        ORDER BY co.course_id, usage_count DESC
        """
    ).fetchall()
    assigned_course_ids: set[int] = set()
    for row in linked_rows:
        course_id = int(row["course_id"])
        if course_id in assigned_course_ids:
            continue
        assigned_course_ids.add(course_id)
        conn.execute(
            "UPDATE courses SET department = ? WHERE id = ?",
            (row["department"], course_id),
        )

def _backfill_organization_scopes(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE teachers
        SET school_code = CASE
                WHEN TRIM(COALESCE(school_code, '')) = '' THEN ?
                ELSE lower(TRIM(school_code))
            END,
            school_name = CASE
                WHEN TRIM(COALESCE(school_name, '')) = '' THEN ?
                ELSE TRIM(school_name)
            END
        """,
        (DEFAULT_SCHOOL_CODE, DEFAULT_SCHOOL_NAME),
    )

def _ensure_organization_catalog_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS organization_schools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_code TEXT NOT NULL UNIQUE,
            school_name TEXT NOT NULL,
            display_order INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL DEFAULT 'manual',
            created_by_teacher_id INTEGER,
            updated_by_teacher_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            deactivated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS organization_colleges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_code TEXT NOT NULL,
            college_name TEXT NOT NULL COLLATE NOCASE,
            display_order INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL DEFAULT 'manual',
            created_by_teacher_id INTEGER,
            updated_by_teacher_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            deactivated_at TEXT,
            UNIQUE (school_code, college_name)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS organization_departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_code TEXT NOT NULL,
            college_name TEXT NOT NULL DEFAULT '' COLLATE NOCASE,
            department_name TEXT NOT NULL COLLATE NOCASE,
            display_order INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL DEFAULT 'manual',
            created_by_teacher_id INTEGER,
            updated_by_teacher_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            deactivated_at TEXT,
            UNIQUE (school_code, college_name, department_name)
        )
        """
    )
    for table_name in ("organization_schools", "organization_colleges", "organization_departments"):
        for column_name, column_def in {
            "display_order": "INTEGER NOT NULL DEFAULT 0",
            "is_active": "INTEGER NOT NULL DEFAULT 1",
            "source": "TEXT NOT NULL DEFAULT 'manual'",
            "created_by_teacher_id": "INTEGER",
            "updated_by_teacher_id": "INTEGER",
            "updated_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
            "deactivated_at": "TEXT",
        }.items():
            try:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
            except sqlite3.OperationalError:
                pass
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_organization_schools_active "
        "ON organization_schools (is_active, display_order, school_name COLLATE NOCASE)",
        "CREATE INDEX IF NOT EXISTS idx_organization_colleges_school "
        "ON organization_colleges (school_code, is_active, display_order, college_name COLLATE NOCASE)",
        "CREATE INDEX IF NOT EXISTS idx_organization_departments_college "
        "ON organization_departments (school_code, college_name COLLATE NOCASE, is_active, display_order, department_name COLLATE NOCASE)",
    ):
        conn.execute(statement)

def _ensure_teacher_organization_memberships_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS teacher_organization_memberships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            school_code TEXT NOT NULL,
            school_name TEXT NOT NULL,
            college TEXT NOT NULL DEFAULT '',
            department TEXT NOT NULL DEFAULT '',
            is_primary INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL DEFAULT 'manual',
            created_by_teacher_id INTEGER,
            updated_by_teacher_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            deactivated_at TEXT,
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
            UNIQUE (teacher_id, school_code)
        )
        """
    )
    for column_name, column_def in {
        "school_name": f"TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_NAME}'",
        "college": "TEXT NOT NULL DEFAULT ''",
        "department": "TEXT NOT NULL DEFAULT ''",
        "is_primary": "INTEGER NOT NULL DEFAULT 0",
        "is_active": "INTEGER NOT NULL DEFAULT 1",
        "source": "TEXT NOT NULL DEFAULT 'manual'",
        "created_by_teacher_id": "INTEGER",
        "updated_by_teacher_id": "INTEGER",
        "updated_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
        "deactivated_at": "TEXT",
    }.items():
        try:
            conn.execute(f"ALTER TABLE teacher_organization_memberships ADD COLUMN {column_name} {column_def}")
        except sqlite3.OperationalError:
            pass

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_teacher_org_memberships_one_school "
        "ON teacher_organization_memberships (teacher_id, school_code)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_org_memberships_teacher "
        "ON teacher_organization_memberships (teacher_id, is_active, is_primary DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_org_memberships_org "
        "ON teacher_organization_memberships (school_code COLLATE NOCASE, department COLLATE NOCASE, is_active)"
    )

    rows = conn.execute(
        """
        SELECT id, school_code, school_name, college, department, created_by_teacher_id
        FROM teachers
        """
    ).fetchall()
    for row in rows:
        scope = build_org_scope(
            school_code=row["school_code"],
            school_name=row["school_name"],
            college=row["college"],
            department=row["department"],
        )
        conn.execute(
            """
            INSERT INTO teacher_organization_memberships (
                teacher_id, school_code, school_name, college, department,
                is_primary, is_active, source, created_by_teacher_id, updated_by_teacher_id, updated_at, deactivated_at
            )
            VALUES (?, ?, ?, ?, ?, 1, 1, 'legacy_primary', ?, ?, CURRENT_TIMESTAMP, NULL)
            ON CONFLICT(teacher_id, school_code) DO UPDATE SET
                school_name = CASE
                    WHEN teacher_organization_memberships.source = 'legacy_primary' THEN excluded.school_name
                    ELSE teacher_organization_memberships.school_name
                END,
                college = CASE
                    WHEN teacher_organization_memberships.source = 'legacy_primary' THEN excluded.college
                    ELSE teacher_organization_memberships.college
                END,
                department = CASE
                    WHEN teacher_organization_memberships.source = 'legacy_primary' THEN excluded.department
                    ELSE teacher_organization_memberships.department
                END,
                is_primary = CASE
                    WHEN teacher_organization_memberships.is_primary = 1 THEN 1
                    ELSE excluded.is_primary
                END,
                is_active = 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(row["id"]),
                scope["school_code"],
                scope["school_name"],
                scope["college"],
                scope["department"],
                row["created_by_teacher_id"],
                row["created_by_teacher_id"],
            ),
        )

    conn.execute(
        """
        UPDATE teacher_organization_memberships
        SET is_primary = 1
        WHERE id IN (
            SELECT MIN(id)
            FROM teacher_organization_memberships
            WHERE COALESCE(is_active, 1) = 1
            GROUP BY teacher_id
        )
          AND teacher_id NOT IN (
            SELECT teacher_id
            FROM teacher_organization_memberships
            WHERE COALESCE(is_active, 1) = 1 AND COALESCE(is_primary, 0) = 1
        )
        """
    )

def _source_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None

def _add_column_if_missing(conn: sqlite3.Connection, table_name: str, column_name: str, column_def: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
    except sqlite3.OperationalError:
        pass

def _ensure_resource_scope_schema(conn: sqlite3.Connection) -> None:
    if _source_table_exists(conn, "chunked_uploads"):
        _add_column_if_missing(conn, "chunked_uploads", "class_offering_id", "INTEGER")

    if _source_table_exists(conn, "classes"):
        for column_name, column_def in {
            "major": "TEXT NOT NULL DEFAULT ''",
            "enrollment_year": "INTEGER",
            "expected_graduation_year": "INTEGER",
            "program_duration_years": "INTEGER",
            "owner_role": "TEXT NOT NULL DEFAULT 'teacher'",
            "owner_user_pk": "INTEGER",
            "scope_level": "TEXT NOT NULL DEFAULT 'school'",
            "updated_at": "TEXT",
            "archived_at": "TEXT",
            "deleted_at": "TEXT",
        }.items():
            _add_column_if_missing(conn, "classes", column_name, column_def)
        conn.execute(
            """
            UPDATE classes
            SET owner_role = COALESCE(NULLIF(TRIM(owner_role), ''), 'teacher'),
                owner_user_pk = COALESCE(owner_user_pk, created_by_teacher_id),
                scope_level = COALESCE(NULLIF(TRIM(scope_level), ''), 'school'),
                updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP),
                major = COALESCE(NULLIF(TRIM(major), ''), academic_major, '')
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_classes_scope_owner "
            "ON classes (owner_role, owner_user_pk, scope_level, updated_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_classes_scope_org "
            "ON classes (scope_level, school_code COLLATE NOCASE, department COLLATE NOCASE, name COLLATE NOCASE)"
        )

    if _source_table_exists(conn, "courses"):
        for column_name, column_def in {
            "owner_role": "TEXT NOT NULL DEFAULT 'teacher'",
            "owner_user_pk": "INTEGER",
            "scope_level": "TEXT NOT NULL DEFAULT 'school'",
            "updated_at": "TEXT",
            "archived_at": "TEXT",
            "deleted_at": "TEXT",
        }.items():
            _add_column_if_missing(conn, "courses", column_name, column_def)
        conn.execute(
            """
            UPDATE courses
            SET owner_role = COALESCE(NULLIF(TRIM(owner_role), ''), 'teacher'),
                owner_user_pk = COALESCE(owner_user_pk, created_by_teacher_id),
                scope_level = COALESCE(NULLIF(TRIM(scope_level), ''), 'school'),
                updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_courses_scope_owner "
            "ON courses (owner_role, owner_user_pk, scope_level, updated_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_courses_scope_org "
            "ON courses (scope_level, school_code COLLATE NOCASE, department COLLATE NOCASE, name COLLATE NOCASE)"
        )

    if _source_table_exists(conn, "textbooks"):
        for column_name, column_def in {
            "owner_role": "TEXT NOT NULL DEFAULT 'teacher'",
            "owner_user_pk": "INTEGER",
            "scope_level": "TEXT NOT NULL DEFAULT 'private'",
            "school_code": f"TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_CODE}'",
            "school_name": f"TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_NAME}'",
            "college": "TEXT NOT NULL DEFAULT ''",
            "department": "TEXT NOT NULL DEFAULT ''",
            "published_at": "TEXT",
            "archived_at": "TEXT",
            "deleted_at": "TEXT",
        }.items():
            _add_column_if_missing(conn, "textbooks", column_name, column_def)
        conn.execute(
            """
            UPDATE textbooks
            SET owner_role = COALESCE(NULLIF(TRIM(owner_role), ''), 'teacher'),
                owner_user_pk = COALESCE(owner_user_pk, teacher_id),
                school_code = COALESCE(NULLIF(TRIM(school_code), ''), (
                    SELECT NULLIF(TRIM(t.school_code), '') FROM teachers t WHERE t.id = textbooks.teacher_id
                ), ?),
                school_name = COALESCE(NULLIF(TRIM(school_name), ''), (
                    SELECT NULLIF(TRIM(t.school_name), '') FROM teachers t WHERE t.id = textbooks.teacher_id
                ), ?),
                college = COALESCE(NULLIF(TRIM(college), ''), (
                    SELECT NULLIF(TRIM(t.college), '') FROM teachers t WHERE t.id = textbooks.teacher_id
                ), ''),
                department = COALESCE(NULLIF(TRIM(department), ''), (
                    SELECT NULLIF(TRIM(t.department), '') FROM teachers t WHERE t.id = textbooks.teacher_id
                ), ''),
                scope_level = COALESCE(NULLIF(TRIM(scope_level), ''), 'private')
            """,
            (DEFAULT_SCHOOL_CODE, DEFAULT_SCHOOL_NAME),
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_textbooks_scope_owner "
            "ON textbooks (owner_role, owner_user_pk, scope_level, updated_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_textbooks_scope_org "
            "ON textbooks (scope_level, school_code COLLATE NOCASE, department COLLATE NOCASE, updated_at DESC)"
        )

    if _source_table_exists(conn, "course_files"):
        for column_name, column_def in {
            "owner_role": "TEXT NOT NULL DEFAULT 'teacher'",
            "owner_user_pk": "INTEGER",
            "scope_level": "TEXT NOT NULL DEFAULT 'department'",
            "class_offering_id": "INTEGER",
            "class_id": "INTEGER",
            "school_code": f"TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_CODE}'",
            "school_name": f"TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_NAME}'",
            "college": "TEXT NOT NULL DEFAULT ''",
            "department": "TEXT NOT NULL DEFAULT ''",
            "published_at": "TEXT",
            "updated_at": "TEXT",
        }.items():
            _add_column_if_missing(conn, "course_files", column_name, column_def)

        conn.execute(
            """
            UPDATE course_files
            SET owner_role = COALESCE(NULLIF(TRIM(owner_role), ''), 'teacher'),
                owner_user_pk = COALESCE(owner_user_pk, uploaded_by_teacher_id, (
                    SELECT created_by_teacher_id FROM courses c WHERE c.id = course_files.course_id
                )),
                school_code = COALESCE(NULLIF(TRIM(school_code), ''), (
                    SELECT NULLIF(TRIM(c.school_code), '') FROM courses c WHERE c.id = course_files.course_id
                ), ?),
                school_name = COALESCE(NULLIF(TRIM(school_name), ''), (
                    SELECT NULLIF(TRIM(c.school_name), '') FROM courses c WHERE c.id = course_files.course_id
                ), ?),
                college = COALESCE(NULLIF(TRIM(college), ''), (
                    SELECT NULLIF(TRIM(c.college), '') FROM courses c WHERE c.id = course_files.course_id
                ), ''),
                department = COALESCE(NULLIF(TRIM(department), ''), (
                    SELECT NULLIF(TRIM(c.department), '') FROM courses c WHERE c.id = course_files.course_id
                ), ''),
                scope_level = CASE
                    WHEN COALESCE(is_public, 1) = 0 OR COALESCE(is_teacher_resource, 0) = 1 THEN 'private'
                    WHEN TRIM(COALESCE(scope_level, '')) = '' THEN 'department'
                    ELSE scope_level
                END,
                published_at = CASE
                    WHEN published_at IS NULL AND COALESCE(is_public, 1) = 1 THEN uploaded_at
                    ELSE published_at
                END,
                updated_at = COALESCE(updated_at, uploaded_at, CURRENT_TIMESTAMP)
            """,
            (DEFAULT_SCHOOL_CODE, DEFAULT_SCHOOL_NAME),
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_course_files_owner_scope "
            "ON course_files (owner_role, owner_user_pk, scope_level, uploaded_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_course_files_scope_org "
            "ON course_files (scope_level, school_code COLLATE NOCASE, department COLLATE NOCASE, class_offering_id)",
            "CREATE INDEX IF NOT EXISTS idx_course_files_course_scope "
            "ON course_files (course_id, scope_level, is_public, is_teacher_resource, uploaded_at DESC)",
        ):
            conn.execute(statement)

    if _source_table_exists(conn, "course_materials"):
        for column_name, column_def in {
            "owner_role": "TEXT NOT NULL DEFAULT 'teacher'",
            "owner_user_pk": "INTEGER",
            "scope_level": "TEXT NOT NULL DEFAULT 'private'",
            "school_code": f"TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_CODE}'",
            "school_name": f"TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_NAME}'",
            "college": "TEXT NOT NULL DEFAULT ''",
            "department": "TEXT NOT NULL DEFAULT ''",
            "published_at": "TEXT",
            "archived_at": "TEXT",
            "deleted_at": "TEXT",
        }.items():
            _add_column_if_missing(conn, "course_materials", column_name, column_def)
        conn.execute(
            """
            UPDATE course_materials
            SET owner_role = COALESCE(NULLIF(TRIM(owner_role), ''), 'teacher'),
                owner_user_pk = COALESCE(owner_user_pk, teacher_id),
                school_code = COALESCE(NULLIF(TRIM(school_code), ''), (
                    SELECT NULLIF(TRIM(t.school_code), '') FROM teachers t WHERE t.id = course_materials.teacher_id
                ), ?),
                school_name = COALESCE(NULLIF(TRIM(school_name), ''), (
                    SELECT NULLIF(TRIM(t.school_name), '') FROM teachers t WHERE t.id = course_materials.teacher_id
                ), ?),
                college = COALESCE(NULLIF(TRIM(college), ''), (
                    SELECT NULLIF(TRIM(t.college), '') FROM teachers t WHERE t.id = course_materials.teacher_id
                ), ''),
                department = COALESCE(NULLIF(TRIM(department), ''), (
                    SELECT NULLIF(TRIM(t.department), '') FROM teachers t WHERE t.id = course_materials.teacher_id
                ), ''),
                scope_level = COALESCE(NULLIF(TRIM(scope_level), ''), 'private')
            """,
            (DEFAULT_SCHOOL_CODE, DEFAULT_SCHOOL_NAME),
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_course_materials_scope_owner "
            "ON course_materials (owner_role, owner_user_pk, scope_level, updated_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_course_materials_scope_org "
            "ON course_materials (scope_level, school_code COLLATE NOCASE, department COLLATE NOCASE)"
        )

    if _source_table_exists(conn, "exam_papers"):
        for column_name, column_def in {
            "owner_role": "TEXT NOT NULL DEFAULT 'teacher'",
            "owner_user_pk": "INTEGER",
            "scope_level": "TEXT NOT NULL DEFAULT 'department'",
            "school_code": f"TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_CODE}'",
            "school_name": f"TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_NAME}'",
            "college": "TEXT NOT NULL DEFAULT ''",
            "department": "TEXT NOT NULL DEFAULT ''",
            "published_at": "TEXT",
            "archived_at": "TEXT",
            "deleted_at": "TEXT",
        }.items():
            _add_column_if_missing(conn, "exam_papers", column_name, column_def)
        conn.execute(
            """
            UPDATE exam_papers
            SET owner_role = COALESCE(NULLIF(TRIM(owner_role), ''), 'teacher'),
                owner_user_pk = COALESCE(owner_user_pk, teacher_id),
                school_code = COALESCE(NULLIF(TRIM(school_code), ''), (
                    SELECT NULLIF(TRIM(t.school_code), '') FROM teachers t WHERE t.id = exam_papers.teacher_id
                ), ?),
                school_name = COALESCE(NULLIF(TRIM(school_name), ''), (
                    SELECT NULLIF(TRIM(t.school_name), '') FROM teachers t WHERE t.id = exam_papers.teacher_id
                ), ?),
                college = COALESCE(NULLIF(TRIM(college), ''), (
                    SELECT NULLIF(TRIM(t.college), '') FROM teachers t WHERE t.id = exam_papers.teacher_id
                ), ''),
                department = COALESCE(NULLIF(TRIM(department), ''), (
                    SELECT NULLIF(TRIM(t.department), '') FROM teachers t WHERE t.id = exam_papers.teacher_id
                ), ''),
                scope_level = COALESCE(NULLIF(TRIM(scope_level), ''), 'department')
            """,
            (DEFAULT_SCHOOL_CODE, DEFAULT_SCHOOL_NAME),
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exam_papers_scope_owner "
            "ON exam_papers (owner_role, owner_user_pk, scope_level, updated_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exam_papers_scope_org "
            "ON exam_papers (scope_level, school_code COLLATE NOCASE, department COLLATE NOCASE, updated_at DESC)"
        )

    if _source_table_exists(conn, "blog_posts"):
        for column_name, column_def in {
            "scope_level": "TEXT NOT NULL DEFAULT 'public'",
            "school_code": f"TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_CODE}'",
            "school_name": f"TEXT NOT NULL DEFAULT '{DEFAULT_SCHOOL_NAME}'",
            "college": "TEXT NOT NULL DEFAULT ''",
            "department": "TEXT NOT NULL DEFAULT ''",
        }.items():
            _add_column_if_missing(conn, "blog_posts", column_name, column_def)
        conn.execute(
            """
            UPDATE blog_posts
            SET scope_level = CASE
                    WHEN visibility = 'class' OR visible_class_id IS NOT NULL THEN 'class'
                    WHEN visibility = 'private' THEN 'private'
                    WHEN TRIM(COALESCE(scope_level, '')) = '' THEN 'public'
                    ELSE scope_level
                END,
                school_code = COALESCE(NULLIF(TRIM(school_code), ''), (
                    SELECT NULLIF(TRIM(t.school_code), '') FROM teachers t
                    WHERE blog_posts.author_role = 'teacher' AND t.id = blog_posts.author_user_pk
                ), (
                    SELECT NULLIF(TRIM(s.school_code), '') FROM students s
                    WHERE blog_posts.author_role = 'student' AND s.id = blog_posts.author_user_pk
                ), ?),
                school_name = COALESCE(NULLIF(TRIM(school_name), ''), (
                    SELECT NULLIF(TRIM(t.school_name), '') FROM teachers t
                    WHERE blog_posts.author_role = 'teacher' AND t.id = blog_posts.author_user_pk
                ), (
                    SELECT NULLIF(TRIM(s.school_name), '') FROM students s
                    WHERE blog_posts.author_role = 'student' AND s.id = blog_posts.author_user_pk
                ), ?),
                college = COALESCE(NULLIF(TRIM(college), ''), (
                    SELECT NULLIF(TRIM(t.college), '') FROM teachers t
                    WHERE blog_posts.author_role = 'teacher' AND t.id = blog_posts.author_user_pk
                ), (
                    SELECT NULLIF(TRIM(s.college), '') FROM students s
                    WHERE blog_posts.author_role = 'student' AND s.id = blog_posts.author_user_pk
                ), ''),
                department = COALESCE(NULLIF(TRIM(department), ''), (
                    SELECT NULLIF(TRIM(t.department), '') FROM teachers t
                    WHERE blog_posts.author_role = 'teacher' AND t.id = blog_posts.author_user_pk
                ), (
                    SELECT NULLIF(TRIM(s.department), '') FROM students s
                    WHERE blog_posts.author_role = 'student' AND s.id = blog_posts.author_user_pk
                ), '')
            """,
            (DEFAULT_SCHOOL_CODE, DEFAULT_SCHOOL_NAME),
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_blog_posts_scope_org "
            "ON blog_posts (scope_level, school_code COLLATE NOCASE, department COLLATE NOCASE, visible_class_id)"
        )

def _sync_organization_catalog_from_existing(conn: sqlite3.Connection) -> None:
    _ensure_organization_catalog_schema(conn)
    source_tables = (
        ("teachers", "school_code", "school_name", "college", "department"),
        ("students", "school_code", "school_name", "college", "department"),
        ("classes", "school_code", "school_name", "college", "department"),
        ("courses", "school_code", "school_name", "college", "department"),
        ("academic_semesters", "school_code", "school_name", "", ""),
        ("electronic_signatures", "school_code", "school_name", "college", "department"),
    )

    schools: dict[str, str] = {DEFAULT_SCHOOL_CODE: DEFAULT_SCHOOL_NAME}
    colleges: set[tuple[str, str]] = set()
    departments: set[tuple[str, str, str]] = set()
    for table_name, school_col, school_name_col, college_col, department_col in source_tables:
        if not _source_table_exists(conn, table_name):
            continue
        selected_columns = [school_col, school_name_col]
        if college_col:
            selected_columns.append(college_col)
        if department_col:
            selected_columns.append(department_col)
        rows = conn.execute(
            f"SELECT DISTINCT {', '.join(selected_columns)} FROM {table_name}"
        ).fetchall()
        for row in rows:
            scope = build_org_scope(
                school_code=row[school_col],
                school_name=row[school_name_col],
                college=row[college_col] if college_col else "",
                department=row[department_col] if department_col else "",
            )
            schools.setdefault(scope["school_code"], scope["school_name"])
            if scope["school_name"] and schools.get(scope["school_code"]) == DEFAULT_SCHOOL_NAME:
                schools[scope["school_code"]] = scope["school_name"]
            if scope["college"]:
                colleges.add((scope["school_code"], scope["college"]))
            if scope["department"]:
                departments.add((scope["school_code"], scope["college"], scope["department"]))

    for school_code, school_name in sorted(schools.items()):
        conn.execute(
            """
            INSERT INTO organization_schools (school_code, school_name, source)
            VALUES (?, ?, 'backfill')
            ON CONFLICT(school_code) DO UPDATE SET
                school_name = CASE
                    WHEN TRIM(COALESCE(organization_schools.school_name, '')) = '' THEN excluded.school_name
                    WHEN organization_schools.source = 'backfill' THEN excluded.school_name
                    ELSE organization_schools.school_name
                END,
                updated_at = CURRENT_TIMESTAMP
            """,
            (school_code, school_name or DEFAULT_SCHOOL_NAME),
        )

    for school_code, college in sorted(colleges):
        clean_college = normalize_org_text(college)
        if not clean_college:
            continue
        conn.execute(
            """
            INSERT INTO organization_colleges (school_code, college_name, source)
            VALUES (?, ?, 'backfill')
            ON CONFLICT(school_code, college_name) DO UPDATE SET
                updated_at = CURRENT_TIMESTAMP
            """,
            (school_code, clean_college),
        )

    for school_code, college, department in sorted(departments):
        clean_department = normalize_org_text(department)
        if not clean_department:
            continue
        clean_college = normalize_org_text(college)
        if clean_college:
            conn.execute(
                """
                INSERT INTO organization_colleges (school_code, college_name, source)
                VALUES (?, ?, 'backfill')
                ON CONFLICT(school_code, college_name) DO UPDATE SET
                    updated_at = CURRENT_TIMESTAMP
                """,
                (school_code, clean_college),
            )
        conn.execute(
            """
            INSERT INTO organization_departments (school_code, college_name, department_name, source)
            VALUES (?, ?, ?, 'backfill')
            ON CONFLICT(school_code, college_name, department_name) DO UPDATE SET
                updated_at = CURRENT_TIMESTAMP
            """,
            (school_code, clean_college, clean_department),
        )

    teacher_rows = conn.execute("SELECT id FROM teachers").fetchall()
    for teacher in teacher_rows:
        teacher_id = int(teacher["id"])
        scope = load_teacher_org_scope(conn, teacher_id)
        conn.execute(
            """
            UPDATE teachers
            SET school_code = ?,
                school_name = ?,
                college = CASE WHEN TRIM(COALESCE(college, '')) = '' THEN ? ELSE TRIM(college) END,
                department = CASE WHEN TRIM(COALESCE(department, '')) = '' THEN ? ELSE TRIM(department) END
            WHERE id = ?
            """,
            (
                scope["school_code"],
                scope["school_name"],
                scope["college"],
                scope["department"],
                teacher_id,
            ),
        )

    conn.execute(
        """
        UPDATE classes
        SET school_code = COALESCE(
                NULLIF(TRIM(school_code), ''),
                (SELECT NULLIF(TRIM(t.school_code), '') FROM teachers t WHERE t.id = classes.created_by_teacher_id),
                ?
            ),
            school_name = COALESCE(
                NULLIF(TRIM(school_name), ''),
                (SELECT NULLIF(TRIM(t.school_name), '') FROM teachers t WHERE t.id = classes.created_by_teacher_id),
                ?
            ),
            college = COALESCE(
                NULLIF(TRIM(college), ''),
                NULLIF(TRIM(academic_college), ''),
                (SELECT NULLIF(TRIM(t.college), '') FROM teachers t WHERE t.id = classes.created_by_teacher_id),
                ''
            )
        """,
        (DEFAULT_SCHOOL_CODE, DEFAULT_SCHOOL_NAME),
    )
    conn.execute(
        """
        UPDATE courses
        SET school_code = COALESCE(
                NULLIF(TRIM(school_code), ''),
                (SELECT NULLIF(TRIM(t.school_code), '') FROM teachers t WHERE t.id = courses.created_by_teacher_id),
                ?
            ),
            school_name = COALESCE(
                NULLIF(TRIM(school_name), ''),
                (SELECT NULLIF(TRIM(t.school_name), '') FROM teachers t WHERE t.id = courses.created_by_teacher_id),
                ?
            ),
            college = COALESCE(
                NULLIF(TRIM(college), ''),
                (SELECT NULLIF(TRIM(t.college), '') FROM teachers t WHERE t.id = courses.created_by_teacher_id),
                ''
            )
        """,
        (DEFAULT_SCHOOL_CODE, DEFAULT_SCHOOL_NAME),
    )
    conn.execute(
        """
        UPDATE academic_semesters
        SET school_code = COALESCE(
                NULLIF(TRIM(school_code), ''),
                (SELECT NULLIF(TRIM(t.school_code), '') FROM teachers t WHERE t.id = academic_semesters.teacher_id),
                ?
            ),
            school_name = COALESCE(
                NULLIF(TRIM(school_name), ''),
                (SELECT NULLIF(TRIM(t.school_name), '') FROM teachers t WHERE t.id = academic_semesters.teacher_id),
                ?
            )
        """,
        (DEFAULT_SCHOOL_CODE, DEFAULT_SCHOOL_NAME),
    )
    conn.execute(
        """
        UPDATE students
        SET school_code = COALESCE(
                NULLIF(TRIM(school_code), ''),
                (SELECT NULLIF(TRIM(c.school_code), '') FROM classes c WHERE c.id = students.class_id),
                ?
            ),
            school_name = COALESCE(
                NULLIF(TRIM(school_name), ''),
                (SELECT NULLIF(TRIM(c.school_name), '') FROM classes c WHERE c.id = students.class_id),
                ?
            ),
            college = COALESCE(
                NULLIF(TRIM(college), ''),
                NULLIF(TRIM(academic_college), ''),
                (SELECT NULLIF(TRIM(c.college), '') FROM classes c WHERE c.id = students.class_id),
                ''
            ),
            department = COALESCE(
                NULLIF(TRIM(department), ''),
                (SELECT NULLIF(TRIM(c.department), '') FROM classes c WHERE c.id = students.class_id),
                ''
            )
        """,
        (DEFAULT_SCHOOL_CODE, DEFAULT_SCHOOL_NAME),
    )
