"""Per-assignment screenshot hash guard.

Every attachment a student uploads already carries a SHA-256 digest
(``submission_files.file_hash`` for final submissions,
``submission_draft_files.file_hash`` for server drafts).  The per-assignment
"hash library" is therefore *derived* from those two tables instead of being
kept in a separate registry: a withdrawn submission deletes its file rows and
frees the hash, a resubmission replaces them, and a draft that is cleared
releases its claims — all by construction, with nothing that can drift.

Only images are guarded (screenshots are what gets copied between students);
exam drawings are excluded because an empty canvas exports identical bytes for
everyone.
"""

from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Iterable, Sequence

HASH_GUARDED_MIME_PREFIX = "image/"
HASH_GUARDED_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".heif", ".tif", ".tiff", ".avif"}
)
EXAM_DRAWING_PATH_PREFIX = "exam_drawings/"
EXAM_DRAWING_KINDS = frozenset({"exam_drawing", "drawing"})
QUESTION_FILES_PREFIX = "exam_question_files"
WHOLE_ASSIGNMENT_SCOPE_LABEL = "本次作业"
MAX_HASH_CHECK_ITEMS = 200


@dataclass(slots=True)
class HashClaim:
    file_hash: str
    student_pk_id: int
    student_name: str
    question_id: str
    file_name: str
    relative_path: str
    source: str  # "submission" | "draft"
    row_id: int


@dataclass(slots=True)
class HashCandidate:
    file_hash: str
    question_id: str = ""
    relative_path: str = ""
    file_name: str = ""


@dataclass(slots=True)
class HashConflict:
    kind: str  # "other" | "self" | "batch"
    file_hash: str
    relative_path: str
    file_name: str
    question_id: str
    question_label: str
    owner_student_pk_id: int | None
    owner_student_name: str
    owner_question_id: str
    owner_question_label: str
    owner_file_name: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": "duplicate_image",
            "kind": self.kind,
            "file_hash": self.file_hash,
            "relative_path": self.relative_path,
            "file_name": self.file_name,
            "question_id": self.question_id,
            "question_label": self.question_label,
            "owner_student_pk_id": self.owner_student_pk_id,
            "owner_student_name": self.owner_student_name,
            "owner_question_id": self.owner_question_id,
            "owner_question_label": self.owner_question_label,
            "owner_file_name": self.owner_file_name,
            "message": self.message,
        }


@dataclass(slots=True)
class HashCheckOptions:
    """Which of the student's own claims still count when checking candidates."""

    include_own_draft: bool = True
    replaced_question_ids: set[str] = field(default_factory=set)
    replaced_relative_paths: set[str] = field(default_factory=set)


def normalize_file_hash(value: Any) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64:
        return ""
    try:
        int(text, 16)
    except ValueError:
        return ""
    return text


def _normalize_relative_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().strip("/")


def question_id_from_relative_path(relative_path: str) -> str:
    normalized = _normalize_relative_path(relative_path)
    if not normalized:
        return ""
    parts = PurePosixPath(normalized).parts
    if len(parts) >= 3 and parts[0] == QUESTION_FILES_PREFIX:
        return str(parts[1] or "").strip()
    return ""


def display_file_name(relative_path: str, fallback: str = "") -> str:
    normalized = _normalize_relative_path(relative_path)
    if normalized:
        return PurePosixPath(normalized).name or normalized
    return str(fallback or "").strip() or "附件"


def is_hash_guarded_attachment(relative_path: str, mime_type: str | None = None, kind: str | None = None) -> bool:
    normalized_path = _normalize_relative_path(relative_path)
    if str(kind or "").strip().lower() in EXAM_DRAWING_KINDS:
        return False
    if normalized_path.lower().startswith(EXAM_DRAWING_PATH_PREFIX):
        return False
    suffix = PurePosixPath(normalized_path).suffix.lower()
    if suffix in HASH_GUARDED_EXTENSIONS:
        return True
    normalized_mime = str(mime_type or "").strip().lower()
    if not normalized_mime and normalized_path:
        normalized_mime = str(mimetypes.guess_type(normalized_path)[0] or "").lower()
    return normalized_mime.startswith(HASH_GUARDED_MIME_PREFIX)


