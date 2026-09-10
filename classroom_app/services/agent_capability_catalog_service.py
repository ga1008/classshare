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
    from .agent_user_confirmation_actions import user_confirmation_action_catalog
    from .agent_file_capability_catalog import file_transport_catalog
    from .agent_platform_route_capability import ROUTE_KEY_PREFIX, route_capability_details, route_capability_inventory

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
    confirmations = user_confirmation_action_catalog(actor_role=actor_role, is_super_admin=is_super_admin)
    routes = route_capability_inventory(app, actor_role=actor_role, is_super_admin=is_super_admin)
    groups = {"read": value["operations"], "write": writes["actions"], "request": requests,
              "secure_input": secure, "user_confirmation": confirmations,
              "file_transports": file_transport_catalog(), "routes": routes}
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
            output.append({name: item[name] for name in ("key", "action", "title", "label", "domain", "status", "executable", "execution_mode", "method", "path", "risk", "tool") if name in item})
        return output

    value["operations"] = select(groups["read"])
    writes["actions"] = select(groups["write"])
    if keys is not None:
        # Route parameters come from the live OpenAPI schema and handler digest,
        # so they are resolved per key rather than pre-rendered for the index.
        platform_routes, _unavailable = route_capability_details(app, [key for key in keys if key.startswith(ROUTE_KEY_PREFIX)])
        known.update(item["key"] for item in platform_routes)
        for item in groups["routes"]:
            known.add(item["key"])
    else:
        platform_routes = select(groups["routes"])
    value.update(writes=writes, platform_requests=select(groups["request"]), platform_routes=platform_routes,
        file_transports=select(groups["file_transports"]),
        user_input_actions=select(groups["secure_input"]) + select(groups["user_confirmation"]),
        catalog_mode="parameters" if keys is not None else "index", available_counts=totals,
        request_guarantee="普通平台请求保留接口观察回执；业务是否最终完成须按返回结果继续核对。",
        route_guarantee="platform_routes 覆盖全站 JSON 接口，以用户本人实时权限执行；status=route_ready 的用 platform_request 调用，route_confirmation_required 的只能提出 platform_route_request 提案由用户确认。",
        next_step="索引仅提供名称。调用 platform_capabilities(keys=[所选名称]) 获取完整参数，再调用对应工具；query可按中英文名称、方法或路径检索索引。优先使用审核能力（read/write/request）；无对应审核能力时使用 platform_routes。file_transports分为文本抽取和文件字节复制，按条目tool调用；secure_input/user_confirmation只能提出由用户安全填写或本人核对的确认提案。")
    if keys is not None:
        value["unavailable_keys"] = [key for key in keys if key not in known]
    return value
