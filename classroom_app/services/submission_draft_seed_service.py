"""Seed a returned submission's answers and attachments into the next-round draft.

When a teacher returns work or approves a 错误提交 appeal, the student can edit what they
handed in, not start from a blank page. The returned submission keeps its
answers/files untouched (it is the fallback if the window lapses); this module
copies them into the student's server draft for the new round, so both the
assignment page and the exam page restore them like any autosaved draft.

The draft's ``server_updated_at`` is set after the submission's ``returned_at``,
which is exactly what ``draft_matches_submission_round`` requires.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from ..config import HOMEWORK_SUBMISSIONS_DIR, HOMEWORK_SUBMISSIONS_LEGACY_DIRS
from .submission_file_alignment import resolve_submission_file_path
from .submission_write_guard import draft_matches_submission_round

EXAM_DRAWING_PREFIX = "exam_drawings/"
EXAM_QUESTION_FILE_PREFIX = "exam_question_files/"
_ROLLBACK_CLEANUP: ContextVar[ExitStack | None] = ContextVar("resubmission_draft_rollback_cleanup", default=None)


@contextmanager
def resubmission_draft_rollback_scope(cleanup: ExitStack):
    """Bind approval-handler copies to the HTTP caller's commit/rollback scope."""
    token = _ROLLBACK_CLEANUP.set(cleanup)
    try:
        yield
    finally:
        _ROLLBACK_CLEANUP.reset(token)


def draft_storage_dir(course_id: Any, assignment_id: Any, student_pk_id: Any) -> Path:
    # Mirrors routers/homework_parts/common._build_submission_draft_storage_dir.
    return Path(HOMEWORK_SUBMISSIONS_DIR) / str(course_id) / str(assignment_id) / "__drafts__" / str(student_pk_id)


def _attachment_index(answers_json: Any) -> dict[str, dict[str, str]]:
    """relative_path(lower) -> {question_id, kind} from the submitted answers payload."""
    try:
        payload = json.loads(answers_json) if isinstance(answers_json, str) else (answers_json or {})
    except (TypeError, ValueError):
        return {}
    answers = payload.get("answers", payload) if isinstance(payload, dict) else payload
    index: dict[str, dict[str, str]] = {}
    if isinstance(answers, dict):
        answers = [{**value, "question_id": str(key)} for key, value in answers.items() if isinstance(value, dict)]
    if not isinstance(answers, list):
        return index
    for item in answers:
        if not isinstance(item, dict):
            continue
        attachments = item.get("attachments")
        for attachment in attachments if isinstance(attachments, list) else []:
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
    # The draft can later be replaced independently of the original submission.
    shutil.copy2(source, target)


def _seed_timestamp(submission: Mapping[str, Any], *, prefer_returned_at: bool = False) -> str:
    now = datetime.now()
    try:
        returned = datetime.fromisoformat(str(submission.get("returned_at") or "").replace("Z", "+00:00"))
        if returned.tzinfo:
            returned = returned.astimezone().replace(tzinfo=None)
        earliest = returned + timedelta(microseconds=1) if returned.microsecond else returned + timedelta(seconds=1)
        now = earliest if prefer_returned_at else max(now, earliest)
    except ValueError:
        pass
    return now.isoformat()


def seed_resubmission_draft(conn, *, assignment: Mapping[str, Any], submission: Mapping[str, Any],
                            previous_submission: Mapping[str, Any] | None = None,
                            rollback_cleanup: ExitStack | None = None,
                            prefer_returned_at: bool = False) -> dict[str, Any]:
    """Copy answers + files of ``submission`` into its student's server draft.

    Runs inside the caller's transaction. Returns ``{"answers": bool, "files": n,
    "missing_files": n}``; a missing physical file is skipped (and counted)
    rather than failing the approval.
    """
    assignment_id = str(assignment["id"])
    rollback_cleanup = rollback_cleanup if rollback_cleanup is not None else _ROLLBACK_CLEANUP.get()
    student_pk_id = int(submission["student_pk_id"])
    # A lazy recovery represents the returned original, not a new student edit.
    # Preserve the precedence of local answers written after that return.
    now = _seed_timestamp(submission, prefer_returned_at=prefer_returned_at)
    answers_json = str(submission.get("answers_json") or "")

    existing = conn.execute(
        "SELECT * FROM submission_drafts WHERE assignment_id = ? AND student_pk_id = ? LIMIT 1",
        (assignment_id, student_pk_id),
    ).fetchone()
    # A repeated return extends the window, never discards edits made in it.
    previous = dict(previous_submission or {})
    same_round = existing and draft_matches_submission_round(existing, submission)
    previous_round = existing and previous.get("resubmission_allowed") and draft_matches_submission_round(existing, previous)
    if same_round or previous_round:
        if not same_round:
            conn.execute("UPDATE submission_drafts SET server_updated_at=?, server_version=COALESCE(server_version,0)+1 WHERE id=?",
                         (now, int(existing["id"])))
        return {"answers": bool(str(existing["answers_json"] or "").strip()), "files": 0,
                "missing_files": 0, "draft_id": int(existing["id"]), "preserved": True}
    if existing:
        draft_id = int(existing["id"])
        # Keep old physical files intact until the caller's transaction succeeds.
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
    if not draft_dir.resolve().is_relative_to(Path(HOMEWORK_SUBMISSIONS_DIR).resolve()):
        raise ValueError("Draft storage path escapes submission storage")
    copied = missing = 0
    files = conn.execute(
        "SELECT * FROM submission_files WHERE submission_id = ? ORDER BY id",
        (int(submission["id"]),),
    ).fetchall()
    # Every seed owns a new directory: rollback must never overwrite or unlink
    # the previous draft's files. Teacher callers attach its cleanup to commit.
    owned_dir = None
    with ExitStack() as failed_seed:
        for row in files:
            file_row = dict(row)
            relative_path = str(file_row.get("relative_path") or file_row.get("original_filename") or "").replace("\\", "/")
            parts = PurePosixPath(relative_path).parts
            # Never turn an absolute, traversal or Windows drive/ADS path into a
            # valid relative upload path merely by trimming its leading slash.
            if not parts or relative_path.startswith("/") or ".." in parts or any(":" in part or "\x00" in part for part in parts):
                missing += 1
                continue
            source_text = resolve_submission_file_path(str(file_row.get("stored_path") or ""))
            if not source_text:
                fallback = Path(str(file_row.get("stored_path") or ""))
                if fallback.resolve().is_relative_to(Path(HOMEWORK_SUBMISSIONS_DIR).resolve()):
                    source_text = str(fallback)
            if not source_text:
                missing += 1
                continue
            source = Path(source_text)
            permitted_roots = (HOMEWORK_SUBMISSIONS_DIR, *HOMEWORK_SUBMISSIONS_LEGACY_DIRS)
            if not any(source.resolve().is_relative_to(Path(root).resolve()) for root in permitted_roots) or not source.is_file():
                missing += 1
                continue
            if owned_dir is None:
                draft_dir.mkdir(parents=True, exist_ok=True)
                owned_dir = Path(tempfile.mkdtemp(prefix="returned-", dir=draft_dir))
                failed_seed.callback(shutil.rmtree, owned_dir)
            target = owned_dir.joinpath(*parts)
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
        if owned_dir is not None and rollback_cleanup is not None:
            rollback_cleanup.callback(shutil.rmtree, owned_dir)
        failed_seed.pop_all()
    return {"answers": bool(answers_json.strip()), "files": copied, "missing_files": missing, "draft_id": draft_id}
