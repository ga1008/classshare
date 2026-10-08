"""Provenance for deterministic repository-to-lesson projections.

Rules belong to teaching ordinals, while session_id is only the current projection.
Tombstones preserve an explicit teacher unbind across subsequent Git pulls.
"""


def ensure_git_learning_bindings_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS class_offering_git_learning_bindings (
            class_offering_id INTEGER NOT NULL,
            repository_id INTEGER NOT NULL,
            lesson_order INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            session_id INTEGER NOT NULL DEFAULT 0,
            owns_binding INTEGER NOT NULL DEFAULT 0,
            suppressed INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (class_offering_id, repository_id, lesson_order)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_git_learning_binding_session "
                 "ON class_offering_git_learning_bindings (class_offering_id, session_id, material_id)")
