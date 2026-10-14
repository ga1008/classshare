"""Seed a returned submission's answers and attachments into the next-round draft.

When a 错误提交 appeal is approved the student must be able to *edit* what they
handed in, not start from a blank page. The returned submission keeps its
answers/files untouched (it is the fallback if the window lapses); this module
copies them into the student's server draft for the new round, so both the
assignment page and the exam page restore them like any autosaved draft.

The draft's ``server_updated_at`` is set after the submission's ``returned_at``,
which is exactly what ``draft_matches_submission_round`` requires.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from ..config import HOMEWORK_SUBMISSIONS_DIR
from .submission_file_alignment import resolve_submission_file_path

EXAM_DRAWING_PREFIX = "exam_drawings/"
EXAM_QUESTION_FILE_PREFIX = "exam_question_files/"


def draft_storage_dir(course_id: Any, assignment_id: Any, student_pk_id: Any) -> Path:
    # Mirrors routers/homework_parts/common._build_submission_draft_storage_dir.
    return Path(HOMEWORK_SUBMISSIONS_DIR) / str(course_id) / str(assignment_id) / "__drafts__" / str(student_pk_id)


def _attachment_index(answers_json: Any) -> dict[str, dict[str, str]]:
    """relative_path(lower) -> {question_id, kind} from the submitted answers payload."""
    try:
        payload = json.loads(answers_json) if isinstance(answers_json, str) else (answers_json or {})
    except (TypeError, ValueError):
        return {}
    answers = payload.get("answers") if isinstance(payload, dict) else payload
    index: dict[str, dict[str, str]] = {}
    if not isinstance(answers, list):
        return index
    for item in answers:
        if not isinstance(item, dict):
            continue
        for attachment in item.get("attachments") or []:
            if not isinstance(attachment, dict):
                continue
            path = str(attachment.get("relative_path") or "").strip().lower()
            if path:
                index[path] = {
                    "question_id": str(attachment.get("question_id") or item.get("question_id") or ""),
                    "kind": str(attachment.get("kind") or "file"),
                }
    return index


def _infer_question_and_kind(relative_path: str, index: Mapping[str, Mapping[str, str]]) -> tuple[str, str]:
    known = index.get(relative_path.lower())
    if known:
        return known.get("question_id") or "", known.get("kind") or "file"
    parts = PurePosixPath(relative_path).parts
    if relative_path.startswith(EXAM_QUESTION_FILE_PREFIX) and len(parts) >= 3:
        return parts[1], "file"
    if relative_path.startswith(EXAM_DRAWING_PREFIX):
        stem = PurePosixPath(relative_path).stem
        return stem.split("_", 1)[0], "drawing"
    return "", "file"


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    try:
        os.link(source, target)  # same volume: no extra space
    except OSError:
        shutil.copy2(source, target)


def seed_resubmission_draft(conn, *, assignment: Mapping[str, Any], submission: Mapping[str, Any]) -> dict[str, Any]:
    """Copy answers + files of ``submission`` into its student's server draft.

    Runs inside the caller's transaction. Returns ``{"answers": bool, "files": n,
    "missing_files": n}``; a missing physical file is skipped (and counted)
    rather than failing the approval.
    """
    assignment_id = str(assignment["id"])
    student_pk_id = int(submission["student_pk_id"])
    now = datetime.now().isoformat()
    answers_json = str(submission.get("answers_json") or "")

    existing = conn.execute(
        "SELECT id FROM submission_drafts WHERE assignment_id = ? AND student_pk_id = ? LIMIT 1",
        (assignment_id, student_pk_id),
    ).fetchone()
    if existing:
        draft_id = int(existing["id"])
        # Only the rows go: deleting a stale round's files inside this
        # transaction would lose them if the approval rolls back. The seeded
        # copies below overwrite same-path files; others stay as harmless leftovers.
        conn.execute("DELETE FROM submission_draft_files WHERE draft_id = ?", (draft_id,))
        conn.execute(
            """
            UPDATE submission_drafts
            SET answers_json = ?, current_page = 0, client_updated_at = ?, server_updated_at = ?,
                server_version = COALESCE(server_version, 0) + 1, status = 'active'
            WHERE id = ?
            """,
            (answers_json, now, now, draft_id),
        )
    else:
        from ..db.connection import execute_insert_returning_id

        draft_id = int(execute_insert_returning_id(
            conn,
            """
            INSERT INTO submission_drafts (
                assignment_id, student_pk_id, answers_json, current_page,
                client_updated_at, server_updated_at, server_version, status
            ) VALUES (?, ?, ?, 0, ?, ?, 1, 'active')
            """,
            (assignment_id, student_pk_id, answers_json, now, now),
        ))

    index = _attachment_index(answers_json)
    draft_dir = draft_storage_dir(assignment["course_id"], assignment_id, student_pk_id)
    copied = missing = 0
    files = conn.execute(
        "SELECT * FROM submission_files WHERE submission_id = ? ORDER BY id",
        (int(submission["id"]),),
    ).fetchall()
    for row in files:
        file_row = dict(row)
        relative_path = str(file_row.get("relative_path") or file_row.get("original_filename") or "").replace("\\", "/").strip("/")
        source_text = resolve_submission_file_path(str(file_row.get("stored_path") or "")) or str(file_row.get("stored_path") or "")
        source = Path(source_text)
        if not relative_path or not source.is_file():
            missing += 1
            continue
        target = draft_dir.joinpath(*PurePosixPath(relative_path).parts)
        _copy_file(source, target)
        question_id, kind = _infer_question_and_kind(relative_path, index)
        conn.execute(
            """
            INSERT INTO submission_draft_files (
                draft_id, question_id, kind, original_filename, relative_path,
                stored_path, mime_type, file_size, file_ext, file_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (draft_id, question_id, kind, file_row.get("original_filename") or target.name, relative_path,
             str(target), file_row.get("mime_type"), file_row.get("file_size"), file_row.get("file_ext"),
             file_row.get("file_hash"), now),
        )
        copied += 1
    return {"answers": bool(answers_json.strip()), "files": copied, "missing_files": missing, "draft_id": draft_id}
