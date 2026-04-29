from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
NEW_TRACKED_DB_PATH = "data/db/classroom.db"
LEGACY_TRACKED_DB_PATH = "data/classroom.db"
TRACKED_DB_PATHS = (NEW_TRACKED_DB_PATH, LEGACY_TRACKED_DB_PATH)
SUBMISSION_PATH_MARKERS = ("files/submissions", "homework_submissions")
MAX_GIT_CANDIDATES = 20


@dataclass
class TableMergeStats:
    name: str
    base_rows: int = 0
    current_scan_rows: int = 0
    current_scan_complete: bool = True
    current_scan_error: str | None = None
    current_point_rows: int = 0
    current_point_errors: int = 0
    final_rows: int = 0


@dataclass
class RecoveryReport:
    started_at: str
    base_revision: str
    base_tracked_path: str
    corrupted_backup: str
    output_path: str
    integrity_check: str
    foreign_key_violations: int
    dashboard_recent_activity_rows: int
    synthesized_assignments: int
    live_database_replaced: bool
    tables: list[dict[str, Any]] = field(default_factory=list)


def _run_git(*args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn


def _list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [str(row[0]) for row in rows]


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table_info(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return conn.execute(f"PRAGMA table_info({_quote_ident(table)})").fetchall()


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in _table_info(conn, table)]


def _primary_key_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = sorted(
        _table_info(conn, table),
        key=lambda row: int(row["pk"]),
    )
    return [str(row["name"]) for row in rows if int(row["pk"]) > 0]


def _single_integer_primary_key(conn: sqlite3.Connection, table: str) -> str | None:
    rows = _table_info(conn, table)
    pk_rows = [row for row in rows if int(row["pk"]) > 0]
    if len(pk_rows) != 1:
        return None
    pk_row = pk_rows[0]
    declared_type = str(pk_row["type"] or "").strip().upper()
    if "INT" not in declared_type:
        return None
    return str(pk_row["name"])


def _build_upsert_sql(table: str, columns: list[str], pk_columns: list[str]) -> str:
    quoted_columns = ", ".join(_quote_ident(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    insert_sql = f"INSERT INTO {_quote_ident(table)} ({quoted_columns}) VALUES ({placeholders})"
    if not pk_columns:
        return insert_sql

    update_columns = [column for column in columns if column not in pk_columns]
    if not update_columns:
        conflict_clause = ", ".join(_quote_ident(column) for column in pk_columns)
        return f"{insert_sql} ON CONFLICT ({conflict_clause}) DO NOTHING"

    conflict_clause = ", ".join(_quote_ident(column) for column in pk_columns)
    update_clause = ", ".join(
        f"{_quote_ident(column)} = excluded.{_quote_ident(column)}" for column in update_columns
    )
    return f"{insert_sql} ON CONFLICT ({conflict_clause}) DO UPDATE SET {update_clause}"


def _insert_row(
    conn: sqlite3.Connection,
    table: str,
    columns: list[str],
    pk_columns: list[str],
    row: tuple[Any, ...],
) -> None:
    sql = _build_upsert_sql(table, columns, pk_columns)
    conn.execute(sql, row)


def _full_scan_rows(
    source_conn: sqlite3.Connection,
    table: str,
    columns: list[str],
) -> tuple[int, bool, str | None]:
    quoted_columns = ", ".join(_quote_ident(column) for column in columns)
    sql = f"SELECT {quoted_columns} FROM {_quote_ident(table)}"
    count = 0
    try:
        for _ in source_conn.execute(sql):
            count += 1
        return count, True, None
    except sqlite3.DatabaseError as exc:
        return count, False, str(exc)


def _copy_full_scan_rows(
    source_conn: sqlite3.Connection,
    target_conn: sqlite3.Connection,
    table: str,
    columns: list[str],
    pk_columns: list[str],
) -> tuple[int, bool, str | None]:
    quoted_columns = ", ".join(_quote_ident(column) for column in columns)
    sql = f"SELECT {quoted_columns} FROM {_quote_ident(table)}"
    copied = 0
    try:
        for row in source_conn.execute(sql):
            _insert_row(target_conn, table, columns, pk_columns, tuple(row))
            copied += 1
        return copied, True, None
    except sqlite3.DatabaseError as exc:
        return copied, False, str(exc)


def _safe_scalar(source_conn: sqlite3.Connection, sql: str) -> int | None:
    try:
        row = source_conn.execute(sql).fetchone()
    except sqlite3.DatabaseError:
        return None
    if not row:
        return None
    value = row[0]
    if value is None:
        return None
    return int(value)


def _copy_point_rows(
    source_conn: sqlite3.Connection,
    target_conn: sqlite3.Connection,
    table: str,
    columns: list[str],
    pk_columns: list[str],
    *,
    start_id: int,
    end_id: int,
    pk_name: str,
) -> tuple[int, int]:
    if end_id < start_id:
        return 0, 0

    quoted_columns = ", ".join(_quote_ident(column) for column in columns)
    sql = (
        f"SELECT {quoted_columns} FROM {_quote_ident(table)} "
        f"WHERE {_quote_ident(pk_name)} = ?"
    )

    copied = 0
    errors = 0
    for current_id in range(start_id, end_id + 1):
        try:
            row = source_conn.execute(sql, (current_id,)).fetchone()
        except sqlite3.DatabaseError:
            errors += 1
            continue
        if row is None:
            continue
        _insert_row(target_conn, table, columns, pk_columns, tuple(row))
        copied += 1
    return copied, errors


def _source_has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table,),
    ).fetchone()
    return row is not None


def _export_git_blob(revision: str, tracked_path: str, output_path: Path) -> None:
    output_path.write_bytes(_run_git("show", f"{revision}:{tracked_path}"))


def _find_latest_valid_git_snapshot() -> tuple[str, Path, str]:
    for tracked_path in TRACKED_DB_PATHS:
        revisions = _run_git(
            "rev-list",
            f"--max-count={MAX_GIT_CANDIDATES}",
            "HEAD",
            "--",
            tracked_path,
        )
        candidates = [line.strip() for line in revisions.decode("ascii").splitlines() if line.strip()]
        if not candidates:
            continue

        for revision in candidates:
            export_path = DATA_DIR / f"classroom.git-base.{revision[:8]}.db"
            _export_git_blob(revision, tracked_path, export_path)
            try:
                with sqlite3.connect(export_path) as conn:
                    result = conn.execute("PRAGMA integrity_check").fetchone()
            except sqlite3.DatabaseError:
                export_path.unlink(missing_ok=True)
                continue
            if result and result[0] == "ok":
                return revision, export_path, tracked_path
            export_path.unlink(missing_ok=True)

    tracked_labels = ", ".join(TRACKED_DB_PATHS)
    raise RuntimeError(f"No valid Git snapshot of {tracked_labels} passed integrity_check.")


def _initialize_output_database(output_path: Path) -> None:
    if output_path.exists():
        output_path.unlink()

    sys.path.insert(0, str(REPO_ROOT))
    import classroom_app.config as app_config
    import classroom_app.database as app_database

    original_config_db_path = app_config.DB_PATH
    original_database_db_path = app_database.DB_PATH
    try:
        app_config.DB_PATH = output_path
        app_database.DB_PATH = output_path
        app_database.init_database()
    finally:
        app_config.DB_PATH = original_config_db_path
        app_database.DB_PATH = original_database_db_path


def _merge_table_from_source(
    source_conn: sqlite3.Connection,
    target_conn: sqlite3.Connection,
    table: str,
    *,
    is_current_source: bool,
) -> TableMergeStats:
    stats = TableMergeStats(name=table)
    if not _source_has_table(source_conn, table):
        stats.final_rows = _safe_scalar(target_conn, f"SELECT COUNT(*) FROM {_quote_ident(table)}") or 0
        return stats

    target_columns = _table_columns(target_conn, table)
    source_columns = set(_table_columns(source_conn, table))
    common_columns = [column for column in target_columns if column in source_columns]
    pk_columns = _primary_key_columns(target_conn, table)

    if not common_columns:
        stats.final_rows = _safe_scalar(target_conn, f"SELECT COUNT(*) FROM {_quote_ident(table)}") or 0
        return stats

    copied, complete, error = _copy_full_scan_rows(
        source_conn,
        target_conn,
        table,
        common_columns,
        pk_columns,
    )
    if is_current_source:
        stats.current_scan_rows = copied
        stats.current_scan_complete = complete
        stats.current_scan_error = error
    else:
        stats.base_rows = copied

    if is_current_source:
        point_pk = _single_integer_primary_key(target_conn, table)
        base_max = None
        current_max = None
        if point_pk is not None:
            base_max = _safe_scalar(target_conn, f"SELECT MAX({_quote_ident(point_pk)}) FROM {_quote_ident(table)}")
            current_max = _safe_scalar(source_conn, f"SELECT MAX({_quote_ident(point_pk)}) FROM {_quote_ident(table)}")
        if point_pk and current_max is not None:
            point_start = 1 if not complete else (base_max or 0) + 1
            point_rows, point_errors = _copy_point_rows(
                source_conn,
                target_conn,
                table,
                common_columns,
                pk_columns,
                start_id=max(1, point_start),
                end_id=current_max,
                pk_name=point_pk,
            )
            stats.current_point_rows = point_rows
            stats.current_point_errors = point_errors

    stats.final_rows = _safe_scalar(target_conn, f"SELECT COUNT(*) FROM {_quote_ident(table)}") or 0
    return stats


def _validate_dashboard_query(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT recipient_role, recipient_user_pk
        FROM message_center_notifications
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return 0

    recipient_role = row[0]
    recipient_user_pk = row[1]
    rows = conn.execute(
        """
        SELECT id, category, title, body_preview, link_url, read_at, created_at
        FROM message_center_notifications
        WHERE recipient_role = ? AND recipient_user_pk = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 6
        """,
        (recipient_role, recipient_user_pk),
    ).fetchall()
    return len(rows)


def _replace_file_with_retry(source_path: Path, destination_path: Path) -> None:
    last_error: PermissionError | None = None
    for _ in range(20):
        try:
            os.replace(source_path, destination_path)
            return
        except PermissionError as exc:
            last_error = exc
            gc.collect()
            time.sleep(0.25)
    if last_error is not None:
        raise last_error


def _extract_submission_path_parts(raw_path: str) -> list[str]:
    normalized = str(raw_path or "").replace("\\", "/").strip()
    if not normalized:
        return []

    for marker in sorted(SUBMISSION_PATH_MARKERS, key=len, reverse=True):
        for token in (f"/{marker}/", f"{marker}/"):
            index = normalized.rfind(token)
            if index < 0:
                continue
            relative = normalized[index + len(token):].strip("/")
            return [part for part in relative.split("/") if part]
    return []


def _infer_course_id_from_submission_files(conn: sqlite3.Connection, assignment_id: int) -> int | None:
    rows = conn.execute(
        """
        SELECT sf.stored_path, sf.relative_path
        FROM submission_files sf
        JOIN submissions s ON s.id = sf.submission_id
        WHERE CAST(s.assignment_id AS INTEGER) = ?
        ORDER BY sf.id ASC
        """,
        (assignment_id,),
    ).fetchall()
    assignment_token = str(assignment_id)
    for stored_path, relative_path in rows:
        raw_path = str(stored_path or relative_path or "").strip()
        if not raw_path:
            continue
        parts = _extract_submission_path_parts(raw_path)
        if len(parts) < 2:
            continue
        course_token = parts[0]
        assignment_path_token = parts[1]
        if assignment_path_token != assignment_token:
            continue
        if course_token.isdigit():
            return int(course_token)
    return None


def _infer_class_offering_id(
    conn: sqlite3.Connection,
    assignment_id: int,
    course_id: int,
) -> int | None:
    class_rows = conn.execute(
        """
        SELECT DISTINCT st.class_id
        FROM submissions s
        JOIN students st ON st.id = s.student_pk_id
        WHERE CAST(s.assignment_id AS INTEGER) = ?
        ORDER BY st.class_id
        """,
        (assignment_id,),
    ).fetchall()
    class_ids = [int(row[0]) for row in class_rows if row[0] is not None]
    if len(class_ids) != 1:
        return None

    offering_row = conn.execute(
        """
        SELECT id
        FROM class_offerings
        WHERE class_id = ? AND course_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (class_ids[0], course_id),
    ).fetchone()
    if offering_row is None:
        return None
    return int(offering_row[0])


def _repair_orphan_submissions(conn: sqlite3.Connection) -> int:
    orphan_rows = conn.execute(
        """
        SELECT DISTINCT CAST(s.assignment_id AS INTEGER) AS assignment_id
        FROM submissions s
        LEFT JOIN assignments a ON a.id = CAST(s.assignment_id AS INTEGER)
        WHERE a.id IS NULL AND CAST(s.assignment_id AS INTEGER) IS NOT NULL
        ORDER BY assignment_id
        """
    ).fetchall()

    repaired = 0
    for row in orphan_rows:
        assignment_id = int(row[0])
        course_id = _infer_course_id_from_submission_files(conn, assignment_id)
        if course_id is None:
            continue
        class_offering_id = _infer_class_offering_id(conn, assignment_id, course_id)
        created_at_row = conn.execute(
            """
            SELECT MIN(submitted_at)
            FROM submissions
            WHERE CAST(assignment_id AS INTEGER) = ?
            """,
            (assignment_id,),
        ).fetchone()
        created_at = created_at_row[0] if created_at_row and created_at_row[0] else None
        conn.execute(
            """
            INSERT INTO assignments (
                id,
                course_id,
                title,
                status,
                requirements_md,
                rubric_md,
                grading_mode,
                created_at,
                exam_paper_id,
                allowed_file_types_json,
                class_offering_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assignment_id,
                course_id,
                f"Recovered assignment #{assignment_id}",
                "published",
                "",
                "",
                "manual",
                created_at,
                None,
                None,
                class_offering_id,
            ),
        )
        repaired += 1
    return repaired


def recover_database(corrupted_db_path: Path) -> RecoveryReport:
    if not corrupted_db_path.exists():
        raise FileNotFoundError(f"Database not found: {corrupted_db_path}")

    started_at = datetime.now().isoformat(timespec="seconds")
    revision, base_snapshot_path, base_tracked_path = _find_latest_valid_git_snapshot()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = DATA_DIR / f"classroom.corrupt.{timestamp}.db"
    output_path = DATA_DIR / f"classroom.recovered.{timestamp}.db"
    report_path = DATA_DIR / f"classroom.recovery_report.{timestamp}.json"

    shutil.copy2(corrupted_db_path, backup_path)
    _initialize_output_database(output_path)

    table_stats: dict[str, TableMergeStats] = {}

    with _connect(output_path) as target_conn, _connect(base_snapshot_path) as base_conn:
        for table in _list_tables(target_conn):
            table_stats[table] = _merge_table_from_source(
                base_conn,
                target_conn,
                table,
                is_current_source=False,
            )
        target_conn.commit()

    with _connect(output_path) as target_conn, _connect(corrupted_db_path) as current_conn:
        for table in _list_tables(target_conn):
            stats = table_stats.setdefault(table, TableMergeStats(name=table))
            current_stats = _merge_table_from_source(
                current_conn,
                target_conn,
                table,
                is_current_source=True,
            )
            stats.current_scan_rows = current_stats.current_scan_rows
            stats.current_scan_complete = current_stats.current_scan_complete
            stats.current_scan_error = current_stats.current_scan_error
            stats.current_point_rows = current_stats.current_point_rows
            stats.current_point_errors = current_stats.current_point_errors
            stats.final_rows = current_stats.final_rows
        synthesized_assignments = _repair_orphan_submissions(target_conn)
        for table_name, stats in table_stats.items():
            stats.final_rows = _safe_scalar(
                target_conn,
                f"SELECT COUNT(*) FROM {_quote_ident(table_name)}",
            ) or 0
        target_conn.commit()
        target_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

    with sqlite3.connect(output_path) as validation_conn:
        integrity_row = validation_conn.execute("PRAGMA integrity_check").fetchone()
        integrity_result = str(integrity_row[0] if integrity_row else "")
        foreign_key_violations = len(validation_conn.execute("PRAGMA foreign_key_check").fetchall())
        dashboard_rows = _validate_dashboard_query(validation_conn)

    live_database_replaced = False
    report = RecoveryReport(
        started_at=started_at,
        base_revision=revision,
        base_tracked_path=base_tracked_path,
        corrupted_backup=str(backup_path),
        output_path=str(output_path),
        integrity_check=integrity_result,
        foreign_key_violations=foreign_key_violations,
        dashboard_recent_activity_rows=dashboard_rows,
        synthesized_assignments=synthesized_assignments,
        live_database_replaced=live_database_replaced,
        tables=[table_stats[name].__dict__ for name in sorted(table_stats)],
    )

    report_path.write_text(
        json.dumps(report.__dict__, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if integrity_result != "ok":
        raise RuntimeError(f"Recovered database integrity_check failed: {integrity_result}")
    if foreign_key_violations:
        raise RuntimeError(f"Recovered database has {foreign_key_violations} foreign key violations.")

    try:
        _replace_file_with_retry(output_path, corrupted_db_path)
        live_database_replaced = True
    except PermissionError:
        live_database_replaced = False
    base_snapshot_path.unlink(missing_ok=True)
    report.live_database_replaced = live_database_replaced
    if live_database_replaced:
        report.output_path = str(corrupted_db_path)
    report_path.write_text(
        json.dumps(report.__dict__, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recover classroom.db from the latest valid Git snapshot plus salvageable rows from the current database."
    )
    default_db_path = DATA_DIR / "db" / "classroom.db"
    if not default_db_path.exists():
        default_db_path = DATA_DIR / "classroom.db"
    parser.add_argument(
        "--db",
        default=str(default_db_path),
        help="Path to the corrupted classroom.db file.",
    )
    args = parser.parse_args()

    report = recover_database(Path(args.db).resolve())
    print(json.dumps(report.__dict__, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
