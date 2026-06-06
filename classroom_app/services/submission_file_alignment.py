"""
Submission file alignment service.

Recovers and realigns the correspondence between database submission
records and actual files on disk.  Handles:
  - stale stored_path (wrong drive / base directory)
  - orphaned submission files (files exist on disk but have no DB record)
  - missing files referenced by DB (stored_path points to non-existent file)
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import sqlite3
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import HOMEWORK_SUBMISSIONS_DIR, HOMEWORK_SUBMISSIONS_LEGACY_DIRS
from ..db.connection import execute_insert_returning_id
from ..storage_paths import extract_relative_after_markers, resolve_migrated_file_path


# ---------------------------------------------------------------------------
# Path helpers (cross-platform)
# ---------------------------------------------------------------------------

def _extract_relative_submission_path(stored_path: str) -> str | None:
    return extract_relative_after_markers(
        stored_path,
        ("homework_submissions", "files/submissions"),
    )


def resolve_submission_file_path(stored_path: str) -> str | None:
    """Given a *stored_path* from the database, resolve it to the actual file
    on disk under :pydata:`HOMEWORK_SUBMISSIONS_DIR`.

    Returns the real path if the file exists, otherwise ``None``.
    """
    resolved = resolve_migrated_file_path(
        stored_path,
        active_root=HOMEWORK_SUBMISSIONS_DIR,
        legacy_roots=HOMEWORK_SUBMISSIONS_LEGACY_DIRS,
        markers=("homework_submissions", "files/submissions"),
    )
    return str(resolved) if resolved else None


def _resolve_stored_path(stored_path: str) -> str | None:
    return resolve_submission_file_path(stored_path)


def _build_expected_stored_path(course_id: int | str,
                                 assignment_id: int | str,
                                 student_pk_id: int | str,
                                 relative_path: str) -> str:
    """Build the canonical *stored_path* for a submission file.

    Uses ``PurePosixPath`` to split the relative path so that nested
    directories (e.g. ``"src/main.py"``) join correctly regardless of OS.
    """
    base = HOMEWORK_SUBMISSIONS_DIR / str(course_id) / str(assignment_id) / str(student_pk_id)
    return str(base.joinpath(*PurePosixPath(relative_path).parts))


def _file_hash_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _infer_mime_type(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _row_value(row: Any, key: str, index: int = 0, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        pass
    try:
        return row[index]
    except (KeyError, TypeError, IndexError):
        pass
    data = _row_dict(row)
    return data.get(key, default)


# ---------------------------------------------------------------------------
# Alignment report
# ---------------------------------------------------------------------------

class AlignmentReport:
    """Collects statistics produced by :func:`repair_submission_file_paths`."""

    def __init__(self) -> None:
        self.started_at: str = ""
        self.finished_at: str = ""
        self.total_stored_paths_checked: int = 0
        self.paths_already_valid: int = 0
        self.paths_repaired: int = 0
        self.paths_still_missing: int = 0
        self.orphan_directories_scanned: int = 0
        self.orphan_files_recovered: int = 0
        self.orphan_submissions_created: int = 0
        self.orphan_assignment_directories_scanned: int = 0
        self.orphan_assignments_created: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Core repair logic
# ---------------------------------------------------------------------------

def repair_stale_stored_paths(conn: sqlite3.Connection) -> AlignmentReport:
    """Scan ``submission_files`` and fix stored_path entries that no longer
    resolve to a file on disk due to a changed base directory or drive letter.
    """
    report = AlignmentReport()
    report.started_at = datetime.now().isoformat(timespec="seconds")

    rows = conn.execute(
        """
        SELECT sf.id, sf.stored_path, sf.relative_path, sf.file_hash,
               s.assignment_id, s.student_pk_id, a.course_id
        FROM submission_files sf
        JOIN submissions s ON s.id = sf.submission_id
        JOIN assignments a ON a.id = s.assignment_id
        ORDER BY sf.id
        """
    ).fetchall()

    report.total_stored_paths_checked = len(rows)

    for row in rows:
        file_id = int(_row_value(row, "id", 0, 0) or 0)
        stored_path = _row_value(row, "stored_path", 1, "")
        relative_path = _row_value(row, "relative_path", 2, "")
        assignment_id = _row_value(row, "assignment_id", 4, "")
        student_pk_id = _row_value(row, "student_pk_id", 5, "")
        course_id = _row_value(row, "course_id", 6, "")

        report.total_stored_paths_checked = max(report.total_stored_paths_checked, file_id)

        resolved = _resolve_stored_path(stored_path)

        if resolved is not None:
            if resolved != stored_path:
                # Path was stale but file found at the new location
                new_stored = _build_expected_stored_path(
                    course_id, assignment_id, student_pk_id, relative_path
                )
                # Verify the canonical path also exists (it should)
                if os.path.isfile(new_stored):
                    conn.execute(
                        "UPDATE submission_files SET stored_path = ? WHERE id = ?",
                        (new_stored, file_id),
                    )
                else:
                    conn.execute(
                        "UPDATE submission_files SET stored_path = ? WHERE id = ?",
                        (resolved, file_id),
                    )
                report.paths_repaired += 1
            else:
                report.paths_already_valid += 1
        else:
            # Try to find the file using the expected path structure
            expected = _build_expected_stored_path(
                course_id, assignment_id, student_pk_id, relative_path
            )
            if os.path.isfile(expected):
                conn.execute(
                    "UPDATE submission_files SET stored_path = ? WHERE id = ?",
                    (expected, file_id),
                )
                report.paths_repaired += 1
            else:
                report.paths_still_missing += 1

    conn.commit()
    report.finished_at = datetime.now().isoformat(timespec="seconds")
    return report


def recover_orphan_files(conn: sqlite3.Connection) -> AlignmentReport:
    """Scan ``homework_submissions/`` on disk and find files that exist but
    have no corresponding ``submission_files`` row.  Reconstruct DB entries
    for these orphaned files.

    This handles cases where git operations wiped DB records but left files
    intact on disk.
    """
    report = AlignmentReport()
    report.started_at = datetime.now().isoformat(timespec="seconds")

    if not HOMEWORK_SUBMISSIONS_DIR.is_dir():
        report.finished_at = datetime.now().isoformat(timespec="seconds")
        return report

    original_factory = getattr(conn, "row_factory", None)
    if hasattr(conn, "row_factory"):
        conn.row_factory = sqlite3.Row

    try:
        # Build set of (assignment_id, student_pk_id) that already
        # have DB submissions
        existing_submissions: dict[tuple[str, str], dict[str, Any]] = {}
        for row in conn.execute(
            """
            SELECT s.id, s.assignment_id, s.student_pk_id, s.student_name,
                   s.status, s.answers_json, s.submitted_at, a.course_id
            FROM submissions s
            JOIN assignments a ON a.id = s.assignment_id
            """
        ):
            data = _row_dict(row)
            key = (str(_row_value(row, "assignment_id", 1)), str(_row_value(row, "student_pk_id", 2)))
            existing_submissions[key] = {
                "submission_id": data.get("id", _row_value(row, "id", 0)),
                "assignment_id": data.get("assignment_id", _row_value(row, "assignment_id", 1)),
                "student_pk_id": data.get("student_pk_id", _row_value(row, "student_pk_id", 2)),
                "student_name": data.get("student_name", _row_value(row, "student_name", 3)),
                "status": data.get("status", _row_value(row, "status", 4)),
                "answers_json": data.get("answers_json", _row_value(row, "answers_json", 5)),
                "submitted_at": data.get("submitted_at", _row_value(row, "submitted_at", 6)),
                "course_id": data.get("course_id", _row_value(row, "course_id", 7)),
            }

        # Scan filesystem
        for course_dir in _iter_numeric_dirs(HOMEWORK_SUBMISSIONS_DIR):
            course_id = course_dir.name
            report.orphan_assignment_directories_scanned += 1

            for assign_dir in _iter_dirs(course_dir):
                assign_id = assign_dir.name

                # Ensure the assignment exists in DB
                assignment_row = conn.execute(
                    "SELECT id, course_id FROM assignments WHERE id = ?",
                    (assign_id,),
                ).fetchone()
                if assignment_row is None:
                    # Try to infer course_id from directory name
                    _ensure_assignment_exists(conn, assign_id, course_id)
                    assignment_row = conn.execute(
                        "SELECT id, course_id FROM assignments WHERE id = ?",
                        (assign_id,),
                    ).fetchone()
                    if assignment_row is None:
                        continue
                    report.orphan_assignments_created += 1

                for student_dir in _iter_dirs(assign_dir):
                    student_pk_id = student_dir.name

                    # Only process numeric student directories (student PK IDs)
                    if not student_pk_id.isdigit():
                        continue

                    # Look up student info
                    student_row = conn.execute(
                        "SELECT id, name, class_id FROM students WHERE id = ?",
                        (int(student_pk_id),),
                    ).fetchone()
                    if student_row is None:
                        # Directory exists but student not in DB — skip
                        continue

                    student_name = student_row["name"]
                    sub_key = (assign_id, student_pk_id)

                    # Get or create the submission record
                    sub_info = existing_submissions.get(sub_key)
                    if sub_info is None:
                        # Create a recovered submission record
                        now_iso = datetime.now().isoformat()
                        # Use directory mtime as submitted_at approximation
                        try:
                            dir_mtime = os.path.getmtime(student_dir)
                            submitted_at = datetime.fromtimestamp(dir_mtime).isoformat()
                        except OSError:
                            submitted_at = now_iso

                        submission_id = execute_insert_returning_id(
                            conn,
                            """
                            INSERT INTO submissions
                                (assignment_id, student_pk_id, student_name,
                                 status, submitted_at, answers_json)
                            VALUES (?, ?, ?, 'submitted', ?, NULL)
                            """,
                            (assign_id, int(student_pk_id), student_name, submitted_at),
                        )
                        sub_info = {
                            "submission_id": submission_id,
                            "assignment_id": assign_id,
                            "student_pk_id": int(student_pk_id),
                            "course_id": int(course_id),
                        }
                        existing_submissions[sub_key] = sub_info
                        report.orphan_submissions_created += 1
                    else:
                        submission_id = sub_info["submission_id"]

                    # Check which files already have DB records
                    existing_files: set[str] = set()
                    for ef in conn.execute(
                        "SELECT relative_path FROM submission_files WHERE submission_id = ?",
                        (submission_id,),
                    ):
                        existing_files.add(str(_row_value(ef, "relative_path", 0, "") or "").replace("\\", "/"))

                    # Walk student directory and register missing files
                    for root, _dirs, files in os.walk(student_dir):
                        for fname in files:
                            full_path = Path(root) / fname
                            # Always use forward slashes for relative_path
                            rel_path = str(
                                full_path.relative_to(student_dir)
                            ).replace("\\", "/")

                            if rel_path in existing_files:
                                continue

                            file_size = full_path.stat().st_size
                            file_hash = _file_hash_sha256(full_path)
                            mime_type = _infer_mime_type(fname)
                            file_ext = Path(fname).suffix.lower()
                            stored_path_str = str(full_path)

                            conn.execute(
                                """
                                INSERT INTO submission_files
                                    (submission_id, original_filename, relative_path,
                                     stored_path, mime_type, file_size, file_ext,
                                     file_hash)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    submission_id,
                                    fname,
                                    rel_path,
                                    stored_path_str,
                                    mime_type,
                                    file_size,
                                    file_ext,
                                    file_hash,
                                ),
                            )
                            report.orphan_files_recovered += 1

                    report.orphan_directories_scanned += 1

        conn.commit()
    finally:
        if hasattr(conn, "row_factory"):
            conn.row_factory = original_factory

    report.finished_at = datetime.now().isoformat(timespec="seconds")
    return report


