"""Human-only business confirmations, distinct from credentials and MCP writes.

Only the authenticated proposal execution route calls this dispatcher. A model
may name a proposal, but cannot attest that a teacher reviewed a grade snapshot.
"""
from __future__ import annotations

from fastapi import HTTPException


USER_CONFIRMATION_ACTION_DEFINITIONS = {
    "publish_classroom_grades": {
        "label": "核对并公布课堂成绩", "done_label": "已公布课堂成绩", "risk": "high",
        "execution_mode": "user_confirmation", "roles": ["teacher"], "human_only": True,
        "description": "仅可生成用户确认提案：选择课堂及成绩材料，平台读取最新成绩来源快照，由本人核对成绩、逐项接受警告并填写说明后公布。MCP 和后台任务不能直接执行，也不能代用户确认。",
        "confirmation_note": "请核对本次成绩与来源。公布后，该课堂学生可以看到自己的公布成绩。",
        "fields": {
            "class_offering_id": {"type": "int", "required": True},
            "material_id": {"type": "int", "required": True},
            "expected_source_hash": {"type": "str", "max_chars": 64},
            "expected_review_hash": {"type": "str", "max_chars": 64},
            "expected_version": {"type": "int", "minimum": 0},
        },
    },
}


def user_confirmation_action_catalog(*, actor_role: str, is_super_admin: bool = False) -> list[dict]:
    return [{"action": action, **definition, "executable": False, "status": "requires_user_confirmation"}
            for action, definition in USER_CONFIRMATION_ACTION_DEFINITIONS.items()
            if actor_role in definition.get("roles", [])]


def prepare_user_confirmation(conn, *, action: str, params: dict, user: dict) -> dict:
    """Create the exact review that the server will bind in its proposal token."""
    if user.get("role") != "teacher" or action not in USER_CONFIRMATION_ACTION_DEFINITIONS:
        raise HTTPException(403, "当前身份不能核对该教师业务。")
    from .grade_publication_service import preview_grade_publication, grade_publication_review_hash

    preview = preview_grade_publication(conn, class_offering_id=params["class_offering_id"],
                                       teacher_id=int(user["id"]), material_id=params["material_id"])
    bound = {"class_offering_id": params["class_offering_id"], "material_id": params["material_id"],
             "expected_source_hash": preview["source_hash"], "expected_version": preview["expected_version"],
             "expected_review_hash": grade_publication_review_hash(preview)}
    return {"params": bound, "review": preview}


def dispatch_user_confirmation(conn, *, user: dict, source_session_id: str, task_id: int,
                               operation_id: str, action: str, params: dict, confirmation_inputs: dict) -> dict:
    if user.get("role") != "teacher" or action not in USER_CONFIRMATION_ACTION_DEFINITIONS:
        raise HTTPException(403, "该操作只能由当前教师本人确认。")
    from .agent_action_registry import validate_action_params
    from .agent_operation_service import claim_user_agent_operation, complete_user_agent_operation
    from .grade_publication_service import publish_grade_snapshot

    clean, errors = validate_action_params(action, params, reject_unknown=True)
    if errors or not clean.get("expected_source_hash") or not clean.get("expected_review_hash") or "expected_version" not in clean:
        raise HTTPException(400, "请先核对本次成绩来源快照。")
    if not isinstance(confirmation_inputs, dict) or set(confirmation_inputs) != {"accepted_warning_codes", "confirmation_note"}:
        raise HTTPException(400, "请提交本次核对的警告选择和说明。")
    warnings = confirmation_inputs["accepted_warning_codes"]
    note = confirmation_inputs["confirmation_note"]
    if (not isinstance(warnings, list) or len(warnings) > 30
            or any(not isinstance(code, str) or not 1 <= len(code) <= 100
                   or any(0xD800 <= ord(char) <= 0xDFFF for char in code) for code in warnings)
            or len(set(warnings)) != len(warnings) or not isinstance(note, str) or len(note) > 2000
            or any(0xD800 <= ord(char) <= 0xDFFF for char in note)):
        raise HTTPException(400, "核对警告或说明格式不正确。")
    declaration = {"accepted_warning_codes": sorted(warnings), "confirmation_note": note.strip()}
    recorded_params = {**clean, "user_confirmation": declaration}
    claim = claim_user_agent_operation(conn, user=user, source_session_id=source_session_id, task_id=task_id,
        operation_id=operation_id, action=action, params=recorded_params)
    operation = claim["operation"]
    if not claim["claimed"]:
        if operation["status"] != "completed":
            raise HTTPException(409, "本次成绩公布尚无确定回执，请先核对。")
        return {"operation_id": operation_id, "result": operation["result"], "replayed": True}
    # This server-side literal is intentionally absent from every tool schema
    # and proposal field. The normal service rechecks ownership/source/version,
    # warnings and its classroom lock in this same receipt transaction.
    result = publish_grade_snapshot(conn, teacher_id=int(user["id"]), **clean, confirmed=True, **declaration)
    result = {**result, "label": "已公布课堂成绩", "ref_id": result["publication_id"],
              "url": f"/classroom/{clean['class_offering_id']}", "confirmation_source": "authenticated_user"}
    complete_user_agent_operation(conn, user=user, source_session_id=source_session_id,
        task_id=task_id, operation_id=operation_id, result=result)
    return {"operation_id": operation_id, "result": result, "replayed": False}