def load_assignment_question_labels(conn, assignment: dict[str, Any] | None) -> dict[str, str]:
    """Map exam question ids to the human label shown to students (第N题)."""
    if not assignment:
        return {}
    paper_id = assignment.get("exam_paper_id")
    if not paper_id:
        return {}
    paper = conn.execute("SELECT questions_json FROM exam_papers WHERE id = ?", (paper_id,)).fetchone()
    if not paper or not paper["questions_json"]:
        return {}
    try:
        paper_data = json.loads(paper["questions_json"])
    except (TypeError, json.JSONDecodeError):
        return {}
    labels: dict[str, str] = {}
    index = 0
    pages = paper_data.get("pages", []) if isinstance(paper_data, dict) else []
    for page in pages:
        if not isinstance(page, dict):
            continue
        for question in page.get("questions", []) or []:
            if not isinstance(question, dict):
                continue
            index += 1
            qid = str(question.get("id") or "").strip()
            if qid:
                labels[qid] = f"第{index}题"
    return labels


def question_label(question_id: str, labels: dict[str, str] | None) -> str:
    qid = str(question_id or "").strip()
    if not qid:
        return WHOLE_ASSIGNMENT_SCOPE_LABEL
    if labels and qid in labels:
        return labels[qid]
    return f"题目 {qid}"


def lock_assignment_image_guard(conn, assignment_id: str) -> None:
    """Serialize hash checks for one assignment across students.

    The per-student submission lock does not stop two *different* students
    from reading "no claim yet" for the same screenshot in the same instant.
    On PostgreSQL take a transaction-scoped advisory lock keyed by the
    assignment (released at commit/rollback); SQLite callers already hold
    ``BEGIN IMMEDIATE`` which serializes all writers database-wide.
    """
    try:
        from ..db.connection import get_configured_db_engine
    except Exception:  # pragma: no cover - defensive import guard
        return
    if get_configured_db_engine() != "postgres":
        return
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext(?))",
        (f"assignment-image-guard:{assignment_id}",),
    )


def load_assignment_hash_claims(conn, assignment_id: str) -> list[HashClaim]:
    """Every guarded image hash currently held by any student for this assignment."""
    claims: list[HashClaim] = []
    submission_rows = conn.execute(
        """
        SELECT sf.id AS row_id, sf.file_hash, sf.relative_path, sf.original_filename, sf.mime_type,
               s.student_pk_id, COALESCE(st.name, s.student_name, '') AS student_name
        FROM submission_files sf
        JOIN submissions s ON s.id = sf.submission_id
        LEFT JOIN students st ON st.id = s.student_pk_id
        WHERE s.assignment_id = ?
          AND sf.file_hash IS NOT NULL AND sf.file_hash <> ''
        """,
        (str(assignment_id),),
    ).fetchall()
    for row in submission_rows:
        item = dict(row)
        file_hash = normalize_file_hash(item.get("file_hash"))
        relative_path = _normalize_relative_path(item.get("relative_path") or item.get("original_filename"))
        if not file_hash or not is_hash_guarded_attachment(relative_path, item.get("mime_type")):
            continue
        claims.append(
            HashClaim(
                file_hash=file_hash,
                student_pk_id=int(item.get("student_pk_id") or 0),
                student_name=str(item.get("student_name") or ""),
                question_id=question_id_from_relative_path(relative_path),
                file_name=display_file_name(relative_path, item.get("original_filename") or ""),
                relative_path=relative_path,
                source="submission",
                row_id=int(item.get("row_id") or 0),
            )
        )

    draft_rows = conn.execute(
        """
        SELECT sdf.id AS row_id, sdf.file_hash, sdf.relative_path, sdf.original_filename, sdf.mime_type,
               sdf.kind, sdf.question_id,
               sd.student_pk_id, COALESCE(st.name, '') AS student_name
        FROM submission_draft_files sdf
        JOIN submission_drafts sd ON sd.id = sdf.draft_id
        LEFT JOIN students st ON st.id = sd.student_pk_id
        WHERE sd.assignment_id = ?
          AND sdf.file_hash IS NOT NULL AND sdf.file_hash <> ''
        """,
        (str(assignment_id),),
    ).fetchall()
    for row in draft_rows:
        item = dict(row)
        file_hash = normalize_file_hash(item.get("file_hash"))
        relative_path = _normalize_relative_path(item.get("relative_path") or item.get("original_filename"))
        if not file_hash or not is_hash_guarded_attachment(relative_path, item.get("mime_type"), item.get("kind")):
            continue
        question_id = str(item.get("question_id") or "").strip() or question_id_from_relative_path(relative_path)
        claims.append(
            HashClaim(
                file_hash=file_hash,
                student_pk_id=int(item.get("student_pk_id") or 0),
                student_name=str(item.get("student_name") or ""),
                question_id=question_id,
                file_name=display_file_name(relative_path, item.get("original_filename") or ""),
                relative_path=relative_path,
                source="draft",
                row_id=int(item.get("row_id") or 0),
            )
        )
    return claims