def _ensure_assignment_exists(
    conn: sqlite3.Connection,
    assign_id: str,
    course_id: str,
) -> None:
    """Create a placeholder assignment record if it does not exist yet."""
    existing = conn.execute(
        "SELECT id FROM assignments WHERE id = ?",
        (assign_id,),
    ).fetchone()
    if existing is not None:
        return

    # Validate course_id
    course = conn.execute(
        "SELECT id FROM courses WHERE id = ?",
        (int(course_id),),
    ).fetchone()
    if course is None:
        return

    # Handle both numeric and UUID-based assignment IDs
    try:
        numeric_id = int(assign_id)
    except ValueError:
        # Non-numeric (e.g. UUID) — skip auto-creation, we cannot
        # synthesize a valid PK for these.
        return

    conn.execute(
        """
        INSERT INTO assignments
            (id, course_id, title, status, requirements_md, rubric_md,
             grading_mode, created_at)
        VALUES (?, ?, ?, 'published', '', '', 'manual', ?)
        """,
        (
            numeric_id,
            int(course_id),
            f"Recovered assignment #{assign_id}",
            datetime.now().isoformat(),
        ),
    )


def _iter_numeric_dirs(parent: Path):
    """Yield subdirectories whose names are numeric."""
    if not parent.is_dir():
        return
    for entry in sorted(parent.iterdir()):
        if entry.is_dir() and entry.name.isdigit():
            yield entry


def _iter_dirs(parent: Path):
    """Yield all subdirectories, both numeric and non-numeric."""
    if not parent.is_dir():
        return
    for entry in sorted(parent.iterdir()):
        if entry.is_dir():
            yield entry


def run_full_alignment(conn: sqlite3.Connection) -> dict[str, Any]:
    """Run both stale-path repair and orphan-file recovery.  Returns a
    combined report dict.
    """
    stale_report = repair_stale_stored_paths(conn)
    orphan_report = recover_orphan_files(conn)

    return {
        "stale_path_repair": stale_report.to_dict(),
        "orphan_recovery": orphan_report.to_dict(),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
