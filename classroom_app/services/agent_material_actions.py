"""Material actions using the same domain policies and transactions as Web."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException


_BINDING_FIELDS = {
    "class_offering_id": {"type": "int", "required": True},
    "session_id": {"type": "int", "minimum": 0},
    "material_id": {"type": "int", "required": True},
    "expected_binding_version": {"type": "str", "required": True, "max_chars": 64},
}

ACTION_DEFINITIONS = {
    "update_material_attributes": {
        "label": "修改材料属性", "done_label": "已保存材料属性", "risk": "medium", "execution_mode": "execute",
        "description": "按正常材料维护权限重命名或调整整棵材料树开放范围；必须提供 materials.attributes 返回的 updated_at，空版本使用 legacy。",
        "fields": {"material_id": {"type": "int", "required": True},
                   "expected_updated_at": {"type": "str", "required": True, "max_chars": 80},
                   "name": {"type": "str", "max_chars": 200}, "scope_level": {"type": "str", "max_chars": 20}},
    },
    "bind_learning_material": {
        "label": "绑定学习材料", "done_label": "已绑定学习材料", "risk": "medium", "execution_mode": "execute",
        "description": "向本人课堂的材料列表添加现有文档并按正常流程授予课堂阅读权限；省略 session_id 表示课堂首页，expected_binding_version 来自 classroom.materials。",
        "fields": _BINDING_FIELDS,
    },
    "unbind_learning_material": {
        "label": "解绑学习材料", "done_label": "已解绑学习材料", "risk": "medium", "execution_mode": "execute",
        "description": "从本人课堂/课时材料列表解绑文档，自动同步主材料；省略 session_id 表示首页，expected_binding_version 来自 classroom.materials。保留文件及正常课堂访问分配。",
        "fields": _BINDING_FIELDS,
    },
}


def execute_material_action(conn, *, actor, action: str, params: dict[str, Any]) -> dict[str, Any]:
    if actor.role != "teacher":
        raise HTTPException(403, "当前账号不能维护课堂材料。")
    if action == "update_material_attributes":
        from .material_attributes_service import update_material_attributes

        changes = {key: params[key] for key in ("name", "scope_level") if key in params}
        if not changes:
            raise HTTPException(400, "请提供需要修改的材料属性。")
        row = update_material_attributes(conn, material_id=params["material_id"], teacher_id=actor.id,
                                         payload=changes, expected_updated_at=params["expected_updated_at"])
        attributes = {key: dict(row).get(key) for key in ("id", "parent_id", "root_id", "name", "material_path", "scope_level", "updated_at")}
        return {"ref_id": int(row["id"]), "url": "/manage/materials", "label": "已保存材料属性", "attributes": attributes}
    if action not in {"bind_learning_material", "unbind_learning_material"}:
        raise HTTPException(400, "未知材料动作。")
    from . import session_learning_materials_service as service

    offering_id, session_id, material_id = params["class_offering_id"], params.get("session_id", 0), params["material_id"]
    # The ordinary binding service checks the same owner and locks this row.
    service._ensure_offering_owner(conn, offering_id, actor.id)
    if service.material_binding_version(conn, offering_id, session_id) != params["expected_binding_version"]:
        raise HTTPException(409, "材料列表已变化，请重新读取课堂材料后再提交。")
    if action == "unbind_learning_material":
        bound_ids = {int(row["material_id"]) for row in service._fetch_rows(conn, offering_id, session_id)} if service.has_material_bindings_table(conn) else set()
        bound_ids.add(service._primary_material_id(conn, offering_id, session_id))
        if material_id not in bound_ids:
            raise HTTPException(404, "所选材料未绑定到此处。")
    operation = service.bind_material_in_transaction if action == "bind_learning_material" else service.unbind_material_in_transaction
    result = operation(conn, offering_id, session_id, material_id, actor.id)
    return {"ref_id": material_id, "url": f"/classroom/{offering_id}", "label": ACTION_DEFINITIONS[action]["done_label"],
            **result, "class_offering_id": offering_id,
            "primary_material_id": service._primary_material_id(conn, offering_id, session_id),
            "binding_version": service.material_binding_version(conn, offering_id, session_id)}
