from __future__ import annotations

import uuid
from typing import Any

from ..config import AI_JOB_POLICY_VERSION
from .ai_durable_job_service import create_ai_job, persist_ai_job_input_files


def stage_process_import_inputs(files: list[dict[str, Any]], *, artifact_key: str | None = None) -> list[dict[str, Any]]:
    return persist_ai_job_input_files(artifact_key or uuid.uuid4().hex, files)


def enqueue_process_import(
    conn,
    *,
    target_type: str,
    target_id: str,
    teacher_id: int,
    input_files: list[dict[str, Any]],
    extra_prompt: str = "",
    scope_id: str = "",
) -> dict[str, Any]:
    row, _ = create_ai_job(
        conn,
        task_type="document_import",
        dedupe_key=f"document-import:{target_type}:{target_id}",
        payload={
            "target_type": target_type,
            "target_id": str(target_id),
            "teacher_id": int(teacher_id),
            "input_files": input_files,
            "extra_prompt": str(extra_prompt or ""),
        },
        scope_type="class_offering" if scope_id else "teacher",
        scope_id=str(scope_id or teacher_id),
        owner_role="teacher",
        owner_user_pk=int(teacher_id),
        source_ref=f"{target_type}:{target_id}",
        policy_version=AI_JOB_POLICY_VERSION,
    )
    return row


def enqueue_process_generation(
    conn,
    *,
    target_type: str,
    target_id: str,
    task_token: str,
    class_offering_id: int,
    teacher_id: int,
    prompt: str = "",
    field_overrides: dict[str, Any] | None = None,
    session_plan: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    row, _ = create_ai_job(
        conn,
        task_type="document_generation",
        dedupe_key=f"document-generation:{target_type}:{target_id}:{task_token}",
        payload={
            "target_type": target_type,
            "target_id": str(target_id),
            "class_offering_id": int(class_offering_id),
            "teacher_id": int(teacher_id),
            "prompt": str(prompt or ""),
            "field_overrides": field_overrides or {},
            "session_plan": session_plan or [],
        },
        scope_type="class_offering",
        scope_id=str(class_offering_id),
        owner_role="teacher",
        owner_user_pk=int(teacher_id),
        source_ref=f"{target_type}:{target_id}",
        policy_version=AI_JOB_POLICY_VERSION,
    )
    return row
