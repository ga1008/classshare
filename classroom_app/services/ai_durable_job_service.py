from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import uuid
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..database import get_db_connection
from ..config import DATA_DIR
from ..db.connection import get_configured_db_engine
from ..db.schema_ai_jobs import ensure_ai_job_schema


JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_RETRY_WAIT = "retry_wait"
JOB_RESULT_READY = "result_ready"
JOB_SUCCEEDED = "succeeded"
JOB_REVIEW_REQUIRED = "review_required"
JOB_REJECTED = "rejected"
JOB_DEAD_LETTER = "dead_letter"
JOB_CANCELLED = "cancelled"
JOB_SUPERSEDED = "superseded"

ACTIVE_JOB_STATUSES = {JOB_QUEUED, JOB_RUNNING, JOB_RETRY_WAIT, JOB_RESULT_READY}
TERMINAL_JOB_STATUSES = {
    JOB_SUCCEEDED,
    JOB_REVIEW_REQUIRED,
    JOB_REJECTED,
    JOB_DEAD_LETTER,
    JOB_CANCELLED,
    JOB_SUPERSEDED,
}


@dataclass(frozen=True, slots=True)
class AIDurableTaskPolicy:
    task_type: str
    priority: int
    max_attempts: int
    lease_seconds: int
    failure_terminal: str


TASK_POLICIES: dict[str, AIDurableTaskPolicy] = {
    "ai_grading": AIDurableTaskPolicy("ai_grading", 10, 8, 900, JOB_REVIEW_REQUIRED),
    "exam_generation": AIDurableTaskPolicy("exam_generation", 20, 6, 900, JOB_REVIEW_REQUIRED),
    "document_import": AIDurableTaskPolicy("document_import", 40, 5, 600, JOB_DEAD_LETTER),
    "document_generation": AIDurableTaskPolicy("document_generation", 30, 6, 900, JOB_REVIEW_REQUIRED),
}
DEFAULT_TASK_POLICY = AIDurableTaskPolicy("generic", 100, 5, 600, JOB_DEAD_LETTER)

MAX_ERROR_CHARS = 1200
AI_JOB_ARTIFACT_MAX_BYTES = max(1, int(os.getenv("AI_JOB_ARTIFACT_MAX_MB", "40"))) * 1024 * 1024
BACKOFF_SECONDS = (5, 20, 60, 300, 900, 1800, 3600, 7200)


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_error(value: Any) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:MAX_ERROR_CHARS]


def durable_task_policy(task_type: str) -> AIDurableTaskPolicy:
    normalized = str(task_type or "").strip()
    return TASK_POLICIES.get(normalized, DEFAULT_TASK_POLICY)


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()


def _artifact_root() -> Path:
    return (Path(DATA_DIR) / "ai_job_artifacts").resolve()


def persist_ai_job_artifact(artifact_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    safe_key = "".join(ch for ch in str(artifact_key or "") if ch.isalnum() or ch in {"-", "_"})[:80]
    if not safe_key:
        raise ValueError("artifact_key is required")
    encoded = _json_dumps(payload).encode("utf-8")
    if len(encoded) > AI_JOB_ARTIFACT_MAX_BYTES:
        raise ValueError(
            f"AI job artifact exceeds {AI_JOB_ARTIFACT_MAX_BYTES // (1024 * 1024)}MB limit"
        )
    root = _artifact_root()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / f"{safe_key}.json").resolve()
    if root not in target.parents:
        raise ValueError("unsafe AI job artifact path")
    temp = target.with_suffix(f".tmp-{uuid.uuid4().hex}")
    temp.write_bytes(encoded)
    temp.replace(target)
    return {
        "relative_path": target.relative_to(Path(DATA_DIR).resolve()).as_posix(),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "size_bytes": len(encoded),
    }


