"""Engine-aware schema for feature-bound, one-time signature authorization.

The legacy signature tables are kept for backward compatibility.  This module
adds the workflow entities and the exact identity/audit columns required to
make every third-party signature use explicit, reviewable and consumable.
"""

from __future__ import annotations

from typing import Any

from .connection import get_configured_db_engine


_SCHEMA_READY = False


SIGNATURE_FUNCTION_POINTS: tuple[tuple[str, str, str, str], ...] = (
    (
        "academic_final_material.grade_register.teacher_signature",
        "期末成绩登记表·教师签名",
        "academic_final_material",
        "期末成绩登记表底部教师签字处",
    ),
    (
        "academic_final_material.exam_analysis.department_review_signature",
        "试卷分析表·系部审核签名",
        "academic_final_material",
        "试卷分析表系部审核栏签名处",
    ),
    (
        "academic_final_material.exam_analysis.dean_review_signature",
        "试卷分析表·教学院长审核签名",
        "academic_final_material",
        "试卷分析表教学院长审核栏签名处",
    ),
    (
        "assessment_plan.examiner_signature",
        "课程考核计划表·命题人签名",
        "assessment_plan",
        "课程考核计划表命题人签名处",
    ),
    (
        "assessment_plan.reviewer_signature",
        "课程考核计划表·审核人签名",
        "assessment_plan",
        "课程考核计划表审核人签名处",
    ),
)


