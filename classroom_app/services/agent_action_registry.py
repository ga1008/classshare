"""结果落地白名单动作注册表（G3）。

Agent 在最终输出里附带 ``proposed_actions`` JSON 提案；平台解析、校验后渲染
为按钮，用户预览/编辑/确认后由平台以当前用户身份执行白名单函数。

原则：
- 只有注册表里的动作能执行；参数逐字段按 schema 清洗，多余字段丢弃。
- 默认走低风险草稿；少数教师确认后的公开动作（如发布博客、发表评论）可直接落地。
- 发送消息必须提供当前用户可联系的明确身份，禁止通过姓名猜测收件人。
- 每次执行写审计事件（谁、何时、什么动作、参数摘要、结果）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from typing import Any

from fastapi import HTTPException

from ..config import SECRET_KEY

MAX_PROPOSED_ACTIONS = 4
ACTION_CONFIRMATION_TOKEN_TTL_SECONDS = 10 * 60
ACTION_CONFIRMATION_TOKEN_PREFIX = "agt-act-v1."

# 注入 runtime prompt 的示例提案。模型常把整段提示词回显进输出，导致平台把这个
# 示例误抽成「可一键落地的动作」。把它抽成常量，既给 prompt 用，也作为抽取时的黑名单。
PROPOSED_ACTIONS_PROMPT_EXAMPLE: dict[str, Any] = {
    "action": "create_assignment_draft",
    "summary": "为《XX课程》创建作业草稿《第3章练习》",
    "params": {
        "class_offering_id": 12,
        "title": "第3章练习",
        "requirements_md": "# 作业要求\n……",
        "rubric_md": "| 维度 | 分值 |\n| --- | --- |",
    },
}

# 字段 schema：type ∈ {int, str, text, str_list}；text 为长文本。
AGENT_ACTION_DEFINITIONS: dict[str, dict[str, Any]] = {
    "generate_session_document": {
        "label": "生成课时学习文档", "done_label": "已提交文档生成", "risk": "medium", "execution_mode": "execute",
        "description": "使用现有课时材料服务创建持久生成任务；返回任务编号后查询 session.document_task 获得最终材料及绑定回执。",
        "fields": {"class_offering_id": {"type": "int", "required": True}, "session_id": {"type": "int", "required": True},
                   "mode": {"type": "str", "max_chars": 20}, "document_type": {"type": "str", "max_chars": 80},
                   "requirement_text": {"type": "text", "max_chars": 10000}},
    },
    "update_class_attributes": {
        "label": "修改班级属性", "done_label": "已修改班级", "risk": "medium", "execution_mode": "execute",
        "description": "复用正常班级维护权限修改属性；expected_updated_at 来自最新属性，空版本使用 legacy。",
        "fields": {"class_id": {"type": "int", "required": True}, "expected_updated_at": {"type": "str", "required": True, "max_chars": 80},
                   **{key: {"type": "str", "max_chars": 200} for key in ("name", "school_code", "school_name", "college", "department", "major", "scope_level")},
                   "description": {"type": "text", "max_chars": 10000}},
    },
    "update_course_attributes": {
        "label": "修改课程属性", "done_label": "已修改课程", "risk": "medium", "execution_mode": "execute",
        "description": "复用正常课程维护权限修改属性；expected_updated_at 来自最新属性，空版本使用 legacy。",
        "fields": {"course_id": {"type": "int", "required": True}, "expected_updated_at": {"type": "str", "required": True, "max_chars": 80},
                   **{key: {"type": "str", "max_chars": 200} for key in ("name", "sect_name", "school_code", "school_name", "college", "department", "scope_level")},
                   "description": {"type": "text", "max_chars": 10000}},
    },
    "update_textbook_attributes": {
        "label": "修改教材属性", "done_label": "已修改教材", "risk": "medium", "execution_mode": "execute",
        "description": "复用正常教材维护权限修改属性；expected_updated_at 来自最新属性，空版本使用 legacy。",
        "fields": {"textbook_id": {"type": "int", "required": True}, "expected_updated_at": {"type": "str", "required": True, "max_chars": 80},
                   **{key: {"type": "str", "max_chars": 200} for key in ("title", "publisher", "publication_date", "scope_level")},
                   **{key: {"type": "str_list", "max_items": 30} for key in ("authors", "tags")}},
    },
    "create_organization_school": {
        "label": "维护学校目录", "done_label": "已保存学校", "risk": "medium", "execution_mode": "execute", "requires_super_admin": True,
        "description": "管理员使用正常组织目录服务创建学校；同代码已存在时按正常页面语义更新名称并启用。",
        "fields": {"school_code": {"type": "str", "required": True, "max_chars": 80}, "school_name": {"type": "str", "required": True, "max_chars": 200}},
    },
    "create_assignment_draft": {
        "label": "创建为作业草稿",
        "done_label": "已创建作业草稿",
        "risk": "low",
        "execution_mode": "execute",
        "description": "在指定课堂创建一份作业草稿（不发布、学生不可见）。",
        "confirmation_note": "确认后将以你的身份创建作业草稿（不会直接面向学生）。",
        "fields": {
            "class_offering_id": {"type": "int", "required": True, "label": "课堂"},
            "title": {"type": "str", "required": True, "max_chars": 120, "label": "作业标题", "editable": True},
            "requirements_md": {"type": "text", "required": True, "max_chars": 60000, "label": "作业要求"},
            "rubric_md": {"type": "text", "required": False, "max_chars": 20000, "label": "评分标准"},
        },
    },
    "save_material_draft": {
        "label": "存为课堂材料草稿",
        "done_label": "已存入材料库",
        "risk": "low",
        "execution_mode": "execute",
        "description": "把内容保存到教师材料库「Agent 草稿」目录（标记 AI 生成）。",
        "confirmation_note": "确认后将以你的身份存入材料库草稿目录（不会自动发布给学生）。",
        "fields": {
            "title": {"type": "str", "required": True, "max_chars": 80, "label": "材料名称", "editable": True},
            "content_md": {"type": "text", "required": True, "max_chars": 120000, "label": "材料内容"},
        },
    },
    "create_blog_draft": {
        "roles": ["teacher", "student"],
        "label": "创建为博客草稿",
        "done_label": "已创建博客草稿",
        "risk": "low",
        "execution_mode": "execute",
        "description": "在博客草稿箱创建一篇草稿（不公开发布）。",
        "confirmation_note": "确认后将以你的身份创建博客草稿（不会公开发布）。",
        "fields": {
            "title": {"type": "str", "required": True, "max_chars": 120, "label": "博客标题", "editable": True},
            "content_md": {"type": "text", "required": True, "max_chars": 60000, "label": "正文"},
            "tags": {"type": "str_list", "required": False, "max_items": 8, "label": "标签"},
        },
    },
    "publish_blog_post": {
        "roles": ["teacher", "student"],
        "label": "发布博客",
        "done_label": "已发布博客",
        "risk": "medium",
        "execution_mode": "execute",
        "description": "以当前用户身份发布一篇博客，按正常博客的可见范围与权限执行。",
        "confirmation_note": "确认后将以你的身份发布博客，学生或其他可见用户可能立即看到。",
        "fields": {
            "title": {"type": "str", "required": True, "max_chars": 120, "label": "博客标题", "editable": True},
            "content_md": {"type": "text", "required": True, "max_chars": 60000, "label": "正文"},
            "tags": {"type": "str_list", "required": False, "max_items": 8, "label": "标签"},
            "visibility": {"type": "str", "required": False, "max_chars": 32, "label": "可见范围"},
            "visible_class_id": {"type": "int", "required": False, "label": "可见班级"},
        },
    },
    "create_blog_comment": {
        "roles": ["teacher", "student"],
        "label": "发表评论",
        "done_label": "已发表评论",
        "risk": "medium",
        "execution_mode": "execute",
        "description": "以当前用户身份在指定博客下发表评论或回复，遵守正常博客可见范围与评论权限。",
        "confirmation_note": "确认后将以你的身份发表评论，帖子作者和可见用户可能立即看到。",
        "fields": {
            "post_id": {"type": "int", "required": True, "label": "博客"},
            "content_md": {"type": "text", "required": True, "max_chars": 5000, "label": "评论内容"},
            "parent_comment_id": {"type": "int", "required": False, "label": "回复的评论"},
        },
    },
    "send_student_notification": {
        "label": "发送学生通知",
        "done_label": "已发送通知",
        "risk": "medium",
        "execution_mode": "execute",
        "description": "向明确选择且当前用户可联系的学生发送平台私信通知。",
        "confirmation_note": "请核对收件人身份；确认后将通过消息中心以你的身份发送。",
        "fields": {
            "title": {"type": "str", "required": False, "max_chars": 80, "label": "通知标题", "editable": True},
            "content_md": {"type": "text", "required": True, "max_chars": 4000, "label": "通知内容"},
            "recipient_identities": {"type": "str_list", "required": True, "max_items": 30, "label": "学生收件人身份（student:编号）", "editable": True},
            "class_offering_id": {"type": "int", "required": False, "label": "课堂"},
            "student_names": {"type": "str_list", "required": False, "max_items": 60, "label": "建议收件人"},
        },
    },
    "send_private_message": {
        "roles": ["teacher", "student"], "label": "发送私信", "done_label": "已发送私信", "risk": "medium", "execution_mode": "execute",
        "description": "通过消息中心向当前用户可联系的师生发送私信。", "confirmation_note": "确认后将以你的身份向选定联系人发送私信。",
        "fields": {"contact_identity": {"type": "str", "required": True, "max_chars": 80, "label": "联系人身份（角色:编号）", "editable": True},
                   "class_offering_id": {"type": "int", "required": False, "label": "课堂"},
                   "content": {"type": "text", "required": True, "max_chars": 4000, "label": "私信内容"}},
    },
}


from .agent_material_actions import ACTION_DEFINITIONS as MATERIAL_ACTION_DEFINITIONS
from .agent_organization_actions import ACTION_DEFINITIONS as ORGANIZATION_ACTION_DEFINITIONS
from .agent_identity_management_adapter import IDENTITY_ACTION_DEFINITIONS
from .agent_assignment_actions import ACTION_DEFINITIONS as ASSIGNMENT_ACTION_DEFINITIONS
from .agent_secure_account_actions import SECURE_ACTION_DEFINITIONS

AGENT_ACTION_DEFINITIONS.update(MATERIAL_ACTION_DEFINITIONS)
AGENT_ACTION_DEFINITIONS.update(ORGANIZATION_ACTION_DEFINITIONS)
AGENT_ACTION_DEFINITIONS.update(IDENTITY_ACTION_DEFINITIONS)
AGENT_ACTION_DEFINITIONS.update(ASSIGNMENT_ACTION_DEFINITIONS)
AGENT_ACTION_DEFINITIONS.update(SECURE_ACTION_DEFINITIONS)


def ensure_action_actor_role(action: str, actor_role: str) -> None:
    definition = AGENT_ACTION_DEFINITIONS.get(action)
    if not definition or actor_role not in definition.get("roles", ["teacher"]):
        raise HTTPException(403, "当前身份无权执行此平台动作。")


def _clean_str(value: Any, *, max_chars: int) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    if len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def validate_action_params(
    action: str,
    params: Any,
    *,
    reject_unknown: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """按注册表 schema 校验/清洗参数。返回 (clean_params, errors)。"""
    definition = AGENT_ACTION_DEFINITIONS.get(str(action or ""))
    if not definition:
        return {}, [f"未知动作：{action}"]
    if not isinstance(params, dict):
        return {}, ["params 必须是 JSON 对象"]
    clean: dict[str, Any] = {}
    errors: list[str] = []
    if reject_unknown:
        allowed = set(definition["fields"].keys())
        for key in sorted(set(str(item) for item in params.keys()) - allowed):
            errors.append(f"字段 {key} 不在动作 schema 中")
    for field_name, spec in definition["fields"].items():
        raw = params.get(field_name)
        field_type = spec["type"]
        if raw in (None, "", []) and not (spec.get("allow_empty") and field_name in params and raw is not None):
            if spec.get("required"):
                errors.append(f"缺少必填字段 {field_name}")
            continue
        if field_type == "int":
            try:
                if isinstance(raw, bool) or isinstance(raw, float):
                    raise ValueError
                parsed = int(raw)
            except (TypeError, ValueError):
                errors.append(f"字段 {field_name} 必须是整数")
                continue
            minimum = int(spec.get("minimum", 1))
            maximum = int(spec.get("maximum", 2**63 - 1))
            if not minimum <= parsed <= maximum:
                errors.append(f"字段 {field_name} 必须在 {minimum} 至 {maximum} 之间")
                continue
            clean[field_name] = parsed
        elif field_type == "bool":
            if type(raw) is not bool:
                errors.append(f"字段 {field_name} 必须是布尔值")
                continue
            clean[field_name] = raw
        elif field_type in ("str", "text"):
            text = _clean_str(raw, max_chars=int(spec.get("max_chars") or 4000))
            if not text and spec.get("required"):
                errors.append(f"字段 {field_name} 不能为空")
                continue
            if text or spec.get("allow_empty"):
                clean[field_name] = text
        elif field_type == "str_list":
            if not isinstance(raw, list):
                raw = [raw]
            items = [
                _clean_str(item, max_chars=80)
                for item in raw[: int(spec.get("max_items") or 8)]
            ]
            clean[field_name] = [item for item in items if item]
        else:  # pragma: no cover - registry misconfiguration guard
            errors.append(f"字段 {field_name} 的类型未支持")
    return clean, errors


def _secret_key_bytes() -> bytes:
    return hashlib.sha256(str(SECRET_KEY or "lanshare-agent-action").encode("utf-8")).digest()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _params_hash(params: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(params).encode("utf-8")).hexdigest()


def issue_action_confirmation_token(
    *,
    teacher_id: int,
    actor_role: str = "teacher",
    task_id: int,
    action_index: int,
    action: str,
    params: dict[str, Any],
    ttl_seconds: int = ACTION_CONFIRMATION_TOKEN_TTL_SECONDS,
) -> dict[str, Any]:
    """Issue a short-lived token binding one previewed action and exact params."""
    clean_params, errors = validate_action_params(action, params, reject_unknown=True)
    if errors:
        raise HTTPException(status_code=400, detail="；".join(errors[:4]))
    expires_at = int(time.time()) + max(30, min(int(ttl_seconds or ACTION_CONFIRMATION_TOKEN_TTL_SECONDS), 3600))
    payload = {
        "v": 1,
        "teacher_id": int(teacher_id),
        "actor_role": actor_role,
        "task_id": int(task_id),
        "action_index": int(action_index),
        "action": str(action or ""),
        "params_hash": _params_hash(clean_params),
        "exp": expires_at,
    }
    payload_b64 = _b64encode(_canonical_json(payload).encode("utf-8"))
    signature = hmac.new(_secret_key_bytes(), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return {
        "confirmation_token": ACTION_CONFIRMATION_TOKEN_PREFIX + payload_b64 + "." + _b64encode(signature),
        "expires_at": expires_at,
        "expires_in_seconds": max(0, expires_at - int(time.time())),
        "params": clean_params,
    }


def verify_action_confirmation_token(
    *,
    token: str,
    teacher_id: int,
    actor_role: str = "teacher",
    task_id: int,
    action_index: int,
    action: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Validate a confirmation token and return the exact clean params it confirms."""
    raw = str(token or "")
    if not raw:
        raise HTTPException(status_code=400, detail="缺少动作确认令牌，请重新预览后确认。")
    if not raw.startswith(ACTION_CONFIRMATION_TOKEN_PREFIX):
        raise HTTPException(status_code=403, detail="动作确认令牌无效，请重新预览后确认。")
    try:
        body = raw[len(ACTION_CONFIRMATION_TOKEN_PREFIX) :]
        payload_b64, signature_b64 = body.split(".", 1)
        expected = hmac.new(_secret_key_bytes(), payload_b64.encode("ascii"), hashlib.sha256).digest()
        actual = _b64decode(signature_b64)
        if not hmac.compare_digest(expected, actual):
            raise ValueError("signature mismatch")
        payload = json.loads(_b64decode(payload_b64).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=403, detail="动作确认令牌无效，请重新预览后确认。") from exc

    if int(payload.get("exp") or 0) < int(time.time()):
        raise HTTPException(status_code=403, detail="动作确认已过期，请重新预览后确认。")
    expected_scope = {
        "teacher_id": int(teacher_id),
        "task_id": int(task_id),
        "action_index": int(action_index),
        "action": str(action or ""),
    }
    if payload.get("actor_role", "teacher") != actor_role:
        raise HTTPException(status_code=403, detail="动作确认令牌与当前身份不匹配。")
    for key, expected_value in expected_scope.items():
        if payload.get(key) != expected_value:
            raise HTTPException(status_code=403, detail="动作确认令牌与当前任务不匹配。")
    clean_params, errors = validate_action_params(action, params, reject_unknown=True)
    if errors:
        raise HTTPException(status_code=400, detail="；".join(errors[:4]))
    if payload.get("params_hash") != _params_hash(clean_params):
        raise HTTPException(status_code=409, detail="动作参数已变化，请重新预览后确认。")
    return clean_params


