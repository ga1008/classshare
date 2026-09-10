"""Reviewed organization directory operations using ordinary admin services."""
from __future__ import annotations

from fastapi import HTTPException


ACTION_DEFINITIONS = {}
for _kind, _label in (("school", "学校"), ("college", "学院"), ("department", "系部")):
    for _verb, _verb_label in (("create", "新建"), ("update", "修改"), ("delete", "停用")):
        _fields = {}
        if _verb == "create":
            _fields["school_code"] = {"type": "str", "required": True, "max_chars": 80}
            if _kind == "department":
                _fields["college_name"] = {"type": "str", "max_chars": 200}
        else:
            _fields[f"{_kind}_id"] = {"type": "int", "required": True}
            _fields["expected_updated_at"] = {"type": "str", "required": True, "max_chars": 80}
        if _verb != "delete":
            _fields[f"{_kind}_name"] = {"type": "str", "required": True, "max_chars": 200}
            _fields["display_order"] = {"type": "int", "minimum": -1000000, "maximum": 1000000}
        if _verb == "update":
            _fields["is_active"] = {"type": "bool"}
        ACTION_DEFINITIONS[f"{_verb}_organization_{_kind}"] = {
            "label": f"{_verb_label}{_label}目录", "done_label": f"已{_verb_label}{_label}目录", "risk": "medium",
            "execution_mode": "execute", "requires_super_admin": True, "roles": ["teacher"], "fields": _fields,
            "description": f"当前管理员按正常组织目录服务{_verb_label}{_label}。"
                + ("新建按现有代码/名称合并并启用；不会创建重复目录。" if _verb == "create" else "expected_updated_at 来自 organization.tree 最新条目；停用保留目录及全部资源引用。"),
        }


def organization_authority_affected_teachers(conn, action: str, params: dict) -> list[int]:
    """Read under the account-management transaction lock, before renaming scopes."""
    from . import organization_management_service as service
    from .organization_scope_service import normalize_org_text

    kind = {"update_organization_college": "college", "update_organization_department": "department"}.get(action)
    if not kind:
        return []
    table = {"college": "organization_colleges", "department": "organization_departments"}[kind]
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (params[f"{kind}_id"],)).fetchone()
    if not row or normalize_org_text(params[f"{kind}_name"]) == row[f"{kind}_name"]:
        return []
    where = "school_code=? AND TRIM(COALESCE(college,''))=?"
    values = [row["school_code"], row["college_name"]]
    if kind == "department":
        where += " AND TRIM(COALESCE(department,''))=?"
        values.append(row["department_name"])
    affected = set()
    for source, column in (("teachers", "id"), ("teacher_organization_memberships", "teacher_id")):
        if service._table_exists(conn, source):
            affected.update(int(item[0]) for item in conn.execute(f"SELECT DISTINCT {column} FROM {source} WHERE {where}", values).fetchall())
    return sorted(affected)


def execute_organization_action(conn, *, actor, action: str, params: dict) -> dict:
    from ..db.connection import get_configured_db_engine
    from . import organization_management_service as service

    if action not in ACTION_DEFINITIONS or actor.role != "teacher" or not actor.is_super_admin:
        raise HTTPException(403, "只有当前管理员可以维护组织目录。")
    verb, _, kind = action.split("_", 2)
    table = {"school": "organization_schools", "college": "organization_colleges", "department": "organization_departments"}[kind]
    values = {key: value for key, value in params.items() if key != "expected_updated_at"}
    if verb != "create":
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?" + (" FOR UPDATE" if get_configured_db_engine() == "postgres" else ""), (params[f"{kind}_id"],)).fetchone()
        if not row:
            raise HTTPException(404, "组织目录条目不存在。")
        if str(row["updated_at"] or "legacy") != params["expected_updated_at"]:
            raise HTTPException(409, "组织目录已变化，请重新读取组织树后再提交。")
        if verb == "update":
            values.setdefault("display_order", int(row["display_order"] or 0))
            values.setdefault("is_active", bool(row["is_active"]))
    elif kind == "department":
        values.setdefault("college_name", "")
    try:
        item = getattr(service, f"{verb}_{kind}")(conn, **values, actor_teacher_id=actor.id)
    except service.OrganizationManagementError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"url": "/manage/system/organizations", "ref_id": item["id"], "label": ACTION_DEFINITIONS[action]["done_label"], "item": item}
