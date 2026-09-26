"""Discover all reviewed abilities, then fetch only the schemas a task needs."""
import re

from fastapi import HTTPException

# The index is English labels + paths; teachers and the model search in
# Chinese. Each query token matches if it, or any expansion, is a substring of
# the item text. Kept small and literal on purpose: this is not a thesaurus.
QUERY_SYNONYMS = {
    "保存": ("save", "configure", "config", "update", "set", "upsert"), "配置": ("config", "configure", "setting"),
    "设置": ("set", "setting", "config", "configure"), "修改": ("update", "edit", "patch", "modify", "set"),
    "编辑": ("edit", "update"), "更新": ("update", "sync", "refresh"), "生成": ("generate", "create", "build"),
    "创建": ("create", "add", "new"), "新建": ("create", "add", "new"), "添加": ("add", "create", "append"),
    "删除": ("delete", "remove"), "移除": ("remove", "delete"), "查询": ("get", "list", "query", "search", "read"),
    "读取": ("get", "read", "list"), "列表": ("list", "get"), "查看": ("get", "list", "read", "view"),
    "搜索": ("search", "query", "find"), "发布": ("publish", "post", "release"), "提交": ("submit", "post"),
    "导入": ("import", "upload"), "导出": ("export", "download"), "上传": ("upload", "import"),
    "下载": ("download", "export"), "同步": ("sync",), "统计": ("stats", "summary", "report", "analytics"),
    "报告": ("report",), "课堂": ("offering", "classroom", "class"), "班级": ("class", "offering"),
    "课程": ("course", "offering"), "课次": ("session", "lesson"), "作业": ("assignment", "homework"),
    "考试": ("exam", "test"), "测验": ("quiz", "exam"), "成绩": ("grade", "score"), "批改": ("grade", "grading", "review"),
    "学生": ("student",), "教师": ("teacher",), "教材": ("textbook", "book"), "材料": ("material",),
    "文档": ("doc", "document", "lessondoc"), "学期": ("semester", "term"), "签名": ("signature",),
    "消息": ("message", "notification"), "通知": ("notification", "notice", "message"), "投票": ("poll", "vote"),
    "分组": ("group",), "小组": ("group",), "签到": ("attendance", "checkin"), "出勤": ("attendance",),
    "博客": ("blog", "post"), "公文": ("gongwen", "document"), "简历": ("resume",), "教案": ("lesson-plan", "lesson_plan", "lessonplan"),
    "评学": ("evaluation",), "考核": ("assessment",), "提示词": ("prompt", "system_prompt"), "大纲": ("syllabus", "outline"),
    "助手": ("ai", "assistant"), "助教": ("ai", "assistant"), "智能": ("ai",), "白板": ("whiteboard",),
    "日历": ("calendar",), "课表": ("schedule", "timetable"), "反馈": ("feedback",), "申请": ("request", "apply", "application"),
    "审批": ("approval", "approve", "review"), "管理": ("manage", "admin"),
}
_TOKEN = re.compile(r"[a-z0-9_./-]+|[一-鿿]+")
MAX_QUERY_HITS = 40


def _query_terms(query):
    """Tokens plus their expansions; a Chinese run is also split into 2-char chunks."""
    terms = []
    for token in _TOKEN.findall(query.casefold()):
        expansions = [token]
        if re.fullmatch(r"[一-鿿]+", token):
            pieces = [token] + [token[i:i + 2] for i in range(len(token) - 1)] if len(token) > 2 else [token]
            for piece in pieces:
                expansions.extend(QUERY_SYNONYMS.get(piece, ()))
        else:
            expansions.extend(QUERY_SYNONYMS.get(token, ()))
            expansions.append(token.replace("_", "-"))
            expansions.append(token.replace("-", "_"))
        terms.append(tuple(dict.fromkeys(expansions)))
    return terms


def _item_text(item):
    return " ".join(str(item.get(name) or "") for name in ("key", "action", "label", "title", "description", "domain",
                                                            "method", "path", "usage")).casefold()


def rank_catalog_items(items, query):
    """Items matching at least one query term, best first; label/path hits rank above descriptions."""
    terms = _query_terms(query)
    if not terms:
        return []
    ranked = []
    for index, item in enumerate(items):
        text = _item_text(item)
        primary = " ".join(str(item.get(name) or "") for name in ("label", "title", "path", "action", "key")).casefold()
        score = 0
        for expansions in terms:
            hit = next((expansion for expansion in expansions if expansion and expansion in text), None)
            if hit is None:
                continue
            score += 3 if hit in primary else 1
            if hit == expansions[0]:
                score += 1  # the literal token beats a synonym
        if score:
            ranked.append((-score, index, item))
    ranked.sort(key=lambda entry: entry[:2])
    return [item for _score, _index, item in ranked[:MAX_QUERY_HITS]]


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
        for item in items:
            known.add(item.get("key", item.get("action")))
        if keys is not None:
            return [item for item in items if item.get("key", item.get("action")) in keys]
        chosen = rank_catalog_items(items, query) if query else items
        return [{name: item[name] for name in ("key", "action", "title", "label", "domain", "status", "executable", "execution_mode",
                                                "method", "path", "risk", "tool", "usage") if name in item} for item in chosen]

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
        route_guarantee="platform_routes 覆盖全站 JSON 接口，以用户本人实时权限执行，均用 platform_request 调用；status=route_destructive_self_check 的是破坏性操作，必须附带 safety_check 自检（服务端核对），硬性拦截的高危操作不会出现在目录中。",
        next_step="检索结果按相关度排序；platform_routes 每条的 usage 已给出方法、路径、参数与 body 字段，可直接调用。需要完整 schema 时再 platform_capabilities(keys=[所选名称])。query 支持中文同义词、路径片段（如 manage/ai）与方法名。优先使用审核能力（read/write/request）；无对应审核能力时使用 platform_routes。file_transports分为文本抽取和文件字节复制，按条目tool调用；secure_input（密码/凭据）只能由用户本人在平台页面填写，Agent 不能代填。")
    if keys is not None:
        value["unavailable_keys"] = [key for key in keys if key not in known]
    return value