def _candidate_json_payloads(text: str) -> list[Any]:
    payloads: list[Any] = []
    for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text):
        payloads.append(match.group(1))
    payloads.append(text)
    parsed_items: list[Any] = []
    for raw in payloads:
        snippet = str(raw or "").strip()
        if "proposed_actions" not in snippet:
            continue
        start = snippet.find("{")
        if start < 0:
            continue
        decoder = json.JSONDecoder()
        index = start
        while index < len(snippet):
            brace = snippet.find("{", index)
            if brace < 0:
                break
            try:
                parsed, _end = decoder.raw_decode(snippet[brace:])
            except json.JSONDecodeError:
                index = brace + 1
                continue
            parsed_items.append(parsed)
            break
    return parsed_items


_PROPOSED_ACTIONS_FENCE_RE = re.compile(
    r"`{3,}[ \t]*[\w-]*[ \t]*\n?(?:(?!`{3,})[\s\S])*?proposed_actions(?:(?!`{3,})[\s\S])*?`{3,}",
    re.IGNORECASE,
)


def strip_proposed_actions_block(text: Any) -> str:
    """移除模型为「结构化动作提案」附在结论末尾的 proposed_actions JSON 块。

    平台已经把提案解析成确认按钮，原始协议 JSON 不应再展示给教师。优先删除围栏代码块；
    若提案以裸 JSON 对象出现，则按花括号配对删除该对象。删不掉的残留保持原样，绝不破坏正文。
    """
    source = str(text or "")
    if "proposed_actions" not in source:
        return source
    cleaned = _PROPOSED_ACTIONS_FENCE_RE.sub("", source)
    if "proposed_actions" in cleaned:
        index = cleaned.find("proposed_actions")
        start = cleaned.rfind("{", 0, index)
        if start >= 0:
            try:
                _obj, end = json.JSONDecoder().raw_decode(cleaned[start:])
                cleaned = cleaned[:start] + cleaned[start + end :]
            except json.JSONDecodeError:
                pass
    # 收尾常见的悬空引导语（如「结构化动作提案：」）和多余空行。
    cleaned = re.sub(r"(?:结构化动作提案|可执行动作提案|proposed[\s_]*actions)\s*[:：]?\s*$", "", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _is_prompt_example_action(action: str, params: dict[str, Any]) -> bool:
    """判断是否为提示词里那条示例提案（回显进输出时必须丢弃）。"""
    if action != PROPOSED_ACTIONS_PROMPT_EXAMPLE["action"]:
        return False
    example_clean, _errors = validate_action_params(
        PROPOSED_ACTIONS_PROMPT_EXAMPLE["action"],
        PROPOSED_ACTIONS_PROMPT_EXAMPLE["params"],
    )
    return params == example_clean


def extract_proposed_actions(text: Any) -> list[dict[str, Any]]:
    """从模型输出文本中抽取 proposed_actions 提案（容忍残缺，无效条目丢弃）。"""
    source = str(text or "")
    if "proposed_actions" not in source:
        return []
    proposals: list[dict[str, Any]] = []
    for payload in _candidate_json_payloads(source):
        if not isinstance(payload, dict):
            continue
        raw_actions = payload.get("proposed_actions")
        if not isinstance(raw_actions, list):
            continue
        for entry in raw_actions:
            if not isinstance(entry, dict):
                continue
            action = str(entry.get("action") or entry.get("type") or "").strip()
            definition = AGENT_ACTION_DEFINITIONS.get(action)
            if not definition:
                continue
            params, errors = validate_action_params(action, entry.get("params") or {})
            if errors:
                continue
            if _is_prompt_example_action(action, params):
                # 模型回显了提示词里的示例提案，丢弃——它不是真实可落地的动作。
                continue
            proposals.append(
                {
                    "action": action,
                    "label": definition["label"],
                    "risk": definition["risk"],
                    "execution_mode": definition["execution_mode"],
                    "confirmation_note": definition.get("confirmation_note") or "",
                    "summary": _clean_str(
                        entry.get("summary") or definition["description"], max_chars=200
                    ),
                    "params": params,
                    "executed": None,
                }
            )
            if len(proposals) >= MAX_PROPOSED_ACTIONS:
                return proposals
        if proposals:
            return proposals
    return proposals


def proposed_actions_prompt_block(*, actor_role: str = "teacher") -> str:
    """注入 runtime prompt 的提案协议说明。"""
    lines = [
        "结构化动作提案：用户已经明确要求执行、且 platform_capabilities 提供对应写能力时，"
        "直接用 platform_write 完成并报告实际回执，不要再附重复执行提案。"
        "只有尚需用户确认的新操作才在最终输出末尾附以下 JSON（平台会渲染确认按钮）；"
        "user_input_actions 中的 secure_input 动作必须生成提案，由用户在平台安全表单填写密码；"
        "不要用提问工具索取密码，不要将密码放入 params、消息、文件或工具参数。"
        "缺少必要参数先使用提问工具补齐，单纯可选建议不要生成动作提案。待确认提案不代表已写入平台：",
        "```json",
        json.dumps(
            {"proposed_actions": [PROPOSED_ACTIONS_PROMPT_EXAMPLE if actor_role == "teacher" else {
                "action": "create_blog_draft", "label": "保存学习总结草稿",
                "params": {"title": "学习总结", "content_md": "请替换为本次总结正文。"},
            }]},
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "（上面只是格式示例，必须换成本次任务的真实动作和参数，不要原样照抄示例文字。）",
        "可用动作与完整参数以 platform_capabilities(keys=[动作名称]) 的当前返回为准；索引不包含参数，不能凭名称猜字段。",
    ]
    lines.append(
        "规则：最多提案 4 个动作；params 必须完整可执行；没有合适动作就不要输出该 JSON 块。"
        " 如果用户要求发布博客或发表评论，先用平台数据/文件核对内容；若涉及近期政策、新闻、技术标准或其他可能更新的信息，"
        "请先使用搜索和 public_fetch 核验并在正文中保留来源链接，再按用户的明确要求执行或提出待确认动作。"
    )
    return "\n".join(lines)


def _owned_offering(conn, teacher_id: int, class_offering_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT co.id, co.course_id, c.name AS course_name
        FROM class_offerings co
        JOIN courses c ON c.id = co.course_id
        WHERE co.id = ? AND co.teacher_id = ?
        LIMIT 1
        """,
        (int(class_offering_id), int(teacher_id)),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="目标课堂不存在或不属于当前教师。")
    return dict(row)


def _ensure_agent_draft_folder(conn, teacher_id: int) -> dict[str, Any]:
    from .session_material_generation_service import _create_folder_row
    from ..db.connection import get_configured_db_engine

    # Serialize folders/names across different concurrent tasks of one actor.
    if get_configured_db_engine() == "postgres":
        conn.execute("SELECT id FROM teachers WHERE id=? FOR UPDATE", (int(teacher_id),)).fetchone()

    row = conn.execute(
        """
        SELECT * FROM course_materials
        WHERE teacher_id = ? AND parent_id IS NULL AND node_type = 'folder' AND name = ?
        LIMIT 1
        """,
        (int(teacher_id), "Agent 草稿"),
    ).fetchone()
    if row:
        return dict(row)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    return _create_folder_row(
        conn,
        teacher_id=int(teacher_id),
        parent_id=None,
        root_id=None,
        material_path="Agent 草稿",
        name="Agent 草稿",
        now=now,
    )


def _execute_create_assignment_draft(conn, teacher_id: int, params: dict[str, Any]) -> dict[str, Any]:
    from .agent_platform_actions import _create_assignment_draft

    offering = _owned_offering(conn, teacher_id, int(params["class_offering_id"]))
    created = _create_assignment_draft(
        conn,
        course_id=int(offering["course_id"]),
        class_offering_id=int(offering["id"]),
        title=params.get("title") or "Agent 作业草稿",
        requirements_md=params.get("requirements_md") or "",
        rubric_md=params.get("rubric_md") or "",
    )
    return {
        "url": created.get("url") or f"/assignment/{created['id']}",
        "label": f"作业草稿 #{created['id']}",
        "ref_id": int(created["id"]),
    }


def _execute_save_material_draft(conn, teacher_id: int, params: dict[str, Any]) -> dict[str, Any]:
    from datetime import datetime, timezone

    from .session_material_generation_service import _create_file_row, _material_path_join
    from .materials_service import make_unique_material_name

    folder = _ensure_agent_draft_folder(conn, teacher_id)
    title = str(params.get("title") or "Agent 材料草稿").strip()
    name = title if title.lower().endswith(".md") else f"{title}.md"
    name = re.sub(r"[\\/:*?\"<>|]", "-", name)
    name = make_unique_material_name(conn, int(teacher_id), int(folder["id"]), name)
    now = datetime.now(timezone.utc).isoformat()
    content = f"{params.get('content_md') or ''}\n\n> 本文档由 LanShare Agent 生成，教师确认后入库。\n"
    created = _create_file_row(
        conn,
        teacher_id=int(teacher_id),
        parent_id=int(folder["id"]),
        root_id=int(folder.get("root_id") or folder["id"]),
        material_path=_material_path_join(str(folder.get("material_path") or ""), name),
        name=name,
        content=content,
        now=now,
    )
    return {
        "url": f"/materials/view/{int(created['id'])}",
        "label": f"材料：{name}",
        "ref_id": int(created["id"]),
        "file_hash": created["file_hash"],
        "file_size": int(created["file_size"]),
        "storage_status": "verified_immutable_blob",
    }


def _execute_create_blog_draft(conn, teacher_id: int, params: dict[str, Any]) -> dict[str, Any]:
    from .agent_platform_actions import _create_teacher_blog_draft

    created = _create_teacher_blog_draft(
        conn,
        teacher_id=int(teacher_id),
        title=params.get("title") or "Agent 博客草稿",
        content_md=params.get("content_md") or "",
        tags=list(params.get("tags") or []),
    )
    return {
        "url": "/blog?tab=mine",
        "label": f"博客草稿 #{created['id']}",
        "ref_id": int(created["id"]),
    }


def _execute_publish_blog_post(conn, teacher_id: int, params: dict[str, Any]) -> dict[str, Any]:
    from .agent_platform_actions import _create_teacher_blog_post

    created = _create_teacher_blog_post(
        conn,
        teacher_id=int(teacher_id),
        title=params.get("title") or "Agent 博客",
        content_md=params.get("content_md") or "",
        tags=list(params.get("tags") or []),
        status="published",
        visibility=params.get("visibility") or "public",
        visible_class_id=params.get("visible_class_id"),
    )
    return {
        "url": f"/blog?post={int(created['id'])}",
        "label": f"博客 #{created['id']}",
        "ref_id": int(created["id"]),
    }


def _execute_create_blog_comment(conn, teacher_id: int, params: dict[str, Any]) -> dict[str, Any]:
    from .agent_platform_actions import _create_teacher_blog_comment

    created = _create_teacher_blog_comment(
        conn,
        teacher_id=int(teacher_id),
        post_id=int(params["post_id"]),
        content_md=params.get("content_md") or "",
        parent_comment_id=params.get("parent_comment_id"),
    )
    return {
        "url": f"/blog?post={int(created['post_id'])}",
        "label": f"评论 #{created['id']}",
        "ref_id": int(created["id"]),
    }


def _execute_send_student_notification(conn, teacher_id: int, params: dict[str, Any]) -> dict[str, Any]:
    from .agent_platform_write_service import execute_actor_action

    return execute_actor_action(conn, actor_role="teacher", actor_id=teacher_id,
                                action="send_student_notification", params=params)


def _execute_send_private_message(conn, teacher_id: int, params: dict[str, Any]) -> dict[str, Any]:
    from .agent_platform_write_service import execute_actor_action

    return execute_actor_action(conn, actor_role="teacher", actor_id=teacher_id,
                                action="send_private_message", params=params)


_ACTION_EXECUTORS = {
    "create_assignment_draft": _execute_create_assignment_draft,
    "save_material_draft": _execute_save_material_draft,
    "create_blog_draft": _execute_create_blog_draft,
    "publish_blog_post": _execute_publish_blog_post,
    "create_blog_comment": _execute_create_blog_comment,
    "send_student_notification": _execute_send_student_notification,
    "send_private_message": _execute_send_private_message,
}


def execute_proposed_action(
    conn,
    *,
    teacher_id: int,
    action: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """执行一个已校验提案。调用方负责归属校验、审计事件与提交。"""
    clean_params, errors = validate_action_params(action, params)
    if errors:
        raise HTTPException(status_code=400, detail="；".join(errors[:4]))
    executor = _ACTION_EXECUTORS.get(action)
    if not executor:
        raise HTTPException(status_code=400, detail=f"动作 {action} 暂不支持执行。")
    return executor(conn, int(teacher_id), clean_params)