def load_ai_job_artifact(reference: dict[str, Any]) -> dict[str, Any]:
    relative = str(reference.get("relative_path") or "").strip().replace("\\", "/")
    if not relative:
        return {}
    root = Path(DATA_DIR).resolve()
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise FileNotFoundError("AI job artifact is missing")
    encoded = path.read_bytes()
    if len(encoded) > AI_JOB_ARTIFACT_MAX_BYTES:
        raise ValueError("AI job artifact exceeds configured limit")
    expected_hash = str(reference.get("sha256") or "").strip().lower()
    actual_hash = hashlib.sha256(encoded).hexdigest()
    if expected_hash and actual_hash != expected_hash:
        raise ValueError("AI job artifact hash mismatch")
    parsed = json.loads(encoded.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("AI job artifact must be a JSON object")
    return parsed


def cleanup_ai_job_artifact(reference: dict[str, Any]) -> None:
    relative = str(reference.get("relative_path") or "").strip().replace("\\", "/")
    if not relative:
        return
    data_root = Path(DATA_DIR).resolve()
    path = (data_root / relative).resolve()
    if data_root not in path.parents:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def persist_ai_job_input_files(artifact_key: str, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist uploaded source files in protected storage for restart-safe jobs."""

    safe_key = "".join(ch for ch in str(artifact_key or "") if ch.isalnum() or ch in {"-", "_"})[:80]
    if not safe_key:
        raise ValueError("artifact_key is required")
    total = sum(len(item.get("data") or b"") for item in files)
    if total <= 0:
        raise ValueError("at least one non-empty input file is required")
    if total > AI_JOB_ARTIFACT_MAX_BYTES:
        raise ValueError(
            f"AI job input files exceed {AI_JOB_ARTIFACT_MAX_BYTES // (1024 * 1024)}MB limit"
        )
    data_root = Path(DATA_DIR).resolve()
    root = (data_root / "ai_job_inputs" / safe_key).resolve()
    if data_root not in root.parents:
        raise ValueError("unsafe AI job input directory")
    root.mkdir(parents=True, exist_ok=True)
    references: list[dict[str, Any]] = []
    for index, item in enumerate(files):
        content = item.get("data") or b""
        if not isinstance(content, bytes) or not content:
            continue
        original_name = Path(str(item.get("name") or f"file_{index}")).name[:180]
        suffix = Path(original_name).suffix.lower()[:16]
        target = (root / f"{index:02d}-{uuid.uuid4().hex}{suffix}").resolve()
        if root not in target.parents:
            raise ValueError("unsafe AI job input path")
        temp = target.with_suffix(target.suffix + f".tmp-{uuid.uuid4().hex}")
        temp.write_bytes(content)
        temp.replace(target)
        references.append(
            {
                "relative_path": target.relative_to(data_root).as_posix(),
                "name": original_name,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
    if not references:
        raise ValueError("at least one non-empty input file is required")
    return references


def load_ai_job_input_files(references: list[dict[str, Any]]) -> list[dict[str, str]]:
    data_root = Path(DATA_DIR).resolve()
    loaded: list[dict[str, str]] = []
    for reference in references:
        relative = str(reference.get("relative_path") or "").strip().replace("\\", "/")
        path = (data_root / relative).resolve()
        if not relative or data_root not in path.parents or not path.is_file():
            raise FileNotFoundError("AI job input file is missing")
        content = path.read_bytes()
        if len(content) != int(reference.get("size_bytes") or -1):
            raise ValueError("AI job input file size mismatch")
        expected = str(reference.get("sha256") or "").strip().lower()
        if expected and hashlib.sha256(content).hexdigest() != expected:
            raise ValueError("AI job input file hash mismatch")
        loaded.append({"path": str(path), "name": Path(str(reference.get("name") or path.name)).name})
    return loaded


def cleanup_ai_job_input_files(references: list[dict[str, Any]]) -> None:
    data_root = Path(DATA_DIR).resolve()
    parents: set[Path] = set()
    for reference in references:
        relative = str(reference.get("relative_path") or "").strip().replace("\\", "/")
        path = (data_root / relative).resolve()
        if not relative or data_root not in path.parents:
            continue
        parents.add(path.parent)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    for parent in sorted(parents, key=lambda item: len(item.parts), reverse=True):
        try:
            parent.rmdir()
        except OSError:
            pass


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def create_ai_job(
    conn,
    *,
    task_type: str,
    dedupe_key: str,
    payload: dict[str, Any],
    priority: int | None = None,
    max_attempts: int | None = None,
    scope_type: str = "",
    scope_id: str = "",
    owner_role: str = "",
    owner_user_pk: int | None = None,
    source_ref: str = "",
    input_schema_version: str = "v1",
    policy_version: str = "",
) -> tuple[dict[str, Any], bool]:
    ensure_ai_job_schema(conn)
    normalized_type = str(task_type or "").strip()
    normalized_dedupe = str(dedupe_key or "").strip()
    if not normalized_type:
        raise ValueError("task_type is required")
    if not normalized_dedupe:
        raise ValueError("dedupe_key is required")
    policy = durable_task_policy(normalized_type)
    now = _iso()
    params = (
        normalized_type,
        int(priority if priority is not None else policy.priority),
        normalized_dedupe,
        _json_dumps(payload),
        canonical_payload_hash(payload),
        str(input_schema_version or "v1"),
        str(policy_version or ""),
        str(scope_type or ""),
        str(scope_id or ""),
        str(owner_role or ""),
        owner_user_pk,
        str(source_ref or ""),
        int(max_attempts if max_attempts is not None else policy.max_attempts),
        now,
        now,
        now,
    )
    engine = get_configured_db_engine()
    created = False
    if engine == "postgres":
        row = conn.execute(
            """
            INSERT INTO ai_jobs (
                task_type, priority, status, dedupe_key, payload_json, payload_hash,
                input_schema_version, policy_version, scope_type, scope_id,
                owner_role, owner_user_pk, source_ref, max_attempts,
                available_at, created_at, updated_at
            )
            VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (dedupe_key) DO NOTHING
            RETURNING *
            """,
            params,
        ).fetchone()
        created = row is not None
    elif engine == "sqlite":
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO ai_jobs (
                task_type, priority, status, dedupe_key, payload_json, payload_hash,
                input_schema_version, policy_version, scope_type, scope_id,
                owner_role, owner_user_pk, source_ref, max_attempts,
                available_at, created_at, updated_at
            )
            VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        created = cursor.rowcount == 1
        row = None
    else:
        raise ValueError(f"Unsupported AI job database engine: {engine!r}")
    if row is None:
        row = conn.execute("SELECT * FROM ai_jobs WHERE dedupe_key = ? LIMIT 1", (normalized_dedupe,)).fetchone()
    if row is None:
        raise RuntimeError("AI job insert completed without a readable row")
    return _row_dict(row), created


def _task_type_filter(task_types: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    normalized = tuple(dict.fromkeys(str(item or "").strip() for item in task_types if str(item or "").strip()))
    if not normalized:
        return "", ()
    return f" AND task_type IN ({','.join('?' for _ in normalized)})", normalized


def _claim_postgres(
    conn,
    *,
    limit: int,
    worker_id: str,
    lease_expires_at: str,
    now: str,
    task_types: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    task_filter, task_params = _task_type_filter(task_types)
    rows = conn.execute(
        f"""
        WITH candidates AS (
            SELECT id
            FROM ai_jobs
            WHERE ((
                    status IN ('queued', 'retry_wait')
                    AND available_at <= ?
                  )
               OR (
                    status = 'running'
                    AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                  ))
              {task_filter}
            ORDER BY priority ASC, available_at ASC, id ASC
            LIMIT ?
            FOR UPDATE SKIP LOCKED
        )
        UPDATE ai_jobs AS jobs
        SET status = 'running',
            attempt_count = jobs.attempt_count + 1,
            locked_at = ?, locked_by = ?, lease_token = ?,
            lease_expires_at = ?, heartbeat_at = ?,
            started_at = COALESCE(jobs.started_at, ?), updated_at = ?,
            last_error_code = '', last_error = ''
        FROM candidates
        WHERE jobs.id = candidates.id
        RETURNING jobs.*
        """,
        (now, now, *task_params, limit, now, worker_id, uuid.uuid4().hex, lease_expires_at, now, now, now),
    ).fetchall()
    # The SQL above intentionally uses one token for a batch. A batch is owned by
    # one worker and each completion still requires job id + token.
    return [_row_dict(row) for row in rows]


def _claim_sqlite(
    conn,
    *,
    limit: int,
    worker_id: str,
    lease_expires_at: str,
    now: str,
    task_types: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    task_filter, task_params = _task_type_filter(task_types)
    rows = conn.execute(
        f"""
        SELECT * FROM ai_jobs
        WHERE ((status IN ('queued', 'retry_wait') AND available_at <= ?)
           OR (status = 'running' AND (lease_expires_at IS NULL OR lease_expires_at <= ?)))
          {task_filter}
        ORDER BY priority ASC, available_at ASC, id ASC
        LIMIT ?
        """,
        (now, now, *task_params, limit),
    ).fetchall()
    claimed: list[dict[str, Any]] = []
    for raw in rows:
        row = _row_dict(raw)
        token = uuid.uuid4().hex
        cursor = conn.execute(
            """
            UPDATE ai_jobs
            SET status = 'running', attempt_count = attempt_count + 1,
                locked_at = ?, locked_by = ?, lease_token = ?,
                lease_expires_at = ?, heartbeat_at = ?,
                started_at = COALESCE(started_at, ?), updated_at = ?,
                last_error_code = '', last_error = ''
            WHERE id = ?
              AND (
                    (status IN ('queued', 'retry_wait') AND available_at <= ?)
                    OR (status = 'running' AND (lease_expires_at IS NULL OR lease_expires_at <= ?))
                  )
            """,
            (now, worker_id, token, lease_expires_at, now, now, now, int(row["id"]), now, now),
        )
        if cursor.rowcount == 1:
            refreshed = conn.execute("SELECT * FROM ai_jobs WHERE id = ?", (int(row["id"]),)).fetchone()
            if refreshed:
                claimed.append(_row_dict(refreshed))
    return claimed


def claim_due_ai_jobs(
    *,
    limit: int = 1,
    worker_id: str | None = None,
    lease_seconds: int | None = None,
    task_types: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 1), 20))
    worker = str(worker_id or os.getenv("AI_JOB_WORKER_ID") or socket.gethostname()).strip()[:80]
    now_dt = _now()
    now = _iso(now_dt)
    lease = max(60, int(lease_seconds or 900))
    lease_expires_at = _iso(now_dt + timedelta(seconds=lease))
    with get_db_connection() as conn:
        ensure_ai_job_schema(conn)
        engine = get_configured_db_engine()
        if engine == "postgres":
            claimed = _claim_postgres(
                conn,
                limit=safe_limit,
                worker_id=worker,
                lease_expires_at=lease_expires_at,
                now=now,
                task_types=task_types,
            )
        elif engine == "sqlite":
            claimed = _claim_sqlite(
                conn,
                limit=safe_limit,
                worker_id=worker,
                lease_expires_at=lease_expires_at,
                now=now,
                task_types=task_types,
            )
        else:
            raise ValueError(f"Unsupported AI job database engine: {engine!r}")
        conn.commit()
    return claimed


def claim_result_ready_ai_jobs(
    *,
    limit: int = 1,
    worker_id: str | None = None,
    lease_seconds: int = 300,
    task_types: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 1), 20))
    worker = str(worker_id or os.getenv("AI_JOB_WORKER_ID") or socket.gethostname()).strip()[:80]
    now_dt = _now()
    now = _iso(now_dt)
    expires = _iso(now_dt + timedelta(seconds=max(60, int(lease_seconds))))
    engine = get_configured_db_engine()
    with get_db_connection() as conn:
        ensure_ai_job_schema(conn)
        task_filter, task_params = _task_type_filter(task_types)
        if engine == "postgres":
            rows = conn.execute(
                f"""
                WITH candidates AS (
                    SELECT id FROM ai_jobs
                    WHERE status = 'result_ready'
                      AND available_at <= ?
                      AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                      {task_filter}
                    ORDER BY priority ASC, available_at ASC, id ASC
                    LIMIT ?
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE ai_jobs AS jobs
                SET delivery_attempt_count = jobs.delivery_attempt_count + 1,
                    locked_at = ?, locked_by = ?, lease_token = ?,
                    lease_expires_at = ?, heartbeat_at = ?, updated_at = ?
                FROM candidates
                WHERE jobs.id = candidates.id
                RETURNING jobs.*
                """,
                (now, now, *task_params, safe_limit, now, worker, uuid.uuid4().hex, expires, now, now),
            ).fetchall()
            claimed = [_row_dict(row) for row in rows]
        elif engine == "sqlite":
            rows = conn.execute(
                f"""
                SELECT * FROM ai_jobs
                WHERE status = 'result_ready' AND available_at <= ?
                  AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                  {task_filter}
                ORDER BY priority ASC, available_at ASC, id ASC
                LIMIT ?
                """,
                (now, now, *task_params, safe_limit),
            ).fetchall()
            claimed = []
            for raw in rows:
                row = _row_dict(raw)
                token = uuid.uuid4().hex
                cursor = conn.execute(
                    """
                    UPDATE ai_jobs
                    SET delivery_attempt_count = delivery_attempt_count + 1,
                        locked_at = ?, locked_by = ?, lease_token = ?,
                        lease_expires_at = ?, heartbeat_at = ?, updated_at = ?
                    WHERE id = ? AND status = 'result_ready' AND available_at <= ?
                      AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                    """,
                    (now, worker, token, expires, now, now, int(row["id"]), now, now),
                )
                if cursor.rowcount == 1:
                    refreshed = conn.execute("SELECT * FROM ai_jobs WHERE id = ?", (int(row["id"]),)).fetchone()
                    if refreshed:
                        claimed.append(_row_dict(refreshed))
        else:
            raise ValueError(f"Unsupported AI job database engine: {engine!r}")
        conn.commit()
    return claimed


def renew_ai_job_lease(job_id: int, lease_token: str, *, lease_seconds: int = 900) -> bool:
    now_dt = _now()
    with get_db_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE ai_jobs
            SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
            WHERE id = ? AND status = 'running' AND lease_token = ?
            """,
            (
                _iso(now_dt),
                _iso(now_dt + timedelta(seconds=max(60, int(lease_seconds)))),
                _iso(now_dt),
                int(job_id),
                str(lease_token),
            ),
        )
        conn.commit()
        return cursor.rowcount == 1


def record_ai_job_attempt_started(conn, job: dict[str, Any], *, stage: str = "execute") -> None:
    attempt_no = int(job.get("attempt_count") or 1)
    engine = get_configured_db_engine()
    params = (int(job["id"]), attempt_no, str(stage), _iso())
    if engine == "postgres":
        conn.execute(
            """
            INSERT INTO ai_job_attempts (job_id, attempt_no, stage, status, started_at)
            VALUES (?, ?, ?, 'running', ?)
            ON CONFLICT (job_id, attempt_no, stage) DO NOTHING
            """,
            params,
        )
    else:
        conn.execute(
            """
            INSERT OR IGNORE INTO ai_job_attempts (job_id, attempt_no, stage, status, started_at)
            VALUES (?, ?, ?, 'running', ?)
            """,
            params,
        )


def record_ai_job_attempt_finished(
    conn,
    job: dict[str, Any],
    *,
    stage: str = "execute",
    status: str,
    provider: str = "",
    model: str = "",
    error_code: str = "",
    error_message: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        UPDATE ai_job_attempts
        SET status = ?, provider = ?, model = ?, error_code = ?,
            error_message = ?, metadata_json = ?, finished_at = ?
        WHERE job_id = ? AND attempt_no = ? AND stage = ?
        """,
        (
            str(status),
            str(provider or ""),
            str(model or ""),
            str(error_code or ""),
            _safe_error(error_message),
            _json_dumps(metadata or {}),
            _iso(),
            int(job["id"]),
            int(job.get("attempt_count") or 1),
            str(stage),
        ),
    )


def store_ai_job_result(
    job: dict[str, Any],
    result: dict[str, Any],
    *,
    provider: str = "",
    model: str = "",
    prompt_version: str = "",
    rubric_hash: str = "",
    policy_version: str = "",
    deterministic_version: str = "",
    confidence: float | None = None,
    review_required: bool = False,
    quality_audit: dict[str, Any] | None = None,
    attempt_status: str = "success",
    attempt_error_code: str = "",
    attempt_error_message: str = "",
) -> dict[str, Any]:
    result_json = _json_dumps(result)
    result_hash = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
    now = _iso()
    with get_db_connection() as conn:
        record_ai_job_attempt_finished(
            conn,
            job,
            status=attempt_status,
            provider=provider,
            model=model,
            error_code=attempt_error_code,
            error_message=attempt_error_message,
        )
        engine = get_configured_db_engine()
        params = (
            int(job["id"]), result_hash, result_json, provider, model,
            prompt_version, rubric_hash, policy_version, deterministic_version,
            confidence, 1 if review_required else 0, _json_dumps(quality_audit or {}), now,
        )
        row = None
        if engine == "postgres":
            row = conn.execute(
                """
                INSERT INTO ai_job_results (
                    job_id, result_hash, result_json, status, provider, model,
                    prompt_version, rubric_hash, policy_version, deterministic_version,
                    confidence, review_required, quality_audit_json, created_at
                )
                VALUES (?, ?, ?, 'candidate', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (job_id, result_hash) DO UPDATE SET result_json = excluded.result_json
                RETURNING *
                """,
                params,
            ).fetchone()
        else:
            conn.execute(
                """
                INSERT OR IGNORE INTO ai_job_results (
                    job_id, result_hash, result_json, status, provider, model,
                    prompt_version, rubric_hash, policy_version, deterministic_version,
                    confidence, review_required, quality_audit_json, created_at
                )
                VALUES (?, ?, ?, 'candidate', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )
        if row is None:
            row = conn.execute(
                "SELECT * FROM ai_job_results WHERE job_id = ? AND result_hash = ?",
                (int(job["id"]), result_hash),
            ).fetchone()
        result_row = _row_dict(row)
        cursor = conn.execute(
            """
            UPDATE ai_jobs
            SET status = 'result_ready', result_id = ?, review_required = ?,
                lease_expires_at = NULL, heartbeat_at = ?, updated_at = ?
            WHERE id = ? AND status = 'running' AND lease_token = ?
            """,
            (
                int(result_row["id"]), 1 if review_required else 0,
                now, now, int(job["id"]), str(job.get("lease_token") or ""),
            ),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            raise RuntimeError("AI job lease changed before result could be stored")
        conn.commit()
        return result_row


def reschedule_ai_job(job: dict[str, Any], *, error_code: str, error_message: str) -> str:
    attempt_count = int(job.get("attempt_count") or 1)
    max_attempts = int(job.get("max_attempts") or durable_task_policy(str(job.get("task_type"))).max_attempts)
    policy = durable_task_policy(str(job.get("task_type") or ""))
    terminal = attempt_count >= max_attempts
    status = policy.failure_terminal if terminal else JOB_RETRY_WAIT
    backoff = BACKOFF_SECONDS[min(max(attempt_count - 1, 0), len(BACKOFF_SECONDS) - 1)]
    jitter = int(job.get("id") or 0) % 7
    available_at = _iso(_now() + timedelta(seconds=backoff + jitter))
    now = _iso()
    with get_db_connection() as conn:
        record_ai_job_attempt_finished(
            conn,
            job,
            status="error",
            error_code=error_code,
            error_message=error_message,
        )
        cursor = conn.execute(
            """
            UPDATE ai_jobs
            SET status = ?, available_at = ?, locked_at = NULL, locked_by = '',
                lease_token = '', lease_expires_at = NULL, heartbeat_at = NULL,
                review_required = ?, last_error_code = ?, last_error = ?,
                updated_at = ?, finished_at = ?
            WHERE id = ? AND status = 'running' AND lease_token = ?
            """,
            (
                status, available_at, 1 if status == JOB_REVIEW_REQUIRED else 0,
                str(error_code or ""), _safe_error(error_message), now,
                now if terminal else None, int(job["id"]), str(job.get("lease_token") or ""),
            ),
        )
        conn.commit()
        if cursor.rowcount != 1:
            return JOB_SUPERSEDED
    return status


def reschedule_ai_job_delivery(job: dict[str, Any], *, error_message: str) -> None:
    delivery_attempt = max(1, int(job.get("delivery_attempt_count") or 1))
    backoff = BACKOFF_SECONDS[min(delivery_attempt - 1, len(BACKOFF_SECONDS) - 1)]
    available_at = _iso(_now() + timedelta(seconds=backoff + int(job.get("id") or 0) % 7))
    now = _iso()
    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE ai_jobs
            SET available_at = ?, locked_at = NULL, locked_by = '', lease_token = '',
                lease_expires_at = NULL, heartbeat_at = NULL,
                last_error_code = 'delivery_failed', last_error = ?, updated_at = ?
            WHERE id = ? AND status = 'result_ready' AND lease_token = ?
            """,
            (available_at, _safe_error(error_message), now, int(job["id"]), str(job.get("lease_token") or "")),
        )
        conn.commit()


def mark_ai_job_succeeded(
    job_id: int,
    result_id: int,
    *,
    review_required: bool = False,
    lease_token: str | None = None,
) -> bool:
    status = JOB_REVIEW_REQUIRED if review_required else JOB_SUCCEEDED
    now = _iso()
    with get_db_connection() as conn:
        token_filter = " AND lease_token = ?" if lease_token else ""
        params: list[Any] = [
            status, int(result_id), 1 if review_required else 0,
            now, now, int(job_id), int(result_id),
        ]
        if lease_token:
            params.append(str(lease_token))
        cursor = conn.execute(
            f"""
            UPDATE ai_jobs
            SET status = ?, result_id = ?, review_required = ?,
                locked_at = NULL, locked_by = '', lease_token = '',
                lease_expires_at = NULL, heartbeat_at = NULL,
                updated_at = ?, finished_at = ?
            WHERE id = ? AND status = 'result_ready' AND result_id = ?
            {token_filter}
            """,
            tuple(params),
        )
        if cursor.rowcount == 1:
            conn.execute("UPDATE ai_job_results SET status = ? WHERE id = ?", (status, int(result_id)))
        conn.commit()
        return cursor.rowcount == 1


def cancel_ai_jobs_for_source(
    conn,
    *,
    task_type: str,
    source_ref: str,
    owner_user_pk: int | None = None,
    reason: str = "cancelled_by_user",
) -> int:
    """Cancel unfinished jobs for one business object.

    The status transition also invalidates any active lease. A worker that is
    already inside a provider request therefore cannot persist or apply a late
    result after the user has cancelled the task.
    """

    now = _iso()
    owner_filter = " AND owner_user_pk = ?" if owner_user_pk is not None else ""
    params: list[Any] = [
        JOB_CANCELLED,
        str(reason or "cancelled_by_user"),
        now,
        now,
        str(task_type or ""),
        str(source_ref or ""),
    ]
    if owner_user_pk is not None:
        params.append(int(owner_user_pk))
    cursor = conn.execute(
        f"""
        UPDATE ai_jobs
        SET status = ?, last_error_code = ?, last_error = '',
            locked_at = NULL, locked_by = '', lease_token = '',
            lease_expires_at = NULL, heartbeat_at = NULL,
            updated_at = ?, finished_at = ?
        WHERE task_type = ? AND source_ref = ?
          AND status IN ('queued', 'running', 'retry_wait', 'result_ready')
          {owner_filter}
        """,
        tuple(params),
    )
    return int(cursor.rowcount or 0)


def cancel_ai_job_by_id(conn, job_id: int, *, reason: str = "cancelled_by_admin") -> dict[str, Any]:
    now = _iso()
    cursor = conn.execute(
        """
        UPDATE ai_jobs
        SET status = 'cancelled', last_error_code = ?, last_error = '',
            locked_at = NULL, locked_by = '', lease_token = '',
            lease_expires_at = NULL, heartbeat_at = NULL,
            updated_at = ?, finished_at = ?
        WHERE id = ? AND status IN ('queued', 'running', 'retry_wait', 'result_ready')
        """,
        (str(reason or "cancelled_by_admin"), now, now, int(job_id)),
    )
    row = conn.execute("SELECT * FROM ai_jobs WHERE id = ?", (int(job_id),)).fetchone()
    if not row:
        raise ValueError("AI job does not exist")
    if cursor.rowcount != 1:
        raise ValueError("AI job is already terminal and cannot be cancelled")
    return _row_dict(row)


def requeue_ai_job(conn, job_id: int, *, reason: str = "manual_requeue") -> dict[str, Any]:
    row = conn.execute("SELECT * FROM ai_jobs WHERE id = ?", (int(job_id),)).fetchone()
    if not row:
        raise ValueError("AI job does not exist")
    job = _row_dict(row)
    if str(job.get("status") or "") not in {
        JOB_REVIEW_REQUIRED,
        JOB_REJECTED,
        JOB_DEAD_LETTER,
        JOB_CANCELLED,
        JOB_SUPERSEDED,
    }:
        raise ValueError("Only terminal AI jobs can be requeued")
    if str(job.get("task_type") or "") == "document_import":
        load_ai_job_input_files(load_ai_job_payload(job).get("input_files") or [])
    extra_attempts = durable_task_policy(str(job.get("task_type") or "")).max_attempts
    now = _iso()
    cursor = conn.execute(
        """
        UPDATE ai_jobs
        SET status = 'queued', available_at = ?, max_attempts = attempt_count + ?,
            result_id = NULL, review_required = 0,
            locked_at = NULL, locked_by = '', lease_token = '',
            lease_expires_at = NULL, heartbeat_at = NULL,
            last_error_code = ?, last_error = '',
            updated_at = ?, finished_at = NULL
        WHERE id = ? AND status IN ('review_required', 'rejected', 'dead_letter', 'cancelled', 'superseded')
        """,
        (now, int(extra_attempts), str(reason or "manual_requeue"), now, int(job_id)),
    )
    if cursor.rowcount != 1:
        raise ValueError("AI job state changed before requeue")
    refreshed = conn.execute("SELECT * FROM ai_jobs WHERE id = ?", (int(job_id),)).fetchone()
    return _row_dict(refreshed)


def load_ai_job_payload(job: dict[str, Any]) -> dict[str, Any]:
    return _json_loads(job.get("payload_json"))


def load_ai_job_result(job: dict[str, Any]) -> dict[str, Any]:
    result_id = job.get("result_id")
    if not result_id:
        return {}
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM ai_job_results WHERE id = ?", (int(result_id),)).fetchone()
    result_row = _row_dict(row)
    result_row["result"] = _json_loads(result_row.get("result_json"))
    return result_row


def ai_durable_job_health_snapshot() -> dict[str, Any]:
    with get_db_connection() as conn:
        ensure_ai_job_schema(conn)
        rows = conn.execute(
            """
            SELECT status, COUNT(*) AS count, MIN(created_at) AS oldest_created_at
            FROM ai_jobs
            WHERE status IN ('queued', 'retry_wait', 'running', 'result_ready', 'review_required', 'dead_letter')
            GROUP BY status
            """
        ).fetchall()
    by_status = {
        str(row["status"]): {
            "count": int(row["count"] or 0),
            "oldest_created_at": str(row["oldest_created_at"] or ""),
        }
        for row in rows
    }
    return {
        "worker_id": str(os.getenv("AI_JOB_WORKER_ID") or socket.gethostname()),
        "by_status": by_status,
        "active_count": sum(item["count"] for status, item in by_status.items() if status in ACTIVE_JOB_STATUSES),
        "review_count": int((by_status.get(JOB_REVIEW_REQUIRED) or {}).get("count") or 0),
        "dead_letter_count": int((by_status.get(JOB_DEAD_LETTER) or {}).get("count") or 0),
    }


def cleanup_terminal_ai_job_files(*, retention_days: int = 7) -> int:
    """Best-effort storage cleanup; immutable DB audit rows are retained."""

    cutoff = _iso(_now() - timedelta(days=max(1, int(retention_days or 7))))
    with get_db_connection() as conn:
        ensure_ai_job_schema(conn)
        rows = conn.execute(
            """
            SELECT task_type, payload_json
            FROM ai_jobs
            WHERE status IN ('succeeded', 'review_required', 'rejected', 'dead_letter', 'cancelled', 'superseded')
              AND COALESCE(finished_at, updated_at) < ?
            """,
            (cutoff,),
        ).fetchall()
    cleaned = 0
    for row in rows:
        payload = _json_loads(row["payload_json"])
        if str(row["task_type"] or "") == "exam_generation":
            reference = payload.get("artifact_ref") or {}
            if reference:
                cleanup_ai_job_artifact(reference)
                cleaned += 1
        elif str(row["task_type"] or "") == "document_import":
            references = payload.get("input_files") or []
            if references:
                cleanup_ai_job_input_files(references)
                cleaned += len(references)
    return cleaned
