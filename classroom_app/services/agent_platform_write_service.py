"""Explicit transactional domain adapters; never forwards arbitrary HTTP writes.

The caller commits business rows and receipt together, or rolls back both. Blog
mention jobs use the same transactional scheduler outbox as ordinary Web posts.
"""
from __future__ import annotations

import re
import hashlib
from typing import Any

from fastapi import HTTPException

from .agent_action_registry import AGENT_ACTION_DEFINITIONS, ensure_action_actor_role, validate_action_params
from .agent_actor_service import resolve_agent_actor
from .agent_operation_service import claim_agent_operation, complete_agent_operation
from .agent_material_actions import ACTION_DEFINITIONS as MATERIAL_ACTION_DEFINITIONS, execute_material_action
from .agent_organization_actions import ACTION_DEFINITIONS as ORGANIZATION_ACTION_DEFINITIONS, execute_organization_action
from .agent_identity_management_adapter import IDENTITY_TRANSACTIONAL_ACTIONS, dispatch_identity_write
from .agent_assignment_actions import ACTION_DEFINITIONS as ASSIGNMENT_ACTION_DEFINITIONS, execute_assignment_action
from .agent_assessment_actions import ACTION_DEFINITIONS as ASSESSMENT_ACTION_DEFINITIONS, execute_assessment_action


TRANSACTIONAL_ACTIONS = {"create_assignment_draft", "save_material_draft", "create_blog_draft", "publish_blog_post", "create_blog_comment", "send_student_notification", "send_private_message",
                         "update_class_attributes", "update_course_attributes", "update_textbook_attributes", "create_organization_school", "generate_session_document"}
TRANSACTIONAL_ACTIONS.update(MATERIAL_ACTION_DEFINITIONS)
TRANSACTIONAL_ACTIONS.update(ORGANIZATION_ACTION_DEFINITIONS)
TRANSACTIONAL_ACTIONS.update(IDENTITY_TRANSACTIONAL_ACTIONS)
TRANSACTIONAL_ACTIONS.update(ASSIGNMENT_ACTION_DEFINITIONS)
TRANSACTIONAL_ACTIONS.update(ASSESSMENT_ACTION_DEFINITIONS)


def platform_write_catalog(*, actor_role: str, is_super_admin: bool = False) -> dict[str, Any]:
    return {"coverage": "partial_reviewed_domain_writes", "actions": [
        {"action": action, **{key: definition[key] for key in ("description", "fields", "risk", "execution_mode")}}
        for action, definition in AGENT_ACTION_DEFINITIONS.items()
        if action in TRANSACTIONAL_ACTIONS and actor_role in definition.get("roles", ["teacher"])
        and (not definition.get("requires_super_admin") or is_super_admin)
    ]}