def _other_student_message(candidate_name: str, owner_name: str, owner_label: str) -> str:
    owner = f"{owner_name} 同学" if owner_name else "其他同学"
    return f"「{candidate_name}」与 {owner} 已上传的截图完全相同（{owner_label}），请重新选取其他截图。"


def _self_message(candidate_name: str, owner_label: str, owner_file_name: str) -> str:
    detail = f"，文件名「{owner_file_name}」" if owner_file_name and owner_file_name != candidate_name else ""
    return f"「{candidate_name}」已在{owner_label}上传过{detail}，请勿重复上传。"


def _batch_message(candidate_name: str, first_name: str, first_label: str) -> str:
    return f"「{candidate_name}」与本次上传的「{first_name}」是同一张截图（{first_label}），请勿重复上传。"


def find_image_hash_conflicts(
    conn,
    *,
    assignment_id: str,
    student_pk_id: int,
    candidates: Sequence[HashCandidate],
    question_labels: dict[str, str] | None = None,
    options: HashCheckOptions | None = None,
    claims: Iterable[HashClaim] | None = None,
) -> list[HashConflict]:
    """Return one conflict per candidate that must not be stored.

    Priority per candidate: another student's claim beats a self claim, which
    beats a duplicate inside the same batch.  Candidates without a valid hash
    are ignored (they cannot be checked, and they are never images we care
    about once the storage layer has hashed them).
    """
    options = options or HashCheckOptions()
    replaced_questions = {str(qid or "").strip() for qid in options.replaced_question_ids}
    replaced_paths = {_normalize_relative_path(path).lower() for path in options.replaced_relative_paths}
    all_claims = list(claims) if claims is not None else load_assignment_hash_claims(conn, assignment_id)
    claims_by_hash: dict[str, list[HashClaim]] = {}
    for claim in all_claims:
        claims_by_hash.setdefault(claim.file_hash, []).append(claim)

    conflicts: list[HashConflict] = []
    seen_in_batch: dict[str, HashCandidate] = {}
    for candidate in candidates:
        file_hash = normalize_file_hash(candidate.file_hash)
        if not file_hash:
            continue
        candidate_path = _normalize_relative_path(candidate.relative_path)
        candidate_name = candidate.file_name or display_file_name(candidate_path)
        candidate_qid = str(candidate.question_id or "").strip() or question_id_from_relative_path(candidate_path)
        candidate_label = question_label(candidate_qid, question_labels)

        other_claim: HashClaim | None = None
        self_claim: HashClaim | None = None
        for claim in claims_by_hash.get(file_hash, []):
            if claim.student_pk_id != int(student_pk_id):
                other_claim = other_claim or claim
                continue
            if claim.source != "draft" or not options.include_own_draft:
                continue
            if claim.question_id in replaced_questions:
                continue
            if candidate_path and claim.relative_path.lower() == candidate_path.lower():
                continue
            if claim.relative_path.lower() in replaced_paths:
                continue
            self_claim = self_claim or claim

        if other_claim is not None:
            owner_label = question_label(other_claim.question_id, question_labels)
            conflicts.append(
                HashConflict(
                    kind="other",
                    file_hash=file_hash,
                    relative_path=candidate_path,
                    file_name=candidate_name,
                    question_id=candidate_qid,
                    question_label=candidate_label,
                    owner_student_pk_id=other_claim.student_pk_id,
                    owner_student_name=other_claim.student_name,
                    owner_question_id=other_claim.question_id,
                    owner_question_label=owner_label,
                    owner_file_name=other_claim.file_name,
                    message=_other_student_message(candidate_name, other_claim.student_name, owner_label),
                )
            )
            continue

        if self_claim is not None:
            owner_label = question_label(self_claim.question_id, question_labels)
            conflicts.append(
                HashConflict(
                    kind="self",
                    file_hash=file_hash,
                    relative_path=candidate_path,
                    file_name=candidate_name,
                    question_id=candidate_qid,
                    question_label=candidate_label,
                    owner_student_pk_id=int(student_pk_id),
                    owner_student_name=self_claim.student_name,
                    owner_question_id=self_claim.question_id,
                    owner_question_label=owner_label,
                    owner_file_name=self_claim.file_name,
                    message=_self_message(candidate_name, owner_label, self_claim.file_name),
                )
            )
            continue

        first = seen_in_batch.get(file_hash)
        if first is not None:
            first_path = _normalize_relative_path(first.relative_path)
            if not candidate_path or first_path.lower() != candidate_path.lower():
                first_qid = str(first.question_id or "").strip() or question_id_from_relative_path(first_path)
                first_label = question_label(first_qid, question_labels)
                first_name = first.file_name or display_file_name(first_path)
                conflicts.append(
                    HashConflict(
                        kind="batch",
                        file_hash=file_hash,
                        relative_path=candidate_path,
                        file_name=candidate_name,
                        question_id=candidate_qid,
                        question_label=candidate_label,
                        owner_student_pk_id=int(student_pk_id),
                        owner_student_name="",
                        owner_question_id=first_qid,
                        owner_question_label=first_label,
                        owner_file_name=first_name,
                        message=_batch_message(candidate_name, first_name, first_label),
                    )
                )
                continue
        seen_in_batch.setdefault(file_hash, candidate)
    return conflicts


