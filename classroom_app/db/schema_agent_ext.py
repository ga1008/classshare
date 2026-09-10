"""Agent 任务中心扩展列（运行时管理、引擎感知、幂等）。

为 ``agent_tasks`` 增加任务对话/重试/附件/来源等扩展列。和 scheduler/gongwen
表一样，这一步在运行时确保（sqlite 与 postgres 都执行），不进中央 postgres
迁移清单；每进程只执行一次。
"""
from __future__ import annotations

import re

from .connection import get_configured_db_engine

_SCHEMA_READY = False

# 列名 -> (sqlite 定义, postgres 定义)
_AGENT_TASK_EXTENSION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("actor_role", "TEXT NOT NULL DEFAULT 'teacher'", "TEXT NOT NULL DEFAULT 'teacher'"),
    ("actor_id", "INTEGER", "BIGINT"),
    ("source_session_hash", "TEXT", "TEXT"),
    ("source_session_key", "TEXT", "TEXT"),
    ("persistent_authorization_id", "TEXT", "TEXT"),
    ("parent_task_id", "INTEGER", "BIGINT"),
    ("origin", "TEXT NOT NULL DEFAULT 'manual'", "TEXT NOT NULL DEFAULT 'manual'"),
    ("attachments_json", "TEXT NOT NULL DEFAULT '[]'", "TEXT NOT NULL DEFAULT '[]'"),
    ("retry_count", "INTEGER NOT NULL DEFAULT 0", "INTEGER NOT NULL DEFAULT 0"),
)


def prepare_sqlite_agent_actor_schema(conn) -> None:
    """Rebuild legacy identity keys before startup's schema transaction begins.

    Foreign keys must be disabled outside a transaction, otherwise dropping the
    old task parent would cascade-delete events and authority records. Reusing
    the original CREATE statement also preserves future extension columns.
    """
    table_sql = {}
    for table in ("agent_tasks", "agent_task_composers"):
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if not row:
            continue
        info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        teacher = next((column for column in info if column[1] == "teacher_id"), None)
        needs_rebuild = bool(teacher and (teacher[3] if table == "agent_tasks" else teacher[5]))
        if needs_rebuild:
            table_sql[table] = str(row[0])
    if not table_sql:
        return
    if conn.in_transaction:
        raise RuntimeError("Agent actor migration requires a connection outside a transaction")
    foreign_keys = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table in table_sql:
            # Another startup process may have completed the migration while
            # this connection was waiting for the exclusive writer lock.
            info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            teacher = next((column for column in info if column[1] == "teacher_id"), None)
            if not teacher or not (teacher[3] if table == "agent_tasks" else teacher[5]):
                continue
            original_sql = str(conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0])
            objects = conn.execute(
                "SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL",
                (table,),
            ).fetchall()
            columns = [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
            temporary = f"{table}_actor_migration"
            replacement = re.sub(r'(?i)(CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)["`\[]?' + table + r'["`\]]?',
                                 rf'\1"{temporary}"', original_sql, count=1)
            if table == "agent_tasks":
                replacement = re.sub(r'(?i)(\bteacher_id\s+INTEGER)\s+NOT\s+NULL', r'\1', replacement, count=1)
            else:
                replacement = re.sub(r'(?i)(\bteacher_id\s+INTEGER)\s+PRIMARY\s+KEY', r'\1', replacement, count=1)
                # Composer rows are short-lived; the key still migrates in-place
                # so active teachers do not lose their presence during startup.
                if "actor_role" not in columns:
                    replacement = replacement.replace("(", "(actor_role TEXT NOT NULL DEFAULT 'teacher', actor_id INTEGER, ", 1)
                    replacement = replacement.rstrip().removesuffix(")") + ", UNIQUE(actor_role, actor_id))"
            conn.execute(replacement)
            quoted = ",".join(f'"{column}"' for column in columns)
            conn.execute(f'INSERT INTO "{temporary}" ({quoted}) SELECT {quoted} FROM "{table}"')
            conn.execute(f'DROP TABLE "{table}"')
            conn.execute(f'ALTER TABLE "{temporary}" RENAME TO "{table}"')
            for item in objects:
                conn.execute(item[0])
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Agent actor migration found {len(violations)} foreign-key violations")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={foreign_keys}")


def ensure_agent_task_extension_schema(conn, *, force: bool = False, engine: str | None = None) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY and not force:
        return
    engine = engine or get_configured_db_engine()
    if engine == "postgres":
        for column_name, _sqlite_def, pg_def in _AGENT_TASK_EXTENSION_COLUMNS:
            conn.execute(
                f"ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS {column_name} {pg_def}"
            )
        conn.execute("ALTER TABLE agent_tasks ALTER COLUMN teacher_id DROP NOT NULL")
        conn.execute("ALTER TABLE agent_task_composers ADD COLUMN IF NOT EXISTS actor_role TEXT NOT NULL DEFAULT 'teacher'")
        conn.execute("ALTER TABLE agent_task_composers ADD COLUMN IF NOT EXISTS actor_id BIGINT")
        conn.execute("ALTER TABLE agent_task_composers DROP CONSTRAINT IF EXISTS agent_task_composers_pkey")
        # Native imports sometimes made this foreign identity an IDENTITY
        # column. Retire generation before allowing a student NULL; PostgreSQL
        # preserves every stored teacher ID and FK, removing its owned sequence.
        conn.execute("ALTER TABLE agent_task_composers ALTER COLUMN teacher_id DROP IDENTITY IF EXISTS")
        conn.execute("ALTER TABLE agent_task_composers ALTER COLUMN teacher_id DROP NOT NULL")
    else:
        import sqlite3

        try:
            cursor = conn.execute('PRAGMA table_info("agent_tasks")')
            rows = cursor.fetchall()
        except AttributeError:
            # Lightweight test doubles used by write-path tests do not expose
            # fetchall(). They are only verifying SQL routing, not runtime DDL.
            return
        existing = {str(row[1]) for row in rows}
        for column_name, sqlite_def, _pg_def in _AGENT_TASK_EXTENSION_COLUMNS:
            if column_name in existing:
                continue
            try:
                conn.execute(f"ALTER TABLE agent_tasks ADD COLUMN {column_name} {sqlite_def}")
            except sqlite3.OperationalError:
                pass
        composer_columns = {str(row[1]) for row in conn.execute('PRAGMA table_info("agent_task_composers")').fetchall()}
        for name, definition in (("actor_role", "TEXT NOT NULL DEFAULT 'teacher'"), ("actor_id", "INTEGER")):
            if name not in composer_columns:
                conn.execute(f"ALTER TABLE agent_task_composers ADD COLUMN {name} {definition}")
    conn.execute("UPDATE agent_tasks SET actor_id=teacher_id WHERE actor_id IS NULL AND actor_role='teacher'")
    conn.execute("UPDATE agent_task_composers SET actor_id=teacher_id WHERE actor_id IS NULL AND actor_role='teacher'")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_composers_actor ON agent_task_composers(actor_role, actor_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_tasks_actor_created ON agent_tasks(actor_role, actor_id, created_at DESC)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_tasks_parent ON agent_tasks (parent_task_id)"
    )
    _SCHEMA_READY = True
