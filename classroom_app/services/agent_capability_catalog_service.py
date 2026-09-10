"""Discover all reviewed abilities, then fetch only the schemas a task needs."""
from fastapi import HTTPException


def capability_catalog(app, *, actor_role, is_super_admin, keys=None, query=None):
    if keys is not None and (not isinstance(keys, list) or not 1 <= len(keys) <= 8
            or any(not isinstance(key, str) or not 1 <= len(key) <= 160
                   or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in key) for key in keys)
            or len(set(keys)) != len(keys)):
        raise HTTPException(400, "每次请选择1至8个不同能力名称。")
    if query is not None and (not isinstance(query, str) or not 1 <= len(query.strip()) <= 80
            or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in query)):
        raise HTTPException(400, "能力检索词无效。")
    if keys is not None and query is not None:
        raise HTTPException(400, "请先检索能力，再按名称读取参数。")

    from .agent_action_registry import AGENT_ACTION_DEFINITIONS
    from .agent_platform_registry import platform_read_catalog
    from .agent_platform_write_service import platform_write_catalog
    from .agent_platform_request_registry import platform_request_catalog
    from .agent_secure_account_actions import secure_action_catalog

    value = platform_read_catalog(app, actor_role=actor_role, is_super_admin=is_super_admin)
    if actor_role == "teacher" and is_super_admin:
        from .agent_identity_management_adapter import identity_read_catalog

        reads = identity_read_catalog()
        value["operations"].extend({**item, "domain": "management", "roles": ["teacher"],
            "requires_super_admin": True, "status": "read_ready", "side_effects": "none",
            "authorization": "normal_platform_account_policy",
            "parameters": {key: {**spec, "in": "query"} for key, spec in item["parameters"].items()}}
            for item in reads)
    writes = platform_write_catalog(actor_role=actor_role, is_super_admin=is_super_admin)
    for item in writes["actions"]:
        item["label"] = AGENT_ACTION_DEFINITIONS[item["action"]]["label"]
    requests = platform_request_catalog(app, include_blocked=True)
    secure = secure_action_catalog(actor_role=actor_role, is_super_admin=is_super_admin)
    groups = {"read": value["operations"], "write": writes["actions"], "request": requests, "secure_input": secure}
    known = set()
    totals = {name: len(items) for name, items in groups.items()}

    def select(items):
        output = []
        for item in items:
            key = item.get("key", item.get("action"))
            known.add(key)
            if keys is not None:
                if key in keys:
                    output.append(item)
                continue
            searchable = " ".join(str(item.get(name) or "") for name in ("key", "action", "label", "title", "description", "domain")).casefold()
            if query and not all(word in searchable for word in query.casefold().split()):
                continue
            output.append({name: item[name] for name in ("key", "action", "title", "label", "domain", "status", "executable", "execution_mode") if name in item})
        return output

    value["operations"] = select(groups["read"])
    writes["actions"] = select(groups["write"])
    value.update(writes=writes, platform_requests=select(groups["request"]), user_input_actions=select(groups["secure_input"]),
        catalog_mode="parameters" if keys is not None else "index", available_counts=totals,
        request_guarantee="普通平台请求保留接口观察回执；业务是否最终完成须按返回结果继续核对。",
        next_step="索引仅提供名称。调用 platform_capabilities(keys=[所选名称]) 获取完整参数，再调用对应工具；query可按中英文名称检索索引。secure_input只能提出由用户安全填写的确认提案。")
    if keys is not None:
        value["unavailable_keys"] = [key for key in keys if key not in known]
    return value