def candidates_from_stored_files(stored_files: Iterable[Any], question_ids: dict[str, str] | None = None) -> list[HashCandidate]:
    """Build guard candidates from ``StoredSubmissionFile`` objects (or dict rows)."""
    candidates: list[HashCandidate] = []
    lookup = {str(key or "").lower(): str(value or "") for key, value in (question_ids or {}).items()}
    for info in stored_files:
        if isinstance(info, dict):
            relative_path = str(info.get("relative_path") or "")
            mime_type = str(info.get("mime_type") or "")
            file_hash = str(info.get("file_hash") or "")
            original = str(info.get("original_filename") or "")
            kind = str(info.get("kind") or "")
        else:
            relative_path = str(getattr(info, "relative_path", "") or "")
            mime_type = str(getattr(info, "mime_type", "") or "")
            file_hash = str(getattr(info, "file_hash", "") or "")
            original = str(getattr(info, "original_filename", "") or "")
            kind = str(getattr(info, "kind", "") or "")
        if not is_hash_guarded_attachment(relative_path, mime_type, kind):
            continue
        normalized_hash = normalize_file_hash(file_hash)
        if not normalized_hash:
            continue
        question_id = lookup.get(_normalize_relative_path(relative_path).lower(), "") or question_id_from_relative_path(relative_path)
        candidates.append(
            HashCandidate(
                file_hash=normalized_hash,
                question_id=question_id,
                relative_path=_normalize_relative_path(relative_path),
                file_name=display_file_name(relative_path, original),
            )
        )
    return candidates


def parse_hash_check_items(raw_items: Any) -> list[HashCandidate]:
    """Validate the JSON body of the client pre-check endpoint."""
    if not isinstance(raw_items, list):
        return []
    candidates: list[HashCandidate] = []
    for item in raw_items[:MAX_HASH_CHECK_ITEMS]:
        if not isinstance(item, dict):
            continue
        file_hash = normalize_file_hash(item.get("hash") or item.get("file_hash"))
        if not file_hash:
            continue
        relative_path = _normalize_relative_path(item.get("relative_path") or "")
        file_name = str(item.get("file_name") or item.get("name") or "").strip()
        candidates.append(
            HashCandidate(
                file_hash=file_hash,
                question_id=str(item.get("question_id") or "").strip(),
                relative_path=relative_path,
                file_name=file_name or display_file_name(relative_path),
            )
        )
    return candidates


def duplicate_image_error_detail(conflicts: Sequence[HashConflict], *, action_label: str) -> dict[str, Any]:
    """HTTP error payload understood by ``collectUploadIssueMessage`` on the client."""
    messages = [conflict.message for conflict in conflicts if conflict.message]
    preview = "；".join(messages[:3])
    remaining = len(messages) - 3
    if remaining > 0:
        preview = f"{preview}；还有 {remaining} 张截图也重复。"
    return {
        "code": "duplicate_image",
        "message": f"{action_label}失败：{preview}" if preview else f"{action_label}失败：存在重复截图。",
        "duplicate_file_count": len(conflicts),
        "duplicate_files": [conflict.to_dict() for conflict in conflicts],
        "dropped_file_count": 0,
        "dropped_files": [],
    }
