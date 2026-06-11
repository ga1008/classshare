"""结果落地白名单动作注册表（G3）。

Agent 在最终输出里附带 ``proposed_actions`` JSON 提案；平台解析、校验后渲染
为按钮，教师预览/编辑/确认后由平台以教师身份执行白名单函数。

原则：
- 只有注册表里的动作能执行；参数逐字段按 schema 清洗，多余字段丢弃。
- 首批动作一律落草稿态（作业草稿/材料草稿/博客草稿），不直接面向学生。
- 发送学生通知降级为「预填跳转」：按钮带着内容跳到消息中心，由教师手动发送。
- 每次执行写审计事件（谁、何时、什么动作、参数摘要、结果）。
"""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException

MAX_PROPOSED_ACTIONS = 4

# 字段 schema：type ∈ {int, str, text, str_list}；text 为长文本。
AGENT_ACTION_DEFINITIONS: dict[str, dict[str, Any]] = {
    "create_assignment_draft": {
        "label": "创建为作业草稿",
        "done_label": "已创建作业草稿",
        "risk": "low",
        "execution_mode": "execute",
        "description": "在指定课堂创建一份作业草稿（不发布、学生不可见）。",
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
        "fields": {
            "title": {"type": "str", "required": True, "max_chars": 80, "label": "材料名称", "editable": True},
            "content_md": {"type": "text", "required": True, "max_chars": 120000, "label": "材料内容"},
        },
    },
    "create_blog_draft": {
        "label": "创建为博客草稿",
        "done_label": "已创建博客草稿",
        "risk": "low",
        "execution_mode": "execute",
        "description": "在博客草稿箱创建一篇草稿（不公开发布）。",
        "fields": {
            "title": {"type": "str", "required": True, "max_chars": 120, "label": "博客标题", "editable": True},
            "content_md": {"type": "text", "required": True, "max_chars": 60000, "label": "正文"},
            "tags": {"type": "str_list", "required": False, "max_items": 8, "label": "标签"},
        },
    },
    "send_student_notification": {
        "label": "去消息中心发送通知",
        "done_label": "已打开消息中心",
        "risk": "medium",
        "execution_mode": "manual_link",
        "description": "通知内容已拟好；为避免误发，请在消息中心确认收件人后手动发送。",
        "fields": {
            "title": {"type": "str", "required": False, "max_chars": 80, "label": "通知标题", "editable": True},
            "content_md": {"type": "text", "required": True, "max_chars": 8000, "label": "通知内容"},
            "student_names": {"type": "str_list", "required": False, "max_items": 60, "label": "建议收件人"},
        },
    },
}


def _clean_str(value: Any, *, max_chars: int) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    if len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def validate_action_params(action: str, params: Any) -> tuple[dict[str, Any], list[str]]:
    """按注册表 schema 校验/清洗参数。返回 (clean_params, errors)。"""
    definition = AGENT_ACTION_DEFINITIONS.get(str(action or ""))
    if not definition:
        return {}, [f"未知动作：{action}"]
    if not isinstance(params, dict):
        return {}, ["params 必须是 JSON 对象"]
    clean: dict[str, Any] = {}
    errors: list[str] = []
    for field_name, spec in definition["fields"].items():
        raw = params.get(field_name)
        field_type = spec["type"]
        if raw in (None, "", []):
            if spec.get("required"):
                errors.append(f"缺少必填字段 {field_name}")
            continue
        if field_type == "int":
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                errors.append(f"字段 {field_name} 必须是整数")
                continue
            if parsed <= 0:
                errors.append(f"字段 {field_name} 必须是正整数")
                continue
            clean[field_name] = parsed
        elif field_type in ("str", "text"):
            text = _clean_str(raw, max_chars=int(spec.get("max_chars") or 4000))
            if not text and spec.get("required"):
                errors.append(f"字段 {field_name} 不能为空")
                continue
            if text:
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
            proposals.append(
                {
                    "action": action,
                    "label": definition["label"],
                    "risk": definition["risk"],
                    "execution_mode": definition["execution_mode"],
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


def proposed_actions_prompt_block() -> str:
    """注入 runtime prompt 的提案协议说明。"""
    lines = [
        "结构化动作提案（重要）：如果你的结论包含可以在平台落地的产物"
        "（作业草案、课堂材料、博客草稿、学生通知文案），请在最终输出的末尾"
        "附带一个 ```json 代码块，格式如下（平台会渲染成确认按钮，教师点击后才会执行，"
        "你自己永远不要声称已经写入平台）：",
        "```json",
        json.dumps(
            {
                "proposed_actions": [
                    {
                        "action": "create_assignment_draft",
                        "summary": "为《XX课程》创建作业草稿《第3章练习》",
                        "params": {
                            "class_offering_id": 12,
                            "title": "第3章练习",
                            "requirements_md": "# 作业要求\n……",
                            "rubric_md": "| 维度 | 分值 |\n| --- | --- |",
                        },
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "可用动作与参数：",
    ]
    for action, definition in AGENT_ACTION_DEFINITIONS.items():
        fields = ", ".join(
            f"{name}({spec['type']}{'，必填' if spec.get('required') else ''})"
            for name, spec in definition["fields"].items()
        )
        lines.append(f"- {action}：{definition['description']} 参数：{fields}")
    lines.append(
        "规则：最多提案 4 个动作；params 必须完整可执行；没有合适动作就不要输出该 JSON 块。"
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

    folder = _ensure_agent_draft_folder(conn, teacher_id)
    title = str(params.get("title") or "Agent 材料草稿").strip()
    name = title if title.lower().endswith(".md") else f"{title}.md"
    name = re.sub(r"[\\/:*?\"<>|]", "-", name)
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


def _execute_send_student_notification(conn, teacher_id: int, params: dict[str, Any]) -> dict[str, Any]:
    # 降级为预填跳转：不直接群发，教师在消息中心确认收件人后手动发送。
    return {
        "url": "/messages",
        "label": "打开消息中心",
        "manual": True,
        "copy_text": params.get("content_md") or "",
    }


_ACTION_EXECUTORS = {
    "create_assignment_draft": _execute_create_assignment_draft,
    "save_material_draft": _execute_save_material_draft,
    "create_blog_draft": _execute_create_blog_draft,
    "send_student_notification": _execute_send_student_notification,
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