def _table_columns(conn: Any, table: str, *, engine: str) -> set[str]:
    if engine == "postgres":
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ?
            """,
            (table,),
        ).fetchall()
        columns: set[str] = set()
        for row in rows:
            keys = row.keys() if hasattr(row, "keys") else ()
            columns.add(str(row["column_name"] if "column_name" in keys else row[0]))
        return columns
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _add_columns(conn: Any, table: str, definitions: dict[str, str], *, engine: str) -> None:
    existing = _table_columns(conn, table, engine=engine)
    for column, definition in definitions.items():
        if column in existing:
            continue
        if engine == "postgres":
            conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
        else:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _seed_function_points(conn: Any, *, engine: str) -> None:
    for key, label, module_key, description in SIGNATURE_FUNCTION_POINTS:
        if engine == "postgres":
            conn.execute(
                """
                INSERT INTO signature_function_points (
                    point_key, label, module_key, description, is_enabled, updated_at
                ) VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT (point_key) DO UPDATE SET
                    label = EXCLUDED.label,
                    module_key = EXCLUDED.module_key,
                    description = EXCLUDED.description,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, label, module_key, description),
            )
        else:
            conn.execute(
                """
                INSERT INTO signature_function_points (
                    point_key, label, module_key, description, is_enabled, updated_at
                ) VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(point_key) DO UPDATE SET
                    label = excluded.label,
                    module_key = excluded.module_key,
                    description = excluded.description,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, label, module_key, description),
            )


def ensure_signature_workflow_schema(conn: Any) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    engine = get_configured_db_engine()
    id_type = "SERIAL" if engine == "postgres" else "INTEGER"
    primary_key = "PRIMARY KEY" if engine == "postgres" else "PRIMARY KEY AUTOINCREMENT"
    timestamp_type = "TIMESTAMP" if engine == "postgres" else "TEXT"

    _add_columns(
        conn,
        "electronic_signatures",
        {"subject_id": "INTEGER"},
        engine=engine,
    )
    _add_columns(
        conn,
        "signature_access_requests",
        {
            "requester_role": "TEXT NOT NULL DEFAULT 'teacher'",
            "requester_id": "INTEGER",
            "decided_at": timestamp_type,
            "cancelled_at": timestamp_type,
        },
        engine=engine,
    )
    _add_columns(
        conn,
        "signature_usage_logs",
        {
            "function_point_key": "TEXT NOT NULL DEFAULT ''",
            "request_id": "INTEGER",
            "request_item_id": "INTEGER",
            "authorization_mode": "TEXT NOT NULL DEFAULT ''",
            "idempotency_key": "TEXT NOT NULL DEFAULT ''",
        },
        engine=engine,
    )

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS signature_function_points (
            id {id_type} {primary_key},
            point_key TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL,
            module_key TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            is_enabled INTEGER NOT NULL DEFAULT 1,
            created_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS signature_access_request_items (
            id {id_type} {primary_key},
            request_id INTEGER NOT NULL,
            function_point_key TEXT NOT NULL,
            function_point_label_snapshot TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            consumed_at {timestamp_type},
            consumed_context_type TEXT NOT NULL DEFAULT '',
            consumed_context_id TEXT NOT NULL DEFAULT '',
            usage_log_id INTEGER,
            created_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (request_id, function_point_key),
            FOREIGN KEY (request_id) REFERENCES signature_access_requests (id) ON DELETE CASCADE,
            FOREIGN KEY (function_point_key) REFERENCES signature_function_points (point_key),
            FOREIGN KEY (usage_log_id) REFERENCES signature_usage_logs (id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS signature_access_request_reviewers (
            id {id_type} {primary_key},
            request_id INTEGER NOT NULL,
            reviewer_role TEXT NOT NULL,
            reviewer_id INTEGER NOT NULL,
            reviewer_kind TEXT NOT NULL DEFAULT '',
            reviewer_name_snapshot TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            review_note TEXT NOT NULL DEFAULT '',
            reviewed_at {timestamp_type},
            created_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (request_id, reviewer_role, reviewer_id),
            FOREIGN KEY (request_id) REFERENCES signature_access_requests (id) ON DELETE CASCADE
        )
        """
    )

    conn.execute(
        """
        UPDATE electronic_signatures
        SET subject_id = owner_id
        WHERE subject_id IS NULL
          AND subject_role = owner_role
          AND owner_id IS NOT NULL
          AND (
              TRIM(COALESCE(subject_name, '')) = ''
              OR LOWER(TRIM(COALESCE(subject_name, ''))) = LOWER(TRIM(COALESCE(owner_name_snapshot, '')))
          )
        """
    )
    conn.execute(
        """
        UPDATE signature_access_requests
        SET requester_role = COALESCE(NULLIF(TRIM(requester_role), ''), 'teacher'),
            requester_id = COALESCE(requester_id, requester_teacher_id)
        WHERE requester_id IS NULL OR TRIM(COALESCE(requester_role, '')) = ''
        """
    )
    # Broad legacy approvals have no registered feature point and must not
    # remain reusable after the feature-bound workflow becomes active.
    conn.execute(
        """
        UPDATE signature_access_requests
        SET status = 'cancelled',
            review_note = CASE
                WHEN TRIM(COALESCE(review_note, '')) = ''
                THEN '已迁移：旧版未绑定功能点的授权已失效，请按功能点重新申请。'
                ELSE review_note
            END,
            cancelled_at = COALESCE(cancelled_at, CURRENT_TIMESTAMP)
        WHERE status IN ('pending', 'approved')
          AND NOT EXISTS (
              SELECT 1 FROM signature_access_request_items item
              WHERE item.request_id = signature_access_requests.id
          )
        """
    )

    conn.execute("DROP INDEX IF EXISTS idx_signature_access_requests_active_unique")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_signature_access_requests_active_unique
        ON signature_access_requests (signature_id, requester_role, requester_id)
        WHERE status = 'pending'
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_signature_usage_idempotency
        ON signature_usage_logs (idempotency_key)
        WHERE idempotency_key <> ''
        """
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_signature_function_points_enabled ON signature_function_points (is_enabled, module_key, point_key)",
        "CREATE INDEX IF NOT EXISTS idx_signature_request_items_available ON signature_access_request_items (function_point_key, status, request_id)",
        "CREATE INDEX IF NOT EXISTS idx_signature_request_reviewers_incoming ON signature_access_request_reviewers (reviewer_role, reviewer_id, status, request_id)",
        "CREATE INDEX IF NOT EXISTS idx_signature_usage_feature_context ON signature_usage_logs (function_point_key, context_type, context_id, created_at)",
    ):
        conn.execute(statement)

    _seed_function_points(conn, engine=engine)
    _SCHEMA_READY = True