def execute_actor_action(conn, *, actor_role: str, actor_id: int, action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Normal business services, no transaction or external execution boundary."""
    actor = resolve_agent_actor(conn, actor_role, actor_id)
    ensure_action_actor_role(action, actor.role)
    if AGENT_ACTION_DEFINITIONS[action].get("requires_super_admin") and not actor.is_super_admin:
        raise HTTPException(403, "当前账号没有此管理员能力。")
    clean, errors = validate_action_params(action, params, reject_unknown=True)
    if errors:
        raise HTTPException(400, "；".join(errors[:4]))
    user = actor.as_user()
    try:
        if action in ASSESSMENT_ACTION_DEFINITIONS:
            return execute_assessment_action(conn, actor=actor, action=action, params=clean)
        if action in ASSIGNMENT_ACTION_DEFINITIONS:
            return execute_assignment_action(conn, actor=actor, action=action, params=clean)
        if action in ORGANIZATION_ACTION_DEFINITIONS:
            return execute_organization_action(conn, actor=actor, action=action, params=clean)
        if action in MATERIAL_ACTION_DEFINITIONS:
            return execute_material_action(conn, actor=actor, action=action, params=clean)
        if action == "generate_session_document":
            from .session_material_generation_jobs import create_scheduled_generation_task

            task = create_scheduled_generation_task(conn, teacher_id=actor.id, class_offering_id=clean["class_offering_id"], session_id=clean["session_id"],
                   trigger_mode=clean.get("mode") or "guided", document_type=clean.get("document_type") or "", requirement_text=clean.get("requirement_text") or "")
            return {"url": f"/classroom/{clean['class_offering_id']}", "label": "已提交课时文档生成", "ref_id": task["id"],
                    "generation_task": task, "completion_status": "pending", "status_operation": "session.document_task"}
        if action in {"update_class_attributes", "update_course_attributes", "update_textbook_attributes"}:
            return _update_resource_attributes(conn, actor=actor, action=action, params=clean)
        if action in {"create_assignment_draft", "save_material_draft"}:
            from .agent_action_registry import execute_proposed_action

            result = execute_proposed_action(conn, teacher_id=actor.id, action=action, params=clean)
            validate_action_receipt(conn, actor_role=actor.role, actor_id=actor.id, action=action, result=result)
            return result
        if action in {"create_blog_draft", "publish_blog_post"}:
            from .blog_service import create_post
            from .blog_effects_service import enqueue_blog_mention

            result = create_post(conn, user, title=clean["title"], content_md=clean["content_md"], tags=clean.get("tags") or [],
                                 status="draft" if action == "create_blog_draft" else "published", visibility=clean.get("visibility") or "public",
                                 visible_class_id=clean.get("visible_class_id"))
            effect_id = enqueue_blog_mention(conn, user, trigger_type="post", trigger_id=int(result["id"]))
            return {"url": f"/blog?post={int(result['id'])}", "label": "博客草稿" if action == "create_blog_draft" else "已发布博客",
                    "ref_id": int(result["id"]), "effect_task_id": effect_id}
        if action == "create_blog_comment":
            from .blog_service import add_comment
            from .blog_notifications import notify_new_comment, notify_post_hot
            from .blog_effects_service import enqueue_blog_mention

            result = add_comment(conn, user, int(clean["post_id"]), content_md=clean["content_md"], parent_comment_id=clean.get("parent_comment_id"),
                                 notify_callback=notify_new_comment, hot_notify_callback=notify_post_hot)
            effect_id = enqueue_blog_mention(conn, user, trigger_type="comment", trigger_id=int(result["id"]))
            return {"url": f"/blog?post={int(result['post_id'])}", "label": "已发表评论", "ref_id": int(result["id"]), "effect_task_id": effect_id}
        if action in {"send_private_message", "send_student_notification"}:
            from .message_center_service import create_private_message

            recipients = [clean["contact_identity"]] if action == "send_private_message" else list(dict.fromkeys(clean["recipient_identities"]))
            if not recipients or any(not re.fullmatch(r"(?:teacher|student):[1-9][0-9]{0,18}", identity) for identity in recipients):
                raise HTTPException(400, "请选择明确的师生联系人身份。")
            if action == "send_student_notification" and any(not identity.startswith("student:") for identity in recipients):
                raise HTTPException(400, "学生通知只能选择学生收件人。")
            content = clean["content"] if action == "send_private_message" else "\n\n".join(filter(None, (clean.get("title"), clean["content_md"])))
            messages = [create_private_message(conn, user, contact_identity=identity, class_offering_id=clean.get("class_offering_id"), content=content)
                        for identity in recipients]
            identifiers = [int(item["message"]["id"]) for item in messages]
            return {"url": "/message-center", "label": f"已发送 {len(identifiers)} 条私信", "ref_id": identifiers[0], "message_ids": identifiers}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    raise HTTPException(409, "该动作正在完成事务和文件对账适配，请使用现有页面操作。")


def _update_resource_attributes(conn, *, actor, action: str, params: dict[str, Any]) -> dict[str, Any]:
    from ..db.connection import get_configured_db_engine
    from . import base_resource_modes_service as service

    kind = {"update_class_attributes": "class", "update_course_attributes": "course", "update_textbook_attributes": "textbook"}[action]
    table = {"class": "classes", "course": "courses", "textbook": "textbooks"}[kind]
    resource_id = params[f"{kind}_id"]
    # Lock before fetching the normal policy row. SQLite's ledger has already
    # reserved its writer transaction; PostgreSQL needs the resource row lock.
    if get_configured_db_engine() == "postgres":
        conn.execute(f"SELECT id FROM {table} WHERE id=? FOR UPDATE", (resource_id,)).fetchone()
    row = getattr(service, f"ensure_teacher_can_manage_{kind}_attributes")(conn, resource_id, actor.id)
    revision = str(dict(row).get("updated_at") or "legacy")
    if params["expected_updated_at"] != revision:
        raise HTTPException(409, "资源已被更新，请重新读取属性后再提交。")
    payload = {key: value for key, value in params.items() if key not in {f"{kind}_id", "expected_updated_at"}}
    if not payload:
        raise HTTPException(400, "请提供需要修改的属性。")
    getattr(service, f"update_{kind}_attributes")(conn, **{f"{kind}_row": row, "teacher_id": actor.id, "payload": payload})
    refreshed = getattr(service, f"ensure_teacher_can_view_{kind}_attributes")(conn, resource_id, actor.id)
    attributes = getattr(service, f"serialize_{kind}_attributes")(conn, refreshed, actor.id)
    return {"url": f"/manage/{table}", "label": "已保存属性", "ref_id": resource_id, "attributes": attributes}


def validate_action_receipt(conn, *, actor_role: str, actor_id: int, action: str, result: dict[str, Any]) -> None:
    """Reconcile a material receipt with its owned row and immutable blob.

    A failed transaction may leave an unreferenced content-addressed blob. It is
    deliberately retained: another transaction may reference the same hash.
    The ordinary db_file_integrity inventory reports those storage orphans.
    """
    if action != "save_material_draft":
        return
    from .file_service import resolve_global_file_path

    row = conn.execute("SELECT file_hash,file_size FROM course_materials WHERE id=? AND teacher_id=?", (result.get("ref_id"), int(actor_id))).fetchone()
    if actor_role != "teacher" or not row or row["file_hash"] != result.get("file_hash") or int(row["file_size"]) != result.get("file_size"):
        raise HTTPException(409, "材料回执与当前记录不一致，请在材料库核对。")
    path = resolve_global_file_path(str(row["file_hash"]))
    if not path or path.stat().st_size != int(row["file_size"]) or hashlib.sha256(path.read_bytes()).hexdigest() != row["file_hash"]:
        raise HTTPException(409, "材料文件不存在或完整性校验失败，请联系管理员核对。")


def dispatch_write(conn, token: str, operation_id: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
    if action in IDENTITY_TRANSACTIONAL_ACTIONS:
        return dispatch_identity_write(conn, token, operation_id, action, params)
    if action not in TRANSACTIONAL_ACTIONS:
        raise HTTPException(409, "该平台动作尚需领域适配。")
    clean, errors = validate_action_params(action, params, reject_unknown=True)
    if errors:
        raise HTTPException(400, "；".join(errors[:4]))
    if action in {"update_organization_college", "update_organization_department"}:
        from .agent_identity_management_adapter import begin_authority_changing_operation, complete_authority_changing_operation

        claimed = begin_authority_changing_operation(conn, token, operation_id, action, clean)
        operation = claimed["operation"]
        if not claimed["claimed"]:
            if operation["status"] != "completed":
                raise HTTPException(409, "组织变更尚无确定回执，请先核对。")
            return {"operation_id": operation_id, "replayed": True, "result": operation["result"]}
        actor = resolve_agent_actor(conn, operation["actor_role"], operation["actor_id"])
        from .agent_organization_actions import organization_authority_affected_teachers

        affected = organization_authority_affected_teachers(conn, action, clean)
        result = execute_organization_action(conn, actor=actor, action=action, params=clean)
        receipt = complete_authority_changing_operation(conn, claimed["proof"], result, affected_teacher_ids=affected)
        return {"operation_id": operation_id, "replayed": False, "result": receipt["result"]}
    claim = claim_agent_operation(conn, token=token, operation_id=operation_id, action=action, params=clean, required_scope="platform:write")
    operation = claim["operation"]
    if not claim["claimed"]:
        if operation["status"] != "completed":
            raise HTTPException(409, "该操作尚未有确定回执，需先核对状态。")
        validate_action_receipt(conn, actor_role=operation["actor_role"], actor_id=int(operation["actor_id"]), action=action, result=operation["result"])
        return {"operation_id": operation_id, "replayed": True, "result": operation["result"]}
    result = execute_actor_action(conn, actor_role=operation["actor_role"], actor_id=int(operation["actor_id"]), action=action, params=clean)
    complete_agent_operation(conn, token=token, operation_id=operation_id, result=result, required_scope="platform:write")
    return {"operation_id": operation_id, "replayed": False, "result": result}


def dispatch_user_write(conn, *, user: dict, source_session_id: str, task_id: int,
                        operation_id: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Fresh terminal-task confirmation; caller commits receipt and proposal together."""
    from .agent_operation_service import claim_user_agent_operation, complete_user_agent_operation
    from .agent_identity_management_adapter import (
        dispatch_user_identity_write, begin_user_authority_changing_operation,
        complete_user_authority_changing_operation,
    )

    arguments = dict(user=user, source_session_id=source_session_id, task_id=task_id,
                     operation_id=operation_id, action=action, params=params)
    if action in IDENTITY_TRANSACTIONAL_ACTIONS:
        return dispatch_user_identity_write(conn, **arguments)
    if action not in TRANSACTIONAL_ACTIONS:
        raise HTTPException(409, "该平台动作尚需领域适配。")
    clean, errors = validate_action_params(action, params, reject_unknown=True)
    if errors:
        raise HTTPException(400, "；".join(errors[:4]))
    arguments["params"] = clean
    authority_change = action in {"update_organization_college", "update_organization_department"}
    claim = (begin_user_authority_changing_operation if authority_change else claim_user_agent_operation)(conn, **arguments)
    operation = claim["operation"]
    if not claim["claimed"]:
        if operation["status"] != "completed":
            raise HTTPException(409, "该操作尚未有确定回执，请核对后重试。")
        validate_action_receipt(conn, actor_role=user["role"], actor_id=int(user["id"]), action=action, result=operation["result"])
        return {"operation_id": operation_id, "replayed": True, "result": operation["result"]}
    affected = []
    if authority_change:
        from .agent_organization_actions import organization_authority_affected_teachers
        affected = organization_authority_affected_teachers(conn, action, clean)
    result = execute_actor_action(conn, actor_role=user["role"], actor_id=int(user["id"]), action=action, params=clean)
    if authority_change:
        receipt = complete_user_authority_changing_operation(conn, claim["proof"], result, affected_teacher_ids=affected)
        result = receipt["result"]
    else:
        complete_user_agent_operation(conn, user=user, source_session_id=source_session_id,
                                     task_id=task_id, operation_id=operation_id, result=result)
    return {"operation_id": operation_id, "replayed": False, "result": result}
